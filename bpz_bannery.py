#!/usr/bin/env python3
"""Bannery Blesk pro ženy z feedu článků – bez n8n a bez HCTI.

Stáhne feed, najde články, které ještě nemají banner, a každý vykreslí
do PNG 1350 × 1350 px pomocí Google Chrome v režimu bez okna.

Použití:
    python3 bpz_bannery.py              # zpracuje všechny nové články
    python3 bpz_bannery.py --limit 3    # nejvýš 3 nové články
    python3 bpz_bannery.py --znovu      # vykreslí znovu i už zpracované

Nepotřebuje žádné knihovny, jen Python 3.9+ a nainstalovaný Google Chrome.
"""
import argparse
import base64
import csv
import html
import json
import os
import shutil
import re
import subprocess
import sys
import tempfile
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote
from email.utils import parsedate_to_datetime
from pathlib import Path

# ---------- nastavení ----------
FEED_URL = "https://content-hub.cncenter.cz/feed/meta-prozeny-20"
SLOZKA = Path(__file__).resolve().parent
VYSTUP = SLOZKA / "bannery"            # sem se ukládají PNG
STAV = SLOZKA / "zpracovane.json"      # ID článků, které už banner mají
LOGO = SLOZKA / "logo.png"
# Chrome: na Macu aplikace, na GitHubu / Linuxu příkaz google-chrome (nebo proměnná CHROME)
CHROME = (
    os.environ.get("CHROME")
    or next((c for c in ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"] if Path(c).exists()), None)
    or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser") or ""
)
VELIKOST = 1350
KATALOG = SLOZKA / "katalog.csv"       # řádky pro Sheet / Meta katalog (celý feed)
BRAND = "Blesk pro ženy"
# Veřejná adresa, pod kterou bude složka s bannery dostupná z internetu,
# např. "https://media.example.cz/bpz-bannery/". Prázdné = zatím nezveřejněno.
VEREJNA_URL = os.environ.get("VEREJNA_URL", "")   # na GitHubu ji doplní workflow
SLOUPCE = ["id", "title", "description", "link", "image_link", "image_link_original",
           "category", "brand", "published", "status", "created_at",
           "availability", "condition", "price"]
# -------------------------------

NS = {"g": "http://base.google.com/ns/1.0"}

CSS = """@import url('https://fonts.googleapis.com/css2?family=Open+Sans:wght@700;800&display=swap');
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { width: 1350px; height: 1350px; font-family: 'Open Sans', Arial, sans-serif; overflow: hidden; background: #16212D; }
.banner { width: 1350px; height: 1350px; position: relative; overflow: hidden; }
.hero-image { width: 100%; height: 100%; object-fit: cover; object-position: center 20%; display: block; }
.overlay { position: absolute; bottom: 0; left: 0; right: 0; height: 65%; background: linear-gradient(to bottom, rgba(22,33,45,0) 0%, rgba(22,33,45,0.2) 25%, rgba(22,33,45,0.7) 60%, rgba(22,33,45,0.94) 100%); }
.logo { position: absolute; top: 40px; left: 40px; width: 220px; height: auto; z-index: 3; filter: drop-shadow(0 4px 14px rgba(0,0,0,0.3)); }
.text { position: absolute; left: 60px; right: 60px; bottom: 78px; z-index: 2; display: flex; flex-direction: column; align-items: flex-start; }
.category { background: #E30613; color: #fff; font-size: 28px; font-weight: 700; line-height: 1; padding: 13px 22px 12px; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 26px; }
.title { font-size: __TITLE_SIZE__px; font-weight: 800; line-height: 1.17; letter-spacing: -0.5px; color: #fff; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; text-overflow: ellipsis; text-shadow: 0 2px 10px rgba(0,0,0,0.45); }
.bottom-bar { position: absolute; bottom: 0; left: 0; right: 0; height: 22px; background: #E30613; z-index: 3; }"""


def nacti_feed():
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        root = ET.fromstring(r.read())
    clanky = []
    for it in root.iter("item"):
        def g(tag):
            return (it.findtext("g:" + tag, namespaces=NS) or "").strip()
        obrazek = g("image_link")
        if not g("id") or not g("title") or not obrazek.startswith("https://"):
            continue
        try:
            datum = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            datum = datetime.now()
        clanky.append({
            "id": g("id"), "title": g("title"), "category": g("category"),
            "image_link": obrazek, "link": g("link"), "datum": datum,
            "description": re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", g("description"))).strip(),
            "pub_date": (it.findtext("pubDate") or "").strip(),
        })
    return clanky


def slug(text, delka=60):
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:delka].rstrip("-") or "clanek"


def soubor_banneru(clanek):
    return VYSTUP / ("%s_%s.png" % (clanek["datum"].strftime("%Y-%m-%d"), slug(clanek["title"])))


def zapis_katalog(clanky):
    """Plný refresh jako v n8n: katalog vždy obsahuje právě články z aktuálního feedu."""
    ted = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    radky = []
    for c in clanky:
        banner = soubor_banneru(c)
        if not banner.exists():
            continue
        radky.append({
            "id": c["id"], "title": c["title"], "description": c["description"],
            "link": c["link"],
            "image_link": (VEREJNA_URL.rstrip("/") + "/" + quote(banner.name)) if VEREJNA_URL else "",
            "image_link_original": c["image_link"], "category": c["category"],
            "brand": BRAND, "published": c["pub_date"], "status": "new",
            "created_at": ted, "availability": "in stock", "condition": "new",
            "price": "5000 CZK",
        })
    with KATALOG.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SLOUPCE)
        w.writeheader()
        w.writerows(radky)
    return len(radky)


def sestav_html(clanek, logo_uri):
    delka = len(clanek["title"])
    velikost_titulku = 66 if delka <= 90 else 58 if delka <= 115 else 52
    css = CSS.replace("__TITLE_SIZE__", str(velikost_titulku))
    e = lambda s: html.escape(s, quote=True)
    # jednopísmenné předložky a spojky nesmí zůstat na konci řádku
    titulek = re.sub(r"(?<![^\s(„])([ksvzouiaKSVZOUIA]) ", "\\1\u00a0", clanek["title"])
    kategorie = (
        '<div class="category">%s</div>' % e(clanek["category"]) if clanek["category"] else ""
    )
    return """<!doctype html><html><head><meta charset="utf-8"><style>%s</style></head><body>
<div class="banner">
  <img class="hero-image" src="%s" alt="">
  <div class="overlay"></div>
  <img class="logo" src="%s" alt="Blesk pro ženy">
  <div class="text">%s<div class="title">%s</div></div>
  <div class="bottom-bar"></div>
</div></body></html>""" % (css, e(clanek["image_link"]), logo_uri, kategorie, e(titulek))


def vykresli(html_text, cil):
    with tempfile.TemporaryDirectory() as tmp:
        stranka = Path(tmp) / "banner.html"
        stranka.write_text(html_text, encoding="utf-8")
        subprocess.run(
            [
                CHROME, "--headless=new",
                *(["--no-sandbox"] if sys.platform.startswith("linux") else []), "--disable-gpu", "--hide-scrollbars",
                "--no-first-run", "--no-default-browser-check",
                "--user-data-dir=" + str(Path(tmp) / "profil"),
                "--force-device-scale-factor=1",
                "--window-size=%d,%d" % (VELIKOST, VELIKOST),
                "--virtual-time-budget=15000",   # počká na fotku a písmo
                "--screenshot=" + str(cil),
                stranka.as_uri(),
            ],
            check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    if not cil.exists() or cil.stat().st_size < 20000:
        raise RuntimeError("Chrome nevytvořil použitelný obrázek")


def main():
    ap = argparse.ArgumentParser(description="Bannery Blesk pro ženy z feedu článků")
    ap.add_argument("--limit", type=int, default=0, help="nejvýš tolik nových článků")
    ap.add_argument("--znovu", action="store_true", help="vykreslit i už zpracované")
    args = ap.parse_args()

    if not CHROME or not Path(CHROME).exists():
        sys.exit("Nenašel jsem Google Chrome.")
    # logo.png, nebo jeho textová podoba logo.b64 (kvůli nahrání přes web GitHubu)
    if LOGO.exists():
        logo_b64 = base64.b64encode(LOGO.read_bytes()).decode()
    else:
        logo_b64 = LOGO.with_suffix(".b64").read_text().strip()
    logo_uri = "data:image/png;base64," + logo_b64
    VYSTUP.mkdir(exist_ok=True)
    hotove = set(json.loads(STAV.read_text())) if STAV.exists() else set()

    clanky = nacti_feed()
    nove = [c for c in clanky if args.znovu or c["id"] not in hotove]
    nove.sort(key=lambda c: c["datum"].timestamp())
    if args.limit:
        nove = nove[-args.limit:]
    print("Ve feedu %d článků, ke zpracování %d." % (len(clanky), len(nove)))

    chyby = 0
    for c in nove:
        cil = soubor_banneru(c)
        try:
            vykresli(sestav_html(c, logo_uri), cil)
        except Exception as ex:  # jeden vadný článek nesmí zastavit ostatní
            chyby += 1
            print("  CHYBA  %s: %s" % (c["title"][:60], ex))
            continue
        hotove.add(c["id"])
        STAV.write_text(json.dumps(sorted(hotove), indent=0))
        print("  OK     " + cil.name)
    pocet = zapis_katalog(clanky)
    print("Katalog: %d řádků v %s" % (pocet, KATALOG.name))
    if not VEREJNA_URL:
        print("Pozor: VEREJNA_URL není nastavená, sloupec image_link je prázdný.")
    print("Hotovo. Bannery jsou ve složce " + str(VYSTUP))
    # selže jen tehdy, když není co zveřejnit – jeden vadný článek zbytek nezastaví
    sys.exit(0 if pocet else 1)


if __name__ == "__main__":
    main()

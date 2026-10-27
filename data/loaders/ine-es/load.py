"""Loads the price of a hotel room by stars in Spain into data/loaders/ine-es/adr_by_stars.csv (P1-DS-07.8).

Source: the monthly press release "Coyuntura Turística Hotelera" of the Spanish National Statistics Institute (INE,
https://www.ine.es/dyngs/Prensa/CTHmmyy.pdf). It gives the ADR, the mean revenue per occupied room, of hotels of five,
four and three stars. INE allows reuse with a reference to the source (https://www.ine.es/ine/aviso_legal.htm).

Rosstat gives one price for 4*-5* hotels, and the backend needs both (P1-DS-07.1). No open data gives the prices of
hotels by stars for all the countries of the catalog; the Spanish ratios are taken as the shape of the price ladder
(data/loaders/rosstat/load.py uses them, the level stays Russian). The file is `month,adr_5,adr_4,adr_3` in euros, in
git (like geonames/ids.csv): INE changes nothing it has published, and the snapshot must not move until the loader is
run again.

The PDF text is read with `pdftotext` (poppler-utils). The loader goes back from the previous month and takes the
latest release of every calendar month, so the file has one row per month of the year, whatever the season of the run;
a release that is not published (404) is replaced by the same month of the year before. It fails when a month has no
release in LOOK_BACK_MONTHS or when a release has no ADR sentence, because INE has reworded it.

Usage: python3 data/loaders/ine-es/load.py
"""

import csv
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import requests

LOADER_DIR = Path(__file__).resolve().parent
OUT_FILE = LOADER_DIR / "adr_by_stars.csv"

URL = "https://www.ine.es/dyngs/Prensa/CTH{mm:02d}{yy:02d}.pdf"
HEADERS = {"User-Agent": "ostrovok-parser/1.0 (https://github.com/parnickname/ostrovok-parser)"}
LOOK_BACK_MONTHS = 30
TIMEOUT_SECONDS = 60

NUMBER = r"(\d+(?:[.,]\d+)?)"
SENTENCE = re.compile(
    rf"Por categorías, el ADR fue de {NUMBER} euros para los hoteles de cinco estrellas, "
    rf"de {NUMBER} euros para los de cuatro y de {NUMBER},? para los de tres"
)


class LoadError(Exception):
    pass


def months_back(today):
    """(year, month) of the previous month and every month before it, LOOK_BACK_MONTHS in all."""
    year, month = today.year, today.month
    for _ in range(LOOK_BACK_MONTHS):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        yield year, month


def fetch_text(year, month):
    """Returns the text of the release, or None when it is not published."""
    url = URL.format(mm=month, yy=year % 100)
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise LoadError(f"{url}: {type(e).__name__}") from None
    if response.status_code == 404:
        return None
    if response.status_code != 200 or not response.content.startswith(b"%PDF"):
        raise LoadError(f"{url}: HTTP {response.status_code}, not a PDF")
    try:
        text = subprocess.run(["pdftotext", "-", "-"], input=response.content, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        raise LoadError(f"pdftotext failed (is poppler-utils installed?): {e}") from None
    return " ".join(text.decode("utf-8", errors="replace").split())


def parse_adr(text, label):
    match = SENTENCE.search(text)
    if match is None:
        raise LoadError(f"{label}: no sentence with the ADR by categories, INE may have reworded it")
    five, four, three = (float(g.replace(",", ".")) for g in match.groups())
    if not five > four > three > 0:
        raise LoadError(f"{label}: ADR {five}, {four}, {three} does not grow with the stars")
    return five, four, three


def main():
    by_month = {}
    try:
        for year, month in months_back(date.today()):
            if month in by_month:
                continue
            text = fetch_text(year, month)
            if text is None:
                print(f"  {year}-{month:02d}: not published, skipped")
                continue
            label = f"{year}-{month:02d}"
            by_month[month] = (label, *parse_adr(text, label))
            if len(by_month) == 12:
                break
        if len(by_month) < 12:
            raise LoadError(f"releases for {len(by_month)} calendar months found, 12 needed")
    except LoadError as e:
        print(f"ine-es load failed: {e}", file=sys.stderr)
        return 1
    rows = sorted(by_month.values())
    with OUT_FILE.open("w", encoding="utf-8", newline="\n") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["month", "adr_5", "adr_4", "adr_3"])
        writer.writerows(rows)
    print(f"{len(rows)} months written to {OUT_FILE}: {rows[0][0]} .. {rows[-1][0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

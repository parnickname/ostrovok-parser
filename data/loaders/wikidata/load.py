"""Loads Russian names of cities from Wikidata into data/loaders/wikidata/city_names_ru.csv (P1-DS-04.3).

GeoNames has no Russian name for about half of the cities, so `name_ru` of those is the Latin name (P1-DS-04.1). Wikidata
(CC0) knows many of them: an item with the GeoNames id (property P1566) and a label in Russian.

For every city of data/loaders/geonames/out/cities.jsonl whose `name_ru` has no Cyrillic, the loader asks the Wikidata
Query Service for the Russian label of the item with the geonameid of the city (data/loaders/geonames/ids.csv). A label is
taken only when it is written in Cyrillic letters, spaces, hyphens, apostrophes and full stops: labels with Latin or Greek
letters, brackets or commas are disambiguations or typos and are skipped.

The result, `id,name_ru` for the cities that got a name, is data/loaders/wikidata/city_names_ru.csv, in git (like
geonames/ids.csv: Wikidata labels change, the snapshot must not change with them until the loader is run again).
data/build.py applies it to `cities` under the manual overrides/city_names_ru.csv. Cities Wikidata has no label for
keep the GeoNames name.

Usage: python3 data/loaders/wikidata/load.py
"""

import csv
import json
import re
import sys
import time
from pathlib import Path

import requests

LOADER_DIR = Path(__file__).resolve().parent
OUT_FILE = LOADER_DIR / "city_names_ru.csv"
CITIES_FILE = LOADER_DIR.parent / "geonames" / "out" / "cities.jsonl"
IDS_FILE = LOADER_DIR.parent / "geonames" / "ids.csv"

ENDPOINT = "https://query.wikidata.org/sparql"
HEADERS = {
    "User-Agent": "ostrovok-parser/1.0 (https://github.com/parnickname/ostrovok-parser; open data snapshot builder)",
    "Accept": "application/sparql-results+json",
}
BATCH = 400
PAUSE_SECONDS = 2
ATTEMPTS = 4
RETRY_WAIT_SECONDS = 15
TIMEOUT_SECONDS = 120

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
CLEAN_LABEL = re.compile(r"^[А-Яа-яЁё0-9 .'’-]+$")


class LoadError(Exception):
    pass


def read_cities():
    with CITIES_FILE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_geonameids():
    with IDS_FILE.open(encoding="utf-8", newline="") as f:
        return {row["id"]: row["geonameid"] for row in csv.DictReader(f)}


def query(geonameids):
    values = " ".join(f'"{g}"' for g in geonameids)
    sparql = (
        "SELECT ?g ?label WHERE { VALUES ?g { %s } ?item wdt:P1566 ?g . "
        '?item rdfs:label ?label . FILTER(LANG(?label) = "ru") }' % values
    )
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = requests.post(ENDPOINT, data={"query": sparql}, headers=HEADERS, timeout=TIMEOUT_SECONDS)
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.RequestException(f"HTTP {response.status_code}")
            response.raise_for_status()
            return response.json()["results"]["bindings"]
        except (requests.RequestException, ValueError) as e:
            if attempt == ATTEMPTS:
                raise LoadError(f"Wikidata Query Service: {e}") from None
            time.sleep(RETRY_WAIT_SECONDS * attempt)


def good_label(label):
    return bool(CYRILLIC.search(label)) and bool(CLEAN_LABEL.match(label))


def main():
    try:
        geonameids = read_geonameids()
        wanted = {}
        for city in read_cities():
            if not CYRILLIC.search(city["name_ru"]):
                wanted[geonameids[city["id"]]] = city["id"]
        print(f"{len(wanted)} cities without a Russian name")
        names = {}
        ids = sorted(wanted, key=int)
        for start in range(0, len(ids), BATCH):
            for row in query(ids[start:start + BATCH]):
                label = row["label"]["value"].strip()
                if good_label(label):
                    names.setdefault(wanted[row["g"]["value"]], label)
            print(f"  {min(start + BATCH, len(ids))}/{len(ids)}: {len(names)} names", flush=True)
            time.sleep(PAUSE_SECONDS)
    except (LoadError, KeyError) as e:
        print(f"wikidata load failed: {e}", file=sys.stderr)
        return 1
    with OUT_FILE.open("w", encoding="utf-8", newline="\n") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["id", "name_ru"])
        for city_id in sorted(names):
            writer.writerow([city_id, names[city_id]])
    print(f"{len(names)} names written to {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

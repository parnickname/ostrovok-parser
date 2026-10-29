"""Loads Russian names of airports from Wikidata into data/loaders/wikidata/airport_names_ru.csv.

OurAirports names airports in English only, so the agent showed «Трансфер Sochi International Airport → …» to a
Russian user (backend issue 13). Wikidata (CC0) has a Russian label for many airports: an item with the IATA airport
code (property P238) and a label in Russian.

For every airport of data/loaders/ourairports/out/airports.jsonl the loader asks the Wikidata Query Service for the
Russian labels of the items with its IATA code. A label is taken only when it is written in Cyrillic letters, digits,
spaces, hyphens, apostrophes, full stops and «» quotes, like city names (load.py): labels with Latin letters, brackets
or commas are disambiguations or typos and are skipped. A code whose items have different good labels is skipped too:
there is no telling which of them is the airport. Many labels are a bare name («Шереметьево», «Минск»), which in a
transfer title reads as the city: a label without the word «аэропорт» gets «Аэропорт » in front.

The result, `iata,name_ru` for the airports that got a name, is data/loaders/wikidata/airport_names_ru.csv, in git
(Wikidata labels change, the snapshot must not change with them until the loader is run again). data/build.py adds it
to `airports` as `name_ru`; airports without a label keep only `name`.

Usage: python3 data/loaders/wikidata/airports.py
"""

import csv
import json
import re
import sys
import time
from pathlib import Path

import requests

LOADER_DIR = Path(__file__).resolve().parent
OUT_FILE = LOADER_DIR / "airport_names_ru.csv"
AIRPORTS_FILE = LOADER_DIR.parent / "ourairports" / "out" / "airports.jsonl"

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
CLEAN_LABEL = re.compile(r"^[А-Яа-яЁё0-9 .'’«»-]+$")


class LoadError(Exception):
    pass


def read_codes():
    with AIRPORTS_FILE.open(encoding="utf-8") as f:
        return sorted({json.loads(line)["iata"] for line in f if line.strip()})


def query(codes):
    values = " ".join(f'"{c}"' for c in codes)
    sparql = (
        "SELECT ?code ?label WHERE { VALUES ?code { %s } ?item wdt:P238 ?code . "
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


def airport_name(label):
    return label if "аэропорт" in label.lower() else "Аэропорт " + label


def main():
    try:
        codes = read_codes()
        print(f"{len(codes)} airports")
        labels = {}
        for start in range(0, len(codes), BATCH):
            for row in query(codes[start:start + BATCH]):
                label = row["label"]["value"].strip()
                if good_label(label):
                    labels.setdefault(row["code"]["value"], set()).add(label)
            print(f"  {min(start + BATCH, len(codes))}/{len(codes)}: {len(labels)} codes with a label", flush=True)
            time.sleep(PAUSE_SECONDS)
    except (LoadError, KeyError) as e:
        print(f"wikidata airports load failed: {e}", file=sys.stderr)
        return 1
    names = {code: airport_name(next(iter(found))) for code, found in labels.items() if len(found) == 1}
    print(f"{len(labels) - len(names)} codes skipped: their items have different labels")
    with OUT_FILE.open("w", encoding="utf-8", newline="\n") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["iata", "name_ru"])
        for code in sorted(names):
            writer.writerow([code, names[code]])
    print(f"{len(names)} names written to {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

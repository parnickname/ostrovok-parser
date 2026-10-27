"""Builds data/loaders/catalog/out/destinations.jsonl from the curated list destinations.csv (P1-DS-03.1).

destinations.csv is written by hand: `id`, `city_id` (a city from GeoNames, P1-DS-04.1), the Russian `name`
and `tags` (P1-DS-03.2, separated by `;`).
Everything else comes from the data of the other loaders:
- `country_code` is the country of the city;
- `airports` are the airports of the city (OurAirports, P1-DS-04.2);
- `stations` are the railway stations of the city (Yandex Rasp, P1-DS-06.2, Russia only); a Russian destination
  without a station must be listed in NO_RAILWAY;
- `description` is the text of descriptions.csv (`id`, `description`, P1-DS-03.5), one or two sentences per
  destination, written by an AI model (provider `ai-text`); to generate them again, rewrite the file, the loader
  only checks it.

Needs data/loaders/geonames/out/cities.jsonl, data/loaders/ourairports/out/airports.jsonl and
data/loaders/yandex-rasp/out/stations.jsonl.

Usage: python3 data/loaders/catalog/load.py
"""

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

LOADER_DIR = Path(__file__).resolve().parent
LIST_FILE = LOADER_DIR / "destinations.csv"
DESCRIPTIONS_FILE = LOADER_DIR / "descriptions.csv"
OUT_FILE = LOADER_DIR / "out" / "destinations.jsonl"
CITIES_FILE = LOADER_DIR.parent / "geonames" / "out" / "cities.jsonl"
AIRPORTS_FILE = LOADER_DIR.parent / "ourairports" / "out" / "airports.jsonl"
STATIONS_FILE = LOADER_DIR.parent / "yandex-rasp" / "out" / "stations.jsonl"

# The catalog data comes from these providers (sources.yaml); the choice of destinations is manual.
SOURCES = ["geonames", "ourairports"]

# P1-DS-03.5: the text of a description, one or two sentences.
DESCRIPTION_MAX_CHARS = 300
DESCRIPTION_SOURCE = "ai-text"

# P1-DS-03.2: the tags of the catalog; each one must belong to at least MIN_PER_TAG destinations.
# `sea` decides which destinations P1-DS-03.4 has to give a sea temperature.
TAGS = ("sea", "warm", "mountains", "city", "nature", "kids", "ski")
MIN_PER_TAG = 5

# Q-006 (README, "Решения команды"): 30-40 destinations, at least a third of them abroad.
MIN_SIZE, MAX_SIZE = 30, 40
HOME_COUNTRY = "RU"

# P1-DS-06.2: Russian destinations that no railway reaches, so they have no station.
NO_RAILWAY = {"gelendzhik"}


class LoadError(Exception):
    pass


def read_jsonl(path):
    if not path.exists():
        raise LoadError(f"{path} not found, run the loader that writes it first")
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_list():
    with LIST_FILE.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_descriptions():
    with DESCRIPTIONS_FILE.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    descriptions = {}
    for line_no, row in enumerate(rows, start=2):
        where = f"{DESCRIPTIONS_FILE.name}:{line_no}"
        dest_id, text = (row["id"] or "").strip(), (row["description"] or "").strip()
        if dest_id in descriptions:
            raise LoadError(f"{where}: duplicate id {dest_id!r}")
        if not text:
            raise LoadError(f"{where}: empty description of {dest_id!r}")
        if len(text) > DESCRIPTION_MAX_CHARS:
            raise LoadError(f"{where}: description of {dest_id!r} is {len(text)} characters, at most {DESCRIPTION_MAX_CHARS}")
        sentences = len(re.findall(r"[.!?](?:\s|$)", text))
        if not 1 <= sentences <= 2:
            raise LoadError(f"{where}: description of {dest_id!r} has {sentences} sentences, one or two expected")
        descriptions[dest_id] = text
    return descriptions


def build_records(entries, cities, airports, stations, descriptions):
    cities_by_id = {c["id"]: c for c in cities}
    airports_by_city = defaultdict(list)
    for airport in airports:
        airports_by_city[airport["city_id"]].append(airport["iata"])
    stations_by_city = defaultdict(list)
    for station in stations:
        stations_by_city[station["city_id"]].append(station["code"])

    records, seen = [], set()
    for line_no, entry in enumerate(entries, start=2):
        where = f"{LIST_FILE.name}:{line_no}"
        dest_id, city_id, name = entry["id"].strip(), entry["city_id"].strip(), entry["name"].strip()
        tags = sorted({t.strip() for t in entry["tags"].split(";") if t.strip()})
        unknown = [t for t in tags if t not in TAGS]
        if unknown:
            raise LoadError(f"{where}: unknown tags {unknown}, expected some of {list(TAGS)}")
        if not dest_id or not name:
            raise LoadError(f"{where}: id and name must not be empty")
        if dest_id in seen:
            raise LoadError(f"{where}: duplicate id {dest_id!r}")
        seen.add(dest_id)
        city = cities_by_id.get(city_id)
        if city is None:
            raise LoadError(f"{where}: city_id {city_id!r} is not in cities")
        iatas = sorted(airports_by_city.get(city_id, []))
        codes = sorted(stations_by_city.get(city_id, []))
        if not iatas and not codes:
            raise LoadError(f"{where}: {dest_id!r} has neither an airport nor a station")
        if city["country_code"] == HOME_COUNTRY and not codes and dest_id not in NO_RAILWAY:
            raise LoadError(f"{where}: {dest_id!r} has no station and is not in NO_RAILWAY")
        if codes and dest_id in NO_RAILWAY:
            raise LoadError(f"{where}: {dest_id!r} is in NO_RAILWAY but has stations {codes}")
        if dest_id not in descriptions:
            raise LoadError(f"{where}: {dest_id!r} has no row in {DESCRIPTIONS_FILE.name}")
        records.append({
            "id": dest_id,
            "city_id": city_id,
            "name": name,
            "country_code": city["country_code"],
            "tags": tags,
            "airports": iatas,
            "stations": codes,
            "description": descriptions[dest_id],
            "sources": SOURCES + (["yandex-rasp"] if codes else []) + [DESCRIPTION_SOURCE],
        })

    unused = sorted(set(descriptions) - seen)
    if unused:
        raise LoadError(f"{DESCRIPTIONS_FILE.name}: descriptions of unknown destinations {unused}")
    abroad = sum(1 for r in records if r["country_code"] != HOME_COUNTRY)
    if not MIN_SIZE <= len(records) <= MAX_SIZE:
        raise LoadError(f"{len(records)} destinations, Q-006 asks for {MIN_SIZE}-{MAX_SIZE}")
    if abroad * 3 < len(records):
        raise LoadError(f"{abroad} of {len(records)} destinations abroad, Q-006 asks for at least a third")
    for tag in TAGS:
        count = sum(1 for r in records if tag in r["tags"])
        if count < MIN_PER_TAG:
            raise LoadError(f"tag {tag!r} has {count} destinations, at least {MIN_PER_TAG} needed")
    records.sort(key=lambda r: r["id"])
    return records, abroad


def write_records(records):
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for r in records
    )
    OUT_FILE.write_bytes(content.encode("utf-8"))


def main():
    try:
        records, abroad = build_records(
            read_list(), read_jsonl(CITIES_FILE), read_jsonl(AIRPORTS_FILE), read_jsonl(STATIONS_FILE),
            read_descriptions(),
        )
    except LoadError as e:
        print(f"catalog load failed: {e}", file=sys.stderr)
        return 1
    write_records(records)
    print(f"{len(records)} destinations written to {OUT_FILE}")
    print(f"  abroad: {abroad}, in {HOME_COUNTRY}: {len(records) - abroad}")
    print("  per tag: " + ", ".join(f"{t} {sum(1 for r in records if t in r['tags'])}" for t in TAGS))
    return 0


if __name__ == "__main__":
    sys.exit(main())

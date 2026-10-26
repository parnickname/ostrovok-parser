"""Builds data/loaders/catalog/out/destinations.jsonl from the curated list destinations.csv (P1-DS-03.1).

destinations.csv is written by hand: `id`, `city_id` (a city from GeoNames, P1-DS-04.1), the Russian `name`
and `tags` (P1-DS-03.2, separated by `;`).
Everything else comes from the data of the other loaders:
- `country_code` is the country of the city;
- `airports` are the airports of the city (OurAirports, P1-DS-04.2);
- `stations` stay empty: stations of Russian cities come with P1-DS-06.2, then Sanya fills them in;
- `description` stays empty: no subtask of the plan fills it.

Needs data/loaders/geonames/out/cities.jsonl and data/loaders/ourairports/out/airports.jsonl.

Usage: python3 data/loaders/catalog/load.py
"""

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

LOADER_DIR = Path(__file__).resolve().parent
LIST_FILE = LOADER_DIR / "destinations.csv"
OUT_FILE = LOADER_DIR / "out" / "destinations.jsonl"
CITIES_FILE = LOADER_DIR.parent / "geonames" / "out" / "cities.jsonl"
AIRPORTS_FILE = LOADER_DIR.parent / "ourairports" / "out" / "airports.jsonl"

# The catalog data comes from these providers (sources.yaml); the choice of destinations is manual.
SOURCES = ["geonames", "ourairports"]

# P1-DS-03.2: the tags of the catalog; each one must belong to at least MIN_PER_TAG destinations.
# `sea` decides which destinations P1-DS-03.4 has to give a sea temperature.
TAGS = ("sea", "warm", "mountains", "city", "nature", "kids", "ski")
MIN_PER_TAG = 5

# Q-006 (README, "Решения команды"): 30-40 destinations, at least a third of them abroad.
MIN_SIZE, MAX_SIZE = 30, 40
HOME_COUNTRY = "RU"


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


def build_records(entries, cities, airports):
    cities_by_id = {c["id"]: c for c in cities}
    airports_by_city = defaultdict(list)
    for airport in airports:
        airports_by_city[airport["city_id"]].append(airport["iata"])

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
        if not iatas:  # no stations yet (P1-DS-06.2), so an airport is the only way to get there
            raise LoadError(f"{where}: {dest_id!r} has no airport")
        records.append({
            "id": dest_id,
            "city_id": city_id,
            "name": name,
            "country_code": city["country_code"],
            "tags": tags,
            "airports": iatas,
            "stations": [],
            "description": "",
            "sources": SOURCES,
        })

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
        records, abroad = build_records(read_list(), read_jsonl(CITIES_FILE), read_jsonl(AIRPORTS_FILE))
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

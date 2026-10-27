"""Builds flight routes into data/loaders/routes/out/routes.jsonl (P1-DS-06.1).

Pairs: every airport of a departure city (README, "Решения команды": a city of RU, BY, KZ, UZ or AM with an
airport) to every airport of every catalog destination, except a destination in the departure city itself.

No open data about flights is in the registry (OpenFlights is rejected: not updated since 2014), so the routes are
derived (provider `direct-flight-rule` in sources.yaml):
- `distance_km` — great-circle distance between the airports, rounded to whole kilometres;
- `direct` — true when both airports are `large_airport` in OurAirports and the distance is below
  DIRECT_MAX_KM (rule proposed by the backend, review round 2, item 3), false otherwise.
CFC-3 has no `sources` field in `routes`, so the mark lives in the registry, here, in CHANGELOG and in the PR.

The airport type comes from OurAirports airports.csv, pinned to a commit of davidmegginson/ourairports-data so a
rebuild gives the same result; cached in data/loaders/routes/cache/ (not in git), a file placed there by hand is
used without any download. Delete it or pass --refresh to fetch it again.

Needs data/loaders/ourairports/out/airports.jsonl and data/loaders/catalog/out/destinations.jsonl.

Usage: python3 data/loaders/routes/load.py [--refresh]
"""

import csv
import json
import math
import sys
from pathlib import Path

import requests

LOADER_DIR = Path(__file__).resolve().parent
CACHE_DIR = LOADER_DIR / "cache"
OUT_FILE = LOADER_DIR / "out" / "routes.jsonl"
AIRPORTS_FILE = LOADER_DIR.parent / "ourairports" / "out" / "airports.jsonl"
DESTINATIONS_FILE = LOADER_DIR.parent / "catalog" / "out" / "destinations.jsonl"

TYPES_COMMIT = "51db9e043efd4b8639a15f7f5f693e815459cb22"
TYPES_URL = f"https://raw.githubusercontent.com/davidmegginson/ourairports-data/{TYPES_COMMIT}/airports.csv"
TYPES_FILE = "airports.csv"

# README, "Решения команды": Q-007 and "Город отправления".
DEPARTURE_COUNTRIES = {"RU", "BY", "KZ", "UZ", "AM"}
EARTH_RADIUS_KM = 6371.0
# impulse: a direct flight is guessed from airport size and distance, not from schedules;
# replace with flight data when a current open source of routes is accepted into the registry.
DIRECT_TYPE = "large_airport"
DIRECT_MAX_KM = 6000


class LoadError(Exception):
    pass


def read_jsonl(path):
    if not path.exists():
        raise LoadError(f"{path} not found, run the loader that writes it first")
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def download(refresh):
    path = CACHE_DIR / TYPES_FILE
    if path.exists() and not refresh:
        return path
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"downloading {TYPES_URL}")
    part = path.with_suffix(path.suffix + ".part")
    try:
        with requests.get(TYPES_URL, stream=True, timeout=60) as response:
            response.raise_for_status()
            with part.open("wb") as f:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
    except requests.RequestException as e:
        part.unlink(missing_ok=True)
        raise LoadError(f"cannot download {TYPES_URL}: {e}") from None
    part.replace(path)
    return path


def read_types(path):
    """Maps IATA code -> OurAirports type."""
    with path.open(encoding="utf-8", newline="") as f:
        return {row["iata_code"]: row["type"] for row in csv.DictReader(f) if row["iata_code"]}


def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def build_records(airports, destinations, types):
    by_iata = {a["iata"]: a for a in airports}
    missing = sorted(iata for iata in by_iata if iata not in types)
    if missing:
        raise LoadError(f"{TYPES_FILE}: no type for airports {missing[:10]}")
    departures = [a for a in airports if a["country_code"] in DEPARTURE_COUNTRIES]
    records = {}
    for dest in destinations:
        for to_iata in dest["airports"]:
            target = by_iata.get(to_iata)
            if target is None:
                raise LoadError(f"destination {dest['id']}: airport {to_iata} is not in airports")
            for source in departures:
                if source["city_id"] == dest["city_id"]:
                    continue
                distance = round(distance_km(source["lat"], source["lon"], target["lat"], target["lon"]))
                key = (source["iata"], to_iata)
                records[key] = {
                    "from_iata": source["iata"],
                    "to_iata": to_iata,
                    "distance_km": distance,
                    "direct": types[source["iata"]] == DIRECT_TYPE and types[to_iata] == DIRECT_TYPE
                    and distance < DIRECT_MAX_KM,
                }
    return [records[k] for k in sorted(records)], departures


def check_pairs(records, departures, destinations):
    """Done-when of 06.1: every pair "departure city — destination" has a route."""
    city_of = {a["iata"]: a["city_id"] for a in departures}
    covered = {(city_of[r["from_iata"]], r["to_iata"]) for r in records}
    missing = []
    for city in sorted({a["city_id"] for a in departures}):
        for dest in destinations:
            if city != dest["city_id"] and not any((city, iata) in covered for iata in dest["airports"]):
                missing.append((city, dest["id"]))
    if missing:
        raise LoadError(f"{len(missing)} pairs without a route, first: {missing[:5]}")


def main():
    refresh = "--refresh" in sys.argv[1:]
    try:
        airports = read_jsonl(AIRPORTS_FILE)
        destinations = read_jsonl(DESTINATIONS_FILE)
        records, departures = build_records(airports, destinations, read_types(download(refresh)))
        check_pairs(records, departures, destinations)
    except LoadError as e:
        print(f"routes load failed: {e}", file=sys.stderr)
        return 1
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for r in records
    )
    OUT_FILE.write_bytes(content.encode("utf-8"))
    cities = len({a["city_id"] for a in departures})
    print(f"{len(records)} routes written to {OUT_FILE}")
    print(f"  from {len(departures)} airports of {cities} departure cities to {len(destinations)} destinations")
    print(f"  direct: {sum(r['direct'] for r in records)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

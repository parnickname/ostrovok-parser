"""Builds flight routes into data/loaders/routes/out/routes.jsonl (P1-DS-06.1).

Pairs: every airport of a departure city (README, "Решения команды": a city of RU, BY, KZ, UZ or AM with an
airport) to every airport of every catalog destination, except a destination in the departure city itself.

`direct` comes from real flights (P1-DS-06.4): data/loaders/travelpayouts/direct_flights.csv, the pairs of airports with
a direct flight in the Aviasales Data API (provider `aviasales-data-api`), written by the Travelpayouts loader, which
needs a token. A pair that is not in the file has no direct flight in the data; the loader fails when the file is
missing, there is no guess any more. `distance_km` is the great-circle distance between the airports, rounded to whole
kilometres. CFC-3 has no `sources` field in `routes`, so the mark lives in the registry, here, in CHANGELOG and in the PR.

Needs data/loaders/ourairports/out/airports.jsonl, data/loaders/catalog/out/destinations.jsonl and
data/loaders/travelpayouts/direct_flights.csv.

Usage: python3 data/loaders/routes/load.py
"""

import csv
import json
import math
import sys
from pathlib import Path

LOADER_DIR = Path(__file__).resolve().parent
OUT_FILE = LOADER_DIR / "out" / "routes.jsonl"
AIRPORTS_FILE = LOADER_DIR.parent / "ourairports" / "out" / "airports.jsonl"
DESTINATIONS_FILE = LOADER_DIR.parent / "catalog" / "out" / "destinations.jsonl"
DIRECT_FILE = LOADER_DIR.parent / "travelpayouts" / "direct_flights.csv"

# README, "Решения команды": Q-007 and "Город отправления".
DEPARTURE_COUNTRIES = {"RU", "BY", "KZ", "UZ", "AM"}
EARTH_RADIUS_KM = 6371.0


class LoadError(Exception):
    pass


def read_jsonl(path):
    if not path.exists():
        raise LoadError(f"{path} not found, run the loader that writes it first")
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_direct_pairs():
    """Returns the set of (from_iata, to_iata) with a direct flight."""
    if not DIRECT_FILE.exists():
        raise LoadError(f"{DIRECT_FILE} not found, run data/loaders/travelpayouts/load.py (needs a token) first")
    with DIRECT_FILE.open(encoding="utf-8", newline="") as f:
        pairs = {(row["from_iata"], row["to_iata"]) for row in csv.DictReader(f)}
    if not pairs:
        raise LoadError(f"{DIRECT_FILE} has no pairs")
    return pairs


def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def build_records(airports, destinations, direct_pairs):
    by_iata = {a["iata"]: a for a in airports}
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
                    "direct": key in direct_pairs,
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
    try:
        airports = read_jsonl(AIRPORTS_FILE)
        destinations = read_jsonl(DESTINATIONS_FILE)
        records, departures = build_records(airports, destinations, read_direct_pairs())
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
    print(f"  direct: {sum(r['direct'] for r in records)} (Aviasales flights)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

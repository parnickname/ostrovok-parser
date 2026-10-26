"""Loads airports from OurAirports into data/loaders/ourairports/out/airports.jsonl (P1-DS-04.2).

Source: https://davidmegginson.github.io/ourairports-data/airports.csv (public domain).
Needs data/loaders/geonames/out/cities.jsonl (P1-DS-04.1): every airport is linked to a city.
The download is cached in data/loaders/ourairports/cache/ (not in git); a file placed there by hand is used
without any download. Delete it or pass --refresh to fetch a new one.

Selection: scheduled_service = yes and an IATA code.

OurAirports has no time zone, so `timezone` is taken from the nearest city of the same country.

`city_id` of an airport, first rule that gives an answer:
1. cities of the same country within MATCH_RADIUS_KM whose name equals the airport's municipality
   (the biggest one);
2. the biggest city of the same country within BIGGEST_RADIUS_KM (Domodedovo and Sheremetyevo belong to
   Moscow, not to the small towns next to them);
3. the nearest city of the same country within MATCH_RADIUS_KM.
An airport without a city in reach is skipped and reported: `city_id` is required and must resolve.

A row of data/overrides/airport_city.csv (`iata,city_id,comment`) replaces that choice for one airport, and its
`timezone` is then the time zone of that city. It is for airports of a big city that sit in a smaller municipality,
like NRT: Narita, not Tokyo. The loader fails when the airport is not in the selection, the city does not exist or is
in another country than the airport.

`cities.airports[]` is derived from `airports.city_id` by data/build.py, this loader does not touch cities.

Usage: python3 data/loaders/ourairports/load.py [--refresh]
"""

import csv
import json
import math
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import requests

LOADER_DIR = Path(__file__).resolve().parent
CACHE_DIR = LOADER_DIR / "cache"
OUT_FILE = LOADER_DIR / "out" / "airports.jsonl"
CITIES_FILE = LOADER_DIR.parent / "geonames" / "out" / "cities.jsonl"
OVERRIDES_FILE = LOADER_DIR.parent.parent / "overrides" / "airport_city.csv"

AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
AIRPORTS_CSV = "airports.csv"

MATCH_RADIUS_KM = 100
BIGGEST_RADIUS_KM = 50
EARTH_RADIUS_KM = 6371.0


class LoadError(Exception):
    pass


def download(refresh):
    path = CACHE_DIR / AIRPORTS_CSV
    if path.exists() and not refresh:
        return path
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"downloading {AIRPORTS_URL}")
    try:
        response = requests.get(AIRPORTS_URL, timeout=60)
        response.raise_for_status()
    except requests.RequestException as e:
        raise LoadError(f"cannot download {AIRPORTS_URL}: {e}") from None
    part = path.with_suffix(".part")
    part.write_bytes(response.content)
    part.replace(path)
    return path


def read_cities():
    if not CITIES_FILE.exists():
        raise LoadError(f"{CITIES_FILE} not found, run data/loaders/geonames/load.py first")
    by_country = defaultdict(list)
    with CITIES_FILE.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                city = json.loads(line)
                by_country[city["country_code"]].append(city)
    return by_country


def read_airports(path):
    airports = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["scheduled_service"] == "yes" and row["iata_code"]:
                airports.append(row)
    return airports


def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def normalize(name):
    name = unicodedata.normalize("NFKD", name.lower())
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = re.sub(r"\bst\b\.?\s*", "saint ", name)
    return re.sub(r"[\W_]+", "", name)


def pick_city(airport, cities):
    """Returns (city, rule) or (None, None); `cities` are the cities of the airport's country."""
    lat, lon = float(airport["latitude_deg"]), float(airport["longitude_deg"])
    near = []
    for city in cities:
        if abs(city["lat"] - lat) > 1.0:  # one degree of latitude is 111 km, beyond MATCH_RADIUS_KM
            continue
        d = distance_km(lat, lon, city["lat"], city["lon"])
        if d <= MATCH_RADIUS_KM:
            near.append((d, city))
    if not near:
        return None, None

    def biggest(items):
        return max(items, key=lambda x: (x[1]["population"], x[1]["id"]))[1]

    municipality = normalize(airport["municipality"])
    if municipality:
        named = [(d, c) for d, c in near if municipality in (normalize(c["name_en"]), normalize(c["name_ru"]))]
        if named:
            return biggest(named), "municipality"
    close = [(d, c) for d, c in near if d <= BIGGEST_RADIUS_KM]
    if close:
        return biggest(close), "biggest"
    return min(near, key=lambda x: (x[0], x[1]["id"]))[1], "nearest"


def nearest_timezone(airport, cities):
    lat, lon = float(airport["latitude_deg"]), float(airport["longitude_deg"])
    return min(cities, key=lambda c: (distance_km(lat, lon, c["lat"], c["lon"]), c["id"]))["timezone"]


def read_overrides():
    """Returns {iata: city_id} from data/overrides/airport_city.csv; no file, no overrides."""
    if not OVERRIDES_FILE.exists():
        return {}
    overrides = {}
    with OVERRIDES_FILE.open(encoding="utf-8", newline="") as f:
        for line_no, row in enumerate(csv.DictReader(f), start=2):
            iata, city_id = (row["iata"] or "").strip(), (row["city_id"] or "").strip()
            if not iata or not city_id:
                raise LoadError(f"{OVERRIDES_FILE.name}:{line_no}: iata and city_id must not be empty")
            if iata in overrides:
                raise LoadError(f"{OVERRIDES_FILE.name}:{line_no}: airport {iata} twice")
            overrides[iata] = city_id
    return overrides


def build_records(airports, cities_by_country, overrides=None):
    overrides = dict(overrides or {})
    cities_by_id = {c["id"]: c for cities in cities_by_country.values() for c in cities}
    records, rules, skipped = [], defaultdict(int), []
    for airport in airports:
        country = airport["iso_country"]
        cities = cities_by_country.get(country, [])
        if airport["iata_code"] in overrides:
            city_id = overrides.pop(airport["iata_code"])
            city = cities_by_id.get(city_id)
            if city is None:
                raise LoadError(f"{OVERRIDES_FILE.name}: {airport['iata_code']}: city {city_id!r} is not in cities")
            if city["country_code"] != country:
                raise LoadError(
                    f"{OVERRIDES_FILE.name}: {airport['iata_code']} is in {country}, city {city_id!r} in {city['country_code']}"
                )
            rule, timezone = "override", city["timezone"]
        else:
            city, rule = pick_city(airport, cities)
            timezone = None
        if city is None:
            skipped.append(airport)
            continue
        rules[rule] += 1
        records.append({
            "iata": airport["iata_code"],
            "name": airport["name"],
            "city_id": city["id"],
            "country_code": country,
            "lat": float(airport["latitude_deg"]),
            "lon": float(airport["longitude_deg"]),
            # impulse: the time zone of the nearest city, wrong for an airport on the other side of a zone
            # border from its nearest city; remove if validation against a time zone dataset shows such cases.
            "timezone": timezone or nearest_timezone(airport, cities),
            "scheduled": True,
        })
    if overrides:
        raise LoadError(f"{OVERRIDES_FILE.name}: not in the selection of airports: {', '.join(sorted(overrides))}")
    records.sort(key=lambda r: r["iata"])
    return records, rules, skipped


def write_records(records):
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for r in records
    )
    OUT_FILE.write_bytes(content.encode("utf-8"))


def main():
    refresh = "--refresh" in sys.argv[1:]
    try:
        airports = read_airports(download(refresh))
        records, rules, skipped = build_records(airports, read_cities(), read_overrides())
    except LoadError as e:
        print(f"ourairports load failed: {e}", file=sys.stderr)
        return 1
    write_records(records)
    print(f"{len(records)} airports written to {OUT_FILE}")
    print(f"  city chosen by: {dict(sorted(rules.items()))}")
    print(f"  skipped, no city of the same country within {MATCH_RADIUS_KM} km: {len(skipped)}")
    for a in skipped[:15]:
        print(f"    {a['iata_code']} {a['name']} ({a['iso_country']}, {a['municipality']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

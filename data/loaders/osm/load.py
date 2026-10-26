"""Loads the hotels of the catalog destinations from OpenStreetMap into data/loaders/osm/out/hotels.jsonl (P1-DS-05.1).

Source: OpenStreetMap through the Overpass API, objects with tourism=hotel (ODbL 1.0, (c) OpenStreetMap contributors).
Needs data/loaders/catalog/out/destinations.jsonl and data/loaders/geonames/out/cities.jsonl.

Per destination, nodes, ways and relations (hotels are mostly drawn as buildings) within RADII_KM of the coordinates
of the destination's city: the first radius that gives at least MIN_HOTELS named hotels is used. A hotel found around
several destinations goes to the nearest one, so every hotel has one `destination_id` and one `id`.

Record, everything not tagged in OSM is left out, nothing is guessed:
- `id` is `osm.<type>.<osm_id>` (type: node, way, relation), `osm_id` is the integer id of the object;
- `name` is the `name` tag, hotels without a name are skipped; `lat`, `lon` are the point or the centre of the object;
- `stars` from `stars` (1-5), `check_in_time` / `check_out_time` from `check_in` / `check_out` (the first HH:MM),
  `pets` from `pets_allowed`, `pets` or `dog` (yes/designated/leashed -> true, no -> false);
- `sources` is `["osm"]`.

Every Overpass answer is cached in data/loaders/osm/cache/ (not in git), so a rerun asks only for what is missing; a
file placed there by hand is used without any request. Pass --refresh to fetch everything again.

Usage: python3 data/loaders/osm/load.py [--refresh]
"""

import json
import math
import re
import sys
import time
from pathlib import Path

import requests

LOADER_DIR = Path(__file__).resolve().parent
CACHE_DIR = LOADER_DIR / "cache"
OUT_FILE = LOADER_DIR / "out" / "hotels.jsonl"
DESTINATIONS_FILE = LOADER_DIR.parent / "catalog" / "out" / "destinations.jsonl"
CITIES_FILE = LOADER_DIR.parent / "geonames" / "out" / "cities.jsonl"

# The public Overpass servers: when one is busy or refuses us, the next attempt goes to the next one.
# Only full-planet servers: regional ones (overpass.osm.ch has Switzerland only) answer 0 hotels elsewhere.
ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
# Overpass answers 406 to the default python-requests User-Agent: the client must introduce itself.
HEADERS = {
    "User-Agent": "ostrovok-parser/1.0 (https://github.com/parnickname/ostrovok-parser; open data snapshot builder)",
    "Accept": "application/json",
}
CONNECT_TIMEOUT_SECONDS = 15
READ_TIMEOUT_SECONDS = 120
RADII_KM = (10, 15, 20, 25)
MIN_HOTELS = 15
EARTH_RADIUS_KM = 6371.0
ATTEMPTS = len(ENDPOINTS)  # one attempt on every server
RETRY_WAIT_SECONDS = 10
PAUSE_SECONDS = 5  # the public servers ask for sequential, unhurried queries

STARS = re.compile(r"^\s*([1-5])(?:\D|$)")
TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)")
PETS_TAGS = ("pets_allowed", "pets", "dog")
PETS_YES = {"yes", "designated", "leashed"}


class LoadError(Exception):
    pass


def read_jsonl(path):
    if not path.exists():
        raise LoadError(f"{path} not found, run the loader that writes it first")
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def query(lat, lon, radius_km):
    around = f"(around:{radius_km * 1000},{lat},{lon})"
    return (
        "[out:json][timeout:90];("
        f'node["tourism"="hotel"]{around};way["tourism"="hotel"]{around};relation["tourism"="hotel"]{around};'
        ");out center tags;"
    )


def overpass(destination_id, radius_km, lat, lon, refresh):
    """Returns the Overpass answer for the destination and radius, from the cache or from the servers."""
    path = CACHE_DIR / f"{destination_id}-{radius_km}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    label = f"{destination_id} within {radius_km} km"
    for attempt in range(ATTEMPTS):
        endpoint = ENDPOINTS[attempt % len(ENDPOINTS)]
        print(f"  {label}: asking {endpoint.split('/')[2]} (attempt {attempt + 1} of {ATTEMPTS})", flush=True)
        try:
            response = requests.post(
                endpoint,
                data={"data": query(lat, lon, radius_km)},
                headers=HEADERS,
                timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
            )
        except requests.RequestException as e:
            problem = type(e).__name__
        else:
            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError:
                    problem = "answer is not JSON"
                else:
                    if "remark" in data:  # Overpass reports a failed or cut short query this way, even with elements
                        problem = f"remark: {data['remark'][:100]}"
                    else:
                        CACHE_DIR.mkdir(parents=True, exist_ok=True)
                        part = path.with_suffix(".part")
                        part.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                        part.replace(path)
                        time.sleep(PAUSE_SECONDS)
                        return data
            else:
                problem = f"HTTP {response.status_code}"
        if attempt + 1 == ATTEMPTS:
            raise LoadError(f"{label}: no answer after {ATTEMPTS} attempts, last problem: {problem}")
        print(f"  {label}: {problem} from {endpoint.split('/')[2]}, next attempt in {RETRY_WAIT_SECONDS} s", flush=True)
        time.sleep(RETRY_WAIT_SECONDS)


def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def parse_stars(value):
    # impulse: "3.5" counts as 3 stars, the schema has whole stars only; remove if half stars are ever needed.
    m = STARS.match(value or "")
    return int(m.group(1)) if m else None


def parse_time(value):
    m = TIME.search(value or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def parse_pets(tags):
    for key in PETS_TAGS:
        value = tags.get(key, "").strip().lower()
        if value in PETS_YES:
            return True
        if value == "no":
            return False
    return None


def parse_hotel(element):
    """Returns the hotel record without `destination_id`, or None for an object without a name or a position."""
    tags = element.get("tags", {})
    name = tags.get("name", "").strip()
    point = element if element["type"] == "node" else element.get("center", {})
    if not name or "lat" not in point or "lon" not in point:
        return None
    record = {
        "id": f"osm.{element['type']}.{element['id']}",
        "name": name,
        "lat": point["lat"],
        "lon": point["lon"],
        "osm_id": element["id"],
        "sources": ["osm"],
    }
    for field, value in (
        ("stars", parse_stars(tags.get("stars"))),
        ("check_in_time", parse_time(tags.get("check_in"))),
        ("check_out_time", parse_time(tags.get("check_out"))),
        ("pets", parse_pets(tags)),
    ):
        if value is not None:
            record[field] = value
    return record


def collect(destinations, cities, refresh):
    """Returns {destination id: {hotel id: record}} for the first radius that gives MIN_HOTELS hotels."""
    found, radii = {}, {}
    for destination in sorted(destinations, key=lambda d: d["id"]):
        city = cities[destination["city_id"]]
        for radius in RADII_KM:
            answer = overpass(destination["id"], radius, city["lat"], city["lon"], refresh)
            hotels = {}
            for element in answer.get("elements", []):
                record = parse_hotel(element)
                if record is not None:
                    hotels[record["id"]] = record
            if len(hotels) >= MIN_HOTELS:
                break
        found[destination["id"]], radii[destination["id"]] = hotels, radius
        print(f"  {destination['id']}: {len(hotels)} named hotels within {radius} km", flush=True)
    return found, radii


def assign_to_nearest(found, destinations, cities):
    """Gives a hotel found around several destinations to the nearest one."""
    centres = {d["id"]: cities[d["city_id"]] for d in destinations}
    owner = {}
    for dest_id in sorted(found):
        for hotel_id, record in found[dest_id].items():
            c = centres[dest_id]
            d = distance_km(record["lat"], record["lon"], c["lat"], c["lon"])
            if hotel_id not in owner or (d, dest_id) < owner[hotel_id][0]:
                owner[hotel_id] = ((d, dest_id), record)
    records = []
    for (_, dest_id), record in owner.values():
        records.append({**record, "destination_id": dest_id})
    records.sort(key=lambda r: r["id"])
    return records


def write_records(records):
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for r in records
    )
    OUT_FILE.write_bytes(content.encode("utf-8"))


def main():
    refresh = "--refresh" in sys.argv[1:]
    try:
        destinations = read_jsonl(DESTINATIONS_FILE)
        cities = {c["id"]: c for c in read_jsonl(CITIES_FILE)}
        found, radii = collect(destinations, cities, refresh)
        records = assign_to_nearest(found, destinations, cities)
        counts = {d["id"]: 0 for d in destinations}
        for r in records:
            counts[r["destination_id"]] += 1
        short = {d: n for d, n in counts.items() if n < MIN_HOTELS}
        if short:
            raise LoadError(
                f"fewer than {MIN_HOTELS} named hotels within {RADII_KM[-1]} km for: "
                + ", ".join(f"{d} ({n})" for d, n in sorted(short.items()))
            )
    except LoadError as e:
        print(f"osm load failed: {e}", file=sys.stderr)
        return 1
    write_records(records)
    print(f"{len(records)} hotels for {len(destinations)} destinations written to {OUT_FILE}")
    print("  per destination (radius km): " + ", ".join(f"{d} {counts[d]} ({radii[d]})" for d in sorted(counts)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

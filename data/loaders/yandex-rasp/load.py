"""Loads railway stations of Russia into data/loaders/yandex-rasp/out/stations.jsonl (P1-DS-06.2).

Source: Yandex Rasp API, method stations_list (https://yandex.ru/dev/rasp/raspapi/), one gzip JSON of all
stations: countries -> regions -> settlements -> stations. `code` is the Yandex station code (`s2000003`), the
same code P1-DS-06.3 asks the schedules for.
The API key is read from the environment variable YANDEX_RASP_API_KEY or from the .env file in the repository
root; it is never written anywhere. The download is cached in data/loaders/yandex-rasp/cache/ (not in git); a file
placed there by hand is used without any download. Delete it or pass --refresh to fetch a new one.

Selection, Russia only (README, "Решения команды"):
- transport_type `train`, station_type `train_station` or `station`, with coordinates. The type alone does not
  mark a terminal: the main stations of Sochi, Irkutsk, Murmansk, Vladivostok are `station`, while some suburban
  platforms of Yekaterinburg are `train_station`;
- the station is named after its settlement: the settlement name itself, or the name followed by " (", "-" or
  " <digit>" ("Москва (Казанский вокзал)", "Казань-Пасс.", "Люберцы 1"); for a hyphenated settlement its first
  part counts too ("Ростов-Главный" in Ростов-на-Дону);
- names marking tourist duplicates, yards, freight, depots, parks and the Moscow central circle are dropped.

`city_id` of a station, first rule that gives an answer:
1. the biggest Russian city within NAME_RADIUS_KM whose `name_ru` equals the settlement name;
2. the nearest Russian city within LATIN_RADIUS_KM whose `name_ru` has no Cyrillic letters (GeoNames gave it
   the English name, as for Murmansk), so the settlement name cannot be compared.
A station without a city is skipped: `city_id` is required and must resolve.

`cities.stations[]` is derived from `stations.city_id` by data/build.py, this loader does not touch cities.

Usage: python3 data/loaders/yandex-rasp/load.py [--refresh]
"""

import gzip
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

import requests

LOADER_DIR = Path(__file__).resolve().parent
CACHE_DIR = LOADER_DIR / "cache"
OUT_FILE = LOADER_DIR / "out" / "stations.jsonl"
CITIES_FILE = LOADER_DIR.parent / "geonames" / "out" / "cities.jsonl"
ENV_FILE = LOADER_DIR.parents[2] / ".env"

STATIONS_URL = "https://api.rasp.yandex.net/v3.0/stations_list/"
STATIONS_FILE = "stations_list.json.gz"
API_KEY_VAR = "YANDEX_RASP_API_KEY"
# The server closes the connection after about 30 seconds; on a slow link the 5 MB answer is cut off.
DOWNLOAD_ATTEMPTS = 5

RUSSIA_CODE = "l225"
COUNTRY = "RU"
STATION_TYPES = {"train_station", "station"}
JUNK = re.compile(r"тур|сорт|тов\.|товар|грузов|депо|парк|мцк|-т-", re.IGNORECASE)
CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)

NAME_RADIUS_KM = 30
LATIN_RADIUS_KM = 5
EARTH_RADIUS_KM = 6371.0


class LoadError(Exception):
    pass


def read_api_key():
    key = os.environ.get(API_KEY_VAR, "").strip()
    if not key and ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == API_KEY_VAR:
                key = value.strip().strip("\"'")
    if not key:
        raise LoadError(f"{API_KEY_VAR} is not set: put it into the environment or into {ENV_FILE.name}")
    return key


def download(refresh):
    """Returns the cached gzip of stations_list, fetching it first when it is missing."""
    path = CACHE_DIR / STATIONS_FILE
    if path.exists() and not refresh:
        return path
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    params = {"apikey": read_api_key(), "lang": "ru_RU", "format": "json"}
    print(f"downloading {STATIONS_URL}")
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            # stream=True keeps the body compressed, as the server sent it.
            with requests.get(STATIONS_URL, params=params, headers={"Accept-Encoding": "gzip"}, stream=True,
                              timeout=60) as response:
                response.raise_for_status()
                content = response.raw.read()
            gzip.decompress(content)  # a cut-off answer fails here
        except (requests.RequestException, OSError, EOFError) as e:
            # The message of a requests error may contain the URL with the key, so only the type is shown.
            status = getattr(getattr(e, "response", None), "status_code", None)
            print(f"  attempt {attempt} of {DOWNLOAD_ATTEMPTS} failed: {type(e).__name__}"
                  + (f", HTTP {status}" if status else ""))
            continue
        part = path.with_suffix(path.suffix + ".part")
        part.write_bytes(content)
        part.replace(path)
        return path
    raise LoadError(f"cannot download {STATIONS_URL}, {DOWNLOAD_ATTEMPTS} attempts failed")


def read_russian_stations(path):
    """Yields (settlement name, station) for every station of Russia."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, EOFError, json.JSONDecodeError) as e:
        raise LoadError(f"{path.name}: cannot read: {e}") from None
    russia = [c for c in data.get("countries", []) if c.get("codes", {}).get("yandex_code") == RUSSIA_CODE]
    if len(russia) != 1:
        raise LoadError(f"{path.name}: expected one country with code {RUSSIA_CODE}, found {len(russia)}")
    for region in russia[0].get("regions", []):
        for settlement in region.get("settlements", []):
            for station in settlement.get("stations", []):
                yield settlement.get("title", ""), station


def normalize(name):
    return name.lower().replace("ё", "е").strip()


def is_selected(settlement, station):
    if station.get("transport_type") != "train" or station.get("station_type") not in STATION_TYPES:
        return False
    if not settlement or not station.get("latitude") or not station.get("longitude"):
        return False
    title = station.get("title", "")
    if JUNK.search(title):
        return False
    name = normalize(title)
    prefixes = {normalize(settlement)}
    if "-" in settlement:
        prefixes.add(normalize(settlement).split("-")[0])
    for prefix in prefixes:
        if name == prefix or name.startswith((prefix + " (", prefix + "-")):
            return True
        if re.fullmatch(re.escape(prefix) + r" \d+", name):
            return True
    return False


def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def read_cities():
    if not CITIES_FILE.exists():
        raise LoadError(f"{CITIES_FILE} not found, run data/loaders/geonames/load.py first")
    with CITIES_FILE.open(encoding="utf-8") as f:
        return [c for c in map(json.loads, filter(str.strip, f)) if c["country_code"] == COUNTRY]


def pick_city(settlement, lat, lon, cities):
    """Returns (city, rule) or (None, None)."""
    near = []
    for city in cities:
        if abs(city["lat"] - lat) > 0.5:  # half a degree of latitude is 55 km, beyond both radii
            continue
        near.append((distance_km(lat, lon, city["lat"], city["lon"]), city))
    named = [(d, c) for d, c in near if d <= NAME_RADIUS_KM and normalize(c["name_ru"]) == normalize(settlement)]
    if named:
        return max(named, key=lambda x: (x[1]["population"], x[1]["id"]))[1], "name"
    latin = [(d, c) for d, c in near if d <= LATIN_RADIUS_KM and not CYRILLIC.search(c["name_ru"])]
    if latin:
        return min(latin, key=lambda x: (x[0], x[1]["id"]))[1], "latin name nearby"
    return None, None


def build_records(stations, cities):
    records, rules, skipped = [], Counter(), []
    seen = set()
    for settlement, station in stations:
        if not is_selected(settlement, station):
            continue
        code = station.get("codes", {}).get("yandex_code")
        if not code:
            raise LoadError(f"station {station.get('title')!r} in {settlement}: no yandex_code")
        if code in seen:
            raise LoadError(f"station code {code} appears twice")
        seen.add(code)
        lat, lon = float(station["latitude"]), float(station["longitude"])
        city, rule = pick_city(settlement, lat, lon, cities)
        if city is None:
            skipped.append((settlement, station["title"]))
            continue
        rules[rule] += 1
        records.append({"code": code, "name": station["title"], "city_id": city["id"], "lat": lat, "lon": lon})
    records.sort(key=lambda r: r["code"])
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
        stations = read_russian_stations(download(refresh))
        records, rules, skipped = build_records(stations, read_cities())
    except LoadError as e:
        print(f"yandex-rasp load failed: {e}", file=sys.stderr)
        return 1
    write_records(records)
    print(f"{len(records)} stations in {len({r['city_id'] for r in records})} cities written to {OUT_FILE}")
    print(f"  city chosen by: {dict(sorted(rules.items()))}")
    print(f"  skipped, the settlement is not a city of cities: {len(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

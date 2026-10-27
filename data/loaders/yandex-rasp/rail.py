"""Loads train schedules between Russian destinations into data/loaders/yandex-rasp/out/rail_routes.jsonl
(P1-DS-06.3).

Pairs: every ordered pair of Russian destinations of the catalog that have stations (decision of the user for
06.3: trains between destinations; Moscow and Saint Petersburg are destinations themselves).

Source: Yandex Rasp API, method search (https://yandex.ru/dev/rasp/raspapi/), without a date, that is the schedule
of all days, transport_type `train`, from the settlement of one destination to the settlement of the other. The
settlement code of a destination is taken from stations_list (cache of load.py): the settlement its stations
belong to.
The API key is read like in load.py. Every answer is cached in data/loaders/yandex-rasp/cache/search/ (not in git),
so a rebuild gives the same result; pass --refresh to ask the API again.

Per pair of stations (`from_station`, `to_station`, both in stations.jsonl):
- `departures` and `train_numbers` — parallel arrays of one length: `departures[i]` (HH:MM, local time of the
  departure station) is the departure of the train `train_numbers[i]`; every distinct (time, train) pair once,
  sorted by time, then by number; one time repeats when several trains leave at it;
- `duration_min` — the median travel time of the trains, in minutes.
A train arriving at or leaving from a station that is not in stations.jsonl is skipped: the backend needs both
ends to resolve.

Needs data/loaders/yandex-rasp/out/stations.jsonl (load.py) and data/loaders/catalog/out/destinations.jsonl.

Usage: python3 data/loaders/yandex-rasp/rail.py [--refresh]
"""

import gzip
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import requests

from load import CACHE_DIR, LOADER_DIR, RUSSIA_CODE, STATIONS_FILE, LoadError, read_api_key

OUT_FILE = LOADER_DIR / "out" / "rail_routes.jsonl"
SEARCH_CACHE = CACHE_DIR / "search"
OWN_STATIONS = LOADER_DIR / "out" / "stations.jsonl"
DESTINATIONS_FILE = LOADER_DIR.parent / "catalog" / "out" / "destinations.jsonl"

SEARCH_URL = "https://api.rasp.yandex.net/v3.0/search/"
PAGE_SIZE = 100
ATTEMPTS = 3

# Done-when of P1-DS-06.3: these pairs of destinations must have a schedule.
REQUIRED_PAIRS = [("moscow", "sochi"), ("moscow", "kazan")]
HOME_COUNTRY = "RU"


def read_jsonl(path):
    if not path.exists():
        raise LoadError(f"{path} not found, run the loader that writes it first")
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_settlements(wanted_codes):
    """Maps station code -> Yandex settlement code, for the wanted stations."""
    path = CACHE_DIR / STATIONS_FILE
    if not path.exists():
        raise LoadError(f"{path} not found, run data/loaders/yandex-rasp/load.py first")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    result = {}
    for country in data.get("countries", []):
        if country.get("codes", {}).get("yandex_code") != RUSSIA_CODE:
            continue
        for region in country.get("regions", []):
            for settlement in region.get("settlements", []):
                for station in settlement.get("stations", []):
                    code = station.get("codes", {}).get("yandex_code")
                    if code in wanted_codes:
                        result[code] = settlement.get("codes", {}).get("yandex_code")
    return result


def destination_settlements(destinations, settlement_of):
    """Maps destination id -> its settlement code; all stations of a destination must share one settlement."""
    result = {}
    for dest in destinations:
        codes = {settlement_of.get(code) for code in dest["stations"]}
        if None in codes or len(codes) != 1:
            raise LoadError(f"destination {dest['id']}: stations {dest['stations']} map to settlements {codes}")
        result[dest["id"]] = codes.pop()
    return result


def fetch(settlement_from, settlement_to, refresh, key):
    """Returns all segments of the search, from the cache or the API."""
    path = SEARCH_CACHE / f"{settlement_from}_{settlement_to}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    segments, offset = [], 0
    while True:
        params = {
            "apikey": key, "from": settlement_from, "to": settlement_to, "transport_types": "train",
            "lang": "ru_RU", "format": "json", "limit": PAGE_SIZE, "offset": offset,
        }
        for attempt in range(1, ATTEMPTS + 1):
            try:
                response = requests.get(SEARCH_URL, params=params, timeout=60)
                if response.status_code == 404:  # no route between the settlements
                    page = {"segments": [], "pagination": {"total": 0}}
                    break
                response.raise_for_status()
                page = response.json()
                break
            except (requests.RequestException, ValueError) as e:
                # The message of a requests error may contain the URL with the key, so only the type is shown.
                status = getattr(getattr(e, "response", None), "status_code", None)
                print(f"  {settlement_from} -> {settlement_to}: attempt {attempt} failed: {type(e).__name__}"
                      + (f", HTTP {status}" if status else ""))
        else:
            raise LoadError(f"search {settlement_from} -> {settlement_to}: {ATTEMPTS} attempts failed")
        segments.extend(page.get("segments", []))
        offset += PAGE_SIZE
        if offset >= page.get("pagination", {}).get("total", 0):
            break
    SEARCH_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(segments, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return segments


def aggregate(segments, known_stations):
    """Groups train segments by (from station, to station)."""
    groups = defaultdict(lambda: {"trains": set(), "durations": []})
    for seg in segments:
        src, dst = seg["from"]["code"], seg["to"]["code"]
        if src not in known_stations or dst not in known_stations or not seg.get("departure"):
            continue
        group = groups[(src, dst)]
        # impulse: all trains of the schedule are taken, seasonal ones too ("только 26 октября");
        # filter by `days` when the agent starts offering exact dates.
        group["trains"].add((seg["departure"][:5], seg["thread"]["number"]))
        group["durations"].append(seg["duration"])
    records = []
    for (src, dst), group in groups.items():
        trains = sorted(group["trains"])
        records.append({
            "from_station": src,
            "to_station": dst,
            "duration_min": max(1, round(statistics.median(group["durations"]) / 60)),
            "departures": [time for time, _ in trains],
            "train_numbers": [number for _, number in trains],
        })
    return records


def main():
    refresh = "--refresh" in sys.argv[1:]
    try:
        stations = {s["code"]: s for s in read_jsonl(OWN_STATIONS)}
        destinations = [
            d for d in read_jsonl(DESTINATIONS_FILE) if d["country_code"] == HOME_COUNTRY and d["stations"]
        ]
        settlements = destination_settlements(destinations, read_settlements(set(stations)))
        key = None
        records, pairs_with_trains = [], set()
        for a in destinations:
            for b in destinations:
                if a["id"] == b["id"]:
                    continue
                path = SEARCH_CACHE / f"{settlements[a['id']]}_{settlements[b['id']]}.json"
                if key is None and (refresh or not path.exists()):
                    key = read_api_key()
                pair = aggregate(fetch(settlements[a["id"]], settlements[b["id"]], refresh, key), stations)
                pair = [r for r in pair if r["from_station"] in a["stations"] and r["to_station"] in b["stations"]]
                if pair:
                    pairs_with_trains.add((a["id"], b["id"]))
                records.extend(pair)
        missing = [p for p in REQUIRED_PAIRS if p not in pairs_with_trains]
        if missing:
            raise LoadError(f"no train schedule for {missing}")
    except LoadError as e:
        print(f"yandex-rasp rail load failed: {e}", file=sys.stderr)
        return 1
    records.sort(key=lambda r: (r["from_station"], r["to_station"]))
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for r in records
    )
    OUT_FILE.write_bytes(content.encode("utf-8"))
    total = len(destinations) * (len(destinations) - 1)
    print(f"{len(records)} station pairs written to {OUT_FILE}")
    print(f"  destination pairs with trains: {len(pairs_with_trains)} of {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

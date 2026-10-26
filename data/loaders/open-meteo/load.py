"""Loads the climate of the catalog destinations from Open-Meteo into data/loaders/open-meteo/out/climate.jsonl (P1-DS-03.3).

Source: Historical Weather API, https://archive-api.open-meteo.com/v1/archive (CC BY 4.0).
Needs data/loaders/catalog/out/destinations.jsonl and data/loaders/geonames/out/cities.jsonl: the climate of a
destination is the climate of the coordinates of its city (not of a resort next to it: Sheregesh is Novokuznetsk here).

For every destination, 12 rows (January first) averaged over the years START_YEAR..END_YEAR:
- `temp_max_c`, `temp_min_c`: mean of the daily maximum and minimum of the month over all its days of all years;
- `precipitation_mm`: total of the month, mean over the years.
`sea_temp_c` is filled by P1-DS-03.4: run load_sea.py after this loader, it adds the field to out/climate.jsonl.

The years are fixed, so a rebuild gives the same file. One response per destination is cached in
data/loaders/open-meteo/cache/ (not in git); a file placed there by hand is used without any request. Delete it or
pass --refresh to fetch a new one.

The free API limits the requests per IP address and answers 429 when the limit is spent. The daily limit stops the
loader at once (rerun it later, the destinations already cached are not requested again); the other limits are waited out.
A network error is retried a few times.

Usage: python3 data/loaders/open-meteo/load.py [--refresh]
"""

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

LOADER_DIR = Path(__file__).resolve().parent
CACHE_DIR = LOADER_DIR / "cache"
OUT_FILE = LOADER_DIR / "out" / "climate.jsonl"
DESTINATIONS_FILE = LOADER_DIR.parent / "catalog" / "out" / "destinations.jsonl"
CITIES_FILE = LOADER_DIR.parent / "geonames" / "out" / "cities.jsonl"

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
START_YEAR, END_YEAR = 2015, 2024
DAILY = ("temperature_2m_max", "temperature_2m_min", "precipitation_sum")
RETRIES = 3
RETRY_WAIT_SECONDS = 65  # the minutely limit of the free API resets after a minute
NETWORK_RETRY_WAIT_SECONDS = 5
PAUSE_SECONDS = 1.0


class LoadError(Exception):
    pass


def read_jsonl(path):
    if not path.exists():
        raise LoadError(f"{path} not found, run the loader that writes it first")
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def get_json(url, params, label):
    """GET with the limits of the free API: waits out the minutely 429, stops on the daily one; retries network errors."""
    for attempt in range(1, RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=60)
        except requests.RequestException as e:
            if attempt == RETRIES:
                raise LoadError(f"{label}: request failed after {RETRIES} attempts: {e}") from None
            print(f"  network error for {label}, retrying in {NETWORK_RETRY_WAIT_SECONDS} s: {type(e).__name__}")
            time.sleep(NETWORK_RETRY_WAIT_SECONDS)
            continue
        if response.status_code != 429:
            break
        reason = response.json().get("reason", "") if response.content else ""
        if "daily" in reason.lower():
            raise LoadError(f"{label}: {reason}")
        if attempt == RETRIES:
            raise LoadError(f"{label}: still 429 after {RETRIES} attempts: {reason}")
        print(f"  429 for {label}, waiting {RETRY_WAIT_SECONDS} s: {reason}")
        time.sleep(RETRY_WAIT_SECONDS)
    if response.status_code != 200:
        raise LoadError(f"{label}: HTTP {response.status_code}: {response.text[:200]}")
    return response.json()


def fetch(destination, city, refresh):
    """Returns the daily series of the destination, from the cache or from the API."""
    path = CACHE_DIR / f"{destination['id']}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    params = {
        "latitude": city["lat"],
        "longitude": city["lon"],
        "start_date": f"{START_YEAR}-01-01",
        "end_date": f"{END_YEAR}-12-31",
        "daily": ",".join(DAILY),
        "timezone": "auto",
    }
    data = get_json(ARCHIVE_URL, params, destination["id"])
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    part.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    part.replace(path)
    time.sleep(PAUSE_SECONDS)
    return data


def monthly_climate(destination_id, data):
    """Twelve rows from the daily series; fails on a month without any value."""
    try:
        daily = data["daily"]
        days = daily["time"]
        series = {name: daily[name] for name in DAILY}
    except KeyError as e:
        raise LoadError(f"{destination_id}: no {e} in the response") from None

    tmax, tmin = defaultdict(list), defaultdict(list)
    rain = defaultdict(lambda: defaultdict(float))  # month -> year -> total of the month
    seen = defaultdict(set)
    for i, day in enumerate(days):
        year, month = int(day[:4]), int(day[5:7])
        if series["temperature_2m_max"][i] is not None:
            tmax[month].append(series["temperature_2m_max"][i])
        if series["temperature_2m_min"][i] is not None:
            tmin[month].append(series["temperature_2m_min"][i])
        if series["precipitation_sum"][i] is not None:
            rain[month][year] += series["precipitation_sum"][i]
            seen[month].add(year)

    rows = []
    for month in range(1, 13):
        if not tmax[month] or not tmin[month] or not seen[month]:
            raise LoadError(f"{destination_id}: no data for month {month}")
        rows.append({
            "destination_id": destination_id,
            "month": month,
            "temp_max_c": round(sum(tmax[month]) / len(tmax[month]), 1),
            "temp_min_c": round(sum(tmin[month]) / len(tmin[month]), 1),
            "precipitation_mm": round(sum(rain[month].values()) / len(seen[month]), 1),
        })
    return rows


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
        records = []
        for destination in sorted(destinations, key=lambda d: d["id"]):
            records.extend(monthly_climate(destination["id"], fetch(destination, cities[destination["city_id"]], refresh)))
    except LoadError as e:
        print(f"open-meteo load failed: {e}", file=sys.stderr)
        return 1
    write_records(records)
    print(f"{len(records)} climate rows for {len(destinations)} destinations written to {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

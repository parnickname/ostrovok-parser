"""Adds the sea temperature to data/loaders/open-meteo/out/climate.jsonl (P1-DS-03.4).

Run it after load.py (P1-DS-03.3): it reads out/climate.jsonl and writes it back with `sea_temp_c` set for the
destinations with the `sea` tag and removed for all others. Running it twice gives the same file; running load.py
again rewrites the file without `sea_temp_c`, so run this one after it every time.

Source: Marine Weather API, https://marine-api.open-meteo.com/v1/marine, `sea_surface_temperature_mean` (CC BY 4.0).
Its history starts on 2022-11-24, so the sea temperature is averaged over the two full years SEA_START_YEAR..
SEA_END_YEAR, not over ten years like the air temperature of load.py. The API moves the coordinates of the city to
the nearest sea cell of its grid itself. The Caspian Sea is not in the model.

`sea_temp_c` is the mean of the daily mean sea surface temperature of the month over all its days of the two years.
Every `sea` destination must get all twelve months, otherwise the loader fails: P1-DS-03.4 is done when they all
have `sea_temp_c`.

One response per destination is cached in data/loaders/open-meteo/cache/ (not in git), as in load.py.

Usage: python3 data/loaders/open-meteo/load_sea.py [--refresh]
"""

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from load import (
    CACHE_DIR, CITIES_FILE, DESTINATIONS_FILE, OUT_FILE, PAUSE_SECONDS, LoadError, get_json, read_jsonl, write_records,
)

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
SEA_START_YEAR, SEA_END_YEAR = 2023, 2024
MIN_DAYS_PER_MONTH = 20  # of the 56-62 days of a month in two years
SEA_TEMP_RANGE_C = (-2.0, 40.0)


class GapError(LoadError):
    """The response has no (or implausible) sea temperature for a destination."""


def fetch_sea(destination, city, refresh):
    path = CACHE_DIR / f"sea-{destination['id']}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    params = {
        "latitude": city["lat"],
        "longitude": city["lon"],
        "start_date": f"{SEA_START_YEAR}-01-01",
        "end_date": f"{SEA_END_YEAR}-12-31",
        "daily": "sea_surface_temperature_mean",
        "timezone": "auto",
    }
    data = get_json(MARINE_URL, params, destination["id"])
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    part.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    part.replace(path)
    time.sleep(PAUSE_SECONDS)
    return data


def monthly_sea_temperature(destination_id, data):
    """Maps month -> mean sea surface temperature; fails when a month has too few values."""
    try:
        days = data["daily"]["time"]
        values = data["daily"]["sea_surface_temperature_mean"]
    except KeyError as e:
        raise GapError(f"{destination_id}: no {e} in the response") from None
    by_month = defaultdict(list)
    for day, value in zip(days, values):
        if value is not None:
            by_month[int(day[5:7])].append(value)
    result = {}
    for month in range(1, 13):
        if len(by_month[month]) < MIN_DAYS_PER_MONTH:
            raise GapError(
                f"{destination_id}: only {len(by_month[month])} sea temperature values for month {month}, "
                f"at least {MIN_DAYS_PER_MONTH} needed (no data at the coordinates of the city?)"
            )
        temp = round(sum(by_month[month]) / len(by_month[month]), 1)
        if not SEA_TEMP_RANGE_C[0] <= temp <= SEA_TEMP_RANGE_C[1]:
            raise GapError(f"{destination_id}: month {month}: implausible sea temperature {temp}")
        result[month] = temp
    return result


def main():
    refresh = "--refresh" in sys.argv[1:]
    try:
        destinations = [d for d in read_jsonl(DESTINATIONS_FILE) if "sea" in d["tags"]]
        cities = {c["id"]: c for c in read_jsonl(CITIES_FILE)}
        climate = read_jsonl(OUT_FILE)
        sea, gaps = {}, []
        for destination in sorted(destinations, key=lambda d: d["id"]):
            data = fetch_sea(destination, cities[destination["city_id"]], refresh)
            try:
                sea[destination["id"]] = monthly_sea_temperature(destination["id"], data)
            except GapError as e:  # all of them are reported at once, not the first one only
                gaps.append(str(e))
        if gaps:
            raise LoadError("no sea temperature for " + str(len(gaps)) + " destination(s):\n  " + "\n  ".join(gaps))
    except LoadError as e:
        print(f"open-meteo sea load failed: {e}", file=sys.stderr)
        return 1
    for row in climate:
        row.pop("sea_temp_c", None)
        if row["destination_id"] in sea:
            row["sea_temp_c"] = sea[row["destination_id"]][row["month"]]
    write_records(sorted(climate, key=lambda r: (r["destination_id"], r["month"])))
    print(f"sea_temp_c set for {len(sea)} destinations ({len(sea) * 12} rows) in {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

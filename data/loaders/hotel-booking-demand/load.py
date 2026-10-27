"""Builds the seasonal factors of hotel prices into data/loaders/hotel-booking-demand/out/calibration.jsonl
(P1-DS-07.2).

Sources:
  Hotel Booking Demand (Antonio, Almeida, Nunes, 2019; CC BY 4.0), the copy published by TidyTuesday, pinned to a
  commit so a rebuild gives the same result:
    https://raw.githubusercontent.com/rfordatascience/tidytuesday/<commit>/data/2020/2020-02-11/hotels.csv
  (the Kaggle page in sources.yaml needs an account to download, the file is the same);
  Nager.Date (https://date.nager.at), public holidays of Russia for HOLIDAYS_YEAR.
Downloads are cached in data/loaders/hotel-booking-demand/cache/ (not in git); a file placed there by hand is used
without any download. Delete it or pass --refresh to fetch a new one.

`season_factor[m]`, January first, rounded to two decimals:
  price_m   = mean `adr` (average daily rate) of the bookings that were not cancelled and have adr > 0, by month of
              arrival, both hotels, all years of the data set;
  ratio_m   = price_m / mean of the twelve price_m;
  holiday_m = Russian public holidays in month m / days in month m (New Year holidays make January busy);
  factor_m  = ratio_m * (1 + holiday_m).

The output is a part of the single `calibration` object (CFC-3): `season_factor` and `sources`. data/build.py merges
it with the parts of the other loaders (P1-DS-07).

Usage: python3 data/loaders/hotel-booking-demand/load.py [--refresh]
"""

import calendar
import csv
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import requests

LOADER_DIR = Path(__file__).resolve().parent
CACHE_DIR = LOADER_DIR / "cache"
OUT_FILE = LOADER_DIR / "out" / "calibration.jsonl"

HOTELS_COMMIT = "d75aaa0d31596ad6487ae0db20138067d301e031"
HOTELS_URL = (
    f"https://raw.githubusercontent.com/rfordatascience/tidytuesday/{HOTELS_COMMIT}/data/2020/2020-02-11/hotels.csv"
)
HOLIDAYS_YEAR = 2026  # the year of the snapshot
HOLIDAYS_URL = f"https://date.nager.at/api/v3/PublicHolidays/{HOLIDAYS_YEAR}/RU"
HOLIDAYS_FILE = f"holidays-RU-{HOLIDAYS_YEAR}.json"

MONTHS = [calendar.month_name[m] for m in range(1, 13)]  # "January", ... as in arrival_date_month


class LoadError(Exception):
    pass


def download(url, name, refresh):
    """Returns the cached file, fetching it first when it is missing."""
    path = CACHE_DIR / name
    if path.exists() and not refresh:
        return path
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}")
    part = path.with_suffix(path.suffix + ".part")
    try:
        with requests.get(url, stream=True, timeout=60) as response:
            response.raise_for_status()
            with part.open("wb") as f:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
    except requests.RequestException as e:
        part.unlink(missing_ok=True)
        raise LoadError(f"cannot download {url}: {e}") from None
    part.replace(path)
    return path


def monthly_prices(path):
    """Mean adr of the kept bookings by month of arrival, January first."""
    total, count = defaultdict(float), defaultdict(int)
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["is_canceled"] != "0":
                continue
            adr = float(row["adr"])
            if adr <= 0:
                continue
            month = row["arrival_date_month"]
            if month not in MONTHS:
                raise LoadError(f"{path.name}: unknown arrival_date_month {month!r}")
            total[month] += adr
            count[month] += 1
    missing = [m for m in MONTHS if not count[m]]
    if missing:
        raise LoadError(f"{path.name}: no bookings for {missing}")
    return [total[m] / count[m] for m in MONTHS]


def holiday_shares(path):
    """Share of public holiday days in every month, January first."""
    holidays = json.loads(path.read_text(encoding="utf-8"))
    days = defaultdict(set)
    for holiday in holidays:
        day = date.fromisoformat(holiday["date"])
        if day.year == HOLIDAYS_YEAR and "Public" in holiday.get("types", []):
            days[day.month].add(day)
    if not days:
        raise LoadError(f"{path.name}: no public holidays of {HOLIDAYS_YEAR}")
    return [len(days[m]) / calendar.monthrange(HOLIDAYS_YEAR, m)[1] for m in range(1, 13)]


def build_record(prices, holidays):
    mean = sum(prices) / len(prices)
    factors = [round(p / mean * (1 + h), 2) for p, h in zip(prices, holidays)]
    return {"season_factor": factors, "sources": ["hotel-booking-demand", "nager-date"]}


def main():
    refresh = "--refresh" in sys.argv[1:]
    try:
        prices = monthly_prices(download(HOTELS_URL, "hotels.csv", refresh))
        holidays = holiday_shares(download(HOLIDAYS_URL, HOLIDAYS_FILE, refresh))
        record = build_record(prices, holidays)
    except LoadError as e:
        print(f"hotel-booking-demand load failed: {e}", file=sys.stderr)
        return 1
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    OUT_FILE.write_bytes(line.encode("utf-8"))
    print(f"season_factor written to {OUT_FILE}")
    for name, price, share, factor in zip(MONTHS, prices, holidays, record["season_factor"]):
        print(f"  {name[:3]}: adr {price:6.1f}, holidays {share:4.0%}, factor {factor}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

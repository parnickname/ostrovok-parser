"""Builds the tourist tax of the Russian destinations into data/loaders/tourist-tax/out/calibration.jsonl (P1-DS-07.6).

Source: rates.csv, filled by hand from the official tables of the regional offices of the Federal Tax Service
("Ставки и льготы по туристическому налогу", one per region, columns `source_url`) and the acts of the municipalities
they quote. One row per Russian destination: `in_force` is yes/no, `act` and `source_url` are required where it is yes.
The tax is a local tax, introduced by a municipality (a law in Moscow and Saint Petersburg).

The output is a part of the single `calibration` object (CFC-3): `tourist_tax_rub_per_night`, roubles per guest per
night by the id of the city, only where the tax is in force, and `sources`. data/build.py merges it with the parts
of the other loaders (P1-DS-07).

The tax is a percentage of the price of the stay (up to 2 % in 2026) but not less than MIN_RUB_PER_NIGHT per night
(Tax Code, chapter 33.1). The prices of the prototype are 1 900-7 500 roubles per guest per night, 1-2 % of which is
below the minimum, so the minimum is the amount per night. The real rates stay in rates.csv for reference.

Needs data/loaders/catalog/out/destinations.jsonl: the loader fails when a Russian destination has no row, or a row
belongs to a city that is not a Russian destination.

Usage: python3 data/loaders/tourist-tax/load.py
"""

import csv
import json
import sys
from pathlib import Path

LOADER_DIR = Path(__file__).resolve().parent
RATES_FILE = LOADER_DIR / "rates.csv"
OUT_FILE = LOADER_DIR / "out" / "calibration.jsonl"
DESTINATIONS_FILE = LOADER_DIR.parent / "catalog" / "out" / "destinations.jsonl"

MIN_RUB_PER_NIGHT = 100
SOURCE = "tourist-tax"


class LoadError(Exception):
    pass


def read_rows():
    with RATES_FILE.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def build_record(rows, destinations):
    russian = {d["city_id"] for d in destinations if d["country_code"] == "RU"}
    seen, tax = set(), {}
    for line_no, row in enumerate(rows, start=2):
        where = f"{RATES_FILE.name}:{line_no}"
        city_id, in_force = row["city_id"].strip(), row["in_force"].strip()
        if city_id not in russian:
            raise LoadError(f"{where}: {city_id!r} is not a Russian destination")
        if city_id in seen:
            raise LoadError(f"{where}: {city_id!r} twice")
        seen.add(city_id)
        if in_force not in ("yes", "no"):
            raise LoadError(f"{where}: in_force must be yes or no, got {in_force!r}")
        if in_force == "yes":
            if not row["act"].strip() or not row["source_url"].strip():
                raise LoadError(f"{where}: {city_id!r} has the tax in force, so act and source_url are required")
            tax[city_id] = MIN_RUB_PER_NIGHT
    missing = sorted(russian - seen)
    if missing:
        raise LoadError(f"no row in {RATES_FILE.name} for the Russian destinations: {', '.join(missing)}")
    return {"tourist_tax_rub_per_night": dict(sorted(tax.items())), "sources": [SOURCE]}


def main():
    try:
        if not DESTINATIONS_FILE.exists():
            raise LoadError(f"{DESTINATIONS_FILE} not found, run data/loaders/catalog/load.py first")
        with DESTINATIONS_FILE.open(encoding="utf-8") as f:
            destinations = [json.loads(line) for line in f if line.strip()]
        record = build_record(read_rows(), destinations)
    except LoadError as e:
        print(f"tourist-tax load failed: {e}", file=sys.stderr)
        return 1
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_bytes((json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    tax = record["tourist_tax_rub_per_night"]
    print(f"tourist tax in force in {len(tax)} of the Russian destinations, {MIN_RUB_PER_NIGHT} rub per night each, written to {OUT_FILE}")
    print("  in force: " + ", ".join(tax))
    return 0


if __name__ == "__main__":
    sys.exit(main())

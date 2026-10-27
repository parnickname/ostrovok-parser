"""Writes the estimated air and rail fare parameters into data/loaders/synthetic/out/calibration.jsonl.

Without `airfare` and `rail` in `calibration` the backend prices every air and rail ticket at 0 rubles and a
connection at 0 minutes (review of the backend, round 2, item 2). Until real sources have run, the values of the
example of CFC-3 (docs/snapshot-format.md) are used, with the provider `synthetic`:
- `airfare` — until P1-DS-07.3 (data/loaders/travelpayouts, needs a Travelpayouts token) has run;
- `rail.rub_per_hour` — until P1-DS-07.7 (data/loaders/rosstat, Rosstat prices of platzkart and kupe tickets) has run.

This part is a fallback: data/build.py replaces a key of it by the same key of another loader and drops `synthetic`
from `sources` when nothing of the part is left (merge_calibration), so the order of the loaders does not matter.

The output is a part of the single `calibration` object (CFC-3): `airfare`, `rail` and `sources`. data/build.py merges
it with the parts of the other loaders (P1-DS-07).

Usage: python3 data/loaders/synthetic/load.py
"""

import json
from pathlib import Path

LOADER_DIR = Path(__file__).resolve().parent
OUT_FILE = LOADER_DIR / "out" / "calibration.jsonl"

# impulse: estimates from the example of CFC-3; remove airfare when P1-DS-07.3 has run once, and rail when
# P1-DS-07.7 has.
AIRFARE = {"base_rub": 2500, "rub_per_km_short": 7, "rub_per_km_long": 5, "long_from_km": 2000, "transfer_minutes": 120}
RAIL = {"rub_per_hour": 450}


def main():
    record = {"airfare": AIRFARE, "rail": RAIL, "sources": ["synthetic"]}
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    OUT_FILE.write_bytes(line.encode("utf-8"))
    print(f"airfare and rail estimates written to {OUT_FILE}")


if __name__ == "__main__":
    main()

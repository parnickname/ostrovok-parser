"""Writes the estimated air and rail fare parameters into data/loaders/synthetic/out/calibration.jsonl.

Without `airfare` and `rail` in `calibration` the backend prices every air and rail ticket at 0 rubles and a
connection at 0 minutes (review of the backend, round 2, item 2). Until real sources exist, the values of the
example of CFC-3 (docs/snapshot-format.md) are used, with the provider `synthetic`:
- `airfare` — until P1-DS-07.3 (Aviasales Data API) gets its Travelpayouts token;
- `rail.rub_per_hour` — data.md has no subtask for it yet; the backend proposes a new one (07.7, the mean cost of an
  hour on the road by Russian Railways tariffs), the team decides.

The output is a part of the single `calibration` object (CFC-3): `airfare`, `rail` and `sources`. data/build.py merges
it with the parts of the other loaders (P1-DS-07).

Usage: python3 data/loaders/synthetic/load.py
"""

import json
from pathlib import Path

LOADER_DIR = Path(__file__).resolve().parent
OUT_FILE = LOADER_DIR / "out" / "calibration.jsonl"

# impulse: estimates from the example of CFC-3; replace airfare with P1-DS-07.3 when the Travelpayouts token
# arrives, and rail when the team adds a subtask with a source for it.
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

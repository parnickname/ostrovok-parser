"""Writes the `early_checkin` block of `calibration` (CFC-10) into data/loaders/hotel-policy/out/calibration.jsonl.

The backend confirms an early check-in when the requested time is not earlier than `earliest` and charges
`price_share` of the price of a night (CFC-8). Q-009 (which chains to base the shares on) was decided by the proposal
of spec 0004: the values of CFC-10. The big chains publish no chain-wide rule: Marriott and Hilton leave the time and
the fee of an early check-in to the hotel, so there is no source to download or to cite, and the values are an
estimate (provider `hotel-chain-rules`, kind `derived`; see README, "Решения команды").

The output is a part of the single `calibration` object (CFC-3): `early_checkin` and `sources`. data/build.py merges
it with the parts of the other loaders (P1-DS-07). The block `date_change` is written by another loader (P2-DS-01.2),
and `schema_version` of `calibration` becomes 2 when both are there.

Usage: python3 data/loaders/hotel-policy/load.py
"""

import json
from pathlib import Path

LOADER_DIR = Path(__file__).resolve().parent
OUT_FILE = LOADER_DIR / "out" / "calibration.jsonl"

PROVIDER = "hotel-chain-rules"
# impulse: the values of the example of CFC-10; replace them when a source with a real share of the price appears.
EARLY_CHECKIN = {"earliest": "10:00", "price_share": 0.5, "sources": [PROVIDER]}


def main():
    record = {"early_checkin": EARLY_CHECKIN, "sources": [PROVIDER]}
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    OUT_FILE.write_bytes(line.encode("utf-8"))
    print(f"early_checkin written to {OUT_FILE}")


if __name__ == "__main__":
    main()

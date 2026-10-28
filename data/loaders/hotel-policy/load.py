"""Writes the blocks `early_checkin` and `date_change` of `calibration` (CFC-10) into
data/loaders/hotel-policy/out/calibration.jsonl.

The backend confirms an early check-in when the requested time is not earlier than `earliest` and charges
`price_share` of the price of a night (CFC-8). A change of the dates costs `fee_share_refundable` or
`fee_share_nonrefundable`, by the kind of the rate.

Q-009 (the source of the values) is decided in README, "Решения команды": there is no rule of the big chains to cite
(Marriott and Hilton leave the time and the fee of an early check-in to the hotel), so the source is the law,
Government decree № 1912 of 27.11.2025 (provider `rf-hotel-rules`, in force 01.03.2026 – 01.03.2032):
- `price_share` 0.5: item 33 caps the fee for an early check-in at the price of half a day (the cap is used as the
  price), and only for up to 12 hours before the check-in time; beyond 12 hours the hotel sets the fee itself;
- `earliest` 10:00: the decree gives no time, the value is the one of CFC-10, an estimate (provider `synthetic`);
- `fee_share_refundable` 0.0: item 16, a refusal before the day of arrival is refunded in full, so a change of the
  dates before that day costs nothing;
- `fee_share_nonrefundable` 0.2: the decree sets only the ceiling (item 16: not more than the price of a day for a
  late refusal or no-show) and knows no change of dates and no non-refundable rate, so the share is the value of
  CFC-10, an estimate (provider `synthetic`).

The output is a part of the single `calibration` object (CFC-3): both blocks and `sources`. data/build.py merges it
with the parts of the other loaders (P1-DS-07) and sets `schema_version` of `calibration` to 2, since both blocks
are there (P2-DS-01.0).

Usage: python3 data/loaders/hotel-policy/load.py
"""

import json
from pathlib import Path

LOADER_DIR = Path(__file__).resolve().parent
OUT_FILE = LOADER_DIR / "out" / "calibration.jsonl"

SOURCES = ["rf-hotel-rules", "synthetic"]
# impulse: `earliest` and `fee_share_nonrefundable` are the values of the example of CFC-10, no source gives them;
# replace them when one does (a rate of a chain with a real fee for a change of the dates).
EARLY_CHECKIN = {"earliest": "10:00", "price_share": 0.5, "sources": SOURCES}
DATE_CHANGE = {"fee_share_refundable": 0.0, "fee_share_nonrefundable": 0.2, "sources": SOURCES}


def main():
    record = {"early_checkin": EARLY_CHECKIN, "date_change": DATE_CHANGE, "sources": SOURCES}
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    OUT_FILE.write_bytes(line.encode("utf-8"))
    print(f"early_checkin and date_change written to {OUT_FILE}")


if __name__ == "__main__":
    main()

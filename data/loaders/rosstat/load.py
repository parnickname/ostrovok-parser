"""Builds hotel bed prices by stars (P1-DS-07.1, Russia) and transfer tariffs (P1-DS-07.4) into
data/loaders/rosstat/out/calibration.jsonl.

Source: Rosstat, average consumer prices of 2025 (https://rosstat.gov.ru/statistics/price,
sred_potreb_cen_2025.xlsx): one sheet per month, the row of the Russian Federation (code 643), the columns in
COLUMNS. The xlsx is read with the standard library.
The download is cached in data/loaders/rosstat/cache/ (not in git); a file placed there by hand is used without any
download. Delete it or pass --refresh to fetch a new one.

`bed_price_rub` — price per guest per night on the Russian level, whole rubles:
- "2", "3" — the mean of the twelve months of 2025;
- "4", "5" — Rosstat gives one price for 4*-5*. The price is assumed to grow by the same factor q from 3* to 4* and
  from 4* to 5*, and the Rosstat price to be the mean of the two: P45 = P3 * (q + q^2) / 2, so
  q = (-1 + sqrt(1 + 8 * P45 / P3)) / 2, "4" = P3 * q, "5" = P3 * q^2.

`transfer` — one tariff for all catalog cities; the backend multiplies it by `country_price_level` (the city
coefficient of 07.4). Means of the twelve months, whole rubles:
- `taxi_rub_per_km` — "Проезд в такси, в расчете на 1 км пути";
- `public_rub_per_person` — "Проезд в городском автобусе, поездка";
- `shuttle_rub_per_person` — "Проезд в междугороднем автобусе, в расчете на 50 км пути": an airport shuttle is a bus
  ride of about that length;
- `taxi_base_rub` and `pets_surcharge_rub` — no open source gives them, they are set by hand (provider `synthetic`):
  the base fare is TAXI_BASE_KM kilometres at the Rosstat rate, the pet surcharge is PETS_SURCHARGE_RUB.

`rail.rub_per_hour` (P1-DS-07.7) — the cost of an hour in a train. Rosstat gives the price of 100 km of a seat in a
second-class (platzkart) and a compartment (kupe) car of a non-branded long-distance train; the mean of the two, per
kilometre, is multiplied by the mean speed of the trains of our schedules (rail_routes of P1-DS-06.3): the great-circle
distance between the stations of every route times RAIL_CIRCUITY, summed over the routes and divided by their
summed duration (longer routes weigh more, as they do in the cost of a ticket).

The output is a part of the single `calibration` object (CFC-3): `bed_price_rub`, `transfer`, `rail` and `sources`.
data/build.py merges it with the parts of the other loaders (P1-DS-07).

Needs data/loaders/yandex-rasp/out/stations.jsonl and rail_routes.jsonl (for the speed of the trains).

Usage: python3 data/loaders/rosstat/load.py [--refresh]
"""

import json
import math
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import requests

LOADER_DIR = Path(__file__).resolve().parent
STATIONS_FILE = LOADER_DIR.parent / "yandex-rasp" / "out" / "stations.jsonl"
RAIL_ROUTES_FILE = LOADER_DIR.parent / "yandex-rasp" / "out" / "rail_routes.jsonl"
CACHE_DIR = LOADER_DIR / "cache"
OUT_FILE = LOADER_DIR / "out" / "calibration.jsonl"

PRICES_FILE = "sred_potreb_cen_2025.xlsx"
PRICES_URL = f"https://rosstat.gov.ru/storage/mediabank/{PRICES_FILE}"
HEADERS = {"User-Agent": "ostrovok-parser/1.0 (https://github.com/parnickname/ostrovok-parser)"}

RUSSIA_CODE = "643"
MONTH_SHEET = re.compile(r"^\d\d\(2025\)$")
COLUMNS = {
    "2": "Проживание в гостинице 2*",
    "3": "Проживание в гостинице 3*",
    "45": "Проживание в гостинице 4*-5*",
    "taxi_km": "Проезд в такси, в расчете на 1 км пути",
    "bus": "Проезд в городском автобусе",
    "intercity_50km": "Проезд в междугороднем автобусе, в расчете на 50 км пути",
    "rail_platzkart": "Проезд в плацкартном вагоне скорого нефирменного поезда дальнего следования",
    "rail_kupe": "Проезд в купейном вагоне скорого нефирменного поезда дальнего следования",
}
# Rosstat prices of a train ticket are per 100 km of the way.
RAIL_PRICE_KM = 100
# impulse: the length of the railway is taken as the great-circle distance times a typical factor, the track length
# is not in our data; replace with the tariff distances when a source gives them.
RAIL_CIRCUITY = 1.2
EARTH_RADIUS_KM = 6371.0
# impulse: hand-set transfer parameters, no open source has them; replace when a taxi tariff source is accepted.
TAXI_BASE_KM = 3
PETS_SURCHARGE_RUB = 300
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


class LoadError(Exception):
    pass


def download(refresh):
    path = CACHE_DIR / PRICES_FILE
    if path.exists() and not refresh:
        return path
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"downloading {PRICES_URL}")
    part = path.with_suffix(path.suffix + ".part")
    try:
        with requests.get(PRICES_URL, headers=HEADERS, stream=True, timeout=60) as response:
            response.raise_for_status()
            with part.open("wb") as f:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
    except requests.RequestException as e:
        part.unlink(missing_ok=True)
        raise LoadError(f"cannot download {PRICES_URL}: {e}") from None
    part.replace(path)
    return path


def column_index(ref):
    """'C12' -> 2."""
    letters = re.match(r"[A-Z]+", ref).group(0)
    index = 0
    for ch in letters:
        index = index * 26 + ord(ch) - ord("A") + 1
    return index - 1


def read_rows(archive, sheet_path, strings):
    """Yields every row of a worksheet as {column index: text}."""
    root = ElementTree.fromstring(archive.read(sheet_path))
    for row in root.iterfind(".//m:sheetData/m:row", NS):
        cells = {}
        for cell in row.iterfind("m:c", NS):
            value = cell.find("m:v", NS)
            if value is None:
                continue
            text = value.text or ""
            if cell.get("t") == "s":
                text = strings[int(text)]
            cells[column_index(cell.get("r"))] = text
        yield cells


def monthly_prices(path):
    """Returns {column key: [price of every month sheet]} for the Russian Federation."""
    with zipfile.ZipFile(path) as archive:
        shared = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        strings = ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")) for si in shared.iterfind("m:si", NS)]
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target") for rel in rels}
        prices = {key: [] for key in COLUMNS}
        months = 0
        for sheet in workbook.iterfind(".//m:sheets/m:sheet", NS):
            if not MONTH_SHEET.match(sheet.get("name", "")):
                continue
            months += 1
            columns, found = {}, False
            for cells in read_rows(archive, "xl/" + targets[sheet.get(REL_NS)], strings):
                for index, text in cells.items():
                    for key, title in COLUMNS.items():
                        if text.startswith(title):
                            columns[key] = index
                if cells.get(0) == RUSSIA_CODE:
                    if set(columns) != set(COLUMNS):
                        raise LoadError(f"sheet {sheet.get('name')}: columns not found: {set(COLUMNS) - set(columns)}")
                    for key, index in columns.items():
                        prices[key].append(float(cells[index]))
                    found = True
                    break
            if not found:
                raise LoadError(f"sheet {sheet.get('name')}: no row {RUSSIA_CODE}")
    if months != 12:
        raise LoadError(f"{path.name}: expected 12 month sheets, found {months}")
    return prices


def read_jsonl(path):
    if not path.exists():
        raise LoadError(f"{path} not found, run the loader that writes it first")
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def distance_km(a, b):
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b["lon"] - a["lon"]) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def mean_train_speed_kmh(stations, rail_routes):
    """Mean speed of the trains of the schedules: summed track length over summed time."""
    by_code = {s["code"]: s for s in stations}
    km = hours = 0.0
    for route in rail_routes:
        km += distance_km(by_code[route["from_station"]], by_code[route["to_station"]]) * RAIL_CIRCUITY
        hours += route["duration_min"] / 60
    if hours == 0:
        raise LoadError("rail_routes is empty, the speed of the trains is unknown")
    return km / hours


def build_record(prices, train_speed_kmh):
    mean = {key: sum(values) / len(values) for key, values in prices.items()}
    p3, p45 = mean["3"], mean["45"]
    # impulse: 4* and 5* split from the single Rosstat price 4*-5* by an even growth factor;
    # replace with separate prices when a source gives 4* and 5* apart.
    q = (-1 + math.sqrt(1 + 8 * p45 / p3)) / 2
    beds = {"2": mean["2"], "3": p3, "4": p3 * q, "5": p3 * q * q}
    transfer = {
        "taxi_base_rub": round(mean["taxi_km"] * TAXI_BASE_KM),
        "taxi_rub_per_km": round(mean["taxi_km"]),
        "pets_surcharge_rub": PETS_SURCHARGE_RUB,
        "shuttle_rub_per_person": round(mean["intercity_50km"]),
        "public_rub_per_person": round(mean["bus"]),
    }
    rail_rub_per_km = (mean["rail_platzkart"] + mean["rail_kupe"]) / 2 / RAIL_PRICE_KM
    return {
        "bed_price_rub": {k: round(v) for k, v in beds.items()},
        "rail": {"rub_per_hour": round(rail_rub_per_km * train_speed_kmh)},
        "transfer": transfer,
        "sources": ["rosstat", "synthetic"],
    }


def main():
    refresh = "--refresh" in sys.argv[1:]
    try:
        speed = mean_train_speed_kmh(read_jsonl(STATIONS_FILE), read_jsonl(RAIL_ROUTES_FILE))
        record = build_record(monthly_prices(download(refresh)), speed)
    except LoadError as e:
        print(f"rosstat load failed: {e}", file=sys.stderr)
        return 1
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    OUT_FILE.write_bytes(line.encode("utf-8"))
    print(f"calibration part written to {OUT_FILE}")
    print(f"  bed_price_rub: {record['bed_price_rub']}")
    print(f"  transfer: {record['transfer']}")
    print(f"  rail: {record['rail']} (mean speed {speed:.1f} km/h)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

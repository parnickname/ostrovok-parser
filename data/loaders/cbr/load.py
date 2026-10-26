"""Loads the exchange rates of the Central Bank of Russia into data/loaders/cbr/out/calibration.jsonl (P1-DS-07.5).

Source: https://www.cbr.ru/scripts/XML_daily.asp, official rates of foreign currencies (cbr.ru).
Needs data/loaders/catalog/out/destinations.jsonl: the rates are loaded for the currencies of the countries of the
catalog, listed in COUNTRY_CURRENCY (the loader fails for a catalog country that is not there).

The output is a part of the single `calibration` object (CFC-3): `fx_rub`, rubles for one unit of the currency
(`Value / Nominal` of the CBR), and `sources`. data/build.py merges it with the parts of the other loaders (P1-DS-07).
The ruble itself is not listed. The date of the rates is the `Date` of the CBR answer, printed at the end: the CBR
answers with the last rates it has when the requested date has none yet.

The answer is cached in data/loaders/cbr/cache/ (not in git); a file placed there by hand is used without any
request. Delete it or pass --refresh to fetch it again.

Usage: python3 data/loaders/cbr/load.py [--refresh]
"""

import json
import sys
from pathlib import Path
from xml.etree import ElementTree

import requests

LOADER_DIR = Path(__file__).resolve().parent
CACHE_FILE = LOADER_DIR / "cache" / "XML_daily.xml"
OUT_FILE = LOADER_DIR / "out" / "calibration.jsonl"
DESTINATIONS_FILE = LOADER_DIR.parent / "catalog" / "out" / "destinations.jsonl"

URL = "https://www.cbr.ru/scripts/XML_daily.asp"
RATES_DATE = "26/10/2026"  # DD/MM/YYYY, the date of the snapshot
HEADERS = {"User-Agent": "ostrovok-parser/1.0 (https://github.com/parnickname/ostrovok-parser)"}

# Currency of the prices of every country of the catalog. RUB is the home currency.
# impulse: MV is priced in USD: the CBR does not publish the Maldivian rufiyaa, and the resorts quote in dollars;
# remove when another source of the MVR rate is accepted.
COUNTRY_CURRENCY = {
    "RU": "RUB", "KZ": "KZT", "TR": "TRY", "TH": "THB", "GE": "GEL", "VN": "VND", "AE": "AED", "AM": "AMD",
    "MV": "USD", "BY": "BYN", "UZ": "UZS", "ID": "IDR", "EG": "EGP", "GR": "EUR", "ES": "EUR", "FR": "EUR",
    "IT": "EUR", "AT": "EUR", "PT": "EUR", "HU": "HUF", "JP": "JPY", "CZ": "CZK",
}


class LoadError(Exception):
    pass


def download(refresh):
    if CACHE_FILE.exists() and not refresh:
        return CACHE_FILE.read_bytes()
    try:
        response = requests.get(URL, params={"date_req": RATES_DATE}, headers=HEADERS, timeout=60)
        response.raise_for_status()
    except requests.RequestException as e:
        raise LoadError(f"cannot download {URL}: {e}") from None
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    part = CACHE_FILE.with_suffix(".part")
    part.write_bytes(response.content)
    part.replace(CACHE_FILE)
    return response.content


def parse_rates(raw):
    """Returns (date of the rates, {currency code: rubles for one unit})."""
    try:
        root = ElementTree.fromstring(raw)
        rates = {}
        for valute in root.iter("Valute"):
            nominal = int(valute.findtext("Nominal"))
            value = float(valute.findtext("Value").replace(",", "."))
            rates[valute.findtext("CharCode")] = round(value / nominal, 8)
        return root.get("Date"), rates
    except (ElementTree.ParseError, AttributeError, TypeError, ValueError) as e:
        raise LoadError(f"cannot read the answer of the CBR: {e}") from None


def build_record(destinations, rates):
    countries = sorted({d["country_code"] for d in destinations})
    unknown = [c for c in countries if c not in COUNTRY_CURRENCY]
    if unknown:
        raise LoadError(f"no currency in COUNTRY_CURRENCY for the catalog countries: {', '.join(unknown)}")
    currencies = sorted({COUNTRY_CURRENCY[c] for c in countries} - {"RUB"})
    missing = [c for c in currencies if c not in rates]
    if missing:
        raise LoadError(f"the CBR has no rate for: {', '.join(missing)}")
    return {"fx_rub": {c: rates[c] for c in currencies}, "sources": ["cbr"]}


def main():
    refresh = "--refresh" in sys.argv[1:]
    try:
        if not DESTINATIONS_FILE.exists():
            raise LoadError(f"{DESTINATIONS_FILE} not found, run data/loaders/catalog/load.py first")
        with DESTINATIONS_FILE.open(encoding="utf-8") as f:
            destinations = [json.loads(line) for line in f if line.strip()]
        date, rates = parse_rates(download(refresh))
        record = build_record(destinations, rates)
    except LoadError as e:
        print(f"cbr load failed: {e}", file=sys.stderr)
        return 1
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_bytes((json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    print(f"fx_rub for {len(record['fx_rub'])} currencies, rates of {date}, written to {OUT_FILE}")
    print("  " + ", ".join(f"{c} {v}" for c, v in record["fx_rub"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

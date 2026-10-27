"""Builds the price level of every catalog country into data/loaders/world-bank/out/calibration.jsonl
(P1-DS-07.1, abroad).

Source: World Bank, World Development Indicators API (https://data.worldbank.org, CC BY 4.0):
  PA.NUS.PPP   — PPP conversion factor, GDP (local currency per international $);
  PA.NUS.FCRF  — official exchange rate (local currency per US$).
Their ratio is the price level of the country against the US (the archived WDI series PA.NUS.PPPC.RF is the same
ratio). `country_price_level[c]` = level of c / level of Russia, so Russia is 1.0, as CFC-3 asks: a multiplier to the
Russian prices. For every country the latest year in YEARS where both indicators exist is taken.
The answers are cached in data/loaders/world-bank/cache/ (not in git); pass --refresh to fetch them again.

Needs data/loaders/catalog/out/destinations.jsonl: the levels are given for the countries of the catalog.

The output is a part of the single `calibration` object (CFC-3): `country_price_level` and `sources`.
data/build.py merges it with the parts of the other loaders (P1-DS-07).

Usage: python3 data/loaders/world-bank/load.py [--refresh]
"""

import json
import sys
from pathlib import Path

import requests

LOADER_DIR = Path(__file__).resolve().parent
CACHE_DIR = LOADER_DIR / "cache"
OUT_FILE = LOADER_DIR / "out" / "calibration.jsonl"
DESTINATIONS_FILE = LOADER_DIR.parent / "catalog" / "out" / "destinations.jsonl"

API_URL = "https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}"
INDICATORS = ("PA.NUS.PPP", "PA.NUS.FCRF")
YEARS = "2018:2025"
HOME_COUNTRY = "RU"


class LoadError(Exception):
    pass


def read_countries():
    if not DESTINATIONS_FILE.exists():
        raise LoadError(f"{DESTINATIONS_FILE} not found, run data/loaders/catalog/load.py first")
    with DESTINATIONS_FILE.open(encoding="utf-8") as f:
        countries = {json.loads(line)["country_code"] for line in f if line.strip()}
    return sorted(countries | {HOME_COUNTRY})


def fetch(indicator, countries, refresh):
    """Returns {country: {year: value}} for one indicator, from the cache or the API."""
    path = CACHE_DIR / f"{indicator}.json"
    if not path.exists() or refresh:
        url = API_URL.format(countries=";".join(countries), indicator=indicator)
        print(f"downloading {url}")
        try:
            response = requests.get(url, params={"format": "json", "per_page": 1000, "date": YEARS}, timeout=180)
            response.raise_for_status()
            answer = response.json()
        except (requests.RequestException, ValueError) as e:
            raise LoadError(f"cannot download {url}: {e}") from None
        if len(answer) < 2 or answer[1] is None:
            raise LoadError(f"{indicator}: unexpected answer {str(answer)[:200]}")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(answer[1], ensure_ascii=False, sort_keys=True), encoding="utf-8")
    values = {}
    for row in json.loads(path.read_text(encoding="utf-8")):
        if row["value"] is not None:
            values.setdefault(row["country"]["id"], {})[int(row["date"])] = row["value"]
    return values


def price_levels(ppp, fx, countries):
    """Price level against the US for every country, the latest year with both indicators."""
    levels = {}
    for country in countries:
        years = set(ppp.get(country, {})) & set(fx.get(country, {}))
        if not years:
            raise LoadError(f"{country}: no year with both {INDICATORS} in {YEARS}")
        year = max(years)
        levels[country] = (ppp[country][year] / fx[country][year], year)
    return levels


def main():
    refresh = "--refresh" in sys.argv[1:]
    try:
        countries = read_countries()
        ppp, fx = (fetch(indicator, countries, refresh) for indicator in INDICATORS)
        levels = price_levels(ppp, fx, countries)
    except LoadError as e:
        print(f"world-bank load failed: {e}", file=sys.stderr)
        return 1
    home = levels[HOME_COUNTRY][0]
    record = {
        "country_price_level": {c: round(level / home, 2) for c, (level, _) in levels.items()},
        "sources": ["world-bank"],
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    OUT_FILE.write_bytes(line.encode("utf-8"))
    print(f"country_price_level for {len(levels)} countries written to {OUT_FILE}")
    for country, (level, year) in sorted(levels.items()):
        print(f"  {country}: {record['country_price_level'][country]} ({year})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Loads countries into data/loaders/passport-index-data/out/countries.jsonl (P1-DS-02.1).

The list of countries is every passport and every destination of the visa matrix, so `visa` (P1-DS-02.2)
never refers to a country that is missing here. Names come from GeoNames.

Sources:
  https://github.com/imorte/passport-index-data (MIT)   passport-index-matrix-iso2.csv: the country codes;
  https://download.geonames.org/export/dump/ (CC BY 4.0) countryInfo.txt: English name and geonameId,
                                                         alternateNamesV2.zip: Russian name (isolanguage = ru).
Downloads are cached in data/loaders/passport-index-data/cache/ (not in git); a file placed there by hand is
used without any download. Delete it or pass --refresh to fetch a new one.

Usage: python3 data/loaders/passport-index-data/countries.py [--refresh]
"""

import csv
import io
import json
import sys
import zipfile
from pathlib import Path

import requests

LOADER_DIR = Path(__file__).resolve().parent
CACHE_DIR = LOADER_DIR / "cache"
OUT_FILE = LOADER_DIR / "out" / "countries.jsonl"

MATRIX_URL = "https://raw.githubusercontent.com/imorte/passport-index-data/master/passport-index-matrix-iso2.csv"
GEONAMES_URL = "https://download.geonames.org/export/dump/"
COUNTRY_INFO = "countryInfo.txt"
ALTERNATE_NAMES_ZIP = "alternateNamesV2.zip"

# Where the preferred GeoNames name is outdated or abbreviated, another Russian name of the same country from
# GeoNames is taken; the loader fails if GeoNames no longer has it.
RUSSIAN_NAME_CHOICES = {
    "CD": "Демократическая Республика Конго",  # preferred: "Заир"
    "KM": "Коморы",  # preferred: "Коморские о-ва"
    "SC": "Сейшельские Острова",  # preferred: "Сейшельские о-ва"
}

# Columns of countryInfo.txt.
INFO_ISO, INFO_NAME, INFO_GEONAME_ID = 0, 4, 16
# Columns of alternateNamesV2.
ALT_ID, ALT_GEONAME_ID, ALT_LANG, ALT_NAME, ALT_PREFERRED, ALT_SHORT, ALT_COLLOQUIAL, ALT_HISTORIC = range(8)


class LoadError(Exception):
    pass


def download(url, refresh):
    """Returns the cached file, fetching it first when it is missing."""
    path = CACHE_DIR / url.rsplit("/", 1)[1]
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


def read_matrix_codes(path):
    """Every passport (first column) and every destination (header) of the matrix."""
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0][0] != "Passport":
        raise LoadError(f"{path.name}: expected a header starting with 'Passport'")
    codes = set(rows[0][1:]) | {row[0] for row in rows[1:] if row}
    bad = sorted(c for c in codes if len(c) != 2 or not c.isalpha() or not c.isupper())
    if bad:
        raise LoadError(f"{path.name}: not ISO 3166-1 alpha-2 codes: {bad}")
    return codes


def read_country_info(path):
    """Maps ISO code -> (English name, geonameId)."""
    info = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            row = line.rstrip("\n").split("\t")
            if len(row) <= INFO_GEONAME_ID:
                raise LoadError(f"{path.name}: expected at least {INFO_GEONAME_ID + 1} columns in {line!r}")
            info[row[INFO_ISO]] = (row[INFO_NAME], row[INFO_GEONAME_ID])
    return info


def read_russian_names(path, wanted_ids):
    """Returns (geonameId -> best Russian name, geonameId -> all Russian names).

    Best is preferred first, then the lowest name id. The short-name flag is not used: for DE it marks "ФРГ",
    not "Германия".
    """
    best = {}
    all_names = {}
    with zipfile.ZipFile(path) as archive:
        with archive.open("alternateNamesV2.txt") as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8", newline="\n"):
                row = line.rstrip("\n").split("\t")
                if len(row) < 8 or row[ALT_LANG] != "ru" or row[ALT_GEONAME_ID] not in wanted_ids:
                    continue
                if row[ALT_COLLOQUIAL] == "1" or row[ALT_HISTORIC] == "1":
                    continue
                all_names.setdefault(row[ALT_GEONAME_ID], set()).add(row[ALT_NAME])
                key = (row[ALT_PREFERRED] != "1", int(row[ALT_ID]))
                current = best.get(row[ALT_GEONAME_ID])
                if current is None or key < current[0]:
                    best[row[ALT_GEONAME_ID]] = (key, row[ALT_NAME])
    return {geoname_id: name for geoname_id, (_, name) in best.items()}, all_names


def build_records(codes, info, russian_names, all_russian_names):
    missing = sorted(c for c in codes if c not in info)
    if missing:
        raise LoadError(f"{COUNTRY_INFO}: no country for codes of the visa matrix: {missing}")
    records = []
    for code in sorted(codes):
        name_en, geoname_id = info[code]
        name_ru = russian_names.get(geoname_id)
        choice = RUSSIAN_NAME_CHOICES.get(code)
        if choice is not None:
            if choice not in all_russian_names.get(geoname_id, ()):
                raise LoadError(f"{code} ({name_en}): {ALTERNATE_NAMES_ZIP} has no Russian name {choice!r}")
            name_ru = choice
        if name_ru is None:
            raise LoadError(f"{code} ({name_en}): no Russian name in {ALTERNATE_NAMES_ZIP}")
        records.append({"code": code, "name_ru": name_ru, "name_en": name_en})
    return records


def write_records(records):
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for r in records
    )
    OUT_FILE.write_bytes(content.encode("utf-8"))


def main():
    refresh = "--refresh" in sys.argv[1:]
    try:
        codes = read_matrix_codes(download(MATRIX_URL, refresh))
        info = read_country_info(download(GEONAMES_URL + COUNTRY_INFO, refresh))
        wanted_ids = {info[c][1] for c in codes if c in info}
        russian, all_russian = read_russian_names(download(GEONAMES_URL + ALTERNATE_NAMES_ZIP, refresh), wanted_ids)
        records = build_records(codes, info, russian, all_russian)
    except LoadError as e:
        print(f"countries load failed: {e}", file=sys.stderr)
        return 1
    write_records(records)
    print(f"{len(records)} countries written to {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Loads cities from GeoNames into data/loaders/geonames/out/cities.jsonl (P1-DS-04.1).

Source: https://download.geonames.org/export/dump/ (CC BY 4.0).
  cities5000.zip        places with population > 5000, or seats of admin divisions down to PPLA;
  alternateNamesV2.zip  Russian names (isolanguage = ru).
Downloads are cached in data/loaders/geonames/cache/ (not in git); a file placed there by hand is
used without any download. Delete it or pass --refresh to fetch a new one.

Selection (decision of the team, README "Решения команды"):
- departure countries (RU, BY, KZ, UZ, AM): population >= 15000, or a capital / admin seat (PPLC, PPLA);
- every other country: everything in cities5000, since catalog cities come from there;
- only the countries of data/loaders/passport-index-data/out/countries.jsonl (the 199 countries of the visa matrix,
  P1-DS-02.1): the backend refuses a city whose country is not in `countries`, and the territories left out (Puerto
  Rico, Guam, Svalbard and so on, 424 cities) have no visa data and are not offered as a citizenship.

`airports` and `stations` are written empty: airports are filled in by P1-DS-04.2, stations by P1-DS-06.2.

`id` of a city is fixed in ids.csv (`geonameid,id`, in git): the backend derives its price seeds and the links of
destinations and hotels from it, so it must not change when GeoNames updates populations or the selection changes.
The first run writes the file from the ids it computes; later runs take the id of a geonameid from the file, give
new cities a slug that is not taken yet, and append them. Lines are never removed, even for a city that is not
selected any more, so an id is never given to another city.

Usage: python3 data/loaders/geonames/load.py [--refresh]
"""

import csv
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import requests

LOADER_DIR = Path(__file__).resolve().parent
CACHE_DIR = LOADER_DIR / "cache"
OUT_FILE = LOADER_DIR / "out" / "cities.jsonl"
IDS_FILE = LOADER_DIR / "ids.csv"
COUNTRIES_FILE = LOADER_DIR.parent / "passport-index-data" / "out" / "countries.jsonl"

BASE_URL = "https://download.geonames.org/export/dump/"
CITIES_ZIP = "cities5000.zip"
ALTERNATE_NAMES_ZIP = "alternateNamesV2.zip"

DEPARTURE_COUNTRIES = {"RU", "BY", "KZ", "UZ", "AM"}
DEPARTURE_MIN_POPULATION = 15000
# Capital and seats of first- and second-level divisions: kept below the population threshold.
SEAT_FEATURE_CODES = {"PPLC", "PPLA"}
# Sections of a place, historical, destroyed and abandoned places are not cities.
SKIPPED_FEATURE_CODES = {"PPLX", "PPLH", "PPLW", "PPLQ", "PPLCH"}

# Columns of the GeoNames `geoname` table.
GEONAME_ID, NAME, ASCII_NAME, LAT, LON, FEATURE_CLASS, FEATURE_CODE, COUNTRY, POPULATION, TIMEZONE = (
    0, 1, 2, 4, 5, 6, 7, 8, 14, 17,
)
# Columns of alternateNamesV2.
ALT_GEONAME_ID, ALT_LANG, ALT_NAME, ALT_PREFERRED, ALT_SHORT, ALT_COLLOQUIAL, ALT_HISTORIC = 1, 2, 3, 4, 5, 6, 7


class LoadError(Exception):
    pass


def download(name, refresh):
    """Returns the cached file, fetching it first when it is missing."""
    path = CACHE_DIR / name
    if path.exists() and not refresh:
        return path
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    url = BASE_URL + name
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


def zip_lines(path, member):
    """Yields the lines of one member of a zip archive, without loading it into memory."""
    with zipfile.ZipFile(path) as archive:
        with archive.open(member) as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8", newline="\n"):
                yield line.rstrip("\n")


def is_selected(row):
    if row[FEATURE_CLASS] != "P" or row[FEATURE_CODE] in SKIPPED_FEATURE_CODES:
        return False
    if row[COUNTRY] not in DEPARTURE_COUNTRIES:
        return True
    return int(row[POPULATION] or 0) >= DEPARTURE_MIN_POPULATION or row[FEATURE_CODE] in SEAT_FEATURE_CODES


def read_country_codes():
    if not COUNTRIES_FILE.exists():
        raise LoadError(f"{COUNTRIES_FILE} not found, run data/loaders/passport-index-data/countries.py first")
    with COUNTRIES_FILE.open(encoding="utf-8") as f:
        return {json.loads(line)["code"] for line in f if line.strip()}


def read_cities(lines, country_codes):
    """Rows of the selected cities, and the number of rows left out because their country is not in country_codes."""
    rows, left_out = [], 0
    for line_no, line in enumerate(lines, start=1):
        if not line:
            continue
        row = line.split("\t")
        if len(row) < 19:
            raise LoadError(f"{CITIES_ZIP}:{line_no}: expected 19 columns, got {len(row)}")
        if not is_selected(row):
            continue
        if row[COUNTRY] in country_codes:
            rows.append(row)
        else:
            left_out += 1
    return rows, left_out


CYRILLIC = re.compile("[\u0400-\u04ff]")


def read_russian_names(lines, wanted_ids):
    """Maps geonameId -> best Russian name.

    Only names tagged `ru` that contain Cyrillic (some are written in Latin: "Ural'sk"), not historic. Order: not
    colloquial first (a colloquial name is the only `ru` name of Murmansk, so it is the last resort), then preferred,
    then not short, then the lowest name id. Cities with no such name are fixed in overrides/city_names_ru.csv.
    """
    best = {}
    for line in lines:
        row = line.split("\t")
        if len(row) < 8 or row[ALT_LANG] != "ru" or row[ALT_GEONAME_ID] not in wanted_ids:
            continue
        if row[ALT_HISTORIC] == "1" or not CYRILLIC.search(row[ALT_NAME]):
            continue
        key = (row[ALT_COLLOQUIAL] == "1", row[ALT_PREFERRED] != "1", row[ALT_SHORT] == "1", int(row[0]))
        current = best.get(row[ALT_GEONAME_ID])
        if current is None or key < current[0]:
            best[row[ALT_GEONAME_ID]] = (key, row[ALT_NAME])
    return {geoname_id: name for geoname_id, (_, name) in best.items()}


def slugify(ascii_name):
    slug = ascii_name.lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", slug).strip("-")


def read_ids():
    """Returns {geonameid: id} from ids.csv, or {} when the file does not exist yet."""
    if not IDS_FILE.exists():
        return {}
    registry = {}
    with IDS_FILE.open(encoding="utf-8", newline="") as f:
        for line_no, entry in enumerate(csv.DictReader(f), start=2):
            geoname_id, city_id = entry["geonameid"].strip(), entry["id"].strip()
            if not geoname_id.isdigit() or not city_id:
                raise LoadError(f"{IDS_FILE.name}:{line_no}: expected a geonameid and an id")
            if geoname_id in registry:
                raise LoadError(f"{IDS_FILE.name}:{line_no}: geonameid {geoname_id} twice")
            registry[geoname_id] = city_id
    if len(set(registry.values())) != len(registry):
        raise LoadError(f"{IDS_FILE.name}: an id belongs to two geonameids")
    return registry


def write_ids(registry):
    IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = ["geonameid,id"] + [f"{g},{registry[g]}" for g in sorted(registry, key=int)]
    IDS_FILE.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def assign_ids(rows, registry):
    """Returns (ids of the rows, registry with the new cities added).

    A city that is in the registry keeps its id. A new one gets the slug of its ASCII name; if that is taken (by any
    id of the registry, selected now or not) the country is appended, then the geonameid. New cities are handled
    biggest first, so on the first run (an empty registry) the biggest city of a clash has the plain slug.
    """
    registry = dict(registry)
    taken = set(registry.values())
    ids = {row[GEONAME_ID]: registry[row[GEONAME_ID]] for row in rows if row[GEONAME_ID] in registry}
    new_rows = [row for row in rows if row[GEONAME_ID] not in registry]
    for row in sorted(new_rows, key=lambda r: (-int(r[POPULATION] or 0), int(r[GEONAME_ID]))):
        slug = slugify(row[ASCII_NAME]) or f"city-{row[GEONAME_ID]}"
        for candidate in (slug, f"{slug}-{row[COUNTRY].lower()}", f"{slug}-{row[GEONAME_ID]}"):
            if candidate not in taken:
                break
        taken.add(candidate)
        ids[row[GEONAME_ID]] = registry[row[GEONAME_ID]] = candidate
    return ids, registry


def build_records(rows, russian_names, ids):
    records = []
    no_russian = 0
    for row in rows:
        geoname_id = row[GEONAME_ID]
        if not row[TIMEZONE]:
            raise LoadError(f"geonameid {geoname_id} ({row[NAME]}): no timezone")
        name_en = row[ASCII_NAME] or row[NAME]
        name_ru = russian_names.get(geoname_id)
        if name_ru is None:
            # impulse: a city without a Russian name in GeoNames gets its English name; the cities that must have
            # a Russian name (departure cities, destinations) are fixed in data/overrides/city_names_ru.csv by
            # build.py, remove when GeoNames has a Russian name for all of them.
            name_ru = name_en
            no_russian += 1
        records.append({
            "id": ids[geoname_id],
            "name_ru": name_ru,
            "name_en": name_en,
            "country_code": row[COUNTRY],
            "lat": float(row[LAT]),
            "lon": float(row[LON]),
            "timezone": row[TIMEZONE],
            "population": int(row[POPULATION] or 0),
            "airports": [],
            "stations": [],
        })
    records.sort(key=lambda r: r["id"])
    return records, no_russian


def write_records(records):
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for r in records
    )
    OUT_FILE.write_bytes(content.encode("utf-8"))


def main():
    refresh = "--refresh" in sys.argv[1:]
    try:
        cities_zip = download(CITIES_ZIP, refresh)
        rows, left_out = read_cities(zip_lines(cities_zip, "cities5000.txt"), read_country_codes())
        alternate_zip = download(ALTERNATE_NAMES_ZIP, refresh)
        russian = read_russian_names(zip_lines(alternate_zip, "alternateNamesV2.txt"), {r[GEONAME_ID] for r in rows})
        ids, registry = assign_ids(rows, read_ids())
        records, no_russian = build_records(rows, russian, ids)
    except LoadError as e:
        print(f"geonames load failed: {e}", file=sys.stderr)
        return 1
    write_records(records)
    write_ids(registry)
    print(f"{len(records)} cities written to {OUT_FILE}")
    print(f"  left out, country not in countries.jsonl: {left_out}")
    print(f"  without a Russian name (English name used): {no_russian}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

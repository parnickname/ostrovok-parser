"""Loads the visa matrix into data/loaders/passport-index-data/out/visa.jsonl (P1-DS-02.2).

Every passport of the matrix against every country of it, statuses translated by the table "Статусы визы"
(docs/snapshot-format.md, spec 0002). The matrix has no pairs where the passport is the destination (`-1` in its
CSV files); they are written with status `home`, so a passport always has a pair with its own country.

Source: https://github.com/imorte/passport-index-data (MIT), passport-index.json:
  {"RU": {"TR": {"status": "visa free", "days": 60}, ...}, ...}
The download is cached in data/loaders/passport-index-data/cache/ (not in git); a file placed there by hand is
used without any download. Delete it or pass --refresh to fetch a new one.

Usage: python3 data/loaders/passport-index-data/visa.py [--refresh]
"""

import json
import sys
from pathlib import Path

import requests

LOADER_DIR = Path(__file__).resolve().parent
CACHE_DIR = LOADER_DIR / "cache"
OUT_FILE = LOADER_DIR / "out" / "visa.jsonl"

MATRIX_URL = "https://raw.githubusercontent.com/imorte/passport-index-data/master/passport-index.json"
SOURCE = "passport-index-data"

# passport-index-data status -> API status, table "Статусы визы".
STATUSES = {
    "visa free": "visa_free",
    "visa on arrival": "visa_on_arrival",
    "eta": "eta",
    "e-visa": "e_visa",
    "visa required": "visa_required",
    "no admission": "no_admission",
}


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


def read_matrix(path):
    try:
        matrix = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise LoadError(f"{path.name}: invalid JSON: {e.msg}") from None
    if not isinstance(matrix, dict) or not all(isinstance(row, dict) for row in matrix.values()):
        raise LoadError(f"{path.name}: expected an object of passports, each an object of destinations")
    return matrix


def build_records(matrix):
    countries = set(matrix)
    records = []
    for passport in sorted(matrix):
        row = matrix[passport]
        missing = sorted(countries - set(row) - {passport})
        if missing:
            raise LoadError(f"passport {passport}: no requirement for {missing}")
        unknown = sorted(set(row) - countries)
        if unknown:
            raise LoadError(f"passport {passport}: destinations that are not passports of the matrix: {unknown}")
        records.append({"passport": passport, "destination": passport, "status": "home", "sources": [SOURCE]})
        for destination in sorted(row):
            if destination == passport:
                continue
            requirement = row[destination]
            status = STATUSES.get(requirement.get("status"))
            if status is None:
                raise LoadError(f"{passport} -> {destination}: unknown status {requirement.get('status')!r}")
            record = {"passport": passport, "destination": destination, "status": status, "sources": [SOURCE]}
            days = requirement.get("days")
            if days is not None:
                if not isinstance(days, int) or isinstance(days, bool) or days < 1:
                    raise LoadError(f"{passport} -> {destination}: days must be a positive integer, got {days!r}")
                record["days"] = days
            records.append(record)
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
        matrix = read_matrix(download(MATRIX_URL, refresh))
        records = build_records(matrix)
    except LoadError as e:
        print(f"visa load failed: {e}", file=sys.stderr)
        return 1
    write_records(records)
    print(f"{len(records)} pairs for {len(matrix)} passports written to {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

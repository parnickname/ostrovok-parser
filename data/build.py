"""Builds data/snapshot/ from loader outputs and manual overrides.

Input:  data/loaders/<source>/out/<dataset>.jsonl, written by the loaders.
        data/overrides/, manual corrections applied on top.
        data/sources.yaml, the registry of sources.
Output: data/snapshot/<dataset>.jsonl, data/snapshot/manifest.json and a copy of data/sources.yaml: the backend
        resolves the names in `sources` by the copy inside the snapshot (CFC-3).

The build is deterministic: the same input always gives byte-identical
dataset files and therefore the same sha256 in the manifest. Only
`built_at` changes between runs.

Usage: python3 data/build.py
"""

import csv
import hashlib
import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
LOADERS_DIR = DATA_DIR / "loaders"
OVERRIDES_DIR = DATA_DIR / "overrides"
SNAPSHOT_DIR = DATA_DIR / "snapshot"
SOURCES_FILE = DATA_DIR / "sources.yaml"

SCHEMA_VERSION = 1

# Dataset name -> function(records, overrides_dir) -> records, defined below.
# Each override is added by the subtask that introduces it (visa.csv: P1-DS-02.4).


class BuildError(Exception):
    pass


def read_jsonl(path):
    shown = path.relative_to(DATA_DIR.parent).as_posix()
    records = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise BuildError(f"{shown}:{line_no}: invalid JSON: {e.msg}") from None
            if not isinstance(obj, dict):
                raise BuildError(f"{shown}:{line_no}: expected a JSON object")
            records.append(obj)
    return records


def collect_loader_outputs():
    """Groups loader output files by dataset name; several loaders may feed one dataset.

    `calibration` is not a list of records: its parts are merged by merge_calibration.
    """
    datasets = defaultdict(list)
    for path in sorted(LOADERS_DIR.glob("*/out/*.jsonl")):
        if path.stem != "calibration":
            datasets[path.stem].extend(read_jsonl(path))
    return datasets


def collect_calibration_parts():
    """Returns (loader name, partial calibration object) for every loader that writes out/calibration.jsonl."""
    return [
        (path.parent.parent.name, record)
        for path in sorted(LOADERS_DIR.glob("*/out/calibration.jsonl"))
        for record in read_jsonl(path)
    ]


def canonical_line(record):
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_dataset(name, records):
    lines = sorted(canonical_line(r) for r in records)
    content = "".join(line + "\n" for line in lines).encode("utf-8")
    (SNAPSHOT_DIR / f"{name}.jsonl").write_bytes(content)
    return {
        "name": name,
        "file": f"{name}.jsonl",
        "records": len(lines),
        "sha256": hashlib.sha256(content).hexdigest(),
        "schema_version": SCHEMA_VERSION,
    }


def apply_city_names(records, overrides_dir):
    """Sets `name_ru` of the cities listed in overrides/city_names_ru.csv (`id,name_ru,comment`).

    For the cities that must have a Russian name but have none in GeoNames. An id that is not in `cities` is an
    error: an override that no longer fits must not stay silently.
    """
    path = overrides_dir / "city_names_ru.csv"
    if not path.exists():
        return records
    shown = path.relative_to(DATA_DIR.parent).as_posix()
    names = {}
    with path.open(encoding="utf-8", newline="") as f:
        for line_no, row in enumerate(csv.DictReader(f), start=2):
            city_id, name = (row["id"] or "").strip(), (row["name_ru"] or "").strip()
            if not city_id or not name:
                raise BuildError(f"{shown}:{line_no}: id and name_ru must not be empty")
            if city_id in names:
                raise BuildError(f"{shown}:{line_no}: city {city_id!r} twice")
            names[city_id] = name
    by_id = {r["id"]: r for r in records}
    for city_id, name in names.items():
        if city_id not in by_id:
            raise BuildError(f"{shown}: city {city_id!r} is not in cities")
        by_id[city_id]["name_ru"] = name
    return records


# impulse: one override per dataset is enough now; make it a list if a dataset needs several.
OVERRIDES = {"cities": apply_city_names}


def merge_calibration(parts):
    """Merges the parts of the single `calibration` object (CFC-3) written by several loaders (P1-DS-07).

    Each loader writes its own partial object to its out/calibration.jsonl. The top-level keys of the parts must
    not overlap; `sources` of all parts are united.
    """
    merged = {}
    owner = {}
    for source, part in parts:
        for key, value in part.items():
            if key == "sources":
                continue
            if key in owner:
                raise BuildError(f"calibration: key {key!r} is given by both {owner[key]} and {source}")
            owner[key] = source
            merged[key] = value
    sources = sorted({name for _, part in parts for name in part.get("sources", [])})
    if sources:
        merged["sources"] = sources
    return [merged] if merged else []


def fill_city_airports(datasets):
    """Sets cities[].airports to the IATA codes of the airports whose city_id is that city.

    `airports.city_id` is the single source of truth (P1-DS-04.2); loaders write `airports: []` for cities.
    """
    by_city = defaultdict(list)
    for airport in datasets.get("airports", []):
        by_city[airport["city_id"]].append(airport["iata"])
    for city in datasets.get("cities", []):
        city["airports"] = sorted(by_city.get(city["id"], []))


def fill_city_stations(datasets):
    """Sets cities[].stations to the codes of the stations whose city_id is that city.

    `stations.city_id` is the single source of truth (P1-DS-06.2); loaders write `stations: []` for cities.
    """
    by_city = defaultdict(list)
    for station in datasets.get("stations", []):
        by_city[station["city_id"]].append(station["code"])
    for city in datasets.get("cities", []):
        city["stations"] = sorted(by_city.get(city["id"], []))


def build():
    datasets = collect_loader_outputs()
    for name, apply_override in OVERRIDES.items():
        datasets[name] = apply_override(datasets.get(name, []), OVERRIDES_DIR)
    fill_city_airports(datasets)
    fill_city_stations(datasets)
    calibration = merge_calibration(collect_calibration_parts())
    if calibration:
        datasets["calibration"] = calibration

    SNAPSHOT_DIR.mkdir(exist_ok=True)
    # Drop datasets left over from earlier builds so the snapshot matches the manifest.
    for stale in SNAPSHOT_DIR.glob("*.jsonl"):
        if stale.stem not in datasets:
            stale.unlink()

    manifest = {
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "datasets": [write_dataset(name, datasets[name]) for name in sorted(datasets)],
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    (SNAPSHOT_DIR / "manifest.json").write_bytes(manifest_text.encode("utf-8"))
    # Not a dataset: it is not in the manifest, and the cleanup above only touches *.jsonl.
    shutil.copyfile(SOURCES_FILE, SNAPSHOT_DIR / "sources.yaml")
    return manifest


def main():
    try:
        manifest = build()
    except BuildError as e:
        print(f"build failed: {e}", file=sys.stderr)
        return 1
    print(f"snapshot built: {len(manifest['datasets'])} dataset(s)")
    for d in manifest["datasets"]:
        print(f"  {d['name']}: {d['records']} records, sha256 {d['sha256'][:12]}")
    print("  sources.yaml copied")
    return 0


if __name__ == "__main__":
    sys.exit(main())

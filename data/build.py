"""Builds data/snapshot/ from loader outputs and manual overrides.

Input:  data/loaders/<source>/out/<dataset>.jsonl, written by the loaders.
        data/overrides/, manual corrections applied on top.
        data/sources.yaml, the registry of sources.
Output: data/snapshot/<dataset>.jsonl, data/snapshot/manifest.json and a copy of data/sources.yaml: the backend
        resolves the names in `sources` by the copy inside the snapshot (CFC-3).

The build is deterministic: the same input always gives byte-identical
dataset files and therefore the same sha256 in the manifest. `built_at`
changes only when some dataset has changed.

Usage: python3 data/build.py
"""

import csv
import hashlib
import json
import math
import re
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
# P2-DS-01.0: `calibration` with both blocks of CFC-10 has schema_version 2, without them 1 (the backend reads both).
CALIBRATION_V2_BLOCKS = ("early_checkin", "date_change")

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


def schema_version(name, records):
    if name == "calibration" and records and all(block in records[0] for block in CALIBRATION_V2_BLOCKS):
        return 2
    return SCHEMA_VERSION


def write_dataset(name, records):
    lines = sorted(canonical_line(r) for r in records)
    content = "".join(line + "\n" for line in lines).encode("utf-8")
    (SNAPSHOT_DIR / f"{name}.jsonl").write_bytes(content)
    return {
        "name": name,
        "file": f"{name}.jsonl",
        "records": len(lines),
        "sha256": hashlib.sha256(content).hexdigest(),
        "schema_version": schema_version(name, records),
    }


WIKIDATA_NAMES_FILE = LOADERS_DIR / "wikidata" / "city_names_ru.csv"
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def apply_wikidata_city_names(records):
    """Sets `name_ru` from Wikidata (P1-DS-04.3) for the cities whose GeoNames name has no Cyrillic.

    The file is `id,name_ru`, written by data/loaders/wikidata/load.py. A name never replaces a Cyrillic one.
    """
    if not WIKIDATA_NAMES_FILE.exists():
        return records
    with WIKIDATA_NAMES_FILE.open(encoding="utf-8", newline="") as f:
        names = {row["id"]: row["name_ru"] for row in csv.DictReader(f)}
    for record in records:
        name = names.get(record["id"])
        if name and not CYRILLIC.search(record["name_ru"]):
            record["name_ru"] = name
    return records


def apply_city_names(records, overrides_dir):
    """Sets `name_ru` of the cities: first from Wikidata, then from overrides/city_names_ru.csv (`id,name_ru,comment`).

    The manual file is for the cities that must have a Russian name but have none in GeoNames and Wikidata, and it
    wins over both. An id that is not in `cities` is an error: an override that no longer fits must not stay
    silently.
    """
    records = apply_wikidata_city_names(records)
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


VISA_STATUSES = {"home", "visa_free", "visa_on_arrival", "eta", "e_visa", "visa_required", "no_admission"}
VISA_CHECKED_SOURCE = "mid-ru"


def apply_visa(records, overrides_dir):
    """Applies overrides/visa.csv (`passport,destination,status,days,comment`, P1-DS-02.4).

    Every row is a pair checked against the reference of the Consular Department of the Russian Foreign Ministry:
    `status` and `days` (empty — no `days`) replace the values of the pair, and `mid-ru` is added to its `sources`.
    A pair that is not in `visa`, a duplicate, an unknown status or a bad `days` is an error.
    """
    path = overrides_dir / "visa.csv"
    if not path.exists():
        return records
    shown = path.relative_to(DATA_DIR.parent).as_posix()
    by_pair = {(r["passport"], r["destination"]): r for r in records}
    seen = set()
    with path.open(encoding="utf-8", newline="") as f:
        for line_no, row in enumerate(csv.DictReader(f), start=2):
            pair = ((row["passport"] or "").strip(), (row["destination"] or "").strip())
            status, days = (row["status"] or "").strip(), (row["days"] or "").strip()
            if pair in seen:
                raise BuildError(f"{shown}:{line_no}: pair {pair} twice")
            seen.add(pair)
            if pair not in by_pair:
                raise BuildError(f"{shown}:{line_no}: pair {pair} is not in visa")
            if status not in VISA_STATUSES:
                raise BuildError(f"{shown}:{line_no}: unknown status {status!r}")
            if days and (not days.isdigit() or int(days) < 1):
                raise BuildError(f"{shown}:{line_no}: days must be a positive integer, got {days!r}")
            record = by_pair[pair]
            record["status"] = status
            if days:
                record["days"] = int(days)
            else:
                record.pop("days", None)
            if VISA_CHECKED_SOURCE not in record["sources"]:
                record["sources"] = record["sources"] + [VISA_CHECKED_SOURCE]
    return records


# impulse: one override per dataset is enough now; make it a list if a dataset needs several.
OVERRIDES = {"cities": apply_city_names, "visa": apply_visa}


FALLBACK_CALIBRATION_LOADER = "synthetic"


def merge_calibration(parts):
    """Merges the parts of the single `calibration` object (CFC-3) written by several loaders (P1-DS-07).

    Each loader writes its own partial object to its out/calibration.jsonl. The top-level keys of the parts must
    not overlap; `sources` of all parts are united. The exception is the part of the loader `synthetic`: it holds
    estimates for what no source gives yet (`airfare` until P1-DS-07.3 has its token, `rail` until P1-DS-07.7 has
    run), so a key of another loader replaces its estimate. A part of `synthetic` all of whose keys are replaced
    does not add its `sources`.
    """
    merged = {}
    owner = {}
    fallback = {}
    for source, part in parts:
        for key, value in part.items():
            if key == "sources":
                continue
            if source == FALLBACK_CALIBRATION_LOADER:
                fallback[key] = value
                continue
            if key in owner:
                raise BuildError(f"calibration: key {key!r} is given by both {owner[key]} and {source}")
            owner[key] = source
            merged[key] = value
    estimates_used = False
    for key, value in fallback.items():
        if key not in merged:
            merged[key] = value
            estimates_used = True
    sources = set()
    for source, part in parts:
        if source == FALLBACK_CALIBRATION_LOADER and not estimates_used:
            continue
        sources.update(part.get("sources", []))
    if sources:
        merged["sources"] = sorted(sources)
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


# P1-DS-05.3: OSM draws one hotel as several objects (a point and a building) and the biggest cities have thousands.
HOTEL_DUPLICATE_RADIUS_M = 200
HOTELS_PER_DESTINATION_MAX = 300
EARTH_RADIUS_M = 6371000.0


def hotel_distance_m(a, b):
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b["lon"] - a["lon"]) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def hotel_rank(record):
    """Smaller is better: the record with more of the optional fields filled in, then the smaller `id`."""
    filled = sum(record.get(field) is not None for field in ("stars", "check_in_time", "check_out_time", "pets"))
    return (-filled, record["id"])


def prepare_hotels(records):
    """Removes duplicate hotels and caps the hotels of a destination (P1-DS-05.3).

    A duplicate is a hotel of the same destination with the same name (letters and digits, case ignored) within
    HOTEL_DUPLICATE_RADIUS_M of another one; of such a group the best-filled record stays (hotel_rank). Then at most
    HOTELS_PER_DESTINATION_MAX hotels stay per destination, the best-filled first. `id` of a kept hotel never changes.
    """
    groups = defaultdict(list)
    for record in sorted(records, key=hotel_rank):
        groups[(record["destination_id"], re.sub(r"[\W_]+", "", record["name"].casefold()))].append(record)
    kept = []
    for group in groups.values():
        # The best record of a group first: it removes every record within the radius of one already kept.
        stay = []
        for record in group:
            if all(hotel_distance_m(record, other) > HOTEL_DUPLICATE_RADIUS_M for other in stay):
                stay.append(record)
        kept.extend(stay)
    by_destination = defaultdict(list)
    for record in kept:
        by_destination[record["destination_id"]].append(record)
    result = []
    for destination in sorted(by_destination):
        result.extend(sorted(by_destination[destination], key=hotel_rank)[:HOTELS_PER_DESTINATION_MAX])
    return result


def read_manifest():
    path = SNAPSHOT_DIR / "manifest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def build():
    datasets = collect_loader_outputs()
    for name, apply_override in OVERRIDES.items():
        datasets[name] = apply_override(datasets.get(name, []), OVERRIDES_DIR)
    if "hotels" in datasets:
        datasets["hotels"] = prepare_hotels(datasets["hotels"])
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
    previous = read_manifest()
    if previous and previous["datasets"] == manifest["datasets"]:
        # Nothing changed: keep the date, so that a scheduled rebuild (data/refresh.py) leaves no difference in git.
        manifest["built_at"] = previous["built_at"]
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

"""Reloads the sources, rebuilds the snapshot and checks it (P1-DS-01.5).

The snapshot changes with a release, but the sources do not stand still: flights, schedules and rates move every week,
the maps and the statistics every month. This script runs the loaders of a group in the order of their dependencies,
then data/build.py and data/validate.py, and stamps `fetched_at` in data/sources.yaml for the sources that were
loaded, when the snapshot has changed. A scheduled GitHub Actions workflow (.github/workflows/refresh.yml) runs it and
opens a pull request with the difference.

Groups:
- weekly: what changes every week: the visa matrix, stations and train schedules, flights and fares, exchange rates;
- monthly: everything else that is loaded from the net (maps, cities, airports, hotels, climate, statistics) and the
  weekly steps with it;
- all: the same as monthly.

A step that needs a secret (Yandex Rasp key, Travelpayouts token) is skipped with a message when the secret is not
set: the data it wrote last time stays. Any other failed step stops the script with exit code 1 and the snapshot is
not touched, except for an optional step (Rosstat), whose failure is a warning. Handmade inputs (destinations.csv, descriptions.csv, tourist-tax/rates.csv, overrides/) are not loaded
and need no step.

Usage: python3 data/refresh.py [--group weekly|monthly|all] [--dry-run]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
ROOT = DATA_DIR.parent
LOADERS = DATA_DIR / "loaders"
SNAPSHOT_MANIFEST = DATA_DIR / "snapshot" / "manifest.json"
SOURCES_FILE = DATA_DIR / "sources.yaml"
ENV_FILE = ROOT / ".env"

WEEKLY, MONTHLY = "weekly", "monthly"


class Step:
    def __init__(self, script, group, providers, refresh=True, secret=None, optional=False, needs=None):
        self.script = script
        self.group = group
        self.providers = providers  # names in sources.yaml whose `fetched_at` this step moves
        self.refresh = refresh  # the loader takes --refresh: it must not reuse its cache
        self.secret = secret  # environment variable the loader cannot work without
        self.needs = needs  # script of a step that must have run in this refresh, else this one is skipped
        self.optional = optional  # a failure is a warning: the source may be closed to the runner, the old data stays

    @property
    def name(self):
        return self.script.removeprefix("loaders/")


# The order is the order of the dependencies: cities -> airports -> stations -> catalog -> everything that reads the
# catalog. Catalog, tourist-tax, synthetic and hotel-policy read no net, they follow what they read.
STEPS = [
    Step("loaders/passport-index-data/countries.py", MONTHLY, ["passport-index-data"]),
    Step("loaders/passport-index-data/visa.py", WEEKLY, ["passport-index-data"]),
    Step("loaders/geonames/load.py", MONTHLY, ["geonames"]),
    Step("loaders/wikidata/load.py", MONTHLY, ["wikidata"], refresh=False),
    Step("loaders/ourairports/load.py", MONTHLY, ["ourairports"]),
    Step("loaders/wikidata/airports.py", MONTHLY, ["wikidata"], refresh=False),
    # The stations list (one request) is weekly, because rail.py takes the settlement codes from it and a clean runner
    # has no cache; the catalog follows it (offline) so that destinations[].stations never lag behind stations.
    # Yandex may refuse the addresses of the GitHub runners (the first run got HTTP errors): then the stations and
    # the schedules of the last run stay.
    Step("loaders/yandex-rasp/load.py", WEEKLY, ["yandex-rasp"], secret="YANDEX_RASP_API_KEY", optional=True),
    Step("loaders/catalog/load.py", WEEKLY, [], refresh=False),
    Step("loaders/open-meteo/load.py", MONTHLY, ["open-meteo"]),
    Step("loaders/open-meteo/load_sea.py", MONTHLY, ["open-meteo"]),
    Step("loaders/osm/load.py", MONTHLY, ["osm"]),
    Step("loaders/yandex-rasp/rail.py", WEEKLY, ["yandex-rasp"], secret="YANDEX_RASP_API_KEY", optional=True,
         needs="loaders/yandex-rasp/load.py"),
    Step("loaders/travelpayouts/load.py", WEEKLY, ["aviasales-data-api"], refresh=False, secret="TRAVELPAYOUTS_TOKEN"),
    Step("loaders/routes/load.py", WEEKLY, [], refresh=False),
    Step("loaders/cbr/load.py", WEEKLY, ["cbr"]),
    Step("loaders/world-bank/load.py", MONTHLY, ["world-bank"]),
    Step("loaders/ine-es/load.py", MONTHLY, ["ine-es"], refresh=False),
    # rosstat.gov.ru has a certificate of a Russian CA and may not answer outside Russia: run the loader by hand then
    # (download the xlsx into loaders/rosstat/cache/), the data of the last run stays.
    Step("loaders/rosstat/load.py", MONTHLY, ["rosstat"], optional=True),
    Step("loaders/hotel-booking-demand/load.py", MONTHLY, ["hotel-booking-demand", "nager-date"]),
    Step("loaders/tourist-tax/load.py", MONTHLY, [], refresh=False),
    Step("loaders/synthetic/load.py", WEEKLY, [], refresh=False),
    Step("loaders/hotel-policy/load.py", WEEKLY, [], refresh=False),
]


def selected(group):
    return [s for s in STEPS if group in ("monthly", "all") or s.group == WEEKLY]


def has_secret(name):
    if os.environ.get(name, "").strip():
        return True
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name and value.strip().strip("\"'"):
                return True
    return False


def run(command):
    print("$ " + " ".join(command), flush=True)
    return subprocess.run(command, cwd=ROOT).returncode


def dataset_hashes():
    if not SNAPSHOT_MANIFEST.exists():
        return {}
    manifest = json.loads(SNAPSHOT_MANIFEST.read_text(encoding="utf-8"))
    return {d["name"]: d["sha256"] for d in manifest["datasets"]}


def stamp_fetched_at(providers, moment):
    """Sets `fetched_at` of the given providers in sources.yaml; the registry is edited line by line, as validate.py reads it."""
    text = SOURCES_FILE.read_text(encoding="utf-8")
    for provider in sorted(providers):
        block = re.compile(
            rf"(^- provider: {re.escape(provider)}\n(?:(?!- provider:).*\n)*?  fetched_at: )[^\n]*", re.MULTILINE
        )
        text, count = block.subn(rf"\g<1>{moment}", text, count=1)
        if count != 1:
            raise RuntimeError(f"{SOURCES_FILE.name}: provider {provider!r} has no fetched_at line")
    SOURCES_FILE.write_text(text, encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--group", choices=["weekly", "monthly", "all"], default="weekly")
    parser.add_argument("--dry-run", action="store_true", help="print the steps and stop")
    args = parser.parse_args()

    steps = selected(args.group)
    if args.dry_run:
        for step in steps:
            note = f" (needs {step.secret})" if step.secret else ""
            print(f"{step.name}{note}")
        return 0

    before = dataset_hashes()
    loaded = set()
    ran = set()
    for step in steps:
        if step.needs and step.needs not in ran:
            print(f"skipped {step.name}: {step.needs} has not run, the data of the last run stays", flush=True)
            continue
        if step.secret and not has_secret(step.secret):
            print(f"skipped {step.name}: {step.secret} is not set, the data of the last run stays", flush=True)
            continue
        command = [sys.executable, str(DATA_DIR / step.script)] + (["--refresh"] if step.refresh else [])
        if run(command) != 0:
            if step.optional:
                print(f"warning: {step.name} failed, the data of the last run stays", file=sys.stderr, flush=True)
                continue
            print(f"refresh failed at {step.name}", file=sys.stderr)
            return 1
        loaded.update(step.providers)
        ran.add(step.script)

    if run([sys.executable, str(DATA_DIR / "build.py")]) != 0 or run([sys.executable, str(DATA_DIR / "validate.py")]) != 0:
        print("refresh failed: the new snapshot does not build or validate", file=sys.stderr)
        return 1

    after = dataset_hashes()
    changed = sorted(name for name in after if after[name] != before.get(name))
    if not changed:
        print("the snapshot has not changed")
        return 0
    stamp_fetched_at(loaded, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    if run([sys.executable, str(DATA_DIR / "build.py")]) != 0:  # the snapshot keeps a copy of sources.yaml
        return 1
    print("the snapshot has changed: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())

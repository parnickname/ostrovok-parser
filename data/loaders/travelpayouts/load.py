"""Loads real flights from the Aviasales Data API (Travelpayouts) for P1-DS-07.3 and P1-DS-06.4.

Source: https://api.travelpayouts.com/aviasales/v3/prices_for_dates, the cheapest tickets found by the users of
Aviasales in the last 48 hours (https://support.travelpayouts.com/hc/ru/articles/203956163). The registration is free;
the token is read from the environment variable TRAVELPAYOUTS_TOKEN or from the .env file in the repository root and is
sent in the header, never in the URL.

Needs data/loaders/ourairports/out/airports.jsonl.

For every airport of the departure cities (RU, BY, KZ, UZ, AM, as in the routes loader) and every one of the next
MONTHS_AHEAD months, one request: direct flights only, one way, in rubles, one ticket per direction. Two results:

- data/loaders/travelpayouts/direct_flights.csv (`from_iata,to_iata`, in git): every pair of airports with a direct
  flight. data/loaders/routes/load.py takes `direct` of `routes` from it (P1-DS-06.4). The file replaces the old guess
  from the size of the airports; a pair that is not in the file has no direct flight in the Aviasales data, so the
  smallest airports, which nobody searched for lately, may be missing.
- out/calibration.jsonl, the `airfare` part of `calibration` (P1-DS-07.3): `rub_per_km_short` for flights shorter than
  LONG_FROM_KM and `rub_per_km_long` for the longer ones are the medians of (price - base_rub) / distance over the
  tickets, the distance is the great-circle one between the airports. `base_rub`, `long_from_km` and
  `transfer_minutes` are not in the data and stay the estimates of the example of CFC-3 (provider `synthetic`).

The loader fails when fewer than MIN_SAMPLES tickets are found for the short or for the long flights: a few tickets
are not a price level.

Usage: TRAVELPAYOUTS_TOKEN=... python3 data/loaders/travelpayouts/load.py
"""

import csv
import json
import math
import os
import statistics
import sys
import time
from datetime import date
from pathlib import Path

import requests

LOADER_DIR = Path(__file__).resolve().parent
OUT_FILE = LOADER_DIR / "out" / "calibration.jsonl"
DIRECT_FILE = LOADER_DIR / "direct_flights.csv"
AIRPORTS_FILE = LOADER_DIR.parent / "ourairports" / "out" / "airports.jsonl"
ENV_FILE = LOADER_DIR.parents[2] / ".env"

API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
TOKEN_VAR = "TRAVELPAYOUTS_TOKEN"
DEPARTURE_COUNTRIES = {"RU", "BY", "KZ", "UZ", "AM"}
MONTHS_AHEAD = 3
PAUSE_SECONDS = 1
ATTEMPTS = 4
RETRY_WAIT_SECONDS = 20
TIMEOUT_SECONDS = 60

# impulse: not in the data of the API; the values of the example of CFC-3, replace when a source gives them.
BASE_RUB = 2500
LONG_FROM_KM = 2000
TRANSFER_MINUTES = 120
MIN_SAMPLES = 30
EARTH_RADIUS_KM = 6371.0


class LoadError(Exception):
    pass


class UnknownOrigin(LoadError):
    """The API does not know the airport (HTTP 400): a small airport without a code in Aviasales."""


def read_token():
    token = os.environ.get(TOKEN_VAR, "").strip()
    if not token and ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == TOKEN_VAR:
                token = value.strip().strip("\"'")
    if not token:
        raise LoadError(f"{TOKEN_VAR} is not set: put it into the environment or into {ENV_FILE.name}")
    return token


def read_airports():
    if not AIRPORTS_FILE.exists():
        raise LoadError(f"{AIRPORTS_FILE} not found, run the loader that writes it first")
    with AIRPORTS_FILE.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def months(today):
    """The next MONTHS_AHEAD months after the current one, as YYYY-MM."""
    year, month = today.year, today.month
    result = []
    for _ in range(MONTHS_AHEAD):
        month += 1
        if month > 12:
            year, month = year + 1, 1
        result.append(f"{year:04d}-{month:02d}")
    return result


def fetch(token, origin, month):
    params = {
        "origin": origin, "departure_at": month, "one_way": "true", "direct": "true", "unique": "true",
        "sorting": "price", "limit": 1000, "currency": "rub", "market": "ru",
    }
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = requests.get(API_URL, params=params, headers={"X-Access-Token": token}, timeout=TIMEOUT_SECONDS)
            if response.status_code in (401, 403):
                raise LoadError(f"the API refused the token (HTTP {response.status_code})")
            if response.status_code == 400:
                raise UnknownOrigin(f"{origin}: HTTP 400 {response.text[:200]!r}")
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.RequestException(f"HTTP {response.status_code}")
            response.raise_for_status()
            body = response.json()
            if not body.get("success", False):
                raise LoadError(f"{origin} {month}: {body.get('error') or body}")
            return body.get("data", [])
        except UnknownOrigin:
            raise
        except (requests.RequestException, ValueError) as e:
            if attempt == ATTEMPTS:
                raise LoadError(f"{origin} {month}: {type(e).__name__}: {e}") from None
            time.sleep(RETRY_WAIT_SECONDS * attempt)


def distance_km(a, b):
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b["lon"] - a["lon"]) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def collect(tickets_of, departures):
    """Returns (direct pairs, {pair: cheapest price}) from the tickets of every departure airport."""
    pairs, prices = set(), {}
    for origin in departures:
        for ticket in tickets_of(origin):
            if ticket.get("transfers", 0) != 0:
                continue
            a, b = ticket.get("origin_airport") or origin, ticket.get("destination_airport")
            if not b or b == a:
                continue
            pairs.add((a, b))
            price = ticket.get("price")
            if isinstance(price, (int, float)) and price > 0:
                prices[(a, b)] = min(price, prices.get((a, b), price))
    return pairs, prices


def airfare(prices, by_iata):
    short, long_ = [], []
    for (a, b), price in prices.items():
        if a not in by_iata or b not in by_iata or price <= BASE_RUB:
            continue
        distance = distance_km(by_iata[a], by_iata[b])
        if distance < 100:
            continue
        (long_ if distance >= LONG_FROM_KM else short).append((price - BASE_RUB) / distance)
    if len(short) < MIN_SAMPLES or len(long_) < MIN_SAMPLES:
        raise LoadError(
            f"{len(short)} short and {len(long_)} long tickets, at least {MIN_SAMPLES} of each are needed"
        )
    return {
        "base_rub": BASE_RUB,
        "long_from_km": LONG_FROM_KM,
        "rub_per_km_long": round(statistics.median(long_), 2),
        "rub_per_km_short": round(statistics.median(short), 2),
        "transfer_minutes": TRANSFER_MINUTES,
    }


def write_outputs(pairs, fare):
    with DIRECT_FILE.open("w", encoding="utf-8", newline="\n") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["from_iata", "to_iata"])
        writer.writerows(sorted(pairs))
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {"airfare": fare, "sources": ["aviasales-data-api", "synthetic"]}
    OUT_FILE.write_bytes((json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())


def main():
    try:
        token = read_token()
        airports = read_airports()
        by_iata = {a["iata"]: a for a in airports}
        departures = sorted(a["iata"] for a in airports if a["country_code"] in DEPARTURE_COUNTRIES)
        wanted_months = months(date.today())

        done, skipped = [], []

        def tickets_of(origin):
            tickets = []
            try:
                for month in wanted_months:
                    tickets.extend(fetch(token, origin, month))
                    time.sleep(PAUSE_SECONDS)
            except UnknownOrigin as e:
                skipped.append(origin)
                print(f"  skipped {e}", flush=True)
                return []
            done.append(origin)
            print(f"  {len(done)}/{len(departures)} {origin}: {len(tickets)} direct tickets", flush=True)
            return tickets

        print(f"{len(departures)} departure airports, months {', '.join(wanted_months)}; about "
              f"{len(departures) * len(wanted_months) * PAUSE_SECONDS // 60} minutes", flush=True)

        pairs, prices = collect(tickets_of, departures)
        if len(skipped) > len(departures) // 2:
            raise LoadError(f"the API refused {len(skipped)} of {len(departures)} airports, the request is wrong")
        if skipped:
            print(f"{len(skipped)} airports unknown to the API: {', '.join(skipped)}")
        fare = airfare(prices, by_iata)
    except LoadError as e:
        print(f"travelpayouts load failed: {e}", file=sys.stderr)
        return 1
    write_outputs(pairs, fare)
    print(f"{len(pairs)} direct pairs written to {DIRECT_FILE}")
    print(f"airfare written to {OUT_FILE}: {fare}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

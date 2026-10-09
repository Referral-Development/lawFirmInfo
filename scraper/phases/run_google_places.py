#!/usr/bin/env python3
"""Runner for Google Places enrichment against the existing firm dataset.

Runs scrape_google_places() across an expanded Kansas city list (county seats
plus every city that has >=5 firms in our data), then merges results into
app/firms_data.js.

Cost monitoring: Google Places Text Search is $32/1k, Place Details is $17/1k.
Expected cost for 142 cities at ~15 places/city ≈ $50-70.

Usage:
    python -m scraper.phases.run_google_places --test       # 3 cities, ~$1
    python -m scraper.phases.run_google_places              # Full run
    python -m scraper.phases.run_google_places --cities wichita,topeka
"""
import argparse
import json
import os
import shutil
from collections import Counter
from datetime import datetime

from dotenv import load_dotenv

load_dotenv("scraper/.env")

from scraper.phases.google_places import (  # noqa: E402
    scrape_google_places, merge_google_into_firms, COUNTY_SEATS_105,
)

INPUT_PATH = "app/firms_data.js"
BACKUP_PATH = f"/tmp/firms_data_google_places_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.js"
GOOGLE_RESULTS_PATH = "/tmp/google_places_raw.json"


def _load_firms():
    with open(INPUT_PATH) as f:
        content = f.read()
    return json.loads(content[len("const FIRMS_DATA = "):-1])


def _save_firms(data):
    with open(INPUT_PATH, "w") as f:
        f.write("const FIRMS_DATA = ")
        json.dump(data, f, indent=2)
        f.write(";")


def _build_city_list(firms, min_firms=5):
    """Start with county seats, add any city with >=min_firms in our data."""
    cities = Counter((f.get("address") or {}).get("city", "") for f in firms)
    popular = {c for c, n in cities.items() if c and n >= min_firms}
    expanded = sorted(popular | set(COUNTY_SEATS_105))
    return expanded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true",
                        help="Run against 3 cities only")
    parser.add_argument("--cities", type=str, default=None,
                        help="Comma-separated city list (overrides default)")
    parser.add_argument("--min-firms", type=int, default=5,
                        help="Include cities with at least N firms in our data")
    args = parser.parse_args()

    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise SystemExit("GOOGLE_MAPS_API_KEY not set in scraper/.env")

    from scraper.utils import places_new
    client = places_new.Client(key=api_key)

    data = _load_firms()
    firms = data["firms"]
    before_total = len(firms)
    before_web = sum(1 for f in firms if f.get("website"))

    if args.cities:
        cities = [c.strip() for c in args.cities.split(",")]
    elif args.test:
        cities = ["Wichita", "Topeka", "Leawood"]
    else:
        cities = _build_city_list(firms, min_firms=args.min_firms)

    print(f"[places] Will query {len(cities)} cities")
    print(f"[places] Starting: {before_total} firms, {before_web} with websites")

    # Scrape
    google_firms = scrape_google_places(client, cities=cities)
    print(f"[places] Scraped {len(google_firms)} places from Google")

    # Cache raw response
    try:
        with open(GOOGLE_RESULTS_PATH, "w") as f:
            json.dump(google_firms, f, indent=2)
        print(f"[places] Raw results saved to {GOOGLE_RESULTS_PATH}")
    except OSError:
        pass

    # Back up and merge
    shutil.copy(INPUT_PATH, BACKUP_PATH)
    print(f"[places] Backed up current data to {BACKUP_PATH}")

    new_firms = merge_google_into_firms(firms, google_firms)

    after_total = len(new_firms)
    after_web = sum(1 for f in new_firms if f.get("website"))

    data["firms"] = new_firms
    data["meta"]["lastGooglePlaces"] = datetime.now().isoformat()
    _save_firms(data)

    print()
    print("=" * 50)
    print("  Google Places Complete")
    print("=" * 50)
    print(f"  Firms:    {before_total} → {after_total} (+{after_total - before_total} new)")
    print(f"  Websites: {before_web} → {after_web} (+{after_web - before_web})")
    print("=" * 50)


if __name__ == "__main__":
    main()

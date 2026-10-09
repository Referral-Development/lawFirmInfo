#!/usr/bin/env python3
"""
Kansas Law Firm Directory Scraper

Comprehensive scraper that finds every active law firm in Kansas using
multiple data sources:
  Phase 1: KS Supreme Court Attorney Registration (primary discovery)
  Phase 2: Google Places API (optional enrichment — coordinates, phone, website)
  Phase 3: Kansas Bar Association directory (practice area enrichment)
  Phase 3b: FindLaw directory (practice areas, phone, website)
  Phase 3c: Avvo directory (attorneys, practice areas, phone)
  Phase 4: Firm website scraping (email, summary, practice areas)
  Phase 5: Nominatim geocoding (coordinates for firms missing them)
  Phase 6: Finalization (multi-pass dedup, scoring, output)

Usage:
  python -m scraper.scraper                      # Full run (free, ~3-10 hours)
  python -m scraper.scraper --use-google          # Include Google Places (~$30-60)
  python -m scraper.scraper --test                # Quick test: limited records per phase
  python -m scraper.scraper --resume              # Resume from last checkpoint
"""
import argparse
import os
import time
from collections import Counter
from dotenv import load_dotenv

from scraper.phases.ks_courts import scrape_ks_courts
from scraper.phases.ksbar import scrape_ksbar, merge_ksbar_into_firms
from scraper.phases.website_scraper import scrape_firm_website
from scraper.utils.normalize import normalize_practice_area, deduplicate_firms
from scraper.utils.referral import calculate_referral_score
from scraper.utils.checkpoint import save_checkpoint, load_checkpoint, clear_checkpoint
from scraper.utils.output import write_firms_data_js

TEST_CITIES = ["Wichita", "Topeka", "Kansas City", "Overland Park", "Lawrence"]
CHECKPOINT_PATH = "data/checkpoint.json"
OUTPUT_PATH = "app/firms_data.js"


def main():
    parser = argparse.ArgumentParser(description="Kansas Law Firm Directory Scraper")
    parser.add_argument("--use-google", action="store_true",
                        help="Enable Google Places API phase (costs ~$30-60)")
    parser.add_argument("--skip-ksbar", action="store_true",
                        help="Skip Kansas Bar Association phase")
    parser.add_argument("--skip-findlaw", action="store_true",
                        help="Skip FindLaw directory phase")
    parser.add_argument("--skip-avvo", action="store_true",
                        help="Skip Avvo directory phase")
    parser.add_argument("--skip-websites", action="store_true",
                        help="Skip firm website scraping phase")
    parser.add_argument("--skip-geocoding", action="store_true",
                        help="Skip Nominatim geocoding phase")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from last checkpoint")
    parser.add_argument("--test", action="store_true",
                        help="Quick test: limit to 100 records per phase")
    args = parser.parse_args()

    load_dotenv("scraper/.env")

    # Resume from checkpoint if available
    if args.resume:
        checkpoint = load_checkpoint(CHECKPOINT_PATH)
    else:
        checkpoint = None

    if checkpoint:
        print(f"[checkpoint] Resuming from phase {checkpoint['phase']} "
              f"with {len(checkpoint['firms'])} firms")
        firms = checkpoint["firms"]
        start_phase = checkpoint["phase"]
        progress = checkpoint.get("progress", {})
    else:
        firms = []
        start_phase = 1
        progress = {}

    # ── Phase 1: KS Supreme Court Attorney Registration ──
    if start_phase <= 1:
        start_from = progress.get("ks_courts_last_idx", 0)
        firms = scrape_ks_courts(
            start_from=start_from,
            delay=1.0,
            test_mode=args.test,
        )
        print(f"[phase 1] Found {len(firms)} firms from KS Courts")
        save_checkpoint(firms, phase=2, path=CHECKPOINT_PATH)

    # ── Phase 2: Google Places API (optional) ──
    if start_phase <= 2 and args.use_google:
        api_key = os.getenv("GOOGLE_MAPS_API_KEY")
        if not api_key:
            print("[phase 2] WARNING: --use-google set but GOOGLE_MAPS_API_KEY not found. Skipping.")
        else:
            from scraper.utils import places_new
            from scraper.phases.google_places import scrape_google_places, merge_google_into_firms
            try:
                from scraper.phases.google_places import COUNTY_SEATS_105
                cities = TEST_CITIES if args.test else COUNTY_SEATS_105
            except ImportError:
                from scraper.phases.google_places import KANSAS_CITIES
                cities = TEST_CITIES if args.test else KANSAS_CITIES

            print(f"[phase 2] Searching Google Places for {len(cities)} cities...")
            client = places_new.Client(key=api_key)
            google_firms = scrape_google_places(client, cities=cities)
            firms = merge_google_into_firms(firms, google_firms)
            print(f"[phase 2] Total after Google merge: {len(firms)} firms")
        save_checkpoint(firms, phase=3, path=CHECKPOINT_PATH)
    elif start_phase <= 2:
        print("[phase 2] Google Places skipped (use --use-google to enable)")
        save_checkpoint(firms, phase=3, path=CHECKPOINT_PATH)

    # ── Phase 3: Kansas Bar Association ──
    if start_phase <= 3 and not args.skip_ksbar:
        print("[phase 3] Scraping Kansas Bar Association directory...")
        ksbar_entries = scrape_ksbar()
        if ksbar_entries:
            firms = merge_ksbar_into_firms(firms, ksbar_entries)
            print(f"[phase 3] Total after KSBar merge: {len(firms)} firms")
        else:
            print("[phase 3] No KSBar data (site may be unavailable)")
        save_checkpoint(firms, phase=4, path=CHECKPOINT_PATH)

    # ── Phase 3b: FindLaw directory ──
    if start_phase <= 4 and not args.skip_findlaw:
        from scraper.phases.findlaw import scrape_findlaw, merge_findlaw_into_firms
        print("[phase 3b] Scraping FindLaw directory...")
        findlaw_firms = scrape_findlaw(delay=1.0, test_mode=args.test)
        if findlaw_firms:
            firms = merge_findlaw_into_firms(firms, findlaw_firms)
            print(f"[phase 3b] Total after FindLaw merge: {len(firms)} firms")
        else:
            print("[phase 3b] No FindLaw data")
        save_checkpoint(firms, phase=4, path=CHECKPOINT_PATH,
                        progress={"findlaw_done": True})

    # ── Phase 3c: Avvo directory ──
    if start_phase <= 4 and not args.skip_avvo:
        from scraper.phases.avvo import scrape_avvo, merge_avvo_into_firms
        print("[phase 3c] Scraping Avvo directory...")
        avvo_entries = scrape_avvo(delay=1.0, test_mode=args.test)
        if avvo_entries:
            firms = merge_avvo_into_firms(firms, avvo_entries)
            print(f"[phase 3c] Total after Avvo merge: {len(firms)} firms")
        else:
            print("[phase 3c] No Avvo data")
        save_checkpoint(firms, phase=4, path=CHECKPOINT_PATH,
                        progress={"avvo_done": True})

    # ── Phase 4: Firm website scraping ──
    if start_phase <= 4 and not args.skip_websites and not args.test:
        print("[phase 4] Scraping firm websites...")
        start_idx = progress.get("website_last_idx", 0)
        websites_to_scrape = [(i, f) for i, f in enumerate(firms) if f.get("website")]
        total = len(websites_to_scrape)
        scraped = 0

        for i, firm in websites_to_scrape:
            if i < start_idx:
                continue
            result = scrape_firm_website(firm["website"], firm["name"], firm["address"]["city"])
            firm["summary"] = result["summary"]
            if result["email"]:
                firm["email"] = result["email"]
            if result.get("practiceAreas"):
                existing = set(firm.get("practiceAreas") or [])
                for area in result["practiceAreas"]:
                    if area not in existing:
                        firm["practiceAreas"].append(area)
                        existing.add(area)
            if "website_scraper" not in firm["sources"]:
                firm["sources"].append("website_scraper")
            scraped += 1
            if scraped % 50 == 0:
                save_checkpoint(firms, phase=4, path=CHECKPOINT_PATH,
                                progress={"website_last_idx": i + 1})
                print(f"[phase 4] Progress: {scraped}/{total}")
            time.sleep(1)

        print(f"[phase 4] Scraped {scraped} firm websites")
        save_checkpoint(firms, phase=5, path=CHECKPOINT_PATH)

    # ── Phase 5: Nominatim geocoding ──
    if start_phase <= 5 and not args.skip_geocoding:
        from scraper.utils.geocode import geocode_firms
        missing_coords = sum(1 for f in firms if not f.get("coordinates"))
        if missing_coords > 0:
            print(f"[phase 5] Geocoding {missing_coords} firms missing coordinates...")
            geocode_firms(firms, delay=1.1)
            still_missing = sum(1 for f in firms if not f.get("coordinates"))
            print(f"[phase 5] Geocoding complete. Still missing: {still_missing}")
        else:
            print("[phase 5] All firms already have coordinates")
        save_checkpoint(firms, phase=6, path=CHECKPOINT_PATH)

    # ── Phase 6: Finalization ──
    print("[phase 6] Deduplicating and finalizing...")
    firms = deduplicate_firms(firms)

    for firm in firms:
        firm["practiceAreas"] = list(set(
            normalize_practice_area(p) for p in firm.get("practiceAreas", [])
        ))
        firm["referralScore"] = calculate_referral_score(firm["practiceAreas"])
        if not firm.get("summary"):
            firm["summary"] = f"{firm['name']} — law firm in {firm['address']['city']}, Kansas"
        # Clean up internal fields not needed by frontend
        firm.pop("attorneys", None)
        firm.pop("attorney_count", None)

    write_firms_data_js(firms, path=OUTPUT_PATH)
    clear_checkpoint(CHECKPOINT_PATH)
    _print_summary(firms)


def _print_summary(firms: list):
    scores = Counter(f["referralScore"] for f in firms)
    source_counts = Counter()
    for f in firms:
        for s in f.get("sources", []):
            source_counts[s] += 1

    missing_areas = sum(1 for f in firms if not f["practiceAreas"])
    missing_coords = sum(1 for f in firms if not f["coordinates"])
    missing_email = sum(1 for f in firms if not f.get("email"))
    missing_phone = sum(1 for f in firms if not f.get("phone"))
    missing_website = sum(1 for f in firms if not f.get("website"))

    print(f"\n{'='*50}")
    print(f"  Scrape Complete")
    print(f"{'='*50}")
    print(f"  Total firms: {len(firms)}")
    print(f"\n  Referral scores:")
    print(f"    Competitor: {scores['competitor']} | High: {scores['high']} "
          f"| Medium: {scores['medium']} | Low: {scores['low']}")
    print(f"\n  Data sources:")
    for src, count in source_counts.most_common():
        print(f"    {src}: {count} firms")
    print(f"\n  Coverage:")
    print(f"    With practice areas: {len(firms) - missing_areas}/{len(firms)}")
    print(f"    With coordinates:    {len(firms) - missing_coords}/{len(firms)}")
    print(f"    With phone:          {len(firms) - missing_phone}/{len(firms)}")
    print(f"    With email:          {len(firms) - missing_email}/{len(firms)}")
    print(f"    With website:        {len(firms) - missing_website}/{len(firms)}")
    print(f"\n  Output: {OUTPUT_PATH}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()

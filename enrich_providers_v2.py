#!/usr/bin/env python3
"""
Additional website enrichment passes for provider CSVs.

Pass A: Address sharing — if any provider at the same address has a website,
        assign it to all other providers at that address (they share a clinic).
Pass B: Phone sharing — same logic using phone number as the key.
Pass C: Google Places phone-number lookup — use inputtype=phonenumber for
        a direct phone→business→website lookup on remaining no-website records.

Usage: python3 enrich_providers_v2.py
"""
import csv
import re
import time
import warnings
from pathlib import Path
from collections import defaultdict

import requests

from scraper.utils import places_new

warnings.filterwarnings("ignore")

DATA_DIR = Path("app/county-data")

FIELDNAMES = [
    "provider_name", "website", "phone_number", "provider_type",
    "city", "state", "county", "street_address", "zip_code",
    "email", "npi_number",
]

# Load Google Maps API key
_GMAPS_KEY = ""
for line in Path("scraper/.env").read_text().splitlines():
    if line.startswith("GOOGLE_MAPS_API_KEY="):
        _GMAPS_KEY = line.split("=", 1)[1].strip()
        break

_FIND_PLACE_URL = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
_PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
_GMAPS_PREFIX = "https://www.google.com/maps"

BAD_DOMAINS = frozenset({
    "healthgrades.com", "zocdoc.com", "vitals.com", "ratemds.com", "webmd.com",
    "doximity.com", "yelp.com", "yellowpages.com", "superpages.com", "whitepages.com",
    "facebook.com", "linkedin.com", "twitter.com", "instagram.com", "google.com",
    "bing.com", "wikipedia.org", "youtube.com", "bbb.org", "manta.com", "mapquest.com",
    "npiprofile.com", "npino.com", "npinumber.org", "npidb.org", "medicare.gov",
    "cms.gov", "npiregistry.cms.hhs.gov", "usnews.com", "castleconnolly.com",
    "sharecare.com", "duckduckgo.com", "chirodirectory.com", "apta.org",
    "findachiropractor.com", "chiromatrix.com", "doctor.com", "wellness.com",
    "psychologytoday.com", "psychology-today.com", "birdeye.com", "practicefusion.com",
})

_PLACES_AVAILABLE = None


def _norm_addr(s):
    return re.sub(r'\s+', ' ', s.lower().strip())


def _norm_domain(url):
    try:
        from urllib.parse import urlparse
        return re.sub(r"^www\.", "", urlparse(url.strip()).netloc.lower())
    except Exception:
        return ""


def _is_bad(url):
    if not url or not url.startswith("http"):
        return True
    d = _norm_domain(url)
    return any(d == b or d.endswith("." + b) for b in BAD_DOMAINS)


def _to_e164(phone):
    digits = re.sub(r"[^\d]", "", phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+{digits}"
    return ""


def _places_phone_lookup(phone_e164):
    """Find Place by phone number → place_id → Details → website."""
    global _PLACES_AVAILABLE
    if not _GMAPS_KEY or _PLACES_AVAILABLE is False or not phone_e164:
        return ""
    try:
        r1 = places_new.legacy_get(
            _FIND_PLACE_URL,
            params={"input": phone_e164, "inputtype": "phonenumber",
                    "fields": "place_id,name", "key": _GMAPS_KEY},
            timeout=10,
        )
        d1 = r1.json()
        s1 = d1.get("status", "")
        if s1 == "REQUEST_DENIED":
            _PLACES_AVAILABLE = False
            print("    [Places] REQUEST_DENIED — stopping.")
            return ""
        if s1 not in ("OK", "ZERO_RESULTS"):
            return ""
        _PLACES_AVAILABLE = True
        candidates = d1.get("candidates", [])
        if not candidates:
            return ""
        place_id = candidates[0]["place_id"]

        r2 = places_new.legacy_get(
            _PLACE_DETAILS_URL,
            params={"place_id": place_id, "fields": "website", "key": _GMAPS_KEY},
            timeout=10,
        )
        d2 = r2.json()
        if d2.get("status") != "OK":
            return ""
        website = d2.get("result", {}).get("website", "")
        if website and not website.startswith(_GMAPS_PREFIX) and not _is_bad(website):
            return website
        return ""
    except Exception:
        return ""


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)


def process_file(path):
    rows = list(csv.DictReader(open(path)))
    before = sum(1 for r in rows if r.get("website", "").strip())
    print(f"\n{'='*55}")
    print(f"{path.stem}  ({len(rows)} records, {before} with website)")
    print(f"{'='*55}")

    # ── Pass A: Share website by street address ──────────────────
    addr_to_web = {}
    for r in rows:
        web = r.get("website", "").strip()
        if not web:
            continue
        addr = _norm_addr(r.get("street_address", ""))
        zip_ = r.get("zip_code", "").strip()[:5]
        if addr and zip_:
            addr_to_web[(addr, zip_)] = web

    pass_a = 0
    for r in rows:
        if r.get("website", "").strip():
            continue
        addr = _norm_addr(r.get("street_address", ""))
        zip_ = r.get("zip_code", "").strip()[:5]
        web = addr_to_web.get((addr, zip_), "")
        if web:
            r["website"] = web
            pass_a += 1
    print(f"  Pass A (address sharing): +{pass_a}")

    # ── Pass B: Share website by phone number ────────────────────
    phone_to_web = {}
    for r in rows:
        web = r.get("website", "").strip()
        if not web:
            continue
        phone = re.sub(r"[^\d]", "", r.get("phone_number", ""))
        if len(phone) == 10:
            phone_to_web[phone] = web

    pass_b = 0
    for r in rows:
        if r.get("website", "").strip():
            continue
        phone = re.sub(r"[^\d]", "", r.get("phone_number", ""))
        web = phone_to_web.get(phone, "") if len(phone) == 10 else ""
        if web:
            r["website"] = web
            pass_b += 1
    print(f"  Pass B (phone sharing):   +{pass_b}")

    if pass_a + pass_b:
        _write(path, rows)

    # ── Pass C: Places phone-number lookup ───────────────────────
    no_web = [r for r in rows if not r.get("website", "").strip()]
    has_phone = [r for r in no_web if r.get("phone_number", "").strip()]
    print(f"  Pass C (Places phone):    {len(has_phone)} to look up...")
    pass_c = 0
    for i, rec in enumerate(has_phone):
        if _PLACES_AVAILABLE is False:
            break
        e164 = _to_e164(rec.get("phone_number", ""))
        if not e164:
            continue
        site = _places_phone_lookup(e164)
        if site:
            rec["website"] = site
            pass_c += 1
        time.sleep(0.1)
        if (i + 1) % 50 == 0:
            print(f"    {i+1}/{len(has_phone)} — {pass_c} found")

    print(f"  Pass C done: +{pass_c}")

    if pass_c:
        _write(path, rows)

    after = sum(1 for r in rows if r.get("website", "").strip())
    print(f"  Total: {before} → {after} (+{after - before})")
    return after - before


def main():
    grand_before = 0
    grand_after = 0
    for p in sorted(DATA_DIR.glob("providers-*.csv")):
        rows_pre = list(csv.DictReader(open(p)))
        grand_before += sum(1 for r in rows_pre if r.get("website", "").strip())
        added = process_file(p)
        grand_after += sum(1 for r in csv.DictReader(open(p)) if r.get("website", "").strip())

    print(f"\n{'='*55}")
    print(f"Grand total websites: {grand_after} (was {grand_before}, +{grand_after - grand_before})")


if __name__ == "__main__":
    main()

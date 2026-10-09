#!/usr/bin/env python3
"""
Scrapes the NPI Registry for chiropractors and physical therapists
in Johnson County KS and Wyandotte County KS.

Outputs:
  app/county-data/providers-johnson-county-ks.csv
  app/county-data/providers-wyandotte-county-ks.csv

Usage: python3 scrape_npi_providers.py
"""
import csv
import re
import sys
import time
import warnings
from pathlib import Path
from urllib.parse import quote_plus

import requests

from scraper.utils import places_new
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")

DATA_DIR = Path("app/county-data")

# Load Google Maps API key
_GMAPS_KEY = ""
_env_path = Path("scraper/.env")
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        if _line.startswith("GOOGLE_MAPS_API_KEY="):
            _GMAPS_KEY = _line.split("=", 1)[1].strip()
            break

_PLACES_URL = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
_PLACES_AVAILABLE = None  # None=untested, True/False after first attempt
NPI_URL = "https://npiregistry.cms.hhs.gov/api/"

FIELDNAMES = [
    "provider_name", "website", "phone_number", "provider_type",
    "city", "state", "county", "street_address", "zip_code",
    "email", "npi_number",
]

# County configs
COUNTIES = {
    "johnson-county-ks": {
        "name": "Johnson County",
        "state": "KS",
        "cities": [
            "Overland Park", "Olathe", "Shawnee", "Lenexa", "Leawood",
            "Prairie Village", "Merriam", "Mission", "Gardner", "Spring Hill",
            "De Soto", "Edgerton", "Roeland Park", "Fairway", "Westwood",
            "Lake Quivira", "Mission Hills", "Mission Woods", "Westwood Hills",
        ],
    },
    "wyandotte-county-ks": {
        "name": "Wyandotte County",
        "state": "KS",
        "cities": [
            "Kansas City", "Bonner Springs", "Edwardsville",
        ],
    },
    "leavenworth-county-ks": {
        "name": "Leavenworth County",
        "state": "KS",
        "cities": [
            "Leavenworth", "Lansing", "Basehor", "Tonganoxie", "Linwood", "Easton",
        ],
    },
    "miami-county-ks": {
        "name": "Miami County",
        "state": "KS",
        "cities": [
            "Paola", "Osawatomie", "Louisburg", "Fontana",
        ],
    },
    "linn-county-ks": {
        "name": "Linn County",
        "state": "KS",
        "cities": [
            "Pleasanton", "La Cygne", "Mound City", "Prescott", "Blue Mound",
        ],
    },
    "douglas-county-ks": {
        "name": "Douglas County",
        "state": "KS",
        "cities": [
            "Lawrence", "Eudora", "Baldwin City", "Lecompton",
        ],
    },
    "franklin-county-ks": {
        "name": "Franklin County",
        "state": "KS",
        "cities": [
            "Ottawa", "Wellsville", "Williamsburg", "Richmond", "Lane",
        ],
    },
    "jefferson-county-ks": {
        "name": "Jefferson County",
        "state": "KS",
        "cities": [
            "Oskaloosa", "Winchester", "Valley Falls", "Meriden",
            "McLouth", "Perry", "Nortonville",
        ],
    },
    "osage-county-ks": {
        "name": "Osage County",
        "state": "KS",
        "cities": [
            "Lyndon", "Osage City", "Burlingame", "Overbrook", "Scranton",
        ],
    },
    "shawnee-county-ks": {
        "name": "Shawnee County",
        "state": "KS",
        "cities": [
            "Topeka", "Silver Lake", "Rossville", "Willard",
            "Auburn", "Wakarusa", "Tecumseh",
        ],
    },
}

PROVIDER_TYPES = {
    "chiropractor": "Chiropractor",
    "physical therapist": "Physical Therapist",
}

_DDG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_DIRECTORY_DOMAINS = frozenset({
    "healthgrades.com", "zocdoc.com", "vitals.com", "ratemds.com",
    "webmd.com", "doximity.com", "psychology-today.com",
    "findlaw.com", "avvo.com", "justia.com", "lawyers.com",
    "yelp.com", "yellowpages.com", "superpages.com", "whitepages.com",
    "facebook.com", "linkedin.com", "twitter.com", "instagram.com",
    "google.com", "bing.com", "wikipedia.org", "youtube.com",
    "bbb.org", "manta.com", "mapquest.com", "chamberofcommerce.com",
    "npiprofile.com", "npino.com", "npinumber.org", "npidb.org",
    "medicare.gov", "cms.gov", "npiregistry.cms.hhs.gov",
    "usnews.com", "castleconnolly.com", "sharecare.com",
    "practicefusion.com", "athenahealth.com", "drchrono.com",
})


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _norm_domain(url: str) -> str:
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        return re.sub(r"^www\.", "", urlparse(url.strip()).netloc.lower())
    except Exception:
        return ""


def _is_directory(url: str) -> bool:
    if not url:
        return True
    domain = _norm_domain(url)
    for d in _DIRECTORY_DOMAINS:
        if domain == d or domain.endswith("." + d):
            return True
    return False


def _clean_phone(raw: str) -> str:
    if not raw:
        return ""
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits[0] == "1":
        return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return raw


def _clean_zip(raw: str) -> str:
    if not raw:
        return ""
    digits = re.sub(r"[^\d]", "", raw)
    return digits[:5] if len(digits) >= 5 else digits


def _extract_npi_record(result: dict) -> dict:
    """Extract a clean record from an NPI API result."""
    basic = result.get("basic", {})
    addresses = result.get("addresses", [])
    enum_type = result.get("enumeration_type", "")

    # Name
    if enum_type == "NPI-2":
        name = basic.get("organization_name", "").strip().title()
    else:
        first = basic.get("first_name", "").strip()
        middle = basic.get("middle_name", "").strip()
        last = basic.get("last_name", "").strip()
        cred = basic.get("credential", "").strip()
        name_parts = [p for p in [first, middle, last] if p]
        name = " ".join(name_parts).title()
        if cred and cred != "--":
            name = f"{name}, {cred}"

    # Prefer LOCATION address, fall back to MAILING
    addr = next(
        (a for a in addresses if a.get("address_purpose") == "LOCATION"),
        addresses[0] if addresses else {},
    )

    city = addr.get("city", "").strip().title()
    state = addr.get("state", "").strip().upper()
    street = addr.get("address_1", "").strip().title()
    if addr.get("address_2"):
        street = f"{street} {addr['address_2'].strip().title()}".strip()
    zip_code = _clean_zip(addr.get("postal_code", ""))
    phone = _clean_phone(addr.get("telephone_number", ""))
    npi = result.get("number", "")

    return {
        "provider_name": name,
        "website": "",
        "phone_number": phone,
        "provider_type": "",  # set by caller
        "city": city,
        "state": state,
        "county": "",  # set by caller
        "street_address": street,
        "zip_code": zip_code,
        "email": "",
        "npi_number": npi,
    }


def fetch_npi(taxonomy_query: str, state: str, city: str) -> list[dict]:
    """Fetch all NPI results for a given taxonomy/state/city with pagination."""
    results = []
    skip = 0
    limit = 200

    while True:
        params = {
            "version": "2.1",
            "taxonomy_description": taxonomy_query,
            "state": state,
            "city": city,
            "limit": limit,
            "skip": skip,
        }
        try:
            r = requests.get(NPI_URL, params=params, timeout=20)
            data = r.json()
        except Exception as e:
            print(f"      NPI error: {e}")
            break

        if data.get("Errors"):
            break

        batch = data.get("results", [])
        if not batch:
            break

        results.extend(batch)
        if len(batch) < limit:
            break  # no more pages

        skip += limit
        time.sleep(0.3)

    return results


def ddg_find_website(name: str, provider_type: str, city: str, state: str) -> str:
    """Search DDG for a provider's website."""
    query = f'"{name}" {provider_type.lower()} {city} {state}'
    url = f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}"
    try:
        r = requests.get(url, headers=_DDG_HEADERS, timeout=12)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and not _is_directory(href):
                # Quick sanity: domain should relate to provider name or location
                domain = _norm_domain(href)
                if domain:
                    return href
        return ""
    except Exception:
        return ""


def scrape_county(slug: str, cfg: dict) -> list[dict]:
    """Scrape NPI for all provider types across all cities in a county."""
    county_name = cfg["name"]
    state = cfg["state"]
    cities = cfg["cities"]

    seen_npis: set[str] = set()
    seen_names: set[str] = set()
    records: list[dict] = []

    for taxonomy_query, display_type in PROVIDER_TYPES.items():
        print(f"\n  [{display_type}]")
        for city in cities:
            raw_results = fetch_npi(taxonomy_query, state, city)
            added = 0
            for res in raw_results:
                rec = _extract_npi_record(res)
                npi = rec["npi_number"]
                name = rec["provider_name"]

                # Dedup
                if npi and npi in seen_npis:
                    continue
                nkey = _norm(name)
                if nkey in seen_names:
                    continue

                # Verify state is KS (exclude cross-border MO results)
                if rec["state"] not in (state, ""):
                    continue

                rec["provider_type"] = display_type
                rec["county"] = county_name

                if npi:
                    seen_npis.add(npi)
                seen_names.add(nkey)
                records.append(rec)
                added += 1

            if added:
                print(f"    {city}: +{added}")
            time.sleep(0.2)

    return records


def _places_find_website(name: str, city: str, state: str) -> str:
    """Google Places Find Place — returns website from Google Business Profile."""
    global _PLACES_AVAILABLE
    if not _GMAPS_KEY or _PLACES_AVAILABLE is False:
        return ""
    try:
        r = places_new.legacy_get(
            _PLACES_URL,
            params={
                "input": f"{name} {city} {state}",
                "inputtype": "textquery",
                "fields": "name,website",
                "key": _GMAPS_KEY,
            },
            timeout=10,
        )
        data = r.json()
        if data.get("status") == "REQUEST_DENIED":
            _PLACES_AVAILABLE = False
            print("    [Places] REQUEST_DENIED — falling back to DDG only")
            return ""
        _PLACES_AVAILABLE = True
        candidates = data.get("candidates", [])
        if not candidates:
            return ""
        website = candidates[0].get("website", "")
        if website and not website.startswith("https://www.google.com/maps") and not _is_directory(website):
            return website
        return ""
    except Exception:
        return ""


def enrich_websites(records: list[dict]) -> int:
    """Website enrichment: Google Places first, DDG fallback."""
    no_web = [r for r in records if not r["website"]]
    print(f"\n  Website enrichment: {len(no_web)} providers to search...")
    enriched = 0

    # Pass 0: Google Places (fast, accurate)
    if _GMAPS_KEY:
        print(f"    Pass 0 (Google Places)...")
        for i, rec in enumerate(no_web):
            if _PLACES_AVAILABLE is False:
                break
            site = _places_find_website(rec["provider_name"], rec["city"], rec["state"])
            if site:
                rec["website"] = site
                enriched += 1
            time.sleep(0.05)
        places_found = enriched
        no_web = [r for r in no_web if not r["website"]]
        print(f"    Places: {places_found} found, {len(no_web)} remaining")

    # Pass 1: DDG fallback for remainder
    for i, rec in enumerate(no_web):
        if i > 0 and i % 20 == 0:
            print(f"    DDG: {i}/{len(no_web)} searched, {enriched} found so far")
        site = ddg_find_website(
            rec["provider_name"], rec["provider_type"], rec["city"], rec["state"]
        )
        if site:
            rec["website"] = site
            enriched += 1
        time.sleep(2.5)

    return enriched


def write_csv(slug: str, records: list[dict]) -> Path:
    path = DATA_DIR / f"providers-{slug}.csv"
    records_sorted = sorted(records, key=lambda r: (r["provider_type"], r["city"], r["provider_name"]))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(records_sorted)
    return path


_GMAPS_PREFIX = "https://www.google.com/maps"


def _strip_gmaps(records: list[dict]) -> int:
    """Clear Google Maps URLs from website field — leave blank instead."""
    cleared = 0
    for r in records:
        w = r.get("website", "")
        if w.startswith(_GMAPS_PREFIX):
            r["website"] = ""
            cleared += 1
    return cleared


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Slugs already fully processed — skip NPI scrape, only clean Maps URLs
    ALREADY_DONE = {"johnson-county-ks", "wyandotte-county-ks"}

    # Clean Google Maps URLs from existing files first
    for slug in ALREADY_DONE:
        p = DATA_DIR / f"providers-{slug}.csv"
        if p.exists():
            rows = list(csv.DictReader(p.open()))
            cleared = _strip_gmaps(rows)
            if cleared:
                with p.open("w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=FIELDNAMES)
                    w.writeheader(); w.writerows(rows)
                print(f"  {slug}: cleared {cleared} Google Maps URLs")

    for slug, cfg in COUNTIES.items():
        if slug in ALREADY_DONE:
            continue

        print(f"\n{'='*55}")
        print(f"{cfg['name']} ({slug})")
        print(f"{'='*55}")

        records = scrape_county(slug, cfg)
        print(f"\n  Raw total: {len(records)} providers")

        # Never store Google Maps links — leave website blank if not a real site
        _strip_gmaps(records)

        enriched = enrich_websites(records)
        print(f"  Websites found: {enriched}")

        path = write_csv(slug, records)
        chiro = sum(1 for r in records if r["provider_type"] == "Chiropractor")
        pt = sum(1 for r in records if r["provider_type"] == "Physical Therapist")
        has_web = sum(1 for r in records if r["website"])
        has_phone = sum(1 for r in records if r["phone_number"])
        print(f"\n  Written: {path}")
        print(f"  Chiropractors: {chiro} | Physical Therapists: {pt}")
        print(f"  Has website: {has_web} | Has phone: {has_phone}")

    print(f"\n{'='*55}")
    print("Done.")


if __name__ == "__main__":
    main()

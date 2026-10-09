#!/usr/bin/env python3
"""
Three-pass website enrichment for provider CSVs (chiropractors & PTs):

Pass 1: DDG phone-number search — "(913) 555-1234 chiropractor" →
        finds the clinic's own site as first result (very targeted)
Pass 2: Domain guessing — normalize org name, try .com variants, verify via HTTP HEAD
Pass 3: DDG name+city search — standard query with 5s+ delays for remainder

Usage: python3 enrich_provider_websites.py
"""
import csv
import re
import socket
import time
import warnings
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import requests

from scraper.utils import places_new
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")

# Load Google Maps API key from scraper/.env
_GMAPS_KEY = ""
_env_path = Path("scraper/.env")
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if line.startswith("GOOGLE_MAPS_API_KEY="):
            _GMAPS_KEY = line.split("=", 1)[1].strip()
            break

DATA_DIR = Path("app/county-data")

FIELDNAMES = [
    "provider_name", "website", "phone_number", "provider_type",
    "city", "state", "county", "street_address", "zip_code",
    "email", "npi_number",
]

DDG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

BAD_DOMAINS = frozenset({
    "healthgrades.com", "zocdoc.com", "vitals.com", "ratemds.com", "webmd.com",
    "doximity.com", "yelp.com", "yellowpages.com", "superpages.com", "whitepages.com",
    "facebook.com", "linkedin.com", "twitter.com", "instagram.com", "google.com",
    "bing.com", "wikipedia.org", "youtube.com", "bbb.org", "manta.com", "mapquest.com",
    "npiprofile.com", "npino.com", "npinumber.org", "npidb.org", "medicare.gov",
    "cms.gov", "npiregistry.cms.hhs.gov", "usnews.com", "castleconnolly.com",
    "sharecare.com", "duckduckgo.com", "angieslist.com", "homeadvisor.com",
    "thumbtack.com", "care.com", "practicefusion.com", "chirodirectory.com",
    "apta.org", "acatoday.org", "americanchiropractors.org", "chiroeco.com",
    "findachiropractor.com", "chiromatrix.com", "chiropractic.org",
    "psychology-today.com", "psychologytoday.com", "therapyfinder.com",
    "therapist.com", "goodtherapy.org", "indeed.com", "glassdoor.com",
    "chamberofcommerce.com", "birdeye.com", "trustpilot.com", "merchantcircle.com",
    "ezlocal.com", "showmelocal.com", "citysearch.com", "insiderpages.com",
    "doctor.com", "wellness.com", "healthline.com", "everydayhealth.com",
})


def _norm_domain(url: str) -> str:
    if not url:
        return ""
    try:
        return re.sub(r"^www\.", "", urlparse(url.strip()).netloc.lower())
    except Exception:
        return ""


def _is_bad(url: str) -> bool:
    if not url or not url.startswith("http"):
        return True
    d = _norm_domain(url)
    return any(d == b or d.endswith("." + b) for b in BAD_DOMAINS)


def _ddg_first_good(query: str) -> str:
    """Return first non-directory URL from DDG lite, or ''."""
    url = f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}"
    try:
        r = requests.get(url, headers=DDG_HEADERS, timeout=12)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and not _is_bad(href):
                return href
        return ""
    except Exception:
        return ""


def _head_resolves(url: str, timeout: int = 6) -> bool:
    """True if an HTTP HEAD request gets a 2xx or 3xx response."""
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True,
                          verify=False,
                          headers={"User-Agent": DDG_HEADERS["User-Agent"]})
        return r.status_code < 400
    except Exception:
        return False


def _name_to_domain_candidates(name: str, city: str) -> list[str]:
    """
    Generate plausible domain names from a clinic name.
    E.g. "Mission Chiropractic LLC" → missionchiropractic.com etc.
    """
    # Strip legal suffixes
    cleaned = re.sub(
        r'\b(llc|pllc|inc|pa|ltd|corp|dba|d\.b\.a\.)\b',
        '', name, flags=re.I
    ).strip()
    # Remove credentials
    cleaned = re.sub(r',\s*(D\.?C\.?|DPT|PT|LPT|MSPT|MPT|DC)\s*$', '', cleaned, flags=re.I).strip()
    # Remove punctuation, collapse spaces
    base = re.sub(r"[^a-z0-9\s]", "", cleaned.lower())
    base = re.sub(r"\s+", "", base).strip()

    if not base or len(base) < 4:
        return []

    # City slug for local variants
    city_slug = re.sub(r"[^a-z0-9]", "", city.lower())

    candidates = []
    for ext in (".com", ".net", ".org"):
        candidates.append(f"https://{base}{ext}")
        candidates.append(f"https://www.{base}{ext}")

    # City-suffixed variants (for common names like "Family Chiropractic")
    if len(base) < 20:  # only for shorter names where city helps disambiguate
        for ext in (".com",):
            candidates.append(f"https://{base}{city_slug}{ext}")
            candidates.append(f"https://{city_slug}{base}{ext}")

    return candidates


def is_org_name(name: str) -> bool:
    """True if the name looks like a clinic/practice rather than a solo individual."""
    if re.search(r'\b(llc|pllc|inc|pa|ltd|corp)\b', name, re.I):
        return True
    org_kw = r'\b(chiropractic|physical\s+therapy|therapy|center|clinic|group|associates|health|wellness|rehab|sports|spine|family|care|institute|services|practice|network)\b'
    return len(re.findall(org_kw, name, re.I)) >= 1


# ---------------------------------------------------------------------------
# Pass 0: Google Places — two-step: Find Place (place_id) → Details (website)
# ---------------------------------------------------------------------------

_FIND_PLACE_URL = "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
_PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
_GMAPS_PREFIX = "https://www.google.com/maps"
_PLACES_AVAILABLE = None  # None = untested, True/False after first call

_STOP_WORDS = {"the","of","and","for","a","an","at","in","llc","inc","pa","dba","pllc"}

def _name_similarity(query: str, result: str) -> bool:
    """True if result name shares at least one meaningful keyword with query."""
    def kw(s):
        return {w.lower() for w in re.split(r"[\s,.\-&]+", s)
                if len(w) > 2 and w.lower() not in _STOP_WORDS}
    return bool(kw(query) & kw(result))


def _places_find_website(name: str, city: str, state: str) -> str:
    """Two-step Places lookup: Find Place → place_id → Details → website."""
    global _PLACES_AVAILABLE
    if not _GMAPS_KEY or _PLACES_AVAILABLE is False:
        return ""
    try:
        # Step 1: Find Place → place_id + matched name
        r1 = places_new.legacy_get(
            _FIND_PLACE_URL,
            params={"input": f"{name} {city} {state}", "inputtype": "textquery",
                    "fields": "place_id,name", "key": _GMAPS_KEY},
            timeout=10,
        )
        d1 = r1.json()
        s1 = d1.get("status", "")
        if s1 == "REQUEST_DENIED":
            _PLACES_AVAILABLE = False
            print("    [Places] REQUEST_DENIED — billing may be off. Skipping.")
            return ""
        if s1 not in ("OK", "ZERO_RESULTS"):
            return ""
        _PLACES_AVAILABLE = True
        candidates = d1.get("candidates", [])
        if not candidates:
            return ""
        place_id = candidates[0]["place_id"]
        matched_name = candidates[0].get("name", "")
        if not _name_similarity(name, matched_name):
            return ""  # matched wrong business

        # Step 2: Place Details → website
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


def pass0_google_places(records: list[dict]) -> int:
    """Use two-step Google Places lookup to get websites from Google Business Profiles."""
    if not _GMAPS_KEY:
        print("\n  Pass 0 (Google Places): no API key found, skipping")
        return 0

    targets = [r for r in records if not r["website"]]
    print(f"\n  Pass 0 (Google Places): {len(targets)} providers to look up...")
    found = 0
    for i, rec in enumerate(targets):
        if _PLACES_AVAILABLE is False:
            break
        site = _places_find_website(rec["provider_name"], rec["city"], rec["state"])
        if site:
            rec["website"] = site
            found += 1
        time.sleep(0.1)  # 2 API calls per provider; 0.1s keeps us under rate limits
        if (i + 1) % 50 == 0:
            print(f"    {i+1}/{len(targets)} — {found} found")
    print(f"    Done: {found} websites via Google Places")
    return found


# ---------------------------------------------------------------------------
# Pass 1: DDG phone search
# ---------------------------------------------------------------------------

def pass1_phone_ddg(records: list[dict]) -> int:
    targets = [r for r in records if not r["website"] and r["phone_number"]]
    print(f"\n  Pass 1 (DDG phone search): {len(targets)} records")
    found = 0
    for i, rec in enumerate(targets):
        phone = rec["phone_number"]
        ptype = rec["provider_type"].lower()
        query = f'{phone} {ptype}'
        site = _ddg_first_good(query)
        if site:
            rec["website"] = site
            found += 1
        time.sleep(4.0)  # conservative delay
        if (i + 1) % 25 == 0:
            print(f"    {i+1}/{len(targets)} — {found} found")
    print(f"    Done: {found} websites via phone search")
    return found


# ---------------------------------------------------------------------------
# Pass 2: Domain guessing + HTTP verification
# ---------------------------------------------------------------------------

def pass2_domain_guess(records: list[dict]) -> int:
    targets = [r for r in records if not r["website"] and is_org_name(r["provider_name"])]
    print(f"\n  Pass 2 (domain guessing): {len(targets)} org-name records")
    found = 0
    for rec in targets:
        candidates = _name_to_domain_candidates(rec["provider_name"], rec["city"])
        for url in candidates:
            if _head_resolves(url):
                rec["website"] = url
                found += 1
                break
        time.sleep(0.3)  # HEAD requests are cheap
    print(f"    Done: {found} websites via domain guessing")
    return found


# ---------------------------------------------------------------------------
# Pass 3: DDG name + city search (org names only, longer delay)
# ---------------------------------------------------------------------------

def pass3_name_ddg(records: list[dict]) -> int:
    targets = [r for r in records if not r["website"] and is_org_name(r["provider_name"])]
    print(f"\n  Pass 3 (DDG name+city): {len(targets)} records")
    found = 0
    for i, rec in enumerate(targets):
        name = rec["provider_name"]
        city = rec["city"]
        state = rec["state"]
        ptype = rec["provider_type"].lower()
        query = f'"{name}" {ptype} {city} {state}'
        site = _ddg_first_good(query)
        if site:
            rec["website"] = site
            found += 1
        time.sleep(5.0)
        if (i + 1) % 20 == 0:
            print(f"    {i+1}/{len(targets)} — {found} found")
    print(f"    Done: {found} websites via name+city search")
    return found


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    for p in sorted(DATA_DIR.glob("providers-*.csv")):
        print(f"\n{'='*55}")
        print(f"{p.stem}")
        print(f"{'='*55}")

        rows = list(csv.DictReader(p.open()))
        before = sum(1 for r in rows if r.get("website", "").strip())
        print(f"  Starting: {len(rows)} records, {before} with website")

        # Pass 0: Google Places (fastest, most reliable — runs first)
        found0 = pass0_google_places(rows)
        _write(p, rows)

        # Pass 1: DDG phone search
        found1 = pass1_phone_ddg(rows)
        _write(p, rows)

        # Pass 2: Domain guessing
        found2 = pass2_domain_guess(rows)
        _write(p, rows)

        # Pass 3: DDG name+city search
        found3 = pass3_name_ddg(rows)
        _write(p, rows)

        after = sum(1 for r in rows if r.get("website", "").strip())
        print(f"\n  Final: {after} websites (+{after - before} from {before})")
        print(f"  Breakdown: places={found0}, phone={found1}, domain={found2}, name={found3}")


def _write(path: Path, rows: list[dict]):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()

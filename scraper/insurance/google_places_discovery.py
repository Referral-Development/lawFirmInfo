"""Google Places discovery of insurance agents/agencies for a county.

Runs a Places Text Search for "insurance agency" against every city in a
county, then pulls Place Details (phone, website) for each unique place_id.
Reusable across counties: edit CITIES/STATE/QUERY_SUFFIX below (or import
`discover()` from another script and pass your own city list).

Writes raw results to output/google_places_raw.json as a JSON array of:
{name, phone, website, formatted_address, city_hint, place_id, business_status,
 types, source}

Only real API responses are written — no fabricated/guessed data. Uses the
legacy Places API (textsearch + details) since that's what the project's key
is enabled for (confirmed working 2026-08-22).
"""
import json
import os
import time
from pathlib import Path

import requests

from scraper.utils import places_new
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
OUTPUT_DIR = HERE / "output"
OUTPUT_PATH = OUTPUT_DIR / "google_places_raw.json"

STATE = "KS"
COUNTY_QUALIFIER = "Johnson County"
CITIES = [
    "Overland Park", "Olathe", "Shawnee", "Lenexa", "Leawood",
    "Prairie Village", "Merriam", "Mission", "Gardner", "Spring Hill",
    "De Soto", "Edgerton", "Roeland Park", "Fairway", "Westwood",
    "Lake Quivira", "Mission Hills", "Mission Woods", "Westwood Hills",
]
QUERY_TEMPLATE = "insurance agency in {city}, {state}"

TEXTSEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
DETAILS_FIELDS = "name,formatted_phone_number,website,formatted_address,business_status,type"


def load_api_key() -> str:
    load_dotenv(REPO_ROOT / "scraper" / ".env")
    load_dotenv(REPO_ROOT / ".env", override=False)
    return os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()


def _text_search(api_key: str, query: str, errors: list) -> list:
    """Runs a Text Search query, following pagetoken pagination (up to 3 pages / 60 results)."""
    results = []
    params = {"query": query, "key": api_key}
    next_token = None
    for _ in range(3):
        if next_token:
            params = {"query": query, "pagetoken": next_token, "key": api_key}

        # A next_page_token isn't valid immediately; retry INVALID_REQUEST a
        # couple times with a growing delay before giving up on that page.
        data = None
        for attempt_delay in (2.0, 3.0, 0.0):
            if next_token or attempt_delay:
                time.sleep(attempt_delay if next_token else 0)
            try:
                resp = places_new.legacy_get(TEXTSEARCH_URL, params=params, timeout=15)
                data = resp.json()
            except (requests.RequestException, ValueError) as e:
                errors.append(f"TextSearch: request error for '{query}': {e}")
                data = None
                break
            if next_token and data.get("status") == "INVALID_REQUEST":
                continue  # token not ready yet, retry
            break

        if data is None:
            break

        status = data.get("status")
        if status == "REQUEST_DENIED":
            errors.append(f"TextSearch: REQUEST_DENIED for '{query}': {data.get('error_message')}")
            break
        if status == "OVER_QUERY_LIMIT":
            errors.append(f"TextSearch: OVER_QUERY_LIMIT for '{query}': {data.get('error_message')}")
            break
        if status == "INVALID_REQUEST" and next_token:
            # Token never became valid in time; just stop paginating this query.
            break
        if status not in ("OK", "ZERO_RESULTS"):
            errors.append(f"TextSearch: status={status} for '{query}': {data.get('error_message')}")
            break

        results.extend(data.get("results", []))
        next_token = data.get("next_page_token")
        if not next_token:
            break
    return results


def _place_details(api_key: str, place_id: str, errors: list) -> dict:
    params = {"place_id": place_id, "fields": DETAILS_FIELDS, "key": api_key}
    try:
        resp = places_new.legacy_get(DETAILS_URL, params=params, timeout=15)
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        errors.append(f"Details: error for place_id={place_id}: {e}")
        return {}
    if data.get("status") != "OK":
        errors.append(f"Details: status={data.get('status')} for place_id={place_id}: {data.get('error_message')}")
        return {}
    return data.get("result", {})


def discover(cities=None, state=None, county_qualifier=None, query_template=None) -> dict:
    """Runs the full text-search + details sweep. Returns {records, errors}."""
    cities = cities or CITIES
    state = state or STATE
    county_qualifier = county_qualifier if county_qualifier is not None else COUNTY_QUALIFIER
    query_template = query_template or QUERY_TEMPLATE

    api_key = load_api_key()
    errors = []
    if not api_key:
        errors.append("Google Places: no API key found (GOOGLE_MAPS_API_KEY not set)")
        return {"records": [], "errors": errors}

    seen_place_ids = set()
    records = []

    for city in cities:
        query = query_template.format(city=f"{city}, {county_qualifier}" if county_qualifier else city, state=state)
        print(f"[google_places_discovery] Text search: {query!r}")
        hits = _text_search(api_key, query, errors)
        print(f"[google_places_discovery]   -> {len(hits)} raw hits")

        for hit in hits:
            place_id = hit.get("place_id")
            if not place_id or place_id in seen_place_ids:
                continue
            seen_place_ids.add(place_id)

            details = _place_details(api_key, place_id, errors)
            time.sleep(0.1)

            name = details.get("name") or hit.get("name") or ""
            phone = details.get("formatted_phone_number") or ""
            website = details.get("website") or ""
            address = details.get("formatted_address") or hit.get("formatted_address") or ""
            status = details.get("business_status") or hit.get("business_status") or ""
            types = details.get("types") or hit.get("types") or []

            records.append({
                "name": name,
                "phone": phone,
                "website": website,
                "formatted_address": address,
                "city_hint": city,
                "place_id": place_id,
                "business_status": status,
                "types": types,
                "source": "Google Places",
            })
        time.sleep(0.2)

    return {"records": records, "errors": errors}


def main():
    print("[google_places_discovery] Discovering insurance agencies via Google Places Text Search")
    print(f"[google_places_discovery] Cities: {len(CITIES)}")

    result = discover()
    records = result["records"]
    errors = result["errors"]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(records, f, indent=2)

    print(f"[google_places_discovery] Wrote {len(records)} unique place records to {OUTPUT_PATH}")
    if errors:
        print("[google_places_discovery] Errors/limitations encountered:")
        for e in errors:
            print(f"  - {e}")

    return {"total": len(records), "errors": errors}


if __name__ == "__main__":
    main()

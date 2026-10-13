import os
import re
import time
import uuid

from scraper.utils import places_new
from scraper.utils.normalize import normalize_firm_name, are_same_firm
from scraper.county.config import SEARCH_QUERIES

_ZIP_RE = re.compile(r'\b(\d{5})\b')
_STATE_RE = re.compile(r',\s*([A-Z]{2})\s+\d{5}')
_CITY_RE = re.compile(r',\s*([^,]+),\s*[A-Z]{2}\s+\d{5}')

TEXT_SEARCH_COST = 0.035  # Text Search Enterprise SKU (search mask includes phone/website)
DETAIL_COST = 0.020  # Places API (New) Details, Enterprise SKU (phone+website)


def _parse_address(formatted: str) -> dict:
    parts = [p.strip() for p in formatted.split(",")]
    zip_match = _ZIP_RE.search(formatted)
    state_match = _STATE_RE.search(formatted)
    city_match = _CITY_RE.search(formatted)
    return {
        "street": parts[0] if parts else "",
        "city": city_match.group(1).strip() if city_match else "",
        "county": "",
        "state": state_match.group(1) if state_match else "",
        "zip": zip_match.group(1) if zip_match else "",
    }


def _is_duplicate(name: str, city: str, existing: list) -> bool:
    for firm in existing:
        if firm["address"]["city"].lower() == city.lower():
            if are_same_firm(name, firm["name"]):
                return True
    return False


def _build_gbp_url(place_id: str) -> str:
    return f"https://www.google.com/maps/place/?q=place_id:{place_id}"


def discover_google(county_config: dict, api_key: str, test_mode: bool = False) -> list:
    client = places_new.Client(key=api_key)
    firms = []
    state = county_config["state"]
    cities = county_config["cities"]
    county_cities_lower = {c.lower() for c in cities}
    zip_codes = set(county_config.get("zip_codes", []))
    if test_mode:
        cities = cities[:3]

    text_searches = 0
    detail_calls = 0
    skipped_out_of_county = 0

    search_locations = [(city, f"{city} {state}") for city in cities]
    # extra_search_terms added <=2% recall in a cache replay of Clay/Jefferson/Boone MO (2026-10), so they are
    # skipped by default to save Places spend. Set PLACES_USE_EXTRA_TERMS=1 to include them.
    if os.environ.get("PLACES_USE_EXTRA_TERMS") == "1":
        for term in county_config.get("extra_search_terms", []):
            search_locations.append((term, term))
    for zc in county_config.get("zip_codes", []):
        search_locations.append((zc, zc))

    for location_label, location_query in search_locations:
        for query_base in SEARCH_QUERIES:
            query = f"{query_base} in {location_query}"
            try:
                response = client.places(query=query)
                text_searches += 1
            except Exception as e:
                print(f"  [google] Error searching '{query}': {e}")
                continue

            page_results = response.get("results", [])
            next_token = response.get("next_page_token")

            pages = 0
            while True:
                for place in page_results:
                    name = place.get("name", "")
                    address = _parse_address(place.get("formatted_address", ""))
                    city_val = address["city"] or location_label

                    # Reject wrong-state same-named cities (e.g. Louisburg, NC
                    # vs Louisburg, KS). A blank state is allowed through since
                    # some detail rows omit it; ZIP membership re-confirms.
                    addr_state = address.get("state", "")
                    if addr_state and addr_state != state:
                        skipped_out_of_county += 1
                        continue

                    in_county = (
                        city_val.lower() in county_cities_lower
                        or (zip_codes and address.get("zip") in zip_codes)
                    )
                    if not in_county:
                        skipped_out_of_county += 1
                        continue

                    if city_val.lower() not in county_cities_lower and address.get("zip") in zip_codes:
                        for c in county_config["cities"]:
                            if c.lower() == "kansas city":
                                city_val = c
                                break

                    if _is_duplicate(name, city_val, firms):
                        continue

                    place_id = place.get("place_id", "")
                    try:
                        details = client.place(place_id=place_id, fields=[
                            "name", "formatted_address", "formatted_phone_number",
                            "website", "geometry"
                        ])["result"]
                        detail_calls += 1
                    except Exception:
                        details = place

                    address = _parse_address(
                        details.get("formatted_address",
                                    place.get("formatted_address", ""))
                    )
                    loc = details.get("geometry", {}).get("location", {})

                    firms.append({
                        "id": str(uuid.uuid4()),
                        "name": details.get("name", name),
                        "practiceAreas": [],
                        "summary": None,
                        "website": details.get("website"),
                        "phone": details.get("formatted_phone_number"),
                        "email": None,
                        "address": address,
                        "coordinates": {"lat": loc["lat"], "lng": loc["lng"]} if loc else None,
                        "sources": ["google_places"],
                        "google_business_profile": _build_gbp_url(place_id) if place_id else "",
                    })
                    time.sleep(0.1)

                if not next_token or pages >= 2:
                    break
                time.sleep(3)
                try:
                    response = client.places(page_token=next_token)
                    text_searches += 1
                    page_results = response.get("results", [])
                    next_token = response.get("next_page_token")
                    pages += 1
                except Exception:
                    break

            time.sleep(0.5)

            if test_mode and len(firms) >= 20:
                break
        if test_mode and len(firms) >= 20:
            break

    # Real spend: only LIVE calls cost money; cache/memo hits are free.
    st = places_new.STATS
    est_cost = (st["live_search"] * TEXT_SEARCH_COST) + (st["live_details"] * DETAIL_COST)
    print(f"  [google] API calls: {text_searches} text searches + {detail_calls} details requested")
    print(f"  {places_new.usage_summary()}")
    print(f"  [google] Estimated cost: ${est_cost:.2f}")
    print(f"  [google] Skipped {skipped_out_of_county} out-of-county results")
    print(f"  [google] Discovered {len(firms)} firms")
    return firms

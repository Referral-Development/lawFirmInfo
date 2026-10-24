#!/usr/bin/env python3
"""
Scrapes the OKBar public Find-a-Lawyer directory for all 14 target OK counties.
Adds new attorneys/firms not already in the county CSVs.

Usage: python3 okbar_scraper.py [slug ...]
       python3 okbar_scraper.py          # all 14 OK counties
"""
import csv
import re
import sys
import time
import warnings
from pathlib import Path

import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")

DATA_DIR = Path("app/county-data")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

START_URL = "https://ams.okbar.org/eweb/startpage.aspx?site=FALWEB"

# OKBar county name → our slug + config
COUNTY_MAP = {
    "Canadian":    ("canadian-county-ok",    "Canadian",    "Oklahoma City"),
    "Cleveland":   ("cleveland-county-ok",   "Cleveland",   "Oklahoma City"),
    "Comanche":    ("comanche-county-ok",    "Comanche",    "Lawton"),
    "Payne": ("payne-county-ok", "Payne", "Stillwater"),
    "Muskogee": ("muskogee-county-ok", "Muskogee", "Muskogee"),
    "Garfield": ("garfield-county-ok", "Garfield", "Enid"),
    "Bryan": ("bryan-county-ok", "Bryan", "Durant"),
    "LeFlore": ("leflore-county-ok", "LeFlore", "Poteau"),
    "Cherokee": ("cherokee-county-ok", "Cherokee", "Tahlequah"),
    "Carter": ("carter-county-ok", "Carter", "Ardmore"),
    "Stephens": ("stephens-county-ok", "Stephens", "Duncan"),
    "Kay": ("kay-county-ok", "Kay", "Ponca City"),
    "Pittsburg": ("pittsburg-county-ok", "Pittsburg", "McAlester"),
    "Delaware": ("delaware-county-ok", "Delaware", "Grove"),
    "Sequoyah": ("sequoyah-county-ok", "Sequoyah", "Sallisaw"),
    "Mayes": ("mayes-county-ok", "Mayes", "Pryor"),
    "Pontotoc": ("pontotoc-county-ok", "Pontotoc", "Ada"),
    "Lincoln": ("lincoln-county-ok", "Lincoln", "Chandler"),
    "McCurtain": ("mccurtain-county-ok", "McCurtain", "Idabel"),
    "Ottawa": ("ottawa-county-ok", "Ottawa", "Miami"),
    "Custer": ("custer-county-ok", "Custer", "Weatherford"),
    "Garvin": ("garvin-county-ok", "Garvin", "Pauls Valley"),
    "Caddo": ("caddo-county-ok", "Caddo", "Anadarko"),
    "Jackson": ("jackson-county-ok", "Jackson", "Altus"),
    "Seminole": ("seminole-county-ok", "Seminole", "Seminole"),
    "Beckham": ("beckham-county-ok", "Beckham", "Elk City"),
    "Texas": ("texas-county-ok", "Texas", "Guymon"),
    "Adair": ("adair-county-ok", "Adair", "Stilwell"),
    "Woodward": ("woodward-county-ok", "Woodward", "Woodward"),
    "McIntosh": ("mcintosh-county-ok", "McIntosh", "Eufaula"),
    "Marshall": ("marshall-county-ok", "Marshall", "Madill"),
    "Pawnee": ("pawnee-county-ok", "Pawnee", "Pawnee"),
    "Kingfisher": ("kingfisher-county-ok", "Kingfisher", "Kingfisher"),
    "Atoka": ("atoka-county-ok", "Atoka", "Atoka"),
    "Craig": ("craig-county-ok", "Craig", "Vinita"),
    "Creek":       ("creek-county-ok",       "Creek",       "Tulsa"),
    "Grady":       ("grady-county-ok",       "Grady",       "Oklahoma City"),
    "Logan":       ("logan-county-ok",       "Logan",       "Oklahoma City"),
    "Mcclain":     ("mcclain-county-ok",     "McClain",     "Oklahoma City"),
    "Oklahoma":    ("oklahoma-county-ok",    "Oklahoma",    "Oklahoma City"),
    "Okmulgee":   ("okmulgee-county-ok",    "Okmulgee",    "Tulsa"),
    "Osage":      ("osage-county-ok",        "Osage",       "Tulsa"),
    "Pottawatomie": ("pottawatomie-county-ok","Pottawatomie","Oklahoma City"),
    "Rogers":      ("rogers-county-ok",      "Rogers",      "Tulsa"),
    "Tulsa":       ("tulsa-county-ok",       "Tulsa",       "Tulsa"),
    "Wagoner":     ("wagoner-county-ok",     "Wagoner",     "Tulsa"),
    "Washington":  ("washington-county-ok",  "Washington",  "Tulsa"),
}

VALID_OK_AREA = {
    "405", "918", "580",  # OK geographic
    "800", "833", "844", "855", "866", "877", "888",  # toll-free
}

_PHONE_RE = re.compile(r'\((\d{3})\)\s*(\d{3})[\-\.](\d{4})')


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _norm_domain(url: str) -> str:
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url.strip()).netloc.lower()
        return re.sub(r"^www\.", "", netloc)
    except Exception:
        return ""


def _clean_phone(raw: str) -> str:
    m = _PHONE_RE.search(raw)
    if not m:
        return ""
    area, prefix, suffix = m.group(1), m.group(2), m.group(3)
    if area not in VALID_OK_AREA:
        return ""
    return f"({area}) {prefix}-{suffix}"


def _get_session_and_hidden() -> tuple[requests.Session, dict]:
    session = requests.Session()
    session.headers.update(HEADERS)
    r = session.get(START_URL, timeout=20, verify=False)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    hidden = {inp["name"]: inp.get("value", "") for inp in soup.find_all("input", type="hidden")}
    return session, hidden


def _parse_cards(soup: BeautifulSoup) -> list[dict]:
    """Parse attorney cards from a results page."""
    records = []
    row_fluid = soup.find("div", class_="row-fluid")
    if not row_fluid:
        return records

    cards = row_fluid.find_all("div", class_="span4", recursive=False)
    if not cards:
        # fallback: direct children
        cards = row_fluid.find_all("div", recursive=False)

    for card in cards:
        lines = [ln.strip() for ln in card.get_text("\n").split("\n") if ln.strip()]
        if not lines:
            continue

        name = lines[0]
        # Skip if name looks like navigation or garbage
        if len(name) < 4 or re.search(r"(browse|search|contact\s+us|view\s+profile)", name, re.I):
            continue

        phone = ""
        email = ""
        website = ""
        city = ""
        state = "OK"
        zip_code = ""
        address_lines = []

        for ln in lines[1:]:
            # Phone
            if re.match(r'\(\d{3}\)', ln):
                phone = _clean_phone(ln)
                continue
            # Email
            if "@" in ln and "." in ln and " " not in ln.strip():
                email = ln.strip()
                continue
            # Website
            if ln.startswith("http") or ln.startswith("www."):
                website = ln.strip() if ln.startswith("http") else f"http://{ln.strip()}"
                continue
            # City, ST  ZIP pattern
            m = re.match(r"^(.+),\s*OK\s+(\d{5})", ln)
            if m:
                city = m.group(1).strip()
                zip_code = m.group(2)
                continue
            # "Phone : Unlisted" / "Address : Unlisted" → skip
            if re.match(r"^(Phone|Address)\s*:", ln, re.I):
                continue
            # Otherwise it's an address line
            address_lines.append(ln)

        # Extract firm name if it appears as a separate line before address
        firm_name = name  # Start with person name; override if firm name found
        if len(address_lines) >= 1:
            # First address line might be firm name (doesn't look like street address)
            potential_firm = address_lines[0]
            if not re.match(r"^\d+\s+\w+|^PO\s+Box|^P\.O\.\s+Box", potential_firm, re.I):
                firm_name = potential_firm

        if not city and address_lines:
            # Try last address line for city pattern without comma
            last = address_lines[-1]
            m2 = re.match(r"^(.+?)\s+OK\s+(\d{5})", last, re.I)
            if m2:
                city = m2.group(1).strip()
                zip_code = m2.group(2)

        records.append({
            "law_firm_name": firm_name,
            "attorney_name": name,
            "website": website,
            "phone": phone,
            "email": email,
            "city": city,
            "zip_code": zip_code,
        })

    return records


def _get_pagination_args(html: str) -> list[int]:
    """Return the pagination arg numbers found in __doPostBack calls."""
    args = list(set(int(m) for m in re.findall(r"__doPostBack\('[^']+','(\d+)'\)", html)))
    args.sort()
    return args


def scrape_county(okbar_county: str, session: requests.Session, hidden: dict) -> list[dict]:
    """Fetch all pages for an OKBar county search. Returns list of raw records."""
    payload = {
        **hidden,
        "C_2_2$ValueDropDownList0": "",
        "C_2_2$ValueTextBox1": "",
        "C_2_2$ValueDropDownList2": okbar_county,
        "C_2_2$ValueDropDownList3": "OK",
        "C_2_2$ValueTextBox4": "",
        "C_2_2$ValueDropDownList5": "",
        "C_2_2$ValueDropDownList6": "",
        "C_2_2$ButtonFindGo": "Search",
    }

    r = session.post(START_URL, data=payload, timeout=30, verify=False)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    all_records = _parse_cards(soup)
    print(f"    Page 1: {len(all_records)} cards", flush=True)

    # Extract hidden fields from results page for pagination
    result_hidden = {inp["name"]: inp.get("value", "") for inp in soup.find_all("input", type="hidden")}
    page_args = _get_pagination_args(r.text)

    # Find the pagination event target
    pg_target_match = re.search(r"__doPostBack\('([^']+)','(\d+)'\)", r.text)
    evt_target = pg_target_match.group(1) if pg_target_match else "C_2_2$Pager$ctl02$LinkButton1"

    results_url = r.url  # stay on same URL

    for page_arg in page_args:
        time.sleep(2.0)
        pg_payload = {
            **result_hidden,
            "__EVENTTARGET": evt_target,
            "__EVENTARGUMENT": str(page_arg),
        }
        r2 = session.post(results_url, data=pg_payload, timeout=30, verify=False)
        if r2.status_code != 200:
            print(f"    Page {page_arg+1}: HTTP {r2.status_code} — stopping", flush=True)
            break
        s2 = BeautifulSoup(r2.text, "html.parser")
        cards = _parse_cards(s2)
        if not cards:
            print(f"    Page {page_arg+1}: 0 cards — stopping", flush=True)
            break
        all_records.extend(cards)
        print(f"    Page {page_arg+1}: {len(cards)} cards (total: {len(all_records)})", flush=True)
        # Update hidden for next page
        result_hidden = {inp["name"]: inp.get("value", "") for inp in s2.find_all("input", type="hidden")}
        # Update event target
        new_tgt = re.search(r"__doPostBack\('([^']+)','(\d+)'\)", r2.text)
        if new_tgt:
            evt_target = new_tgt.group(1)

    return all_records


def merge_into_csv(slug: str, county_name: str, msa: str, raw_records: list[dict]) -> int:
    """Merge new records into the county CSV. Returns count of new firms added."""
    csv_path = DATA_DIR / f"{slug}.csv"
    if not csv_path.exists():
        print(f"  SKIP: {csv_path} not found")
        return 0

    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    fieldnames = list(rows[0].keys()) if rows else [
        "law_firm_name", "website", "google_business_profile", "legal_directory_listing",
        "city", "state", "county", "phone_number", "email", "practice_area",
        "street_address", "zip_code", "msa", "priority", "number_of_lawyers",
    ]

    existing_names = {_norm(r["law_firm_name"]) for r in rows}
    existing_phones = {r["phone_number"].strip() for r in rows if r.get("phone_number", "").strip()}
    existing_domains = {_norm_domain(r["website"]) for r in rows if r.get("website", "").strip()}

    new_rows = []
    for rec in raw_records:
        name = rec["law_firm_name"].strip()
        phone = rec["phone"].strip()
        website = rec["website"].strip()
        email = rec["email"].strip()
        city = rec["city"].strip()
        zip_code = rec["zip_code"].strip()

        if not name or len(name) < 4:
            continue

        # Dedup by normalized name
        if _norm(name) in existing_names:
            continue

        # Dedup by phone
        if phone and phone in existing_phones:
            continue

        # Dedup by domain
        domain = _norm_domain(website)
        if domain and domain in existing_domains:
            continue

        # Fuzzy name match (first 15 chars)
        nkey = _norm(name)
        is_dup = False
        if len(nkey) >= 10:
            for ex in existing_names:
                if len(ex) >= 8 and (nkey[:15] == ex[:15]):
                    is_dup = True
                    break
        if is_dup:
            continue

        # Add to seen sets
        existing_names.add(nkey)
        if phone:
            existing_phones.add(phone)
        if domain:
            existing_domains.add(domain)

        new_row = {
            "law_firm_name": name,
            "website": website,
            "google_business_profile": "",
            "legal_directory_listing": "",
            "city": city,
            "state": "OK",
            "county": county_name,
            "phone_number": phone,
            "email": email,
            "practice_area": "General Practice",
            "street_address": "",
            "zip_code": zip_code,
            "msa": msa,
            "priority": "1",
            "number_of_lawyers": "",
        }
        # Ensure all fieldnames present
        for fn in fieldnames:
            if fn not in new_row:
                new_row[fn] = ""
        new_rows.append(new_row)

    if new_rows:
        all_rows = rows + new_rows
        all_rows.sort(key=lambda r: (r.get("city", ""), r.get("law_firm_name", "")))
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(all_rows)

    return len(new_rows)


def run(target_slugs: list[str] | None = None):
    print("OKBar Find-a-Lawyer scraper starting...\n")

    # Build mapping: slug → okbar_county_name
    slug_to_okbar = {v[0]: k for k, v in COUNTY_MAP.items()}

    if target_slugs:
        counties_to_run = {k: v for k, v in COUNTY_MAP.items() if v[0] in target_slugs}
    else:
        counties_to_run = COUNTY_MAP

    session, hidden = _get_session_and_hidden()
    total_new = 0

    for okbar_county, (slug, county_name, msa) in counties_to_run.items():
        print(f"\n{okbar_county} County ({slug})...")

        try:
            raw = scrape_county(okbar_county, session, hidden)
            print(f"  Total raw records: {len(raw)}")
        except Exception as e:
            print(f"  ERROR scraping: {e}")
            # Re-initialize session and hidden fields
            try:
                session, hidden = _get_session_and_hidden()
            except Exception:
                pass
            continue

        added = merge_into_csv(slug, county_name, msa, raw)
        total_new += added
        print(f"  -> {added} new firms added to {slug}")

        # Re-init hidden from a fresh page for next county
        time.sleep(3.0)
        try:
            r = session.get(START_URL, timeout=15, verify=False)
            hidden = {inp["name"]: inp.get("value", "") for inp in BeautifulSoup(r.text, "html.parser").find_all("input", type="hidden")}
        except Exception:
            pass

    print(f"\n{'='*50}")
    print(f"Total new firms added across all counties: {total_new}")

    # Print updated counts
    print("\nUpdated counts:")
    total = 0
    for p in sorted(DATA_DIR.glob("*-county-ok.csv")):
        rows = list(csv.DictReader(p.open()))
        total += len(rows)
        print(f"  {p.stem}: {len(rows)}")
    print(f"  TOTAL OK: {total}")


if __name__ == "__main__":
    slugs = sys.argv[1:] if sys.argv[1:] else None
    run(slugs)

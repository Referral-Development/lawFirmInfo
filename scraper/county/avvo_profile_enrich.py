#!/usr/bin/env python3
"""Fill phone/website/practice_area/street from Avvo profile pages (JSON-LD) for rows whose
legal_directory_listing is an Avvo profile URL and that miss phone or website.
Usage: python -m scraper.county.avvo_profile_enrich <slug>
"""
import csv, json, re, sys, time
from pathlib import Path
from bs4 import BeautifulSoup
from curl_cffi import requests as creq

PRI = {"Personal Injury": 5, "Criminal Defense": 5, "DUI": 5, "Workers' Compensation": 5, "Medical Malpractice": 5,
       "Family Law": 4, "Divorce": 4, "General": 4, "Estate Planning": 2, "Bankruptcy": 2, "Real Estate": 2,
       "Business Law": 2, "Immigration": 2}


def main():
    slug = sys.argv[1]
    path = Path(f"app/county-data/{slug}.csv")
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    cols = list(rows[0].keys())
    s = creq.Session(impersonate="chrome120", verify=False)
    done = changed = 0
    for r in rows:
        u = r["legal_directory_listing"]
        real_site = r["website"] and not re.search(r"avvo|justia|google\.com/maps|goo\.gl|g\.page|findlaw|yelp|martindale", r["website"], re.I)
        if "avvo.com/attorneys/" not in u or (r["phone_number"] and real_site):
            continue
        for attempt in range(4):
            try:
                resp = s.get(u, timeout=30)
            except Exception:
                time.sleep(5); continue
            if resp.status_code == 429:
                time.sleep(90); continue
            break
        else:
            continue
        done += 1
        if resp.status_code != 200:
            time.sleep(3); continue
        soup = BeautifulSoup(resp.text, "lxml")
        for b in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(b.string)
            except Exception:
                continue
            if isinstance(d, dict) and d.get("@type") == "LocalBusiness":
                ph = re.sub(r"^\+1\s*", "", d.get("telephone") or "").strip()
                if ph and not r["phone_number"]:
                    r["phone_number"] = ph; changed += 1
                web = [x for x in (d.get("sameAs") or []) if x.startswith("http") and not re.search(
                    r"avvo|linkedin|facebook|twitter|instagram|youtube|google", x, re.I)]
                if web and not real_site:
                    r["website"] = web[0]; changed += 1
                a = d.get("address") or {}
                if a.get("streetAddress") and not r["street_address"]:
                    r["street_address"] = a["streetAddress"]
                ka = d.get("knowsAbout") or []
                if ka and r["practice_area"] in ("", "General"):
                    r["practice_area"] = ka[0]
        time.sleep(2.5)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)
    print(slug, "profiles fetched", done, "fields filled", changed)


if __name__ == "__main__":
    main()

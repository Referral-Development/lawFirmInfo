#!/usr/bin/env python3
"""Practice-area Google Places sweep (Places API New via places_new, cached + capped).
Usage: python -m scraper.county.places_pa_sweep <county_key> <max_cities> -> data/directory_sweep/<slug>_places_pa.json
Only searches the N largest/most-law-dense cities (first N in config order) x PA queries.
"""
import json, os, sys, re
from pathlib import Path
from dotenv import load_dotenv
from scraper.county.config import get_county_config
from scraper.utils import places_new
from scraper.county.google_places import _parse_address

PAS = ["divorce family law attorney", "personal injury attorney", "criminal defense lawyer DUI",
       "estate planning probate attorney", "bankruptcy attorney", "workers compensation attorney",
       "real estate business attorney", "immigration social security disability lawyer"]


def main():
    load_dotenv("scraper/.env")
    cfg = get_county_config(sys.argv[1]); n = int(sys.argv[2])
    client = places_new.Client(key=os.getenv("GOOGLE_MAPS_API_KEY"))
    out = {}
    cities = cfg["cities"][:n]
    for c in cities:
        for pa in PAS:
            try:
                resp = client.places(query=f"{pa} in {c} {cfg['state']}")
            except places_new.BudgetExceeded as e:
                print("BUDGET", e); break
            except Exception as e:
                print("err", e); continue
            for pl in resp.get("results", []):
                a = _parse_address(pl.get("formatted_address", ""))
                if a["state"] and a["state"] != cfg["state"]:
                    continue
                out[pl.get("place_id")] = {"name": pl.get("name", ""), "firm": pl.get("name", ""),
                    "phone": pl.get("formatted_phone_number", "") or "", "website": pl.get("website", "") or "",
                    "street": a["street"], "city": a["city"], "state": a["state"], "zip": a["zip"],
                    "source": "google_places_pa",
                    "url": __import__("scraper.utils.gbp_link", fromlist=["x"]).profile_url(pl.get('place_id'), pl.get("name", ""), a["city"], a["state"]) or f"https://www.google.com/maps/place/?q=place_id:{pl.get('place_id')}"}
        print(c, len(out), places_new.usage_summary(), flush=True)
    p = Path("data/directory_sweep"); p.mkdir(parents=True, exist_ok=True)
    json.dump(list(out.values()), open(p / f"{cfg['slug']}_places_pa.json", "w"))


if __name__ == "__main__":
    main()

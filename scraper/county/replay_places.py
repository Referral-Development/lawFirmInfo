"""Free replay of a finished county against the Places cache (PLACES_MAX_REQUESTS=0): measures recall of cheaper
query/location variants without any billing. Usage: python -m scraper.county.replay_places <county_key>"""
import os, sys, json, time
time.sleep = lambda *_: None
os.environ["PLACES_MAX_REQUESTS"] = "0"   # replay from cache only: any miss errors out, nothing is billed
from dotenv import load_dotenv; load_dotenv("scraper/.env")
import scraper.county.google_places as gp
from scraper.county.config import get_county_config
cfg = get_county_config(sys.argv[1])
res = {}
import copy
variants = {
 "full": cfg,
 "no zip searches": {**cfg, "zip_codes": []},
 "no extra terms": {**cfg, "extra_search_terms": []},
 "no zips+no extra": {**cfg, "zip_codes": [], "extra_search_terms": []},
 "zips only (big cities by zip)": {**cfg, "cities": [], "extra_search_terms": []},
}
gp.SEARCH_QUERIES = ["law firm", "attorney", "lawyer"]
from scraper.utils import places_new
import io, contextlib
for label, c2 in variants.items():
    for k in places_new.STATS: places_new.STATS[k] = 0
    with contextlib.redirect_stdout(io.StringIO()):
        firms = gp.discover_google(c2, os.getenv("GOOGLE_MAPS_API_KEY"))
    res[label] = {f["google_business_profile"] for f in firms}
base = res["full"]
for k, v in res.items():
    print(f"{k}: {len(v)} firms, recall {round(100*len(v & base)/len(base),1)}%")

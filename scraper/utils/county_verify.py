"""Verify each CSV row actually lies in the target county using the free US Census
geocoder (no API key, not Google). Caches to data/census_geocode_cache.json.

Why: city names (Kansas City, Liberty, Arnold, Pacific) straddle county lines, so
city-name matching in discovery lets other-county firms through (Clay MO run
pulled in Jackson/Platte KC firms).

Usage: python -m scraper.utils.county_verify <csv> <county_fips5>
"""
import csv, json, re, sys, time, pathlib
import requests

CACHE = pathlib.Path("data/census_geocode_cache.json")
URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"


def _load():
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def county_fips(addr: str, cache: dict):
    if addr in cache:
        return cache[addr]
    res = None
    for attempt in range(3):
        try:
            r = requests.get(URL, params={"address": addr, "benchmark": "Public_AR_Current",
                                          "vintage": "Current_Current", "layers": "Counties",
                                          "format": "json"}, timeout=30)
            m = r.json()["result"]["addressMatches"]
            if not m:
                res = ""
            else:
                c = m[0]["geographies"]["Counties"][0]
                res = c["STATE"] + c["COUNTY"]
            break
        except Exception:
            time.sleep(2)
    if res is not None:
        cache[addr] = res
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(cache))
    return res


_SUITE = re.compile(r",?\s*(suite|ste\.?|unit|bldg\.?|building|floor|fl\.?|room|rm\.?|apt\.?|#)\s*\S*.*$", re.I)


def row_addr(x):
    street = _SUITE.sub("", x['street_address'].split(",")[-1] if re.match(r"^(p\.?\s*o\.?\s*box)", x['street_address'].split(",")[0], re.I) and "," in x['street_address'] else x['street_address'])
    street = re.sub(r"^(P\.?O\.? Box \d+),\s*", "", street, flags=re.I)
    return f"{street}, {x['city']}, {x['state']} {x['zip_code']}".strip()


def classify(rows, fips):
    """returns (in_county, out_county, unknown) lists of rows"""
    cache = _load()
    ins, outs, unk = [], [], []
    for x in rows:
        if not x.get("street_address"):
            unk.append(x); continue
        f = county_fips(row_addr(x), cache)
        if f is None or f == "":
            unk.append(x)
        elif f == fips:
            ins.append(x)
        else:
            x = dict(x); x["_fips"] = f
            outs.append(x)
    return ins, outs, unk


if __name__ == "__main__":
    rows = list(csv.DictReader(open(sys.argv[1], encoding="utf-8")))
    i, o, u = classify(rows, sys.argv[2])
    print(f"in={len(i)} out={len(o)} unknown={len(u)}")

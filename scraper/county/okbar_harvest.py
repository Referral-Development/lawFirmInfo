#!/usr/bin/env python3
"""Harvest the OKBar Find-a-Lawyer directory for one OK county into merge_candidates record format.
Results cap at 200/search, so capped searches are split by last-name prefix (field ValueTextBox4).
Usage: python -m scraper.county.okbar_harvest <OKBar county name e.g. Osage> <outfile.json>"""
import json, sys, string, io, contextlib
sys.path.insert(0, ".")
import okbar_scraper as ob

class Sess:
    def __init__(self, s, extra): self.s, self.extra = s, extra
    def post(self, url, data=None, **kw):
        if data and "C_2_2$ButtonFindGo" in data: data = {**data, **self.extra}
        return self.s.post(url, data=data, **kw)
    def __getattr__(self, k): return getattr(self.s, k)

def search(county, prefix):
    s, h = ob._get_session_and_hidden()
    with contextlib.redirect_stdout(io.StringIO()):
        return ob.scrape_county(county, Sess(s, {"C_2_2$ValueTextBox4": prefix}), h)

def rec(r):
    return {"name": r["attorney_name"], "firm": r["law_firm_name"], "phone": r["phone"], "street": "",
            "city": r["city"], "state": "OK", "zip": r["zip_code"], "website": r["website"],
            "email": r["email"], "source": "OKBar"}

def walk(county, prefix, out):
    recs = search(county, prefix)
    if len(recs) >= 195 and len(prefix) < 3:
        for ch in string.ascii_uppercase: walk(county, prefix + ch, out)
    else:
        if len(recs) >= 195: print("WARN capped at", prefix, file=sys.stderr)
        out.extend(recs)

def main():
    county, out = sys.argv[1], sys.argv[2]
    allr = []; walk(county, "", allr)
    seen, rows = set(), []
    for r in allr:
        k = (r["attorney_name"], r["phone"], r["city"])
        if k not in seen: seen.add(k); rows.append(rec(r))
    json.dump(rows, open(out, "w"), indent=1)
    print(county, len(rows))
main()

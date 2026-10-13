#!/usr/bin/env python3
"""Merge directory-sweep / MoBar candidate records into a county CSV.

Dedup vs existing rows (phone, address+token, firm-name token overlap, attorney surname +
same ZIP), drop non-law entities, then verify the county of every candidate via the
Census geocoder (scraper.utils.county_verify). Writes accepted rows to the CSV and a
review file of near-duplicates / unknowns to data/directory_sweep/<slug>_review.json.

Usage: python -m scraper.county.merge_candidates <county_key> <fips5> <records.json> [<records2.json> ...]
Records: list of {name, firm, phone, street, city, state, zip, website, email, source}
"""
import csv, json, re, sys
from pathlib import Path

from scraper.county.config import get_county_config
from scraper.utils.entity_filters import is_non_law
from scraper.utils.county_verify import classify

STOP = {"law", "laws", "office", "offices", "firm", "llc", "pc", "p", "c", "lc", "llp", "the", "of", "at", "and",
        "attorney", "attorneys", "lawyer", "lawyers", "group", "associates", "inc", "legal", "a", "counsel",
        "counselor", "dba", "pllc", "services", "chtd", "pa", "mo", "co", "company", "jr", "sr", "ii", "iii", "iv"}
LAWKW = re.compile(r"\blaw\b|attorney|lawyer|legal|counsel|esq|\bP\.?C\.?\b|LLP|L\.?C\.?\b|firm|&|associates|"
                   r"[A-Z][a-z]+,\s*[A-Z]", re.I)
BAD = re.compile(r"insurance|bank|mortgage|loan|finance|financial|tax\b|account|realty|real estate (?:sales|agent)|bail|"
                 r"title co|wealth|notary|check|state of|church|\bcpa\b|retired|not practicing|none|n/a|"
                 r"university|school|hospital|county of|city of|dept|department|court\b|prosecut|public defender|"
                 r"attorney general|sheriff|police|prosecuting|public administrator|legal ?shield|investigat|"
                 r"municipal|bureau|commission|board of|federal|u\.s\.|united states|national guard|army|navy",
                 re.I)
d10 = lambda p: re.sub(r"\D", "", p or "")[-10:]


def toks(n):
    return {t for t in re.findall(r"[a-z]+", (n or "").lower()) if t not in STOP and len(t) > 1}


def st(a):
    return re.sub(r"[^a-z0-9]", "", (a or "").lower().split("#")[0].split(",")[0])[:12]


def surnames(n):
    # "Last, First M." -> last ; else all tokens
    if "," in n:
        return {re.sub(r"[^a-z]", "", n.split(",")[0].lower())}
    return toks(n)


def main():
    key, fips, files = sys.argv[1], sys.argv[2], sys.argv[3:]
    cfg = get_county_config(key)
    slug = cfg["slug"]
    path = Path(f"app/county-data/{slug}.csv")
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    cols = list(rows[0].keys())
    recs = []
    for fpath in files:
        raw = json.load(open(fpath))
        if isinstance(raw, dict):
            for src, lst in raw.items():
                for r in lst:
                    r.setdefault("source", src); recs.append(r)
        else:
            recs += raw
    # cheap pre-filter before geocoding: ZIP must belong to the county (cfg zips + zips seen in the MoBar directory)
    zset = set(cfg.get("zip_codes", []))
    sib = {"64079", "64078", "64102", "64105", "64106", "64108", "64110", "64111", "64112", "64114", "64127",
           "64130", "64131", "64150", "64151", "64152", "64153", "64154"}
    try:
        md = json.load(open("data/mobar_dir.json"))
        pre = key.split("_")[0]
        zset |= {k.split(":")[1] for k, v in md.items() if k.split(":")[0] == pre and len(k.split(":")) == 2 and v["rows"]}
    except Exception:
        pass
    zset -= sib
    recs = [r for r in recs if (r.get("zip") in zset) or (not r.get("zip") and (r.get("city") or "").lower() in
            {c.lower() for c in cfg["cities"]} and (r.get("city") or "").lower() != "kansas city")]
    print("records after zip pre-filter:", len(recs), flush=True)
    ph = {d10(r["phone_number"]) for r in rows if r["phone_number"]}
    rt = [(toks(r["law_firm_name"]), st(r["street_address"]), r["zip_code"], r["law_firm_name"].lower()) for r in rows]
    seen, cand, review = set(), [], []
    for x in recs:
        firm = (x.get("firm") or x.get("name") or "").strip()
        name = (x.get("name") or "").strip()
        if not firm or BAD.search(firm) or is_non_law(firm) or (x.get("state") and x["state"] != cfg["state"]):
            continue
        if x.get("source") in ("yellowpages", "findlaw", "google_places_pa") and not LAWKW.search(firm):
            continue
        # a firm label that is just a person's "Last, First" with no law keywords is OK (solo practitioner)
        p = d10(x.get("phone"))
        if p and p in ph:
            continue
        t = toks(firm); x["street"] = x.get("street") or x.get("address") or ""; a = st(x.get("street")); z = x.get("zip", "")
        dup = False
        for rtk, ra, rz, rname in rt:
            if t and rtk and (t <= rtk or rtk <= t or len(t & rtk) / len(t | rtk) >= .6):
                dup = True; break
            if a and a == ra and (t & rtk or (surnames(name) & rtk)):
                dup = True; break
            if name and "," in name and z and z == rz and (surnames(name) & rtk):
                dup = True; break
        k2 = (frozenset(t), p or a)
        if dup or k2 in seen:
            continue
        seen.add(k2)
        cand.append((x, firm))
    new = []
    for x, firm in cand:
        new.append({"law_firm_name": firm, "website": x.get("website") or "",
                    "google_business_profile": "", "legal_directory_listing": x.get("url") or "",
                    "city": x.get("city", ""), "state": cfg["state"], "county": cfg["name"].replace(" County", ""),
                    "phone_number": x.get("phone", ""), "email": x.get("email", ""),
                    "practice_area": "General", "street_address": x.get("street", ""),
                    "zip_code": x.get("zip", ""), "msa": cfg["msa"], "priority": "4",
                    "_src": x.get("source", "")})
    ins, outs, unk = classify(new, fips)
    # unknown (no street / no geocode): accept only if ZIP is in the county's zip list
    zips = set(cfg.get("zip_codes", []))
    unk_ok = [u for u in unk if u["zip_code"] in zips and u["zip_code"] not in
              {"64079", "64078", "64102", "64105", "64106", "64108", "64110", "64111", "64112", "64114", "64127", "64130", "64131"}]
    unk_rej = [u for u in unk if u not in unk_ok]
    add = ins + unk_ok
    out = [{k: x.get(k, "") for k in cols} for x in add]
    rows += out
    rows.sort(key=lambda r: r["law_firm_name"].lower())
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)
    Path("data/directory_sweep").mkdir(parents=True, exist_ok=True)
    json.dump({"out_of_county": len(outs), "unknown_rejected": unk_rej}, open(f"data/directory_sweep/{slug}_review.json", "w"))
    by = {}
    for x in add:
        by[x["_src"]] = by.get(x["_src"], 0) + 1
    print(f"{slug}: candidates {len(new)} -> in-county {len(ins)} + zip-ok unknown {len(unk_ok)}; "
          f"out-of-county {len(outs)}, unknown rejected {len(unk_rej)}; added by source {by}; total rows {len(rows)}")


if __name__ == "__main__":
    main()

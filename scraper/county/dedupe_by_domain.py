#!/usr/bin/env python3
"""Collapse attorneys listed individually under the same firm website.
Within a county CSV, group rows by real-website domain (not directory/GBP). In a group:
  * person-named rows (no firm keyword) are dropped when the group has a firm-named row (they're its attorneys);
  * if the group has no firm-named row, keep the richest person row and rename it from NAME_OVERRIDES if known.
  * firm-named rows at the same normalized street address with the same domain are merged (keep the richest).
Removed rows are logged to data/directory_sweep/<slug>_domain_merged.csv.
Usage: python -m scraper.county.dedupe_by_domain <slug> [...]
"""
import csv, re, sys
from urllib.parse import urlsplit

LAWKW = re.compile(r"\blaw\b|attorneys?\b|lawyers?\b|legal|counsel|\bP\.?C\.?\b|LLP|\bL\.?C\.?\b|firm|&|associates|"
                   r"\bPLLC\b|\bLLC\b|group|partners|\band\b|company|\bco\b", re.I)
SKIP = re.compile(r"google|avvo|justia|yellowpages|findlaw|martindale|yelp|facebook|mapquest", re.I)
NAME_OVERRIDES = {"kutakrock.com": "Kutak Rock LLP"}
st = lambda a: re.sub(r"[^a-z0-9]", "", (a or "").lower().split("#")[0].split(",")[0])[:14]
rich = lambda r: sum(bool(r[k]) for k in ("phone_number", "street_address", "email", "practice_area")) + (r["practice_area"] not in ("", "General"))


def main():
    for slug in sys.argv[1:]:
        path = f"app/county-data/{slug}.csv"
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
        cols = list(rows[0].keys())
        groups = {}
        for i, r in enumerate(rows):
            h = urlsplit(r["website"]).netloc.lower().replace("www.", "")
            if h and not SKIP.search(h):
                groups.setdefault(h, []).append(i)
        drop, log = set(), []
        for h, idx in groups.items():
            if len(idx) < 2:
                continue
            firms = [i for i in idx if LAWKW.search(rows[i]["law_firm_name"])]
            persons = [i for i in idx if i not in firms]
            if firms:
                for i in persons:
                    drop.add(i); log.append((i, f"attorney at {rows[firms[0]]['law_firm_name']} ({h})"))
                byaddr = {}
                for i in firms:
                    byaddr.setdefault(st(rows[i]["street_address"]), []).append(i)
                for a, g in byaddr.items():
                    if a and len(g) > 1:
                        g.sort(key=lambda i: rich(rows[i]), reverse=True)
                        for i in g[1:]:
                            drop.add(i); log.append((i, f"same site+address as {rows[g[0]]['law_firm_name']}"))
            else:
                persons.sort(key=lambda i: rich(rows[i]), reverse=True)
                keep = persons[0]
                if h in NAME_OVERRIDES:
                    rows[keep]["law_firm_name"] = NAME_OVERRIDES[h]
                for i in persons[1:]:
                    drop.add(i); log.append((i, f"attorney at same firm site ({h}), kept {rows[keep]['law_firm_name']}"))
        out = [r for i, r in enumerate(rows) if i not in drop]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(out)
        with open(f"data/directory_sweep/{slug}_domain_merged.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(cols + ["reason"])
            for i, why in log:
                w.writerow([rows[i][c] for c in cols] + [why])
        print(slug, len(rows), "->", len(out), f"({len(log)} merged)")


if __name__ == "__main__":
    main()

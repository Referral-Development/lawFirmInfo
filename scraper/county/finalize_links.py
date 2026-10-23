#!/usr/bin/env python3
"""Final link/website normalization + stats for county CSVs.
Website column: real website > GBP link > legal directory link (Aidan's rule, 2026-10-09). Strips tracking params.
Usage: python -m scraper.county.finalize_links <slug> [<slug> ...]
"""
import csv, re, sys
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from scraper.utils.dedup_candidates import find_duplicate_candidates

GBP = re.compile(r'google\.com/maps|maps\.google\.com|maps\.app\.goo\.gl|g\.page|goo\.gl/maps')
DIR = re.compile(r'avvo|justia|martindale|findlaw|lawyers\.com|yelp|superlawyers|nolo|allbiz|yellowpages|facebook|mapquest|bbb\.org', re.I)


def clean(u):
    if not u:
        return u
    p = urlsplit(u)
    q = [(k, v) for k, v in parse_qsl(p.query) if not (k.lower().startswith(('utm_', 'npcmp')) or k in ('gclid', 'fbclid', 'y_source'))]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ''))


def main():
    for slug in sys.argv[1:]:
        path = f"app/county-data/{slug}.csv"
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
        cols = list(rows[0].keys())
        for x in rows:
            w = clean(x['website'])
            if w and (GBP.search(w) or DIR.search(w)):
                if DIR.search(w) and not x['legal_directory_listing']:
                    x['legal_directory_listing'] = w
                w = ""
            x['website'] = w or x['google_business_profile'] or x['legal_directory_listing']
        rows.sort(key=lambda r: r['law_firm_name'].lower())
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)
        n = len(rows)
        g = sum(1 for x in rows if GBP.search(x['website']))
        d = sum(1 for x in rows if x['website'] and DIR.search(x['website']) and not GBP.search(x['website']))
        z = sum(1 for x in rows if not x['website'])
        a = n - g - d - z
        p = lambda k: sum(1 for x in rows if x[k])
        print(f"{slug} {n}: actual {a} ({100*a/n:.0f}%) gbp {g} directory {d} none {z} "
              f"phone {p('phone_number')} ({100*p('phone_number')/n:.0f}%) email {p('email')} ({100*p('email')/n:.0f}%) "
              f"dupgroups {len(find_duplicate_candidates(rows))}")


if __name__ == "__main__":
    main()

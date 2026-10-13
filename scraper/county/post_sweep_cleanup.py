#!/usr/bin/env python3
"""Cleanup of rows added by directory sweeps: person-name rows without contact info, duplicates of the
same person across sources, attorneys sharing an existing firm's address, university/hospital/dental etc.
Usage: python -m scraper.county.post_sweep_cleanup <slug> <pre_sweep_csv>
Rows removed are written to data/directory_sweep/<slug>_removed.csv (nothing is silently lost).
"""
import csv, re, sys
from pathlib import Path

LAWKW = re.compile(r"\blaw\b|attorney|lawyer|legal|counsel|esq|\bP\.?C\.?\b|LLP|\bL\.?C\.?\b|firm|&|associates|"
                   r"\bPLLC\b|\bLLC\b|group|partners", re.I)
BAD2 = re.compile(r"\bdds\b|\bdmd\b|dental|dentist|clinic|medical|hospital|\bhall\b|university|college|school|"
                  r"national lawyers association|lawyers association|legal ?shield|\bmd\b|chiropract|"
                  r"realty|insurance|\bcpa\b|accounting|bail bond|bank\b|credit union|church|county|court\b|"
                  r"prosecut|public defender|sheriff|police|city of|state of", re.I)
d10 = lambda p: re.sub(r"\D", "", p or "")[-10:]
norm = lambda n: re.sub(r"[^a-z]", "", re.sub(r"\b(jr|sr|ii|iii|iv|mr|mrs|ms|attorney|at law|esq)\b\.?", "", n.lower()))
st = lambda a, z: re.sub(r"[^a-z0-9]", "", (a or "").lower().split("#")[0])[:14] + "|" + (z or "")


def main():
    slug, pre = sys.argv[1], sys.argv[2]
    path = Path(f"app/county-data/{slug}.csv")
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    cols = list(rows[0].keys())
    old_names = {r["law_firm_name"] for r in csv.DictReader(open(pre, encoding="utf-8"))}
    old = [r for r in rows if r["law_firm_name"] in old_names]
    new = [r for r in rows if r["law_firm_name"] not in old_names]
    old_addr = {st(r["street_address"], r["zip_code"]): r["law_firm_name"] for r in old if r["street_address"]}
    old_phone = {d10(r["phone_number"]): r["law_firm_name"] for r in old if r["phone_number"]}
    removed, keep, seen = [], [], {}
    # merge duplicates of the same name among new rows (keep the richest)
    score = lambda r: sum(bool(r[k]) for k in ("phone_number", "street_address", "website", "email"))
    for r in sorted(new, key=score, reverse=True):
        k = norm(r["law_firm_name"])
        if k in seen:
            for f in ("phone_number", "street_address", "website", "email"):
                if not seen[k][f] and r[f]:
                    seen[k][f] = r[f]
            removed.append((r, "duplicate name in new rows")); continue
        seen[k] = r
    for r in seen.values():
        n, s = r["law_firm_name"], r["street_address"]
        person = not LAWKW.search(n)
        if BAD2.search(n) or BAD2.search(s):
            removed.append((r, "non-law (dental/university/hospital/gov/etc.)")); continue
        if d10(r["phone_number"]) and d10(r["phone_number"]) in old_phone:
            removed.append((r, f"same phone as existing '{old_phone[d10(r['phone_number'])]}'")); continue
        if s and st(s, r["zip_code"]) in old_addr and person:
            removed.append((r, f"individual at existing firm address '{old_addr[st(s, r['zip_code'])]}'")); continue
        if person and not r["phone_number"] and not s:
            removed.append((r, "individual attorney, no contact info/firm resolvable (unresolved)")); continue
        keep.append(r)
    final = sorted(old + keep, key=lambda r: r["law_firm_name"].lower())
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(final)
    out = Path(f"data/directory_sweep/{slug}_removed.csv")
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(cols + ["reason"])
        for r, why in removed:
            w.writerow([r[c] for c in cols] + [why])
    import collections
    print(slug, "old", len(old), "new", len(new), "kept new", len(keep), "-> total", len(final),
          dict(collections.Counter(w.split(" '")[0] for _, w in removed)))


if __name__ == "__main__":
    main()

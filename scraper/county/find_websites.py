#!/usr/bin/env python3
"""Find real websites for rows that only have a directory/GBP/blank link, via Bing + page verification.
A candidate is accepted only if the fetched page contains the row's phone digits (any common format) or the
street number + first street word AND the city. Writes found sites to `website`; logs every acceptance.
Usage: python -m scraper.county.find_websites <slug>
"""
import base64, csv, re, sys, time
from urllib.parse import urlsplit, parse_qs, quote_plus
from bs4 import BeautifulSoup
from curl_cffi import requests as creq

SKIP = re.compile(r"avvo|justia|martindale|findlaw|lawyers\.com|yelp|superlawyers|nolo|allbiz|yellowpages|facebook|"
                  r"mapquest|bbb\.org|linkedin|twitter|instagram|youtube|google|bing\.|wikipedia|indeed|glassdoor|"
                  r"zoominfo|manta|chamberofcommerce|yelp|expertise|lawyer\.com|hg\.org|lawinfo|birdeye|"
                  r"buzzfile|dnb\.com|opencorporates|bizapedia|mylife|spokeo|whitepages|usnews|superpages|"
                  r"cylex|nextdoor|pinterest|tiktok|mo\.gov|courts\.mo|mobar|casetext|leagle|vlex|fastcase|"
                  r"clustrdata|lawyerslegion|lawyerratingz|attorneyatlaw|attorneypages|lawrato|attorneysmo|"
                  r"patch\.com|mapquest|foursquare|waze|apple\.com|uslegal", re.I)
d10 = lambda p: re.sub(r"\D", "", p or "")[-10:]


def decode(href):
    try:
        q = parse_qs(urlsplit(href).query).get("u", [""])[0]
        if q.startswith("a1"):
            b = q[2:]
            b += "=" * (-len(b) % 4)
            return base64.urlsafe_b64decode(b).decode()
    except Exception:
        pass
    return href if not href.startswith("https://www.bing.com") else ""


def verify(s, url, row):
    try:
        r = s.get(url, timeout=20)
    except Exception:
        return False
    if r.status_code != 200:
        return False
    soup = BeautifulSoup(r.text, "lxml")
    txt = re.sub(r"\s+", " ", soup.get_text(" ")).lower()
    ph = d10(row["phone_number"])
    ok_phone = False
    if ph:
        pat = r"\(?%s\)?[\s.\-]*%s[\s.\-]*%s" % (ph[:3], ph[3:6], ph[6:])
        ok_phone = re.search(pat, txt) is not None
    st = row["street_address"].lower()
    m = re.match(r"(\d+)\s+(\w+)", st)
    ok_addr = bool(m and re.search(r"\b%s\s+\w*\s*%s" % (re.escape(m.group(1)), re.escape(m.group(2))), txt) and row["city"].lower() in txt)
    name_toks = [t for t in re.findall(r"[a-z]{4,}", row["law_firm_name"].lower()) if t not in
                 {"law", "firm", "office", "offices", "attorney", "attorneys", "group", "legal", "associates"}]
    title = (soup.title.get_text().lower() if soup.title else "")
    ok_name = any(t in title or t in txt[:3000] or t in urlsplit(url).netloc for t in name_toks)
    return (ok_phone or ok_addr) and ok_name


def main():
    slug = sys.argv[1]
    path = f"app/county-data/{slug}.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    cols = list(rows[0].keys())
    s = creq.Session(impersonate="chrome120", verify=False)
    tried = found = 0
    for r in rows:
        w = r["website"]
        if w and not re.search(r"avvo|justia|google\.com/maps|maps\.google\.com|goo\.gl|g\.page|findlaw|yelp|martindale|yellowpages", w, re.I):
            continue
        q = f'"{r["law_firm_name"]}" {r["city"]} MO attorney {r["phone_number"]}'.strip()
        try:
            resp = s.get("https://www.bing.com/search?q=" + quote_plus(q), timeout=25)
        except Exception:
            continue
        if resp.status_code != 200:
            time.sleep(10); continue
        tried += 1
        soup = BeautifulSoup(resp.text, "lxml")
        cands = []
        for a in soup.select("li.b_algo h2 a"):
            u = decode(a.get("href", ""))
            if u.startswith("http") and not SKIP.search(urlsplit(u).netloc):
                cands.append(u)
        for u in cands[:3]:
            root = f"{urlsplit(u).scheme}://{urlsplit(u).netloc}/"
            if verify(s, u, r):
                r["website"] = root; found += 1
                print(f"  FOUND {r['law_firm_name']} -> {root}", flush=True)
                break
        time.sleep(1.5)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)
    print(slug, "searched", tried, "websites found", found)


if __name__ == "__main__":
    main()

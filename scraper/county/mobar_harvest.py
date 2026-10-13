#!/usr/bin/env python3
"""Harvest Missouri Bar LawyerSearch (opt-in public directory) by county via ASP.NET postbacks.
Usage: python -m scraper.county.mobar_harvest <county_value> <outfile>   e.g. 010 data/mobar_boone.json
Output: list of {name, firm, address, city, state, zip, phone, email, website, counties}
"""
import json, re, sys, time
import requests
from bs4 import BeautifulSoup

URL = "https://mobar.org/public/LawyerSearch.aspx"
GRID = "ctl00$store$GridView1"


def hidden(soup):
    return {i["name"]: i.get("value", "") for i in soup.find_all("input", type="hidden") if i.get("name")}


LABELS = ["Firm", "Date Admitted", "Business Office In", "Address", "Phone Number", "Fax Number",
          "Email Address", "Website", "Practice Areas", "Counties of Practice"]


def _cf_decode(h):
    k = int(h[:2], 16)
    return "".join(chr(int(h[i:i + 2], 16) ^ k) for i in range(2, len(h), 2))


def parse_detail(soup):
    email = ""
    cf = soup.find(attrs={"data-cfemail": True})
    if cf:
        try:
            email = _cf_decode(cf["data-cfemail"])
        except Exception:
            pass
    t = soup.get_text("\n", strip=True)
    end = t.find("Missouri Lawyers are not certified")
    t = t[:end] if end > 0 else t
    k = t.find("Date Admitted")
    head = [l.strip() for l in t[:k].split("\n") if l.strip()]
    if "Firm:" in head:
        fi = len(head) - 1 - head[::-1].index("Firm:")
        name = head[fi - 1]
        body = "\n".join(head[fi:]) + "\n" + t[k:]
    else:
        name = head[-1] if head else ""
        body = t[k:]
    lines = [l.strip() for l in body.split("\n") if l.strip()]
    vals, cur = {}, None
    for l in lines:
        lab = l[:-1] if l.endswith(":") else None
        if lab in LABELS:
            cur = lab; vals[cur] = []
        elif cur:
            vals[cur].append(l)
    g = lambda x: " ".join(vals.get(x, []))
    email = "" if email.lower().endswith("@mobar.org") else email
    d = {"name": name, "firm": g("Firm"), "phone": g("Phone Number"), "email": email,
         "website": g("Website"), "business_office": g("Business Office In")}
    d["phone"] = "" if d["phone"].startswith("None") else d["phone"]
    d["website"] = "" if d["website"].startswith("None") else d["website"]
    addr = vals.get("Address", [])
    d.update(address="", city="", state="", zip="")
    if addr:
        d["address"] = ", ".join(addr[:-1]) if len(addr) > 1 else ""
        m = re.match(r"^(.*?),\s*([A-Z]{2}),?\s*(\d{5})?", addr[-1].replace("\xa0", " "))
        if m:
            d["city"], d["state"], d["zip"] = m.group(1).strip(), m.group(2), m.group(3) or ""
    d["counties"] = ",".join(vals.get("Counties of Practice", []))
    return d


def main():
    county, out = sys.argv[1], sys.argv[2]
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
    r = s.get(URL, timeout=30)
    soup = BeautifulSoup(r.text, "lxml")
    form = hidden(soup)
    form.update({"ctl00$store$ddlPractice": "", "ctl00$store$ddlLang": "", "ctl00$store$ddlCounty": county,
                 "ctl00$store$btnSearch": "Search"})
    # default option values for the other dropdowns
    for sel in ("ctl00$store$ddlPractice", "ctl00$store$ddlLang"):
        el = soup.find("select", {"name": sel})
        form[sel] = el.find("option")["value"] if el else ""
    r = s.post(URL, data=form, timeout=60)
    soup = BeautifulSoup(r.text, "lxml")
    n = len(soup.find_all("a", href=re.compile(r"Select\$\d+")))
    print("results", n, flush=True)
    res = []
    for i in range(n):
        form = hidden(soup)
        form["__EVENTTARGET"], form["__EVENTARGUMENT"] = GRID, f"Select${i}"
        rr = None
        for _ in range(3):
            try:
                rr = s.post(URL, data=form, timeout=60); break
            except Exception:
                time.sleep(3)
        if rr is None:
            continue
        s2 = BeautifulSoup(rr.text, "lxml")
        d = parse_detail(s2)
        res.append(d)
        if i % 25 == 0:
            print(i, d.get("name"), d.get("firm"), d.get("city"), flush=True)
            json.dump(res, open(out, "w"))
        time.sleep(0.4)
    json.dump(res, open(out, "w"))
    print("DONE", len(res))


if __name__ == "__main__":
    main()

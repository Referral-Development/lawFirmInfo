#!/usr/bin/env python3
"""Generic (any-state) legal-directory discovery sweep for a county in config.py.

Sources: Justia (county page, city pages, practice-area x city), Avvo (city
pages, JSON-LD), YellowPages (city attorneys pages), FindLaw (city pages).
Writes raw records to data/directory_sweep/<slug>_raw.json. It does NOT touch
the county CSV — merging/dedup/county verification is a separate step
(see merge_directory_sweep.py).

Usage: python -m scraper.county.directory_sweep <county_key> [--no-pa]
"""
import json, re, sys, time
from pathlib import Path
from bs4 import BeautifulSoup
from curl_cffi import requests as creq

from scraper.county.config import get_county_config

OUT = Path("data/directory_sweep")
PRACTICE_AREAS = [
    'agricultural-law','animal-dog-law','antitrust-law','appeals-appellate','arbitration-mediation',
    'asbestos-mesothelioma','bankruptcy','business-law','civil-rights','collections','construction-law',
    'consumer-law','criminal-law','divorce','domestic-violence','dui-dwi','education-law','elder-law',
    'employment-law','energy-oil-gas-law','environmental-law','estate-planning','family-law',
    'foreclosure-defense','government-administrative-law','health-care-law','immigration-law',
    'insurance-claims','insurance-defense','intellectual-property','juvenile-law','landlord-tenant',
    'legal-malpractice','medical-malpractice','military-law','municipal-law','nursing-home-abuse',
    'patents','personal-injury','probate','products-liability','real-estate-law','securities-law',
    'social-security-disability','tax-law','trademarks','traffic-tickets','white-collar-crime',
    'workers-compensation',
]
STATE_NAMES = {"MO": "missouri", "KS": "kansas", "TX": "texas", "OK": "oklahoma"}


def sess():
    return creq.Session(impersonate="chrome120", verify=False)


def get(s, url, delay=0.8, tries=3):
    for _ in range(tries):
        try:
            r = s.get(url, timeout=25)
        except Exception:
            time.sleep(3); continue
        if r.status_code == 429:
            time.sleep(75); continue
        time.sleep(delay)
        return r
    return None


def slug(c):
    return re.sub(r"[^a-z0-9]+", "-", c.lower().replace("'", "")).strip("-")


# ---------------- Justia ----------------
def _jaddr(card):
    el = card.find("div", class_=re.compile("address"))
    if not el:
        return "", "", "", ""
    parts = [p.strip() for p in el.get_text("|", strip=True).split("|") if p.strip()]
    street = parts[0] if len(parts) >= 2 else ""
    m = re.match(r"^(.+?),\s*([A-Z]{2})\s*(\d{5})?", parts[-1]) if parts else None
    return (street, m.group(1).strip(), m.group(2), m.group(3) or "") if m else (street, "", "", "")


def justia_cards(html, state):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for card in soup.find_all("div", class_=re.compile(r"jld-card")):
        nm = card.find("strong", class_=re.compile("name"))
        if not nm:
            continue
        a = nm.find("a")
        name = (a.get_text(strip=True) if a else nm.get_text(strip=True))
        street, city, st, z = _jaddr(card)
        if st and st != state:
            continue
        ph = ""
        pe = card.find("strong", class_="phone")
        if pe and pe.find("a", href=re.compile("^tel:")):
            ph = pe.find("a", href=re.compile("^tel:")).get_text(strip=True)
        web = ""
        for l in card.find_all("a", href=True):
            if l.get("data-button-tag") == "website" and l["href"].startswith("http") and "justia.com" not in l["href"]:
                web = l["href"]; break
        prof = card.find("a", href=re.compile(r"justia\.com/lawyer/"))
        out.append({"name": name, "firm": name, "phone": ph, "street": street, "city": city,
                    "state": st or state, "zip": z, "website": web, "source": "justia",
                    "url": prof["href"] if prof else ""})
    return out


def paged(s, url, parse, maxpages=40):
    res, seen_sig = [], set()
    for p in range(1, maxpages + 1):
        u = url if p == 1 else f"{url}{'&' if '?' in url else '?'}page={p}"
        r = get(s, u)
        if r is None or r.status_code != 200:
            break
        items = parse(r.text)
        sig = tuple(i["name"] for i in items[:5])
        if not items or sig in seen_sig:
            break
        seen_sig.add(sig)
        res.extend(items)
    return res


def sweep_justia(s, cfg, do_pa=True):
    st, sn = cfg["state"], STATE_NAMES[cfg["state"]]
    res = []
    parse = lambda h: justia_cards(h, st)
    res += paged(s, f"https://www.justia.com/lawyers/{sn}/{slug(cfg['name'].replace(' County',''))}-county", parse)
    for c in cfg["cities"]:
        cs = slug(c)
        res += paged(s, f"https://www.justia.com/lawyers/{sn}/{cs}", parse)
        if do_pa:
            for pa in PRACTICE_AREAS:
                r = get(s, f"https://www.justia.com/lawyers/{pa}/{sn}/{cs}", delay=0.4)
                if r is None or r.status_code != 200:
                    continue
                items = justia_cards(r.text, st)
                for p in range(2, 8):
                    if len(items) < 15 * (p - 1):
                        break
                    rr = get(s, f"https://www.justia.com/lawyers/{pa}/{sn}/{cs}?page={p}", delay=0.4)
                    if rr is None or rr.status_code != 200:
                        break
                    more = justia_cards(rr.text, st)
                    if not more or more[0]["name"] == items[0]["name"]:
                        break
                    items += more
                for i in items:
                    i["pa"] = pa
                res += items
        print(f"  justia {c}: cumulative {len(res)}", flush=True)
    return res


# ---------------- Avvo ----------------
def avvo_parse(html):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for b in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(b.string)
        except Exception:
            continue
        for d in (d if isinstance(d, list) else [d]):
            if isinstance(d, dict) and d.get("@type") == "Person" and d.get("name"):
                w = d.get("worksFor", {}) or {}
                ad = w.get("address", {}) or {}
                out.append({"name": d["name"], "firm": w.get("name") or d["name"],
                            "phone": w.get("telephone") or "", "street": ad.get("streetAddress") or "",
                            "city": ad.get("addressLocality") or "", "state": ad.get("addressRegion") or "",
                            "zip": ad.get("postalCode") or "", "website": "", "source": "avvo",
                            "url": d.get("url", "")})
    return out


def sweep_avvo(s, cfg):
    st = cfg["state"]
    res = []
    for c in cfg["cities"]:
        url = f"https://www.avvo.com/all-lawyers/{st.lower()}/{slug(c).replace('-', '_')}.html"
        items = paged(s, url, avvo_parse, maxpages=60)
        res += [i for i in items if not i["state"] or i["state"] == st]
        print(f"  avvo {c}: cumulative {len(res)}", flush=True)
    return res


# ---------------- YellowPages ----------------
def yp_parse(html):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for card in soup.find_all(class_=re.compile(r"\bv-card\b")):
        n = card.find("a", class_="business-name")
        if not n:
            continue
        loc = card.find(class_="locality")
        m = re.match(r"^(.+?),\s*([A-Z]{2})\s*(\d{5})?", loc.get_text(strip=True)) if loc else None
        st = card.find(class_="street-address")
        ph = card.find(class_="phone")
        w = card.find("a", class_=re.compile("website"), href=re.compile("^http"))
        out.append({"name": n.get_text(strip=True), "firm": n.get_text(strip=True),
                    "phone": ph.get_text(strip=True) if ph else "", "street": st.get_text(strip=True) if st else "",
                    "city": m.group(1).strip().rstrip(",") if m else "", "state": m.group(2) if m else "",
                    "zip": (m.group(3) or "") if m else "", "website": w["href"] if w else "",
                    "source": "yellowpages", "url": ""})
    return out


def sweep_yp(s, cfg):
    st = cfg["state"]
    res = []
    for c in cfg["cities"]:
        for term in ("attorneys", "lawyers", "law-firms"):
            items = paged(s, f"https://www.yellowpages.com/{slug(c)}-{st.lower()}/{term}", yp_parse, maxpages=8)
            res += [i for i in items if not i["state"] or i["state"] == st]
        print(f"  yp {c}: cumulative {len(res)}", flush=True)
    return res


# ---------------- FindLaw ----------------
def fl_parse(html):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for card in soup.find_all(["div", "li"], class_=re.compile(r"(result|card|listing)", re.I)):
        n = card.find(["h2", "h3", "a"], class_=re.compile(r"(name|title|heading)", re.I)) or card.find(["h2", "h3"])
        if not n:
            continue
        name = n.get_text(strip=True)
        txt = card.get_text("|", strip=True)
        m = re.search(r"([^|]+?),\s*([A-Z]{2})\s*(\d{5})", txt)
        ph = re.search(r"\(?\d{3}\)?[ -]\d{3}-\d{4}", txt)
        if not (name and m):
            continue
        out.append({"name": name, "firm": name, "phone": ph.group(0) if ph else "", "street": "",
                    "city": m.group(1).strip().split("|")[-1], "state": m.group(2), "zip": m.group(3),
                    "website": "", "source": "findlaw", "url": ""})
    return out


def sweep_findlaw(s, cfg):
    st, sn = cfg["state"], STATE_NAMES[cfg["state"]]
    res = []
    for c in cfg["cities"]:
        items = paged(s, f"https://lawyers.findlaw.com/{sn}/{slug(c)}/", fl_parse, maxpages=10)
        res += [i for i in items if i["state"] == st]
    print(f"  findlaw: {len(res)}", flush=True)
    return res


def main():
    key = sys.argv[1]
    do_pa = "--no-pa" not in sys.argv
    cfg = get_county_config(key)
    OUT.mkdir(parents=True, exist_ok=True)
    s = sess()
    allrec = {}
    for name, fn in (("avvo", lambda: sweep_avvo(s, cfg)), ("yellowpages", lambda: sweep_yp(s, cfg)),
                     ("findlaw", lambda: sweep_findlaw(s, cfg)),
                     ("justia", lambda: sweep_justia(s, cfg, do_pa))):
        try:
            allrec[name] = fn()
        except Exception as e:
            print(f"[{name}] FAILED: {e}", flush=True)
            allrec[name] = []
        print(f"[{name}] {len(allrec[name])} raw records", flush=True)
        json.dump(allrec, open(OUT / f"{cfg['slug']}_raw.json", "w"))
    print("DONE", {k: len(v) for k, v in allrec.items()})


if __name__ == "__main__":
    main()

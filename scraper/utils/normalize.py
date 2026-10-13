import json
import os
import re
from urllib.parse import urlparse
from rapidfuzz import process, fuzz

CANONICAL_PRACTICE_AREAS = [
    "Personal Injury", "Family Law", "Criminal Defense", "Estate Planning",
    "Probate", "Workers' Compensation", "Bankruptcy", "Business Law",
    "Real Estate", "Immigration", "Employment Law", "Medical Malpractice",
    "Insurance Defense", "Social Security Disability", "Civil Litigation",
    "Tax Law", "Intellectual Property", "Environmental Law", "Elder Law",
    "DUI", "Divorce", "Child Custody", "Wrongful Death", "Civil Rights", "Zoning"
]

_SUFFIX_PATTERN = re.compile(
    r'\b(llc|llp|lp|pllc|pc|p\.?a\.?|law firm|law group|law office|law|'
    r'attorney at law|attorneys at law|attorney)\b',
    re.IGNORECASE
)
_ASSOC_PATTERN = re.compile(r'\b(and|&)\b', re.IGNORECASE)
_PUNCT_PATTERN = re.compile(r"[^\w\s]")
_MULTI_SPACE = re.compile(r'\s+')

_PRACTICE_ALIASES = {
    "workers comp": "Workers' Compensation",
    "workers compensation": "Workers' Compensation",
    "pi": "Personal Injury",
    "dui/dwi": "DUI",
    "wills": "Estate Planning",
    "wills and trusts": "Estate Planning",
}


def normalize_firm_name(name: str) -> str:
    """Return a normalized firm name for fuzzy dedup comparison."""
    s = name.lower()
    s = _SUFFIX_PATTERN.sub('', s)
    s = _ASSOC_PATTERN.sub('', s)
    s = _PUNCT_PATTERN.sub('', s)
    s = _MULTI_SPACE.sub(' ', s).strip()
    return s


def are_same_firm(name_a: str, name_b: str, threshold: int = 85) -> bool:
    """Return True if two firm names refer to the same firm."""
    norm_a = normalize_firm_name(name_a)
    norm_b = normalize_firm_name(name_b)
    score = fuzz.token_sort_ratio(norm_a, norm_b)
    return score >= threshold


def normalize_practice_area(raw: str) -> str:
    """Map a raw practice area string to the closest canonical area."""
    # Check aliases first (handles abbreviations that score below threshold)
    lower = raw.strip().lower()
    if lower in _PRACTICE_ALIASES:
        return _PRACTICE_ALIASES[lower]
    result = process.extractOne(
        raw,
        CANONICAL_PRACTICE_AREAS,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=85
    )
    if result:
        return result[0]
    return raw.strip().title()


# ---------------------------------------------------------------------------
# Multi-pass deduplication
# ---------------------------------------------------------------------------

_SOURCE_PRIORITY = {
    "google_places": 4,
    "findlaw": 3,
    "avvo": 3,
    "justia": 3,
    "ks_courts": 2,
    "website_scraper": 2,
    "ksbar": 1,
}


def _get_base_domain(url: str | None) -> str:
    """Extract base domain from URL, stripping www."""
    if not url:
        return ""
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def _best_source_score(sources: list, source_priority: dict = None) -> int:
    if source_priority is None:
        source_priority = _SOURCE_PRIORITY
    return max((source_priority.get(s, 0) for s in sources), default=0)


def _merge_firm_records(primary: dict, secondary: dict) -> dict:
    """Merge secondary into primary, following source priority rules."""
    p_score = _best_source_score(primary.get("sources", []))
    s_score = _best_source_score(secondary.get("sources", []))

    # Address: prefer the record with more complete address
    p_addr = primary.get("address", {})
    s_addr = secondary.get("address", {})
    if not p_addr.get("street") and s_addr.get("street"):
        p_addr["street"] = s_addr["street"]
    if not p_addr.get("zip") and s_addr.get("zip"):
        p_addr["zip"] = s_addr["zip"]
    if not p_addr.get("county") and s_addr.get("county"):
        p_addr["county"] = s_addr["county"]

    # Phone: prefer non-None, higher source wins ties
    if not primary.get("phone") and secondary.get("phone"):
        primary["phone"] = secondary["phone"]
    elif primary.get("phone") and secondary.get("phone") and s_score > p_score:
        primary["phone"] = secondary["phone"]

    # Email: prefer non-None
    if not primary.get("email") and secondary.get("email"):
        primary["email"] = secondary["email"]

    # Website: prefer non-None, higher source wins ties
    if not primary.get("website") and secondary.get("website"):
        primary["website"] = secondary["website"]
    elif primary.get("website") and secondary.get("website") and s_score > p_score:
        primary["website"] = secondary["website"]

    # Coordinates: prefer non-None, Google wins
    if not primary.get("coordinates") and secondary.get("coordinates"):
        primary["coordinates"] = secondary["coordinates"]
    elif secondary.get("coordinates") and "google_places" in secondary.get("sources", []):
        primary["coordinates"] = secondary["coordinates"]

    # Summary: prefer non-None
    if not primary.get("summary") and secondary.get("summary"):
        primary["summary"] = secondary["summary"]

    # Practice areas: union
    existing = set(primary.get("practiceAreas") or [])
    for area in secondary.get("practiceAreas") or []:
        if area not in existing:
            primary.setdefault("practiceAreas", []).append(area)
            existing.add(area)

    # Sources: union
    existing_sources = set(primary.get("sources", []))
    for src in secondary.get("sources", []):
        if src not in existing_sources:
            primary.setdefault("sources", []).append(src)
            existing_sources.add(src)

    # Attorneys: union (if field exists)
    if "attorneys" in secondary:
        # attorneys may be strings or dicts (statewide import) -> unhashable; key on JSON repr
        def _akey(a):
            return a if isinstance(a, str) else json.dumps(a, sort_keys=True, default=str)
        existing_attys = {_akey(a) for a in (primary.get("attorneys") or [])}
        for atty in secondary["attorneys"]:
            if _akey(atty) not in existing_attys:
                primary.setdefault("attorneys", []).append(atty)
                existing_attys.add(_akey(atty))
        primary["attorney_count"] = len(primary.get("attorneys", []))

    # Use the better name (prefer non-person-name if available from firm)
    if primary.get("name", "").count(",") > 0 and secondary.get("name", "").count(",") == 0:
        # primary looks like "Last, First" (person name), secondary looks like firm name
        primary["name"] = secondary["name"]

    return primary


class _UnionFind:
    """Simple union-find for transitive dedup merging."""
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _pass1_exact_match(firms: list) -> list:
    """Group by exact normalized name + city. Merge groups."""
    groups = {}
    for firm in firms:
        city = (firm.get("address") or {}).get("city", "").lower()
        key = (normalize_firm_name(firm.get("name", "")), city)
        if key not in groups:
            groups[key] = firm
        else:
            groups[key] = _merge_firm_records(groups[key], firm)

    return list(groups.values())


def _pass2_fuzzy_match(firms: list, threshold: int = 88) -> list:
    """Pairwise fuzzy matching within same city using union-find."""
    # Group by city first to reduce comparisons
    city_groups = {}
    for i, firm in enumerate(firms):
        city = (firm.get("address") or {}).get("city", "").lower()
        city_groups.setdefault(city, []).append(i)

    uf = _UnionFind(len(firms))

    for city, indices in city_groups.items():
        if len(indices) < 2:
            continue
        # Precompute normalized names for this city
        names = [(idx, normalize_firm_name(firms[idx].get("name", ""))) for idx in indices]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                idx_a, name_a = names[i]
                idx_b, name_b = names[j]
                score = fuzz.token_sort_ratio(name_a, name_b)
                if score >= threshold:
                    uf.union(idx_a, idx_b)

    # Merge groups
    merge_groups = {}
    for i in range(len(firms)):
        root = uf.find(i)
        if root not in merge_groups:
            merge_groups[root] = firms[i]
        else:
            merge_groups[root] = _merge_firm_records(merge_groups[root], firms[i])

    return list(merge_groups.values())


def _pass3_domain_phone_dedup(firms: list) -> list:
    """Group by shared website domain or phone number."""
    # Index firms by domain and phone
    domain_groups = {}
    phone_groups = {}

    for i, firm in enumerate(firms):
        domain = _get_base_domain(firm.get("website"))
        if domain:
            domain_groups.setdefault(domain, []).append(i)
        phone = (firm.get("phone") or "").strip()
        if phone:
            phone_groups.setdefault(phone, []).append(i)

    uf = _UnionFind(len(firms))

    for indices in domain_groups.values():
        for i in range(1, len(indices)):
            uf.union(indices[0], indices[i])

    for indices in phone_groups.values():
        for i in range(1, len(indices)):
            uf.union(indices[0], indices[i])

    merge_groups = {}
    for i in range(len(firms)):
        root = uf.find(i)
        if root not in merge_groups:
            merge_groups[root] = firms[i]
        else:
            merge_groups[root] = _merge_firm_records(merge_groups[root], firms[i])

    return list(merge_groups.values())


def _pass4_validation_log(firms: list, log_path: str = "data/potential_duplicates.log") -> None:
    """Log remaining potential duplicates for manual review."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    # Sort by normalized name for adjacent comparison
    sorted_firms = sorted(firms, key=lambda f: normalize_firm_name(f.get("name", "")))
    potential = []

    for i in range(len(sorted_firms) - 1):
        a = sorted_firms[i]
        b = sorted_firms[i + 1]
        city_a = (a.get("address") or {}).get("city", "").lower()
        city_b = (b.get("address") or {}).get("city", "").lower()
        name_a = normalize_firm_name(a.get("name", ""))
        name_b = normalize_firm_name(b.get("name", ""))
        score = fuzz.token_sort_ratio(name_a, name_b)

        # Same city, >80% similar, or any city >90% similar
        if (city_a == city_b and score > 80) or score > 90:
            potential.append(f"SCORE={score} | {a['name']} ({city_a}) <-> {b['name']} ({city_b})")

    with open(log_path, "w") as f:
        f.write(f"Potential duplicate pairs: {len(potential)}\n\n")
        for line in potential:
            f.write(line + "\n")

    if potential:
        print(f"[dedup] Logged {len(potential)} potential duplicates to {log_path}")


def deduplicate_firms(firms: list, log_path: str = "data/potential_duplicates.log") -> list:
    """Multi-pass deduplication. Returns deduplicated list."""
    count_before = len(firms)

    firms = _pass1_exact_match(firms)
    after_p1 = len(firms)

    firms = _pass2_fuzzy_match(firms)
    after_p2 = len(firms)

    firms = _pass3_domain_phone_dedup(firms)
    after_p3 = len(firms)

    _pass4_validation_log(firms, log_path)

    print(f"[dedup] {count_before} → {after_p1} (exact) → {after_p2} (fuzzy) → {after_p3} (domain/phone)")
    return firms

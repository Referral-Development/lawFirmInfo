"""
Automated duplicate-CANDIDATE detection for a finalized county CSV.

Motivation: the Saline County KS test run (2026-09-18) found 3 real
duplicate/same-entity pairs by manually reading a 38-row file. That does not
scale to a several-thousand-row county (Harris County TX has 11,000+ rows).
This module automates the DETECTION step -- same phone number, or same
street address -- so it can run on any size file. It deliberately does NOT
auto-merge anything. Every historical false-merge bug in this project
(common surname collision, two family members sharing an office running
different businesses, a shared toll-free line across unrelated branches)
happened because a script merged automatically on weak evidence. Per the
project's standing "when uncertain, don't guess" rule, this only surfaces
candidate groups for a human/agent to verify against an outside source
(the same way Saline's Montoya/Wyatt/Vogel pairs were resolved) before
deciding whether to merge, keep both, or investigate further.

See tests/scraper/test_dedup_candidates.py for the regression cases drawn
from real historical bugs (Wyatt Law Office / Bruce H. Wyatt Law Office in
Saline were flagged as SEPARATE despite sharing a surname -- correctly, they
turned out to be two distinct real firms).
"""
import re
from collections import defaultdict


def _normalize_phone(phone: str) -> str:
    return re.sub(r'\D', '', phone or '')


def _normalize_address(street: str, city: str) -> str:
    s = (street or '').strip().lower()
    s = re.sub(r'[^\w\s]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    c = (city or '').strip().lower()
    return f"{s}|{c}"


def find_duplicate_candidates(rows: list, name_key: str = "law_firm_name",
                               phone_key: str = "phone_number",
                               street_key: str = "street_address",
                               city_key: str = "city") -> dict:
    """
    Group rows sharing an exact (normalized) phone number or an exact
    (normalized) street+city address. Returns:

        {
            "phone": [[row, row, ...], ...],   # groups sharing a phone
            "address": [[row, row, ...], ...], # groups sharing an address
        }

    Only groups with 2+ rows are included. Toll-free numbers (800/888/877/
    866/855/844/833) are excluded from the phone grouping -- a shared
    toll-free line across genuinely distinct branches/agents is common (see
    the insurance pipeline's Armed Forces Insurance toll-free dedup bug) and
    is a real false-positive source, not evidence of duplication.

    This function makes NO merge decision. Each returned group is a
    candidate that still needs the same manual verification Saline's
    Montoya/Wyatt/Vogel pairs got (cross-check against an outside source
    like Avvo/Yelp/lawyers.com) before merging or excluding anything.
    """
    TOLL_FREE_PREFIXES = ("1800", "1888", "1877", "1866", "1855", "1844", "1833",
                          "800", "888", "877", "866", "855", "844", "833")

    by_phone = defaultdict(list)
    by_address = defaultdict(list)

    for row in rows:
        phone = _normalize_phone(row.get(phone_key, ""))
        if phone and not any(phone.startswith(p) and len(phone) in (10, 11)
                              for p in TOLL_FREE_PREFIXES):
            by_phone[phone].append(row)

        street = (row.get(street_key, "") or "").strip()
        if street:
            addr_key = _normalize_address(street, row.get(city_key, ""))
            by_address[addr_key].append(row)

    phone_groups = [rs for rs in by_phone.values() if len(rs) > 1]
    address_groups = [rs for rs in by_address.values() if len(rs) > 1]

    return {"phone": phone_groups, "address": address_groups}


def summarize_candidates(candidates: dict, name_key: str = "law_firm_name") -> str:
    """Human-readable summary for a report/commit message, not a decision."""
    lines = []
    for kind in ("phone", "address"):
        groups = candidates.get(kind, [])
        if not groups:
            continue
        lines.append(f"{len(groups)} candidate group(s) sharing a {kind}:")
        for g in groups:
            names = ", ".join(r.get(name_key, "?") for r in g)
            lines.append(f"  - {names}")
    return "\n".join(lines) if lines else "No duplicate candidates found."

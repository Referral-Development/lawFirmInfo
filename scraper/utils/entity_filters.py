"""
Shared, tested non-law / non-referral entity detection.

This consolidates logic that previously existed only as prose lessons in
Claude Code memory files and as duplicated/hardcoded pattern lists spread
across final_cleanup.py, clean_all_shallow_ks.py, remove_govt_legal_entities.py,
and the various deep_clean_<county>.py / statebar_to_csv.py scripts (KS + TX
pipelines). Those scripts are left as-is (not modified) to avoid risking
already-shipped county data; this module is meant to be the one place NEW
county-cleanup code imports from going forward, so a bug fix here benefits
every future county automatically instead of needing to be re-discovered.

Every safety rule encoded here has a corresponding regression test in
tests/scraper/test_entity_filters.py, tied to the specific historical bug
that motivated it (see docstrings + comments referencing the TX lessons in
docs, e.g. "lesson 15", "lesson 32"). If you find a new false positive or
false negative, add BOTH the fix here AND a test case, not just the fix.
"""
import re

# ---------------------------------------------------------------------------
# Law-firm indicator words. Presence of any of these generally means "don't
# exclude even if a govt/corp pattern also matches" -- but see EXACT_MATCH_
# ONLY names below, which bypass this (a law-sounding name can still be a
# real non-law entity, e.g. "County Attorney" contains no law word but
# "District Attorney's Office" doesn't either -- those are matched by the
# GOVT_PATTERNS regex directly, not gated behind this list).
# ---------------------------------------------------------------------------
LAW_INDICATORS = (
    "law", "legal", "attorney", "attorneys", "counsel", "counselor",
    "llp", "pllc", "p.c.", "pc", "p.a.", "pa", "esq", "mediator",
    "mediation", "arbitr",
)

# "& Associates"/"and Associates" is a strong solo/small-practice naming
# signal in a dataset already filtered to licensed attorneys -- TX lesson 16
# ("Tyler Flood & Associates, Inc." is a real DWI defense firm despite the
# corporate "Inc." suffix that would otherwise trigger the generic-corp-
# suffix rule below).
# NOTE: deliberately no leading \b before "&" -- \b never matches between
# two non-word characters (e.g. a space and "&"), which is the exact TX
# lesson-6 gotcha ("Villarreal& Associates" vs "Villarreal & Associates").
# "&" itself needs no boundary assertion since it can't be part of a word.
_ASSOCIATES_RE = re.compile(r'(?:&|\band\b)\s+associates\b', re.IGNORECASE)

# A bare corporate suffix (LLC/LP/Group/Holdings/Inc/Corp) with NO other law
# word anywhere in the name is a strong signal of a non-law entity -- but
# NOT a safe blanket rule on its own (TX lesson 29: many huge, very real law
# firms use bare L.L.P./LLC with no other indicator -- Baker Botts L.L.P.,
# Shook Hardy & Bacon L.L.P., Fulbright & Jaworski L.L.P.). This regex is
# intentionally used only as one signal among several in is_non_law(), never
# as a standalone exclusion by itself.
GENERIC_CORP_SUFFIX_RE = re.compile(
    r'\b(?:company|corporation|corp\.?|inc\.?|holdings|group|solutions|'
    r'advisors|partners|capital)\b',
    re.IGNORECASE,
)

# Purely numeric "company" values are data-entry errors (e.g. a founding
# year typed into the wrong field), not real firm names -- TX lesson 17.
NUMERIC_ONLY_RE = re.compile(r'^\s*\d+\s*$')

# ---------------------------------------------------------------------------
# Exact-match-only non-law names. These are short/common-sounding enough
# that a bare substring or whole-word match against a full name would
# collide with real surnames (TX lesson 32: "Valero" is both a real energy
# company AND a common Hispanic surname -- "Dolores Carina Valero" is a real
# unrelated attorney). Only exclude when the row's name, once normalized,
# EXACTLY equals one of these -- never a substring/whole-word match within
# a longer name.
# ---------------------------------------------------------------------------
EXACT_MATCH_ONLY_NAMES = {
    "valero", "usaa", "calpine",
}

# Multi-word phrases are safe to match as substrings since they're specific
# enough not to collide with an ordinary name (TX lesson 32's alternative
# safe path: "valero energy", "one valero way").
_PHRASE_NON_LAW_RE = re.compile(
    r'\b(?:valero\s+energy|one\s+valero\s+way|usaa\s+(?:insurance|federal)|'
    r'calpine\s+corporation)\b',
    re.IGNORECASE,
)

# Exact-name non-law entities (whole normalized name must match exactly --
# these are specific enough that substring matching isn't needed, but kept
# exact to avoid any accidental partial-name collision).
NON_LAW_EXACT_NAMES = {
    # Government / court entities with no law word (bare offices)
    "county attorney", "county attorney's office", "district attorney",
    "district attorney's office", "county treasurer", "county clerk",
    "register of deeds", "clerk of the district court", "public defender",
    "public defender's office", "board of county commissioners",
    "county counselor", "district judge",
    # Financial / non-law institutions seen across multiple counties/states
    "advantage trust company", "fund administrative services llc",
    "umb bank", "farmers bank & trust", "bok financial",
    # Healthcare
    "university of kansas health system", "shawnee mission medical center",
    "ku medical center",
    # Nonprofit / non-referral (real legal work, but not a fee-generating
    # referral target for this tool -- TX lesson 24)
    "kansas legal services", "saint francis ministries",
    # Misc non-law businesses that have shown up in KS county pulls
    "blue beacon inc",
}

# ---------------------------------------------------------------------------
# Government / institutional pattern matches. Broader than the exact-name
# set above -- these catch name SHAPES, not just specific known entities.
# Guarded: only fires when the name has NO law indicator (see LAW_INDICATORS)
# UNLESS the pattern is itself unambiguous (e.g. "sheriff's office").
# ---------------------------------------------------------------------------
GOVT_PATTERNS = re.compile(
    r'\b(?:'
    r'city\s+(?:clerk|hall|prosecutor|of\s+\w+)|county\s+(?:clerk|treasurer|counselor)|'
    r'department\s+of|dept\.?\s+of|public\s+library|fire\s+(?:station|department)|'
    r'police\s+department|sheriff(?!.*(?:law|attorney|legal))|'
    r'district\s+court|circuit\s+court|municipal\s+court|probate\s+court|'
    r'judicial\s+district|u\.?s\.?\s+district\s+court|united\s+states\s+district|'
    r'board\s+of\s+(?:county\s+)?commissioners|register\s+of\s+deeds|'
    r'unified\s+school\s+district|school\s+district|'
    r'district\s+attorney|county\s+attorney|public\s+defender'
    r')\b',
    re.IGNORECASE,
)

# A generic "justice center" phrase is often a real government building
# name (e.g. "Tim Curry Criminal Justice Center") but can ALSO be part of a
# real solo law firm's brand name ("Jump Start Legal Justice Center, PLLC"
# -- TX lesson 31). Guard it the same way as the rest: only exclude when no
# law indicator is present.
_JUSTICE_CENTER_RE = re.compile(r'\bjustice\s+center\b', re.IGNORECASE)

NON_LAW_KEYWORDS = re.compile(
    r'\b(?:disposal\s+service|trucking|construction|insurance\s+agency|'
    r'real\s+estate|realty|bank\b|credit\s+union|veterinary|auto\s+parts|'
    r'hardware|restaurant|dental|medical\s+center|hospital|health\s+system|'
    r'chiropractic|physical\s+therapy)\b',
    re.IGNORECASE,
)


def _has_law_indicator(lname: str) -> bool:
    return any(kw in lname for kw in LAW_INDICATORS)


def is_non_law(name: str) -> bool:
    """
    Return True if `name` should be excluded as a non-referral entity
    (government office, financial/healthcare institution, non-law business,
    nonprofit legal-aid org, or a data-entry placeholder).

    This is intentionally conservative -- see the module docstring. When in
    doubt, this returns False (keep the row) per the project's standing
    "when uncertain, exclude [a decision], don't guess" rule applied to
    filtering: it's safer to leave a genuine solo attorney in than to
    silently drop a real referral target.
    """
    if not name or not name.strip():
        return False

    raw = name.strip()
    lname = raw.lower()

    if NUMERIC_ONLY_RE.match(raw):
        return True

    if lname in NON_LAW_EXACT_NAMES:
        return True

    if lname in EXACT_MATCH_ONLY_NAMES:
        return True

    if _PHRASE_NON_LAW_RE.search(raw):
        return True

    has_law = _has_law_indicator(lname)

    if not has_law and GOVT_PATTERNS.search(raw):
        return True

    if not has_law and _JUSTICE_CENTER_RE.search(raw):
        return True

    if not has_law and NON_LAW_KEYWORDS.search(raw):
        return True

    # Generic corporate suffix with no law word at all -- but "& Associates"
    # is a protective signal (lesson 16), and this whole check only fires
    # when there's ALSO no law indicator (so "Tyler Flood & Associates,
    # Inc." is protected twice over: has "& Associates" AND is checked only
    # when has_law is False, but "Associates" itself isn't a LAW_INDICATOR,
    # so the explicit _ASSOCIATES_RE check below is what actually saves it).
    if not has_law and not _ASSOCIATES_RE.search(raw) and GENERIC_CORP_SUFFIX_RE.search(raw):
        return True

    return False

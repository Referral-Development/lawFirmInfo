"""
Tests for scraper.utils.dedup_candidates -- the automated duplicate-CANDIDATE
detector built after the Saline County KS test run (2026-09-18), where 3
real duplicate/same-entity pairs were only found by manually reading a
38-row file.
"""
from scraper.utils.dedup_candidates import find_duplicate_candidates, summarize_candidates


def _row(name, phone="", street="", city=""):
    return {
        "law_firm_name": name,
        "phone_number": phone,
        "street_address": street,
        "city": city,
    }


class TestPhoneGrouping:
    def test_same_phone_flagged_as_candidate(self):
        # Real case from Saline County KS: CAD Law / CAD Law LC shared a
        # phone number and were genuinely the same firm.
        rows = [
            _row("CAD Law", phone="(785) 407-9128"),
            _row("CAD Law LC", phone="785-407-9128"),
            _row("Unrelated Firm", phone="785-999-0000"),
        ]
        candidates = find_duplicate_candidates(rows)
        assert len(candidates["phone"]) == 1
        names = {r["law_firm_name"] for r in candidates["phone"][0]}
        assert names == {"CAD Law", "CAD Law LC"}

    def test_toll_free_shared_number_not_flagged(self):
        # Insurance pipeline lesson: a shared toll-free line across genuinely
        # distinct branches is common and must NOT be treated as a dup signal.
        rows = [
            _row("Armed Forces Insurance - Branch A", phone="1-800-555-0100"),
            _row("Armed Forces Insurance - Branch B", phone="800-555-0100"),
        ]
        candidates = find_duplicate_candidates(rows)
        assert candidates["phone"] == []

    def test_no_false_positive_on_distinct_phones(self):
        rows = [
            _row("Bruce H. Wyatt Law Office", phone="785-493-1825"),
            _row("Wyatt Law Office", phone="(785) 404-2400"),
        ]
        candidates = find_duplicate_candidates(rows)
        # Different phones -> not flagged by phone at all. (Same surname
        # alone is never a signal this module uses.)
        assert candidates["phone"] == []


class TestAddressGrouping:
    def test_same_address_flagged_as_candidate(self):
        rows = [
            _row("Christopher A. Vogel", street="2035 E Iron Ave, Ste 209R", city="Salina"),
            _row("Brown & Vogel, LLC", street="2035 E Iron Ave Suite 101", city="Salina"),
        ]
        candidates = find_duplicate_candidates(rows)
        # Note: suite numbers differ slightly in this real example, so this
        # deliberately does NOT match -- proving the function requires an
        # EXACT normalized address, not a fuzzy one, to avoid over-merging
        # a building with multiple unrelated tenants (TX lesson 20/28).
        assert candidates["address"] == []

    def test_exact_suite_match_flagged(self):
        rows = [
            _row("Jane Smith", street="500 Main St Ste 200", city="Salina"),
            _row("Smith & Partners LLP", street="500 main st ste 200", city="salina"),
        ]
        candidates = find_duplicate_candidates(rows)
        assert len(candidates["address"]) == 1


    def test_blank_street_addresses_never_grouped(self):
        # Real bug caught during the Saline County KS build: two firms with
        # a BLANK street_address (only a city on file) were wrongly grouped
        # as an address match because the empty street normalized to a
        # truthy "|city" key. A missing street is not evidence of anything.
        rows = [
            _row("Bever Dye, LC", street="", city="Salina"),
            _row("Samantha Angell", street="", city="Salina"),
        ]
        candidates = find_duplicate_candidates(rows)
        assert candidates["address"] == []


class TestSummary:
    def test_summary_reports_no_candidates(self):
        rows = [_row("Solo Firm A", phone="785-111-1111")]
        candidates = find_duplicate_candidates(rows)
        assert "No duplicate candidates" in summarize_candidates(candidates)

    def test_summary_reports_groups(self):
        rows = [
            _row("CAD Law", phone="785-407-9128"),
            _row("CAD Law LC", phone="785-407-9128"),
        ]
        candidates = find_duplicate_candidates(rows)
        summary = summarize_candidates(candidates)
        assert "CAD Law" in summary
        assert "sharing a phone" in summary

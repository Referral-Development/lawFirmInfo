"""
Regression tests for scraper.utils.entity_filters.

Every test here is tied to a REAL historical bug documented in the project's
memory files (project_texas_counties.md lessons, or the Saline County KS
test run) -- not a hypothetical. If you change is_non_law() and one of
these starts failing, you are very likely about to reintroduce a bug that
was already found and fixed once.
"""
from scraper.utils.entity_filters import is_non_law


class TestRealFirmsNeverExcluded:
    """Firms that were WRONGLY excluded by an earlier version of this logic
    somewhere in the project's history -- must always survive."""

    def test_mcalpine_law_firm_not_excluded_by_calpine_substring(self):
        # TX lesson 15: "calpine" (Calpine Corporation) matched as a bare
        # substring inside "McAlpine Law Firm" and wrongly excluded a real firm.
        assert not is_non_law("McAlpine Law Firm")

    def test_valero_as_surname_not_excluded(self):
        # TX lesson 32: "Dolores Carina Valero" is a real unrelated attorney,
        # not the Valero Energy corporation.
        assert not is_non_law("Dolores Carina Valero")

    def test_tyler_flood_associates_inc_not_excluded(self):
        # TX lesson 16: a real, well-known Houston DWI defense firm that
        # combines "& Associates" with "Inc." -- must not be caught by the
        # generic corporate-suffix rule.
        assert not is_non_law("Tyler Flood & Associates, Inc.")

    def test_bare_llp_biglaw_firm_not_excluded(self):
        # TX lesson 29: many huge real firms use bare LLC/LLP with no other
        # law-word indicator.
        assert not is_non_law("Baker Botts L.L.P.")
        assert not is_non_law("Shook Hardy & Bacon L.L.P.")

    def test_jump_start_legal_justice_center_not_excluded(self):
        # TX lesson 31: an unguarded "justice center" pattern wrongly caught
        # this real solo civil-rights litigation firm.
        assert not is_non_law("Jump Start Legal Justice Center, PLLC")

    def test_wyatt_law_office_not_excluded(self):
        # Saline County KS test run: a real, distinct family-law practice.
        assert not is_non_law("Wyatt Law Office")

    def test_brown_and_vogel_not_excluded(self):
        assert not is_non_law("Brown & Vogel, LLC")


class TestNonLawEntitiesCorrectlyExcluded:
    """Entities that genuinely leaked into shipped county data before being
    caught -- must always be excluded."""

    def test_calpine_corporation_excluded(self):
        assert is_non_law("Calpine Corporation")

    def test_valero_energy_excluded(self):
        assert is_non_law("Valero Energy")

    def test_district_attorneys_office_excluded(self):
        assert is_non_law("District Attorney's Office")

    def test_saline_govt_entities_excluded(self):
        # Real entries found uncleaned in the original Saline County KS file.
        assert is_non_law("Blue Beacon Inc")
        assert is_non_law("Advantage Trust Company")
        assert is_non_law("Kansas Legal Services")
        assert is_non_law("Saint Francis Ministries")

    def test_numeric_only_placeholder_excluded(self):
        # TX lesson 17: a bare year typed into the company field.
        assert is_non_law("1958")

    def test_generic_corp_suffix_no_law_word_excluded(self):
        assert is_non_law("Acme Holdings, Inc.")


class TestEdgeCases:
    def test_blank_name_not_excluded(self):
        # Blank/placeholder company routing is handled upstream; this
        # function should never crash or wrongly flag an empty string.
        assert not is_non_law("")
        assert not is_non_law("   ")

    def test_law_indicator_protects_against_govt_pattern(self):
        # A name containing both a govt-shaped word and a law indicator
        # should NOT be excluded (e.g. a real "City Prosecutor Defense
        # Attorneys" -- contrived but tests the guard logic directly).
        assert not is_non_law("Smith City Law Office")

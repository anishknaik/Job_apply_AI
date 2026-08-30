"""Stage A tests.

Every test in TestRegressions corresponds to a bug found by auditing the
first real run against 6,906 live postings. They are the reason the filter
engine uses word-boundary matching, blocklist masking and per-location
evaluation instead of plain substring checks.
"""
from datetime import datetime, timedelta, timezone

import pytest

from jobpipe.filters import FilterRules, evaluate, load_rules

NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)


def posting(**overrides):
    base = {
        "title": "Software Engineer",
        "location": "New York, NY",
        "description": "",
        "remote_flag": 0,
        "posted_at": (NOW - timedelta(days=3)).isoformat(),
    }
    base.update(overrides)
    return base


@pytest.fixture
def rules():
    """The real shipped rules, so tests fail if config drifts."""
    return load_rules()


class TestTitleRules:
    def test_allows_engineer(self, rules):
        assert evaluate(posting(title="Software Engineer"), rules, NOW)[0]

    def test_blocks_seniority_above_ic(self, rules):
        for title in ["Director of Engineering", "VP of Data",
                      "Principal Engineer", "Head of AI"]:
            passed, reason = evaluate(posting(title=title), rules, NOW)
            assert not passed, title
            assert reason.startswith("title_exclude")

    def test_blocks_non_technical_roles(self, rules):
        for title in ["Enterprise Account Executive", "Recruiter",
                      "Commercial Counsel", "Executive Assistant"]:
            assert not evaluate(posting(title=title), rules, NOW)[0], title

    def test_blocklist_beats_allowlist(self, rules):
        # Contains "engineer" (allowed) and "sales engineer" (blocked).
        passed, reason = evaluate(posting(title="Sales Engineer"), rules, NOW)
        assert not passed
        assert reason.startswith("title_exclude")


class TestLocationRules:
    def test_allows_new_york(self, rules):
        assert evaluate(posting(location="New York, NY"), rules, NOW)[0]

    def test_allows_remote(self, rules):
        assert evaluate(posting(location="Remote - United States"), rules, NOW)[0]

    def test_allows_us_cities_outside_new_york(self, rules):
        """Relocation is on the table, so the allowlist is US-wide."""
        for location in ["Seattle, WA", "San Francisco, CA", "Austin, TX",
                         "US-CA-Menlo Park", "United States"]:
            passed, reason = evaluate(posting(location=location), rules, NOW)
            assert passed, f"{location!r} dropped by {reason}"

    def test_rejects_a_location_that_is_neither_allowed_nor_excluded(self, rules):
        """A city-only foreign location matches nothing in either list, so the
        allowlist is what stops it — this is why the rule is not simply
        emptied now that the whole US is acceptable."""
        passed, reason = evaluate(posting(location="Gurugram"), rules, NOW)
        assert not passed
        assert reason.startswith("location_allow")

    def test_missing_location_passes_through_to_stage_b(self, rules):
        assert evaluate(posting(location=None), rules, NOW)[0]
        assert evaluate(posting(location=""), rules, NOW)[0]


class TestAgeRule:
    def test_rejects_stale_posting(self, rules):
        old = (NOW - timedelta(days=200)).isoformat()
        passed, reason = evaluate(posting(posted_at=old), rules, NOW)
        assert not passed
        assert reason.startswith("max_age_days")

    def test_missing_date_passes(self, rules):
        assert evaluate(posting(posted_at=None), rules, NOW)[0]

    def test_unparseable_date_passes(self, rules):
        assert evaluate(posting(posted_at="not a date"), rules, NOW)[0]


class TestDescriptionRule:
    def test_rejects_clearance_requirement(self, rules):
        passed, reason = evaluate(
            posting(description="You must hold an active security clearance."),
            rules, NOW)
        assert not passed
        assert reason.startswith("description_exclude")


class TestEarlyCareer:
    """A recent graduate's highest-value postings must not be discarded."""

    def test_new_grad_titles_are_not_excluded(self, rules):
        for title in ["Software Engineer, New Grad (2027)",
                      "AI Research Scientist, New Grad",
                      "Software Engineer (New Grad Program)",
                      "Software Engineer, Early Career"]:
            passed, reason = evaluate(posting(title=title), rules, NOW)
            assert passed, f"{title!r} was dropped: {reason}"

    def test_grad_programmes_are_exempt_from_the_age_rule(self, rules):
        """Campus programmes are posted months ahead and stay open."""
        old = (NOW - timedelta(days=127)).isoformat()
        passed, reason = evaluate(
            posting(title="Software Engineer (New Grad Program)",
                    posted_at=old), rules, NOW)
        assert passed, f"dropped: {reason}"

    def test_ordinary_roles_are_still_subject_to_the_age_rule(self, rules):
        old = (NOW - timedelta(days=127)).isoformat()
        passed, reason = evaluate(posting(title="Software Engineer",
                                          posted_at=old), rules, NOW)
        assert not passed
        assert reason.startswith("max_age_days")

    def test_internships_are_still_excluded(self, rules):
        assert not evaluate(posting(title="Software Engineer Intern"),
                            rules, NOW)[0]


class TestRegressions:
    """Each of these was a real false positive or negative on live data."""

    def test_member_of_technical_staff_survives_the_staff_blocklist(self, rules):
        """81 postings lost on the first run. The standard IC title at AI labs."""
        for title in ["Member of Technical Staff",
                      "Member of Technical Staff (Software Engineer, GPU)",
                      "Member of the Technical Staff, Inference"]:
            passed, reason = evaluate(posting(title=title), rules, NOW)
            assert passed, f"{title!r} was dropped: {reason}"

    def test_masking_does_not_disable_the_rest_of_the_blocklist(self, rules):
        """Masking must not become a blanket whitelist for the title."""
        passed, _ = evaluate(
            posting(title="Staff Engineer, Member of Technical Staff"),
            rules, NOW)
        assert not passed

    def test_intern_does_not_match_internal(self, rules):
        """'intern' matched "Internal Agent" under substring matching."""
        passed, reason = evaluate(
            posting(title="Member of the Technical Staff, Internal Agent"),
            rules, NOW)
        assert passed, f"dropped by {reason}"

    def test_intern_still_blocks_actual_internships(self, rules):
        assert not evaluate(posting(title="Software Engineer Intern"), rules, NOW)[0]
        assert not evaluate(posting(title="Engineering Internship"), rules, NOW)[0]

    def test_ny_does_not_match_germany_or_sunnyvale(self):
        """'ny' matched "Germany" and "Sunnyvale" under substring matching.

        Tests the matcher rather than the outcome: both places are now decided
        by other rules (Germany excluded, Sunnyvale allowed by name), so only
        a direct check still isolates the word-boundary behaviour.
        """
        from jobpipe.filters import _first_match
        for token in ["munich, germany", "sunnyvale, ca", "brittany"]:
            assert _first_match(token, ["ny", "new york"]) is None, token
        assert _first_match("new york, ny", ["ny"]) == "ny"

    def test_multi_location_role_including_new_york_survives(self, rules):
        """101 postings lost: a NY role also open in London died on 'london'."""
        for location in [
            "San Francisco; New York City; London, UK",
            "United Kingdom; Dublin; United States; New York; Sofia",
            "San Francisco, CA | New York City, NY",
        ]:
            passed, reason = evaluate(posting(location=location), rules, NOW)
            assert passed, f"{location!r} was dropped: {reason}"

    def test_foreign_only_multi_location_still_rejected(self, rules):
        passed, reason = evaluate(
            posting(location="London; Berlin; Belgrade"), rules, NOW)
        assert not passed
        assert reason.startswith("location_exclude")

    def test_remote_in_an_excluded_country_is_rejected(self, rules):
        """The word 'remote' must not launder an excluded country."""
        for location in ["India - Remote", "Netherlands (remote)",
                         "Remote - Toronto, Canada"]:
            passed, _ = evaluate(posting(location=location, remote_flag=1),
                                 rules, NOW)
            assert not passed, f"{location!r} should be rejected"

    def test_remote_flag_alone_does_not_bypass_exclusions(self, rules):
        assert not evaluate(
            posting(location="Bangalore, India", remote_flag=1), rules, NOW)[0]


class TestRuleLoading:
    def test_shipped_config_loads(self, rules):
        assert rules.title_require_any
        assert rules.title_exclude
        assert rules.max_age_days == 60

    def test_empty_allowlist_disables_the_rule(self):
        rules = FilterRules(
            title_require_any=[], title_exclude=[], title_exclude_unless=[],
            location_allow=[], location_exclude=[], max_age_days=None,
            max_age_exempt_titles=[], max_years_experience=None,
            sponsorship_allow=[], sponsorship_exclude=[],
            description_exclude=[],
        )
        assert evaluate(posting(title="Chief Vibes Officer"), rules, NOW)[0]

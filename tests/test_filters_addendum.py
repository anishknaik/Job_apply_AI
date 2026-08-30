"""Stage A rules added by the addendum: §1.3 seniority + YoE, §3.4 sponsorship."""
from datetime import datetime, timedelta, timezone

import pytest

from jobpipe.filters import evaluate, load_rules, minimum_years_required

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)


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
    return load_rules()


class TestSeniorityRestored:
    """§1.3 — the calibration set scored all 9 senior-titled postings 1-3."""

    def test_senior_titles_are_excluded(self, rules):
        for title in ["Senior Software Engineer",
                      "Sr. Machine Learning Engineer",
                      "Senior Data Scientist, Platform"]:
            passed, reason = evaluate(posting(title=title), rules, NOW)
            assert not passed, title
            assert reason.startswith("title_exclude")

    def test_lead_and_manager_titles_are_excluded(self, rules):
        for title in ["Engineering Lead, Web Platform",
                      "Tech Lead, Infrastructure",
                      "Engineering Manager, Data",
                      "Technical Lead Manager - Cortex AI"]:
            assert not evaluate(posting(title=title), rules, NOW)[0], title

    def test_word_boundaries_still_protect_real_titles(self, rules):
        """§1.3 warns substring `lead` kills "Leadership"/"Lead Generation"."""
        for title in ["Software Engineer, Leadership Tools",
                      "Data Scientist, Leaderboards"]:
            passed, reason = evaluate(posting(title=title), rules, NOW)
            assert passed, f"{title!r} dropped by {reason}"

    def test_sr_does_not_match_unrelated_words(self, rules):
        """`sr` must not fire inside SRE or similar."""
        passed, reason = evaluate(posting(title="SRE, Platform"), rules, NOW)
        assert passed, f"dropped by {reason}"

    def test_ic_titles_still_pass(self, rules):
        for title in ["Software Engineer", "Machine Learning Engineer",
                      "Data Scientist", "Member of Technical Staff",
                      "Software Engineer, New Grad (2027)"]:
            passed, reason = evaluate(posting(title=title), rules, NOW)
            assert passed, f"{title!r} dropped by {reason}"


class TestYearsOfExperience:
    def test_detects_a_plain_floor(self):
        assert minimum_years_required(
            "We want 5+ years of experience in Python.") == 5

    def test_detects_the_reversed_ordering(self):
        assert minimum_years_required(
            "Experience: at least 7 years building systems.") == 7

    def test_takes_the_lower_bound_of_a_range(self):
        """"3-5 years" has a real bar of 3, not 5."""
        assert minimum_years_required(
            "3-5 years of experience required.") == 3
        assert minimum_years_required(
            "2 to 4 years of relevant experience.") == 2

    def test_takes_the_lowest_across_several_statements(self):
        text = ("3+ years of experience required. "
                "8+ years of experience preferred.")
        assert minimum_years_required(text) == 3

    def test_ignores_numbers_unrelated_to_experience(self):
        """The rule must not fire on company history or benefits copy."""
        assert minimum_years_required(
            "Founded 10 years ago, we now serve 5 million users.") is None
        assert minimum_years_required(
            "Unlimited PTO after 2 years of tenure at the company.") is None

    def test_ignores_ceilings_rather_than_floors(self):
        assert minimum_years_required(
            "Up to 5 years of experience considered.") is None

    def test_detects_a_requirement_without_the_word_experience(self):
        """OpenAI's real phrasing, which an experience-only anchor missed."""
        assert minimum_years_required(
            "- 10+ years in a quantitative role such as Data Science, "
            "B2B Demand Gen Marketing Science, Growth Analytics") == 10

    def test_the_looser_pattern_does_not_fire_on_company_trivia(self):
        """Guards added alongside the broader anchor."""
        for text in [
            "Unlimited PTO after 2 years of tenure at the company.",
            "With 10 years of operating history behind us...",
            "We celebrated our 5 years anniversary of the product.",
            "Equity with 4 years of vesting.",
        ]:
            assert minimum_years_required(text) is None, text

    def test_handles_empty_description(self):
        assert minimum_years_required("") is None
        assert minimum_years_required(None) is None

    def test_kills_a_posting_above_the_threshold(self, rules):
        passed, reason = evaluate(
            posting(description="Requires 8+ years of experience in ML."),
            rules, NOW)
        assert not passed
        assert reason.startswith("max_years_experience")

    def test_keeps_a_posting_at_or_below_the_threshold(self, rules):
        for text in ["2+ years of experience preferred.",
                     "0-2 years of experience; new grads welcome.",
                     "4 years of experience with Python."]:
            passed, reason = evaluate(posting(description=text), rules, NOW)
            assert passed, f"{text!r} dropped by {reason}"


class TestSponsorship:
    """§3.4 — a false positive here is invisible and permanent."""

    def test_kills_explicit_refusals(self, rules):
        for text in [
            "We are unable to sponsor visas for this role.",
            "This position does not sponsor employment visas.",
            "No sponsorship is available for this position.",
            "Applicants must have permanent work authorization.",
            "Candidates must be legally authorized to work without sponsorship.",
        ]:
            passed, reason = evaluate(posting(description=text), rules, NOW)
            assert not passed, f"missed: {text!r}"
            assert reason.startswith("sponsorship"), reason

    def test_does_NOT_kill_postings_that_offer_sponsorship(self, rules):
        """The failure that would delete the candidate's best roles."""
        for text in [
            "We are happy to sponsor visas for exceptional candidates.",
            "Visa sponsorship is available for this role.",
            "We offer visa sponsorship and relocation assistance.",
            "This role is eligible for sponsorship.",
            "We sponsor H-1B and green card applications.",
        ]:
            passed, reason = evaluate(posting(description=text), rules, NOW)
            assert passed, f"wrongly killed a sponsoring role: {text!r} ({reason})"

    def test_is_reported_as_its_own_rule(self, rules):
        _, reason = evaluate(
            posting(description="We cannot sponsor work visas."), rules, NOW)
        assert reason.split(":")[0] == "sponsorship", \
            "must be auditable separately from description_exclude"

    def test_silent_on_postings_that_never_mention_it(self, rules):
        assert evaluate(posting(description="Build great software."),
                        rules, NOW)[0]

    def test_a_positive_declaration_overrides_an_ambiguous_negative(self, rules):
        """Anthropic's real boilerplate, on every one of its postings.

        Lexically almost identical to a refusal, semantically the opposite.
        Matching it would delete a tier-1 company's entire board silently.
        """
        text = ("Visa sponsorship: We do sponsor visas! However, we aren't "
                "able to successfully sponsor visas for every role and every "
                "candidate.")
        passed, reason = evaluate(posting(description=text), rules, NOW)
        assert passed, f"would have deleted Anthropic's whole board ({reason})"

    def test_every_allow_term_is_negation_safe(self, rules):
        """The property that makes the override safe at all.

        An allow phrase that appears inside a refusal silently cancels it.
        This rule shipped that bug twice — "able to sponsor visas for" matched
        inside "not currently able to sponsor visas for fellows", and
        "sponsorship is available" matched inside "No sponsorship is
        available". Add new allow phrases to this list first.
        """
        refusals = [
            "no sponsorship is available for this position",
            "sponsorship is not available for our new grad positions",
            "we do not offer visa sponsorship",
            "we are not able to provide visa sponsorship",
            "we are unable to offer sponsorship for this role",
            "candidates are not eligible for visa sponsorship",
            "we are not currently able to sponsor visas for fellows",
            "we cannot sponsor visas at this time",
            "this role does not offer sponsorship",
            "applicants must not require sponsorship now or in the future",
        ]
        for term in rules.sponsorship_allow:
            for refusal in refusals:
                assert term not in refusal, (
                    f"allow term {term!r} appears inside the refusal "
                    f"{refusal!r} and would cancel it")


class TestSponsorshipExtraction:
    """Stage B needs the sentences, because Stage A cannot resolve them."""

    def test_pulls_out_the_relevant_sentences(self):
        from jobpipe.filters import extract_sponsorship_sentences
        text = ("We build great software.\n"
                "Visa Sponsorship: We are not currently able to sponsor "
                "visas for fellows.\n"
                "Unlimited PTO.")
        found = extract_sponsorship_sentences(text)
        assert len(found) == 1
        assert "not currently able to sponsor" in found[0].lower()

    def test_captures_both_sides_of_a_contradiction(self):
        """The Anthropic Fellows case Stage B has to adjudicate."""
        from jobpipe.filters import extract_sponsorship_sentences
        text = ("Visa sponsorship: We do sponsor visas!\n"
                "However we are not currently able to sponsor visas for fellows.")
        found = extract_sponsorship_sentences(text)
        assert len(found) == 2

    def test_returns_nothing_when_unmentioned(self):
        from jobpipe.filters import extract_sponsorship_sentences
        assert extract_sponsorship_sentences("Build great software.") == []
        assert extract_sponsorship_sentences(None) == []

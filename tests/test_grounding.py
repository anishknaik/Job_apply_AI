"""Grounding check. No network — the provider is faked.

This is the only check on the only generated prose in the system, so the
tests are mostly about it failing loudly: an empty result, a partial verdict,
and a claim routed to the wrong evidence all have to block rather than pass.
"""
import json

import pytest

from jobpipe import bullets, grounding
from jobpipe.providers import ProviderResponse, ScoringProvider


@pytest.fixture(scope="module")
def bank():
    return bullets.load_bank()


def claim(text="Distilled traces into Qwen2.5-7B.", about="candidate",
          supported="supported", source="dist-distill"):
    return {"claim": text, "about": about, "supported": supported,
            "source": source}


class FakeProvider(ScoringProvider):
    name = "gemini"
    known_models = ("gemini-3.1-flash-lite",)

    def __init__(self, claims=None, refused=False):
        self.payload = {"claims": claims if claims is not None else [claim()]}
        self.refused = refused
        self.calls = []

    def count_tokens(self, model, text):
        return len(text) // 4

    def cache_minimum_tokens(self, model):
        return 4096

    def score(self, *, model, system_prompt, user_text, schema):
        self.calls.append({"model": model, "system_prompt": system_prompt,
                           "user_text": user_text, "schema": schema})
        if self.refused:
            return ProviderResponse(text=None, refused=True,
                                    refusal_reason="policy")
        return ProviderResponse(text=json.dumps(self.payload),
                                input_tokens=100, output_tokens=50)


LETTER = "I distilled DeepSeek-R1 traces into Qwen2.5-7B."
POSTING = "We build clinical LLM tooling."


class TestVerdicts:
    def test_all_supported_passes(self, bank):
        result = grounding.check(FakeProvider([claim(), claim()]), bank,
                                 LETTER, POSTING, "sys")
        assert result.ok and result.accuracy == 1.0

    def test_unsupported_blocks(self, bank):
        result = grounding.check(
            FakeProvider([claim(), claim(supported="unsupported", source="")]),
            bank, LETTER, POSTING, "sys")
        assert not result.ok
        assert len(result.problems) == 1

    def test_partial_also_blocks(self, bank):
        """§2.2 blocks on false OR partial. A claim the evidence only
        half-supports is the overstatement case, which is the likelier
        failure — outright invention is rarer than inflation."""
        result = grounding.check(
            FakeProvider([claim(supported="partial")]), bank, LETTER,
            POSTING, "sys")
        assert not result.ok

    def test_accuracy_is_the_supported_share(self, bank):
        result = grounding.check(
            FakeProvider([claim(), claim(), claim(supported="partial"),
                          claim(supported="unsupported")]),
            bank, LETTER, POSTING, "sys")
        assert result.accuracy == 0.5


class TestFailingLoudly:
    def test_an_empty_extraction_raises(self, bank):
        """Zero claims is indistinguishable from a clean letter and would
        silently disable the only check on generated prose."""
        with pytest.raises(RuntimeError, match="no claims"):
            grounding.check(FakeProvider([]), bank, LETTER, POSTING, "sys")

    def test_a_refusal_raises(self, bank):
        with pytest.raises(RuntimeError, match="refused"):
            grounding.check(FakeProvider(refused=True), bank, LETTER,
                            POSTING, "sys")

    def test_it_never_returns_modified_text(self, bank):
        """§2.2: surface and stop. There is no code path here that edits the
        letter, and the result carries no corrected version to be mistaken
        for one."""
        result = grounding.check(FakeProvider(), bank, LETTER, POSTING, "sys")
        assert not hasattr(result, "corrected")
        assert not hasattr(result, "fixed_text")


class TestEvidenceRouting:
    """A claim must be checked against evidence that can settle it."""

    def test_the_posting_reaches_the_checker(self, bank):
        provider = FakeProvider()
        grounding.check(provider, bank, LETTER, "Linked Evidence maps "
                        "summaries to source.", "sys")
        assert "Linked Evidence" in provider.calls[0]["user_text"]

    def test_the_letter_reaches_the_checker(self, bank):
        provider = FakeProvider()
        grounding.check(provider, bank, LETTER, POSTING, "sys")
        assert LETTER in provider.calls[0]["user_text"]

    def test_a_letter_is_still_labelled_a_letter(self, bank):
        """The `label` parameter must leave a letter's request unchanged."""
        provider = FakeProvider()
        grounding.check(provider, bank, LETTER, POSTING, "sys")
        assert "=== THE LETTER TO CHECK ===" in provider.calls[0]["user_text"]

    def test_a_short_answer_is_labelled_as_one(self, bank):
        """§6.1: a form answer is checked by the same rules as a letter."""
        provider = FakeProvider()
        grounding.check(provider, bank, LETTER, POSTING, "sys", kind="answer")
        text = provider.calls[0]["user_text"]
        assert "=== THE APPLICATION ANSWER TO CHECK ===" in text
        assert "THE LETTER TO CHECK" not in text

    def test_subjects_are_constrained(self):
        items = grounding.GROUNDING_SCHEMA["properties"]["claims"]["items"]
        assert items["properties"]["about"]["enum"] == ["candidate",
                                                        "employer"]

    def test_verdicts_are_constrained(self):
        items = grounding.GROUNDING_SCHEMA["properties"]["claims"]["items"]
        assert set(items["properties"]["supported"]["enum"]) == {
            "supported", "unsupported", "partial"}

    def test_the_prompt_routes_employer_claims_to_the_posting(self, bank):
        prompt = grounding.build_system_prompt(bank, "PROFILE")
        assert "check against the JOB POSTING" in prompt
        assert "check against the MASTER PROFILE" in prompt

    def test_the_prompt_is_adversarial(self, bank):
        """'Does this look accurate' is a question a model answers yes to."""
        prompt = grounding.build_system_prompt(bank, "PROFILE")
        assert "Assume something is wrong" in prompt

    def test_the_whole_bank_is_available_as_evidence(self, bank):
        prompt = grounding.build_system_prompt(bank, "PROFILE")
        for bullet in bank.bullets.values():
            assert bullet.id in prompt

    def test_numbers_must_match_exactly(self, bank):
        assert "7,210 is not 'over 7,000'" in \
            grounding.build_system_prompt(bank, "PROFILE")


class TestNegativeControlIsRecorded:
    """A checker that never fails on real input proves nothing on its own.

    Post-Phase-2 §2.1 asked for a reliability figure: 10 independently
    generated letters all scored 100%. That is consistent with the writer not
    fabricating AND with the checker being permissive, so it was followed by
    an injection test. Six fabrications of different shapes, all caught:

        inflated metric      99% where the bank says 94%      -> partial
        invented scale       "led a team of twelve"           -> unsupported
        invented employer    "at Google DeepMind"             -> unsupported
        absent skill         "tuned HNSW index parameters"    -> unsupported
        overstated work      CDC architecture "from scratch"  -> partial
        employer claim       "50 million conversations"       -> unsupported

    The two graded `partial` are the important ones: both describe real work
    with the framing inflated, which is the likelier failure than outright
    invention and the one a casual read would pass.

    These are recorded rather than re-run — the injection test costs money and
    needs the network. What is pinned here is that the verdict vocabulary
    those results depend on cannot be quietly narrowed.
    """

    def test_partial_exists_as_a_distinct_verdict(self):
        """Collapsing partial into supported would let both inflation cases
        through; collapsing it into unsupported would lose the distinction
        that makes the report actionable."""
        assert "partial" in grounding.VERDICTS
        assert set(grounding.VERDICTS) == {"supported", "unsupported",
                                           "partial"}

    def test_partial_blocks(self, bank):
        result = grounding.check(
            FakeProvider([claim(supported="partial")]), bank, LETTER,
            POSTING, "sys")
        assert not result.ok

    def test_employer_claims_are_checkable(self):
        """'Abridge processes 50 million conversations' is unsupported by the
        posting; without the employer subject there is nothing to check it
        against and it would pass."""
        assert "employer" in grounding.SUBJECTS

    def test_exact_number_matching_is_instructed(self, bank):
        """The 94% -> 99% case turns entirely on this line."""
        assert "Numbers must match exactly" in \
            grounding.build_system_prompt(bank, "PROFILE")


class TestReport:
    def test_a_clean_report_says_so(self, bank):
        result = grounding.check(FakeProvider(), bank, LETTER, POSTING, "sys")
        assert "grounding OK" in result.report()

    def test_a_failing_report_quotes_the_claim(self, bank):
        bad = claim(text="I led a team of twelve.", supported="unsupported",
                    source="")
        result = grounding.check(FakeProvider([bad]), bank, LETTER, POSTING,
                                 "sys")
        report = result.report()
        assert "I led a team of twelve." in report
        assert "unsupported" in report

    def test_the_report_shows_nearest_evidence_for_partials(self, bank):
        near = claim(text="Built CDC infrastructure from scratch.",
                     supported="partial",
                     source="mirrored an existing AWS architecture")
        result = grounding.check(FakeProvider([near]), bank, LETTER, POSTING,
                                 "sys")
        assert "nearest evidence" in result.report()


class TestWhatIsUnderTest:
    """The system prompt, the `claim` field and the heading must name the same
    thing. When they disagreed, the checker took 38 of 45 claims from the job
    posting's requirement list and judged them as the candidate's."""

    @staticmethod
    def _claim_field(schema):
        return schema["properties"]["claims"]["items"]["properties"]["claim"]

    def test_the_letter_request_is_unchanged(self, bank):
        assert grounding.schema_for("letter") is grounding.GROUNDING_SCHEMA
        prompt = grounding.build_system_prompt(bank, "PROFILE")
        assert prompt == grounding.build_system_prompt(bank, "PROFILE",
                                                       kind="letter")
        assert prompt.startswith("You are checking a cover letter")
        assert "ONLY from" not in prompt

    def test_an_answer_prompt_never_calls_it_a_letter(self, bank):
        prompt = grounding.build_system_prompt(bank, "PROFILE", kind="answer")
        framing = prompt.split("=== BULLET BANK ===")[0]
        assert "letter" not in framing.lower()
        assert "job application form" in framing

    def test_an_answer_is_told_the_posting_is_not_a_source_of_claims(
            self, bank):
        prompt = grounding.build_system_prompt(bank, "PROFILE", kind="answer")
        assert "never extract a claim from it" in prompt

    def test_the_scope_rule_is_on_the_field_the_model_fills(self, bank):
        """HANDOFF §5: a rule stated in prose and missing at the point of use
        does not hold."""
        provider = FakeProvider()
        grounding.check(provider, bank, LETTER, POSTING, "sys", kind="answer")
        claim = self._claim_field(provider.calls[0]["schema"])
        assert "never extract a claim from it" in claim["description"]

    def test_the_answer_schema_leaves_the_letter_schema_alone(self):
        grounding.schema_for("answer")
        claim = self._claim_field(grounding.GROUNDING_SCHEMA)
        assert claim["description"].endswith("from the letter.")

    def test_an_unknown_kind_is_refused(self, bank):
        with pytest.raises(ValueError, match="letter"):
            grounding.build_system_prompt(bank, "PROFILE", kind="memo")

"""Cover letter generation. No network — the provider is faked.

This is the only module in Phase 2 that writes prose, so it is the only place
a fabricated claim can enter. The tests are about the guardrails: which
exemplars are allowed to shape the voice, which phrases are refused, and that
a bad draft is surfaced rather than quietly returned.
"""
import json

import pytest

from jobpipe import bullets, letters
from jobpipe.providers import ProviderResponse, ScoringProvider


@pytest.fixture(scope="module")
def bank():
    return bullets.load_bank()


PARAS = {
    "why_this_role": "Something specific about the team and its problem.",
    "primary_evidence": "Distilled traces into Qwen2.5-7B, 43% to 94%.",
    "secondary_evidence": "Built the evaluation harness around it.",
    "closing": "How do you handle evaluation when ground truth is scarce?",
}


class FakeProvider(ScoringProvider):
    name = "gemini"
    known_models = ("gemini-3.5-flash",)

    def __init__(self, payloads, refused=False):
        self.payloads = list(payloads)
        self.refused = refused
        self.calls = []

    def count_tokens(self, model, text):
        return len(text) // 4

    def cache_minimum_tokens(self, model):
        return 4096

    def score(self, *, model, system_prompt, user_text, schema):
        self.calls.append({"model": model, "user_text": user_text,
                           "system_prompt": system_prompt, "schema": schema})
        if self.refused:
            return ProviderResponse(text=None, refused=True,
                                    refusal_reason="policy")
        return ProviderResponse(text=json.dumps(self.payloads.pop(0)),
                                input_tokens=100, output_tokens=50)


ROW = {"id": 1, "company": "Abridge", "title": "Software Engineer, GenAI",
       "location": "NYC", "description": "Clinical LLM evaluation at scale."}


class TestVoiceCorpusIsCurated:
    """§4.1 — the corpus shapes the output, so what is in it matters."""

    def test_the_cofounder_letter_is_excluded(self):
        """McKinsey's letter opens 'As the co-founder of an AI company', a
        claim absent from master_profile.md. A few-shot example containing an
        unsupported claim teaches the model to make it."""
        chosen = {name for name, _ in letters.VOICE_EXEMPLARS}
        assert not any("McKinsey" in name for name in chosen)

    def test_the_templated_letters_are_excluded(self):
        """CityOfHope, Fanatics and Fervorly all close on the identical
        'bring that combination of X, Y and Z' scaffold this prompt bans."""
        chosen = {name for name, _ in letters.VOICE_EXEMPLARS}
        for weak in ("CityOfHope", "Fanatics", "Fervorly"):
            assert not any(weak in name for name in chosen)

    def test_norc_is_the_anchor(self):
        chosen = [name for name, _ in letters.VOICE_EXEMPLARS]
        assert any("NORC" in name for name in chosen)

    def test_every_exemplar_carries_a_reason(self):
        for _, note in letters.VOICE_EXEMPLARS:
            assert len(note) > 30, "curation without a reason rots"

    def test_the_letter_citing_this_project_is_excluded(self):
        """ErinBakers says "the pipeline now covers 142 companies" and quotes
        this project's own calibration figures. master_profile.md is explicit:
        the current period is availability, not experience, and "if a
        generated document ever describes current project work, that is
        fabrication." The claims are true and the profile still forbids
        citing them, so an exemplar carrying them teaches the wrong thing."""
        chosen = {name for name, _ in letters.VOICE_EXEMPLARS}
        assert not any("ErinBakers" in name for name in chosen)

    def test_the_letter_using_a_banned_phrase_is_excluded(self):
        """Granules uses "welcome the chance", which is in BANNED_PHRASES —
        one of his own tells. Showing the model a pattern the prompt bans is
        self-defeating."""
        chosen = {name for name, _ in letters.VOICE_EXEMPLARS}
        assert not any("Granules" in name for name in chosen)

    def test_the_ban_list_overlaps_the_corpus_it_was_measured_from(self):
        """KNOWN, and recorded here 2026-08-30 rather than left to be
        rediscovered.

        Four of the six exemplars contain "welcome the chance" — including
        BOTH that have been in use since Phase 2. This is not an oversight in
        the curation: BANNED_PHRASES was derived FROM this corpus ("Banned
        because they are HIS tells"), so the letters it was measured from
        necessarily contain them.

        The consequence is that the prompt demonstrates a phrase it forbids in
        the same breath. That is exactly why `banned_used()` and its single
        retry are load-bearing rather than belt-and-braces — guidance never
        substitutes for the check, and this is the sharpest example of why.

        Pinned at the current count so the overlap cannot grow unnoticed.
        Excluding every contaminated letter would cut the corpus to two, which
        is the problem the 2026-08-30 audit set out to fix. The real fix —
        truncating exemplars before the closing, where the tells cluster — is
        a slice 4 decision, when Claude writes the prose.
        """
        contaminated = [
            note for note, text in letters.load_exemplars()
            if any(b.lower() in text.lower() for b in letters.BANNED_PHRASES)]
        assert len(contaminated) <= 4, (
            f"exemplar contamination grew to {len(contaminated)}; the corpus "
            f"is teaching more banned phrases than it did at the audit")

    def test_the_corpus_is_large_enough_to_carry_a_voice(self):
        """Two exemplars was the whole corpus until the 2026-08-30 audit.
        Examples alone drift back toward generic model voice by the third
        paragraph; too few is what makes that happen fastest."""
        assert len(letters.VOICE_EXEMPLARS) >= 4

    def test_a_missing_corpus_raises_rather_than_generating_generic(self,
                                                                    tmp_path):
        with pytest.raises(letters.LetterError, match="no voice exemplars"):
            letters.load_exemplars(tmp_path)


class TestBannedPhrases:
    @pytest.mark.parametrize("phrase", [
        "welcome the chance", "bring that combination",
        "I am writing to apply", "look forward to discussing",
    ])
    def test_the_measured_tells_are_banned(self, phrase):
        assert phrase in letters.BANNED_PHRASES

    def test_detection_is_case_insensitive(self):
        assert letters.banned_used("I WELCOME THE CHANCE to talk")

    def test_clean_text_reports_nothing(self):
        assert letters.banned_used("A specific question about the work.") == []

    def test_detection_names_the_offender(self):
        found = letters.banned_used("I look forward to discussing this.")
        assert "look forward to discussing" in found


class TestClosingShapeRule:
    """Phrase bans alone moved the tell rather than removing it — three
    drafts closed on the same template with different words."""

    def test_the_rule_forbids_the_eagerness_construction(self):
        rule = letters.CLOSING_SHAPE_RULE.lower()
        assert "subject is \"i\"" in rule
        assert "eagerness" in rule

    def test_it_offers_concrete_alternatives(self):
        assert letters.CLOSING_SHAPE_RULE.count("*") >= 4

    def test_it_forbids_summarising_the_letter(self):
        assert "summarise the letter" in letters.CLOSING_SHAPE_RULE

    def test_the_rule_reaches_the_prompt(self, bank):
        prompt = letters.build_system_prompt(bank, "PROFILE",
                                             [("note", "example text")])
        assert letters.CLOSING_SHAPE_RULE in prompt


class TestSystemPrompt:
    def _prompt(self, bank):
        return letters.build_system_prompt(bank, "PROFILE TEXT",
                                           [("a note", "example letter")])

    def test_it_requires_support_not_consistency(self, bank):
        assert "Not 'consistent with' — supported" in self._prompt(bank)

    def test_it_forbids_raising_sponsorship(self, bank):
        """§4.3 — a positioning decision, not concealment."""
        prompt = self._prompt(bank)
        assert "Never mention visa status" in prompt
        assert "explicitly asks" in prompt

    def test_the_whole_bank_is_offered_as_evidence(self, bank):
        prompt = self._prompt(bank)
        for bullet in bank.bullets.values():
            assert bullet.id in prompt

    def test_it_encodes_the_position_not_credential_rule(self, bank):
        """Derived by reading the letters: the weak three follow the opening
        with a credential, the strong one with a position."""
        prompt = self._prompt(bank)
        assert "State a position, do not recite a credential" in prompt
        assert "leaderboard exercise" in prompt

    def test_it_is_byte_stable(self, bank):
        assert self._prompt(bank) == self._prompt(bank)


class TestGeneration:
    def test_a_clean_draft_returns_first_time(self, bank):
        provider = FakeProvider([PARAS])
        result = letters.generate_letter(provider, bank, ROW, "sys", "m")
        assert result["attempts"] == 1
        assert result["banned_used"] == []
        assert "Qwen2.5-7B" in result["text"]

    def test_a_banned_phrase_triggers_exactly_one_retry(self, bank):
        bad = dict(PARAS, closing="I welcome the chance to discuss.")
        provider = FakeProvider([bad, PARAS])
        result = letters.generate_letter(provider, bank, ROW, "sys", "m")
        assert len(provider.calls) == 2
        assert result["attempts"] == 2
        assert result["banned_used"] == []

    def test_the_retry_names_the_offending_phrase(self, bank):
        bad = dict(PARAS, closing="I welcome the chance to discuss.")
        provider = FakeProvider([bad, PARAS])
        letters.generate_letter(provider, bank, ROW, "sys", "m")
        assert "welcome the chance" in provider.calls[1]["user_text"]

    def test_it_does_not_loop_forever(self, bank):
        """Unbounded retries would quietly spend money on the expensive
        model. Two attempts, then surface it for a human."""
        bad = dict(PARAS, closing="I welcome the chance to discuss.")
        provider = FakeProvider([bad, bad])
        result = letters.generate_letter(provider, bank, ROW, "sys", "m")
        assert len(provider.calls) == 2
        assert result["banned_used"] == ["welcome the chance"]

    def test_a_refusal_raises(self, bank):
        with pytest.raises(letters.LetterError, match="refused"):
            letters.generate_letter(FakeProvider([], refused=True), bank,
                                    ROW, "sys", "m")

    def test_empty_prose_raises(self, bank):
        empty = {k: "" for k in PARAS}
        with pytest.raises(letters.LetterError, match="empty"):
            letters.generate_letter(FakeProvider([empty]), bank, ROW,
                                    "sys", "m")

    def test_selected_bullets_are_offered_to_the_writer(self, bank):
        provider = FakeProvider([PARAS])
        chosen = bank.select(["dist-distill", "dist-ablation"])
        letters.generate_letter(provider, bank, ROW, "sys", "m",
                                selected=chosen)
        sent = provider.calls[0]["user_text"]
        assert "dist-distill" in sent


class TestStructure:
    def test_the_schema_enforces_four_named_paragraphs(self):
        required = letters.LETTER_SCHEMA["required"]
        assert required == ["why_this_role", "primary_evidence",
                            "secondary_evidence", "closing"]

    def test_body_joins_with_blank_lines(self):
        assert letters.body_text(PARAS).count("\n\n") == 3

    def test_body_skips_empty_paragraphs(self):
        assert letters.body_text(dict(PARAS, closing="")).count("\n\n") == 2

    def test_length_target_matches_his_own_letters(self):
        """His run 360-431 words. The first generated batch came in at
        249-309 and read visibly thin next to them."""
        assert letters.word_count_ok(" ".join(["w"] * 390))
        assert letters.word_count_ok(" ".join(["w"] * 360))
        assert not letters.word_count_ok(" ".join(["w"] * 280))
        assert not letters.word_count_ok(" ".join(["w"] * 600))


class TestLetterScaffolding:
    """His letters have a date, salutation and sign-off. The first generated
    batch had none, which is the first thing he noticed. All of it is
    assembled from fixed data — a model asked to write a salutation will
    eventually invent a hiring manager's name."""

    ROW = {"company": "Abridge", "title": "SWE"}
    META = {"name": "Jane Q. Example", "email": "a@b.com",
            "phone": "(555) 010-0000"}

    def test_bare_call_returns_only_the_body(self):
        assert letters.assemble(PARAS) == letters.body_text(PARAS)

    def test_full_letter_has_the_scaffolding(self):
        out = letters.assemble(PARAS, row=self.ROW, meta=self.META,
                               today="August 14, 2026")
        assert out.startswith("Jane Q. Example")
        assert "August 14, 2026" in out
        assert "Dear Abridge Hiring Team," in out
        assert out.rstrip().endswith("Sincerely,\nJane Q. Example")

    def test_the_body_survives_intact(self):
        out = letters.assemble(PARAS, row=self.ROW, meta=self.META)
        assert letters.body_text(PARAS) in out

    def test_salutation_names_the_company(self):
        assert letters.salutation({"company": "Sierra"}) == \
            "Dear Sierra Hiring Team,"

    def test_salutation_degrades_without_a_company(self):
        assert letters.salutation({"company": ""}) == "Dear Hiring Team,"

    def test_no_invented_recipient(self):
        """Nothing in the scaffolding is generated, so there is no path by
        which a hiring manager's name could be fabricated."""
        out = letters.assemble(PARAS, row=self.ROW, meta=self.META)
        assert "Dear Abridge Hiring Team," in out

    def test_the_date_has_no_leading_zero(self):
        """'August 05, 2026' is not how he writes it, and %-d is not
        portable to Windows."""
        import re
        out = letters.assemble(PARAS, row=self.ROW, meta=self.META)
        assert not re.search(r"\b[A-Z][a-z]+ 0\d,", out)

    def test_prose_model_is_not_the_scoring_model(self):
        """§5: do not cost-optimise this call."""
        from jobpipe.providers import DEFAULT_MODELS, DEFAULT_PROSE_MODELS
        assert DEFAULT_PROSE_MODELS["gemini"] != DEFAULT_MODELS["gemini"]

    def test_the_prose_model_is_pinned_not_an_alias(self):
        """An alias silently changes model under a calibrated prompt."""
        from jobpipe.providers import DEFAULT_PROSE_MODELS
        assert "latest" not in DEFAULT_PROSE_MODELS["gemini"]


class TestBlockNamesMatchTheAssembledLetter:
    """The per-slot label must name the block it actually points at.

    `measure.SLOT_NAMES` used to be a second, hand-written copy of the body
    slots. It listed four names while `assemble()` emits seven blocks, so
    every label was two positions off — `[0]` was reported as "why this role"
    when it is the name/contact/date header, and `[5]` came out as an
    unnamed "slot 5" when it is the closing. `cli measure` calls that number
    "the number that says which instruction is wrong", so it named the wrong
    instruction on every line.

    These tests fail if anyone adds, removes or reorders a block in
    `assemble()` without updating `BLOCK_NAMES` beside it.
    """

    ROW = {"company": "Abridge", "title": "SWE"}
    META = {"name": "Jane Q. Example", "email": "a@b.com",
            "phone": "(555) 010-0000"}

    def _blocks(self):
        from jobpipe import measure
        return measure.paragraphs(
            letters.assemble(PARAS, row=self.ROW, meta=self.META,
                             today="August 14, 2026"))

    def test_every_block_has_a_name_and_every_name_a_block(self):
        assert len(self._blocks()) == len(letters.BLOCK_NAMES)

    def test_each_body_slot_lands_under_its_own_label(self):
        blocks = self._blocks()
        for offset, (key, name) in enumerate(letters.BODY_SLOTS):
            index = letters.BODY_OFFSET + offset
            assert blocks[index] == PARAS[key], f"{name} is not at [{index}]"
            assert letters.BLOCK_NAMES[index] == name

    def test_the_scaffolding_blocks_are_named_too(self):
        blocks = self._blocks()
        assert letters.BLOCK_NAMES[0] == "header"
        assert blocks[0].startswith("Jane Q. Example")
        assert letters.BLOCK_NAMES[1] == "salutation"
        assert blocks[1] == "Dear Abridge Hiring Team,"
        assert letters.BLOCK_NAMES[-1] == "sign-off"
        assert blocks[-1].startswith("Sincerely,")

    def test_measure_labels_are_the_same_object_not_a_copy(self):
        """A copy is what drifted in the first place."""
        from jobpipe import measure
        assert measure.SLOT_NAMES is letters.BLOCK_NAMES

    def test_the_schema_requires_exactly_the_body_slots(self):
        assert letters.LETTER_SCHEMA["required"] == list(letters.BODY_KEYS)
        assert (tuple(letters.LETTER_SCHEMA["properties"])
                == letters.BODY_KEYS)

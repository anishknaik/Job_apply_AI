"""Bullet selection. No network — the provider is faked throughout.

The load-bearing property is that the model cannot introduce anything. The
schema constrains it, `Bank.select` re-checks, and the ranking step can only
reorder. Each of those is tested as an impossibility rather than a behaviour.
"""
import json

import pytest

from jobpipe import bullets, tailor
from jobpipe.providers import ProviderResponse, ScoringProvider


@pytest.fixture(scope="module")
def bank():
    return bullets.load_bank()


class FakeProvider(ScoringProvider):
    name = "gemini"
    known_models = ("gemini-3.1-flash-lite",)

    def __init__(self, payload=None, refused=False):
        self.payload = payload
        self.refused = refused
        self.calls = []

    def count_tokens(self, model, text):
        return len(text) // 4

    def cache_minimum_tokens(self, model):
        return 2048

    def score(self, *, model, system_prompt, user_text, schema):
        self.calls.append({"model": model, "system_prompt": system_prompt,
                           "user_text": user_text, "schema": schema})
        if self.refused:
            return ProviderResponse(text=None, refused=True,
                                    refusal_reason="policy")
        return ProviderResponse(text=json.dumps(self.payload),
                                input_tokens=100, output_tokens=50)


def payload(**kw):
    base = {"posting_skills": ["qlora", "evaluation"], "variant": "ai_ml",
            "reasoning": "why", "selected": ["dist-distill", "dist-ablation"],
            "skill_order": ["GenAI & LLM"]}
    base.update(kw)
    return base


ROW = {"id": 1, "company": "Cohere", "title": "Applied AI Engineer",
       "location": "NYC", "description": "Build agent pipelines with QLoRA."}


class TestSchemaConstrainsTheModel:
    """§2.1 — fabrication made structurally difficult, not just forbidden."""

    def test_bullet_ids_are_an_enum_of_the_bank(self, bank):
        schema = tailor.build_selection_schema(bank)
        allowed = schema["properties"]["selected"]["items"]["enum"]
        assert set(allowed) == set(bank.bullets)

    def test_the_enum_is_not_merely_a_description(self, bank):
        """An unknown id must be rejected by the API, not by prose."""
        schema = tailor.build_selection_schema(bank)
        assert "enum" in schema["properties"]["selected"]["items"]

    def test_variant_is_constrained(self, bank):
        schema = tailor.build_selection_schema(bank)
        assert schema["properties"]["variant"]["enum"] == list(tailor.VARIANTS)

    def test_skill_sections_are_constrained_to_surfaceable_ones(self, bank):
        """Exposure-tier sections must not even be orderable."""
        schema = tailor.build_selection_schema(bank)
        allowed = set(schema["properties"]["skill_order"]["items"]["enum"])
        assert allowed == set(bank.surfaceable_skills())

    def test_a_minimum_selection_size_is_enforced_structurally(self, bank):
        """Asking for ~16 in prose returned 9 against a real posting. The
        renderer drops what will not fit and says so, but under-selection is
        invisible — it just yields a thin resume."""
        schema = tailor.build_selection_schema(bank)
        assert schema["properties"]["selected"]["minItems"] == \
            min(tailor.MIN_BULLETS, len(bank.bullets))

    def test_the_floor_cannot_exceed_the_bank(self, bank):
        """A bank smaller than MIN_BULLETS must stay satisfiable."""
        schema = tailor.build_selection_schema(bank)
        assert schema["properties"]["selected"]["minItems"] <= len(bank.bullets)

    def test_there_is_no_free_text_output_field(self, bank):
        """`reasoning` is the only prose, and it never reaches the page."""
        props = tailor.build_selection_schema(bank)["properties"]
        assert "bullet_text" not in props and "text" not in props


class TestSystemPrompt:
    def test_every_bullet_appears(self, bank):
        prompt = tailor.build_system_prompt(bank)
        for bullet in bank.bullets.values():
            assert bullet.id in prompt

    def test_it_is_byte_stable_across_calls(self, bank):
        """Caching only fires on an identical prefix."""
        assert tailor.build_system_prompt(bank) == \
            tailor.build_system_prompt(bank)

    def test_it_forbids_writing(self, bank):
        prompt = tailor.build_system_prompt(bank).lower()
        assert "you do not write" in prompt
        assert "may not author" in prompt

    def test_it_says_absence_is_the_honest_answer(self, bank):
        """§2.3 — the rule that stops keyword-driven invention."""
        assert "Absence is honest" in tailor.build_system_prompt(bank)

    def test_it_instructs_concentration(self, bank):
        """Measured: one bullet from ten roles loses a third of the page."""
        assert "Concentrate." in tailor.build_system_prompt(bank)

    def test_exposure_skills_are_not_offered(self, bank):
        """Compared as whole comma-separated entries, not substrings: the
        Exposure skill 'C' occurs inside 'CNN' and 'ChromaDB', so a naive
        `in` check fails on correct output. Same substring trap as Phase 1's
        `intern` matching 'Internal'."""
        prompt = tailor.build_system_prompt(bank)
        section = prompt.split("SKILL SECTIONS AVAILABLE")[1]
        offered = set()
        for line in section.splitlines():
            if ":" not in line:
                continue
            offered.update(e.strip() for e in line.split(":", 1)[1].split(","))
        assert not (offered & bank.exposure_skills())


class TestSelection:
    def test_returns_resolved_bullets(self, bank):
        provider = FakeProvider(payload())
        result = tailor.select_bullets(provider, bank, ROW, "sys")
        assert result["bullet_ids"] == ["dist-distill", "dist-ablation"]
        assert result["variant"] == "ai_ml"

    def test_an_unknown_id_raises(self, bank):
        """Belt and braces: the enum should prevent it, but a provider that
        ignores enums must fail loudly rather than silently drop a bullet."""
        provider = FakeProvider(payload(selected=["dist-distill", "invented"]))
        with pytest.raises(bullets.BankError):
            tailor.select_bullets(provider, bank, ROW, "sys")

    def test_an_empty_selection_raises(self, bank):
        """Otherwise this renders an almost-blank resume and says nothing."""
        provider = FakeProvider(payload(selected=[]))
        with pytest.raises(RuntimeError, match="no bullets"):
            tailor.select_bullets(provider, bank, ROW, "sys")

    def test_a_refusal_raises(self, bank):
        with pytest.raises(RuntimeError, match="refused"):
            tailor.select_bullets(FakeProvider(refused=True), bank, ROW, "sys")

    def test_an_unknown_variant_falls_back(self, bank):
        provider = FakeProvider(payload(variant="nonsense"))
        assert tailor.select_bullets(provider, bank, ROW,
                                     "sys")["variant"] == "ai_ml"

    def test_the_posting_reaches_the_model(self, bank):
        provider = FakeProvider(payload())
        tailor.select_bullets(provider, bank, ROW, "sys")
        sent = provider.calls[0]["user_text"]
        assert "Cohere" in sent and "QLoRA" in sent


class TestRankingCannotInvent:
    """§3.1a: preference between honest options, nothing more."""

    def test_ranking_preserves_the_models_order(self, bank):
        ids = ["nec-yolo", "dist-distill"]
        ranked = tailor.rank_selection(bank, ids, ["qlora"])
        assert [b.id for b in ranked] == ids

    def test_ranking_never_adds_a_bullet(self, bank):
        ids = ["dist-distill", "dist-ablation"]
        assert len(tailor.rank_selection(bank, ids, ["qlora"])) == len(ids)

    def test_ranking_never_alters_text(self, bank):
        before = bank.bullets["dist-distill"].render_text()
        ranked = tailor.rank_selection(bank, ["dist-distill"], ["qlora"])
        assert ranked[0].render_text() == before

    def test_variant_mapping_covers_every_phase_1_archetype(self):
        from jobpipe import scoring
        mapped = set(tailor.VARIANT_FOR_ARCHETYPE) | {"non_technical"}
        assert mapped == set(scoring.ARCHETYPES)

    def test_every_mapping_points_at_a_real_variant(self):
        assert set(tailor.VARIANT_FOR_ARCHETYPE.values()) <= set(tailor.VARIANTS)

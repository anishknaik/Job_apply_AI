"""Phase 7A, slice 1: the grounding checker's output ceiling.

On 2026-11-05 Flash Lite looped on one Chime draft and produced ~285k
characters of claims, identically twice, with the JSON cut off (HANDOFF §8).
Drafting at volume in an apply session will hit it again. The fix is a
ceiling on the checker's output and a plain, named failure when it is hit -
never a pass, never a bare parse error.

No network and no gitignored files: the bank is built by hand and the
providers' clients are faked, so this passes on a clean checkout.
"""
import json
import types as pytypes

import pytest

from jobpipe import grounding
from jobpipe.bullets import Bank, Bullet
from jobpipe.providers import ProviderResponse, ScoringProvider
from jobpipe.providers.anthropic_provider import AnthropicProvider
from jobpipe.providers.gemini_provider import GeminiProvider
from jobpipe.providers.vertex_provider import VertexGeminiProvider


def bank():
    bullet = Bullet(id="b1", role="r1", text="Built a thing.", source="profile §1",
                    archetypes=("ai_ml",), skills=("python",), metrics=False, rank=1)
    return Bank(meta={"name": "Jane Example"}, education={}, roles={"r1": {}},
                bullets={"b1": bullet}, skills={})


def claim(text="Built a thing.", supported="supported"):
    return {"claim": text, "about": "candidate", "supported": supported, "source": "b1"}


class Uncapped(ScoringProvider):
    """A provider that knows nothing of ceilings: the base score_capped must
    still reach its score(), as every existing test double relies on."""
    name = "gemini"

    def __init__(self, text=None, truncated=False):
        self.text = text if text is not None else json.dumps({"claims": [claim()]})
        self.truncated = truncated
        self.calls = []

    def count_tokens(self, model, text):
        return 1

    def cache_minimum_tokens(self, model):
        return 1

    def score(self, *, model, system_prompt, user_text, schema):
        self.calls.append(model)
        return ProviderResponse(text=self.text, truncated=self.truncated)


class Capped(Uncapped):
    def score_capped(self, *, max_output_tokens, **kw):
        self.calls.append(("capped", max_output_tokens))
        return ProviderResponse(text=self.text, truncated=self.truncated)


class TestTheCeiling:
    def test_the_checker_asks_for_a_ceiling(self, monkeypatch):
        monkeypatch.delenv("JOBPIPE_GROUNDING_MAX_OUTPUT_TOKENS", raising=False)
        provider = Capped()
        grounding.check(provider, bank(), "Built a thing.", "posting", "sys", model="m")
        assert provider.calls == [("capped", grounding.DEFAULT_MAX_OUTPUT_TOKENS)]

    def test_the_ceiling_is_overridable_but_never_zero(self, monkeypatch):
        monkeypatch.setenv("JOBPIPE_GROUNDING_MAX_OUTPUT_TOKENS", "16384")
        assert grounding.max_output_tokens() == 16384
        monkeypatch.setenv("JOBPIPE_GROUNDING_MAX_OUTPUT_TOKENS", "0")
        assert grounding.max_output_tokens() == grounding.DEFAULT_MAX_OUTPUT_TOKENS
        monkeypatch.setenv("JOBPIPE_GROUNDING_MAX_OUTPUT_TOKENS", "lots")
        assert grounding.max_output_tokens() == grounding.DEFAULT_MAX_OUTPUT_TOKENS

    def test_a_provider_without_a_ceiling_still_works(self):
        """The base class routes score_capped to score, so a double written
        before ceilings existed is unchanged and the check still runs."""
        provider = Uncapped()
        result = grounding.check(provider, bank(), "Built a thing.", "posting", "sys", model="m")
        assert result.ok and provider.calls == ["m"]

    def test_hitting_the_ceiling_is_a_named_loop_error(self):
        looped = Capped(text='{"claims": [' + ('{"claim": "x", "about": "candidate", '
                                                '"supported": "supported", "source": "b1"},') * 500,
                        truncated=True)
        with pytest.raises(grounding.GroundingLoopError, match="the checker looped"):
            grounding.check(looped, bank(), "Built a thing.", "posting", "sys", model="m")

    def test_a_cut_off_object_without_the_flag_is_still_a_loop_error(self):
        """A provider that cannot report truncation hands back a half object;
        that must read as the checker failing, not as a pass or a stack trace."""
        with pytest.raises(grounding.GroundingLoopError, match="did not return one JSON object"):
            grounding.check(Uncapped(text='{"claims": [{"claim": "x"'), bank(),
                            "Built a thing.", "posting", "sys", model="m")

    def test_a_loop_error_is_a_runtime_error_so_the_tool_fails_closed(self):
        assert issubclass(grounding.GroundingLoopError, RuntimeError)


# ---------------------------------------------------------------------------
# The real providers set the ceiling and report when it was hit
# ---------------------------------------------------------------------------

def _vertex(response):
    p = object.__new__(VertexGeminiProvider)
    p._project, p._location, p._metered = "test-project", "global", False
    calls = []

    def generate_content(*, model, contents, config):
        calls.append(config)
        return response
    p._client = pytypes.SimpleNamespace(models=pytypes.SimpleNamespace(
        generate_content=generate_content))
    return p, calls


def _vertex_reply(finish="STOP"):
    return pytypes.SimpleNamespace(
        text='{"claims": []}', prompt_feedback=None,
        candidates=[pytypes.SimpleNamespace(finish_reason=finish)],
        usage_metadata=pytypes.SimpleNamespace(prompt_token_count=1, candidates_token_count=1,
                                               cached_content_token_count=0))


class TestVertex:
    def test_score_capped_sets_the_ceiling_and_score_does_not(self):
        p, calls = _vertex(_vertex_reply())
        p.score_capped(max_output_tokens=4096, model="m", system_prompt="s", user_text="u",
                       schema={"type": "object"})
        p.score(model="m", system_prompt="s", user_text="u", schema={"type": "object"})
        assert calls[0].max_output_tokens == 4096
        assert calls[1].max_output_tokens is None, "scoring is not capped; only the checker is"

    def test_max_tokens_is_reported_as_truncated_not_refused(self):
        p, _ = _vertex(_vertex_reply("MAX_TOKENS"))
        r = p.score_capped(max_output_tokens=10, model="m", system_prompt="s", user_text="u",
                           schema={"type": "object"})
        assert r.truncated is True and r.refused is False

    def test_a_clean_stop_is_not_truncated(self):
        p, _ = _vertex(_vertex_reply())
        r = p.score(model="m", system_prompt="s", user_text="u", schema={"type": "object"})
        assert r.truncated is False


class TestDeveloperApi:
    def test_the_ceiling_goes_into_generation_config(self):
        p = object.__new__(GeminiProvider)
        calls = []

        def create(**kw):
            calls.append(kw)
            return pytypes.SimpleNamespace(output_text='{"claims": []}', usage=None)
        p._client = pytypes.SimpleNamespace(interactions=pytypes.SimpleNamespace(create=create))
        p.score_capped(max_output_tokens=4096, model="m", system_prompt="s", user_text="u",
                       schema={"type": "object"})
        p.score(model="m", system_prompt="s", user_text="u", schema={"type": "object"})
        assert calls[0]["generation_config"] == {"temperature": 0, "max_output_tokens": 4096}
        assert calls[1]["generation_config"] == {"temperature": 0}


class TestAnthropic:
    def _provider(self, stop_reason):
        p = object.__new__(AnthropicProvider)
        p._system_blocks = lambda text: [{"type": "text", "text": text}]
        calls = []

        def create(**kw):
            calls.append(kw)
            return pytypes.SimpleNamespace(
                content=[pytypes.SimpleNamespace(type="text", text='{"claims": []}')],
                usage=pytypes.SimpleNamespace(input_tokens=1, output_tokens=1),
                stop_reason=stop_reason)
        p._client = pytypes.SimpleNamespace(messages=pytypes.SimpleNamespace(create=create))
        return p, calls

    def test_the_ceiling_is_max_tokens_and_hitting_it_is_truncated(self):
        p, calls = self._provider("max_tokens")
        r = p.score_capped(max_output_tokens=2048, model="m", system_prompt="s", user_text="u",
                           schema={"type": "object"})
        assert calls[0]["max_tokens"] == 2048
        assert r.truncated is True and r.refused is False

    def test_plain_score_keeps_its_old_ceiling(self):
        p, calls = self._provider("end_turn")
        r = p.score(model="m", system_prompt="s", user_text="u", schema={"type": "object"})
        assert calls[0]["max_tokens"] == 1024 and r.truncated is False

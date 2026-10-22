"""Revenium metering. No network — the middleware is never imported here.

What matters is that metering is invisible when off and accurate when on:

  * with no key, a call is byte-identical to before — the unpatched SDK
    rejects a `usage_metadata` keyword, so sending one would break scoring;
  * with metering on, each call carries the stage and posting it belongs to,
    and a Pro escalation is told apart from the Flash call before it, which is
    the whole question the dashboard exists to answer.
"""
import types as pytypes

import pytest

from jobpipe import grounding, metering, scoring
from jobpipe.providers.vertex_provider import VertexGeminiProvider

from test_vertex_provider import FakeModels, provider_with, reply


class MeteredModels(FakeModels):
    """Accepts the keyword the patched SDK accepts."""

    def generate_content(self, *, model, contents, config, usage_metadata):
        self.calls.append({"model": model, "usage_metadata": usage_metadata})
        return self.response


def metered_provider(response):
    p = provider_with(response)
    p._client = pytypes.SimpleNamespace(models=MeteredModels(response))
    p._metered = True
    return p


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.delenv("REVENIUM_METERING_API_KEY", raising=False)
    monkeypatch.delenv("JOBPIPE_METERING_ENV", raising=False)
    metering.reset_for_tests()
    yield
    metering.reset_for_tests()


class TestOffByDefault:
    def test_no_key_means_off(self):
        assert metering.enabled() is False

    def test_a_blank_key_is_no_key(self, monkeypatch):
        monkeypatch.setenv("REVENIUM_METERING_API_KEY", "   ")
        assert metering.enabled() is False

    def test_an_unmetered_call_sends_no_extra_keyword(self):
        """FakeModels takes no `usage_metadata`; passing one would raise."""
        p = provider_with(reply())
        p.score(model="gemini-2.5-flash", system_prompt="s", user_text="u",
                schema={"type": "object"})
        assert len(p._client.models.calls) == 1

    def test_skipping_init_leaves_it_off(self):
        assert object.__new__(VertexGeminiProvider)._metered is False


class TestTags:
    def test_product_and_environment_always_present(self):
        assert metering.usage_metadata() == {"productName": "jobpipe",
                                             "environment": "production"}

    def test_environment_is_configurable(self, monkeypatch):
        monkeypatch.setenv("JOBPIPE_METERING_ENV", "demo")
        assert metering.usage_metadata()["environment"] == "demo"

    def test_nesting_adds_and_inner_wins(self):
        with metering.tagged(task_type="outer", agent="a"):
            with metering.tagged(task_type="inner"):
                meta = metering.usage_metadata()
            assert metering.usage_metadata()["task_type"] == "outer"
        assert meta["task_type"] == "inner" and meta["agent"] == "a"
        assert "task_type" not in metering.usage_metadata()

    def test_blank_values_are_dropped(self):
        with metering.tagged(task_type="x", agent="", trace_id=None):
            meta = metering.usage_metadata()
        assert "agent" not in meta and "trace_id" not in meta

    def test_a_posting_is_its_id_and_company(self):
        with metering.for_posting({"id": 42, "company": "Revenium"}):
            meta = metering.usage_metadata()
        assert meta["trace_id"] == "42"
        assert meta["organizationName"] == "Revenium"


ROW = {"id": 7, "company": "Acme", "title": "Engineer", "location": None,
       "remote_flag": 0, "posted_at": None, "description": "Build things."}


class TestCallSites:
    def test_a_metered_call_carries_the_tags(self):
        p = metered_provider(reply())
        with metering.tagged(task_type="t"):
            p.score(model="gemini-2.5-flash", system_prompt="s",
                    user_text="u", schema={"type": "object"})
        assert p._client.models.calls[0]["usage_metadata"]["task_type"] == "t"

    def test_escalation_is_told_apart_from_the_first_call(self):
        """The Pro share of spend is a group-by on task_type."""
        p = metered_provider(reply(text='{"overall": 6}'))
        run = scoring.ScoreRun()
        scoring.score_posting(p, ROW, "s", run, model="gemini-2.5-flash",
                              escalation_model="gemini-2.5-pro")
        tags = [c["usage_metadata"] for c in p._client.models.calls]
        assert [t["task_type"] for t in tags] == ["score_flash", "score_pro"]
        assert [t["agent"] for t in tags] == ["stage_b_scorer",
                                              "pro_escalator"]
        assert {t["trace_id"] for t in tags} == {"7"}
        assert {t["organizationName"] for t in tags} == {"Acme"}

    def test_no_escalation_outside_the_band(self):
        p = metered_provider(reply(text='{"overall": 9}'))
        scoring.score_posting(p, ROW, "s", scoring.ScoreRun(),
                              model="gemini-2.5-flash",
                              escalation_model="gemini-2.5-pro")
        assert [c["usage_metadata"]["task_type"]
                for c in p._client.models.calls] == ["score_flash"]

    def test_grounding_takes_its_posting_from_the_caller(self):
        p = metered_provider(reply(text='{"claims": []}'))
        with metering.for_posting(ROW), pytest.raises(Exception):
            # Zero claims is a failed check by design; only the tags matter.
            grounding.check(p, None, "letter", "posting", "s",
                            model="gemini-2.5-flash-lite")
        meta = p._client.models.calls[0]["usage_metadata"]
        assert meta["task_type"] == "grounding_check"
        assert meta["transaction_name"] == "letter"
        assert meta["organizationName"] == "Acme"

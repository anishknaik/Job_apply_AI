"""The Vertex backend. No network — the genai client is faked.

What is worth testing here is precisely what differs from the Developer API,
because everything else is shared and already covered:

  * the usage field names, which are different words for the same numbers and
    would silently report zero tokens if mapped wrong;
  * refusals, which must come back as a ProviderResponse rather than an
    exception, so one blocked posting does not cost the nine after it;
  * the backend switch, which is the rollback path.
"""
import types as pytypes

import pytest

from jobpipe import providers
from jobpipe.providers import ProviderError
from jobpipe.providers.vertex_provider import VertexGeminiProvider

SCHEMA = {"type": "object", "properties": {"overall": {"type": "integer"}}}


class FakeModels:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents,
                           "config": config})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def provider_with(response):
    """A provider whose client is faked, bypassing __init__'s auth."""
    p = object.__new__(VertexGeminiProvider)
    p._project, p._location = "test-project", "global"
    p._client = pytypes.SimpleNamespace(models=FakeModels(response))
    return p


def reply(text='{"overall": 8}', prompt=7125, out=13, cached=6982):
    return pytypes.SimpleNamespace(
        text=text,
        prompt_feedback=None,
        candidates=[pytypes.SimpleNamespace(finish_reason="STOP")],
        usage_metadata=pytypes.SimpleNamespace(
            prompt_token_count=prompt, candidates_token_count=out,
            cached_content_token_count=cached))


class TestTheBackendSwitch:
    def test_it_refuses_rather_than_defaulting(self, monkeypatch):
        """No default: this variable decides which account is billed.

        Defaulting to `api` made that choice silently, so a process that
        failed to load .env - a script run from outside the repo root, a cron
        entry with the wrong working directory - fell back to the Developer
        API. With a key exported in a shell profile that is a silent charge to
        the prepaid balance instead of the GCP credit.
        """
        monkeypatch.delenv("JOBPIPE_GEMINI_BACKEND", raising=False)
        with pytest.raises(providers.ProviderError) as exc:
            providers.gemini_backend()
        assert "no default" in str(exc.value)
        assert "vertex" in str(exc.value) and "api" in str(exc.value)

    def _removed_test_default_is_the_developer_api(self, monkeypatch):
        monkeypatch.delenv("JOBPIPE_GEMINI_BACKEND", raising=False)
        assert providers.gemini_backend() == "api"

    def test_vertex_is_opt_in(self, monkeypatch):
        monkeypatch.setenv("JOBPIPE_GEMINI_BACKEND", "vertex")
        assert providers.gemini_backend() == "vertex"

    def test_a_typo_fails_loudly_rather_than_falling_back(self, monkeypatch):
        """Silently falling back to the API would bill the card while the
        operator believed they were spending credit."""
        monkeypatch.setenv("JOBPIPE_GEMINI_BACKEND", "Vertexx")
        with pytest.raises(ProviderError, match="must be 'api' or 'vertex'"):
            providers.gemini_backend()

    def test_case_and_whitespace_are_forgiven(self, monkeypatch):
        monkeypatch.setenv("JOBPIPE_GEMINI_BACKEND", "  VERTEX ")
        assert providers.gemini_backend() == "vertex"


class TestConstruction:
    def test_a_missing_project_says_which_project_is_meant(self, monkeypatch):
        """Not just "set a project" — the credits may sit on a different
        project from the one that issued the API key, and that distinction is
        the whole reason this backend exists."""
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        with pytest.raises(ProviderError, match="holds the credits"):
            VertexGeminiProvider()

    def test_an_unreadable_service_account_names_the_role_needed(
            self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
        bad = tmp_path / "not-a-key.json"
        bad.write_text("{}", encoding="utf-8")
        with pytest.raises(ProviderError, match="roles/aiplatform.user"):
            VertexGeminiProvider(credentials_path=str(bad))


class TestUsageMapping:
    def test_the_vertex_field_names_are_mapped(self):
        """prompt_token_count / candidates_token_count /
        cached_content_token_count, not the Developer API's total_*_tokens.
        Mapped wrong, every call would report zero and the cache-hit rate
        would look like a failure that was not happening."""
        p = provider_with(reply())
        r = p.score(model="gemini-2.5-flash", system_prompt="sys",
                    user_text="posting", schema=SCHEMA)
        assert r.text == '{"overall": 8}'
        assert r.input_tokens == 7125
        assert r.output_tokens == 13
        assert r.cached_tokens == 6982
        assert r.refused is False

    def test_absent_usage_metadata_reports_zero_not_none(self):
        empty = reply()
        empty.usage_metadata = None
        r = provider_with(empty).score(model="m", system_prompt="s",
                                       user_text="u", schema=SCHEMA)
        assert (r.input_tokens, r.output_tokens, r.cached_tokens) == (0, 0, 0)


class TestRequestShape:
    def test_the_profile_goes_in_system_instruction(self):
        """Byte-identical there on every call is what makes implicit caching
        fire; moving it into `contents` would silently end caching."""
        p = provider_with(reply())
        p.score(model="m", system_prompt="THE PROFILE", user_text="posting",
                schema=SCHEMA)
        config = p._client.models.calls[0]["config"]
        assert config.system_instruction == "THE PROFILE"
        assert p._client.models.calls[0]["contents"] == "posting"

    def test_thinking_is_disabled_explicitly(self):
        """Thinking output is a distinct and more expensive SKU, and scoring
        emits a small structured object. Set, not left to the default."""
        p = provider_with(reply())
        p.score(model="m", system_prompt="s", user_text="u", schema=SCHEMA)
        config = p._client.models.calls[0]["config"]
        assert config.thinking_config.thinking_budget == 0

    def test_output_is_constrained_to_the_schema(self):
        p = provider_with(reply())
        p.score(model="m", system_prompt="s", user_text="u", schema=SCHEMA)
        config = p._client.models.calls[0]["config"]
        assert config.response_mime_type == "application/json"
        assert config.response_schema == SCHEMA
        assert config.temperature == 0


class TestRefusals:
    def test_a_blocked_prompt_returns_rather_than_raises(self):
        """`scoring` treats a refusal as a scoreable outcome. Raising would
        abort the run and lose every posting after it — one blocked job
        description must not cost the other nine."""
        blocked = reply()
        blocked.prompt_feedback = pytypes.SimpleNamespace(
            block_reason="SAFETY")
        r = provider_with(blocked).score(model="m", system_prompt="s",
                                         user_text="u", schema=SCHEMA)
        assert r.refused is True
        assert "SAFETY" in r.refusal_reason
        assert r.text is None

    def test_a_non_stop_finish_reason_is_a_refusal(self):
        stopped = reply()
        stopped.candidates = [pytypes.SimpleNamespace(
            finish_reason="PROHIBITED_CONTENT")]
        r = provider_with(stopped).score(model="m", system_prompt="s",
                                         user_text="u", schema=SCHEMA)
        assert r.refused is True
        assert "PROHIBITED_CONTENT" in r.refusal_reason

    def test_max_tokens_is_not_a_refusal(self):
        """Truncated output is a schema problem for the caller to notice, not
        a refusal — treating it as one would hide a real parse failure."""
        truncated = reply()
        truncated.candidates = [pytypes.SimpleNamespace(
            finish_reason="MAX_TOKENS")]
        assert provider_with(truncated).score(
            model="m", system_prompt="s", user_text="u",
            schema=SCHEMA).refused is False

    def test_a_transport_failure_names_project_and_location(self):
        """A 404 here almost always means the model is not served in this
        region — gemini-3.1-flash-lite is global-only. The error has to say
        where it was looking."""
        p = provider_with(RuntimeError("404 model not found"))
        with pytest.raises(ProviderError) as caught:
            p.score(model="gemini-3.1-flash-lite", system_prompt="s",
                    user_text="u", schema=SCHEMA)
        assert "test-project" in str(caught.value)
        assert "global" in str(caught.value)


class TestCacheFloor:
    def test_it_shares_the_floors_with_the_developer_api_provider(self):
        """One definition. A floor defined twice is a floor that drifts."""
        from jobpipe.providers.gemini_provider import _CACHE_MINIMUMS
        p = object.__new__(VertexGeminiProvider)
        for model, minimum in _CACHE_MINIMUMS.items():
            assert p.cache_minimum_tokens(model) == minimum

    def test_an_unknown_model_gets_the_larger_floor(self):
        """Fails loudly rather than silently costing ~10x."""
        p = object.__new__(VertexGeminiProvider)
        assert p.cache_minimum_tokens("gemini-9.9-unknown") == 4096

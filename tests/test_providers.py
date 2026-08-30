"""Provider abstraction (addendum §2). No network, no spend.

The point of the abstraction is that scoring.py never learns which vendor it
is talking to, so these tests check the contract rather than either
implementation's internals.
"""
import pytest

from jobpipe.providers import (DEFAULT_ESCALATION_MODELS, DEFAULT_MODELS,
                               ProviderError, ProviderResponse,
                               ScoringProvider, default_escalation_model,
                               default_model, get_provider)


class TestRegistry:
    def test_gemini_is_the_default_provider(self, monkeypatch):
        monkeypatch.delenv("JOBPIPE_PROVIDER", raising=False)
        assert default_model() == "gemini-3.1-flash-lite"

    def test_env_selects_the_provider(self, monkeypatch):
        monkeypatch.setenv("JOBPIPE_PROVIDER", "anthropic")
        assert default_model() == "claude-haiku-4-5"
        assert default_escalation_model() == "claude-sonnet-5"

    def test_unknown_provider_names_itself_clearly(self, monkeypatch):
        monkeypatch.setenv("JOBPIPE_PROVIDER", "hotdog")
        with pytest.raises(ProviderError, match="unknown provider"):
            get_provider()

    def test_dead_model_is_not_offered_as_a_default(self):
        """gemini-2.5-flash-lite is what addendum §2 names, and the live API
        reports it as no longer available to new users."""
        assert "2.5-flash-lite" not in DEFAULT_MODELS["gemini"]
        assert "2.5-flash-lite" not in DEFAULT_ESCALATION_MODELS["gemini"]

    def test_escalation_stays_within_the_provider(self):
        """§2: escalate the model, not the provider."""
        for name in DEFAULT_MODELS:
            primary = DEFAULT_MODELS[name].split("-")[0]
            escalated = DEFAULT_ESCALATION_MODELS[name].split("-")[0]
            assert primary == escalated, name


class TestContract:
    """Both providers must satisfy the same interface."""

    @pytest.mark.parametrize("module_name,class_name", [
        ("jobpipe.providers.gemini_provider", "GeminiProvider"),
        ("jobpipe.providers.anthropic_provider", "AnthropicProvider"),
    ])
    def test_implements_the_abstract_interface(self, module_name, class_name):
        import importlib
        cls = getattr(importlib.import_module(module_name), class_name)
        assert issubclass(cls, ScoringProvider)
        for method in ("count_tokens", "cache_minimum_tokens", "score"):
            assert callable(getattr(cls, method, None)), method
        assert cls.name and cls.known_models

    def test_missing_key_fails_with_a_useful_message(self, monkeypatch):
        from jobpipe.providers.gemini_provider import GeminiProvider
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(ProviderError) as exc:
            GeminiProvider()
        # The billing point is the reason §2 chose the paid tier at all.
        assert "billing" in str(exc.value).lower()

    def test_gemini_accepts_either_key_variable(self, monkeypatch):
        """The SDK documents GEMINI_API_KEY; .env may use GOOGLE_API_KEY."""
        from jobpipe.providers import gemini_provider
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        captured = {}

        class FakeGenai:
            @staticmethod
            def Client(api_key=None):
                captured["key"] = api_key
                return object()

        import sys
        import types as pytypes
        fake_module = pytypes.ModuleType("google")
        fake_module.genai = FakeGenai
        monkeypatch.setitem(sys.modules, "google", fake_module)
        monkeypatch.setitem(sys.modules, "google.genai", FakeGenai)

        gemini_provider.GeminiProvider()
        assert captured["key"] == "test-key"


class TestCacheFloors:
    """A prefix below the floor silently disables caching on both vendors."""

    def test_gemini_floors(self):
        from jobpipe.providers.gemini_provider import GeminiProvider
        floor = GeminiProvider.cache_minimum_tokens
        obj = object.__new__(GeminiProvider)
        assert floor(obj, "gemini-2.5-flash-lite") == 2048
        assert floor(obj, "gemini-3.1-flash-lite") == 4096
        assert floor(obj, "gemini-9.9-unknown") == 4096, \
            "unknown models must default to the strictest floor"

    def test_anthropic_floors(self):
        from jobpipe.providers.anthropic_provider import AnthropicProvider
        floor = AnthropicProvider.cache_minimum_tokens
        obj = object.__new__(AnthropicProvider)
        assert floor(obj, "claude-haiku-4-5") == 4096
        assert floor(obj, "claude-sonnet-5") == 1024
        assert floor(obj, "claude-unknown-9") == 4096


class TestProviderResponse:
    def test_defaults_are_zero_not_none(self):
        """scoring.py adds these straight into running totals."""
        r = ProviderResponse(text="{}")
        assert (r.input_tokens, r.output_tokens, r.cached_tokens) == (0, 0, 0)
        assert r.refused is False

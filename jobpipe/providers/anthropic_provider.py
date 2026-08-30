"""Anthropic scoring provider.

Kept as the final rung of addendum §2's escalation ladder
(Flash-Lite → … → Claude Haiku 4.5) and as a second opinion when a Gemini
score looks wrong. Not the default any more.

Two provider-specific quirks the interface hides:

  * Caching is explicit — a `cache_control` breakpoint with a 1-hour TTL on
    the system block, rather than Gemini's implicit prefix caching.
  * `temperature` must NOT be sent. Non-default sampling parameters are
    rejected with a 400 on claude-sonnet-5, so determinism comes from the
    structured-output schema instead.
"""
from __future__ import annotations

import logging
import os

from .base import ProviderError, ProviderResponse, ScoringProvider

log = logging.getLogger(__name__)

_CACHE_MINIMUMS = {
    "claude-haiku-4-5": 4096,
    "claude-sonnet-5": 1024,
    "claude-opus-5": 512,
}
_DEFAULT_CACHE_MINIMUM = 4096


class AnthropicProvider(ScoringProvider):
    name = "anthropic"
    known_models = ("claude-haiku-4-5", "claude-sonnet-5")

    def __init__(self, api_key: str | None = None):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - import guard
            raise ProviderError(
                "anthropic is not installed. pip install anthropic") from exc

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError(
                "ANTHROPIC_API_KEY is not set. Note this is the "
                "pay-as-you-go API key, not the Pro subscription — they are "
                "separate billing systems.")
        self._client = anthropic.Anthropic(api_key=key)

    def _system_blocks(self, system_prompt: str) -> list[dict]:
        return [{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }]

    def count_tokens(self, model: str, text: str) -> int:
        counted = self._client.messages.count_tokens(
            model=model,
            system=self._system_blocks(text),
            messages=[{"role": "user", "content": "x"}],
        )
        return counted.input_tokens

    def cache_minimum_tokens(self, model: str) -> int:
        for prefix, minimum in _CACHE_MINIMUMS.items():
            if model.startswith(prefix):
                return minimum
        return _DEFAULT_CACHE_MINIMUM

    def score(self, *, model: str, system_prompt: str, user_text: str,
              schema: dict) -> ProviderResponse:
        response = self._client.messages.create(
            model=model,
            max_tokens=1024,
            system=self._system_blocks(system_prompt),
            # No temperature — see module docstring.
            thinking={"type": "disabled"},
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": user_text}],
        )

        text = next((b.text for b in response.content if b.type == "text"), None)
        usage = response.usage
        return ProviderResponse(
            text=text,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cached_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            refused=response.stop_reason == "refusal",
            refusal_reason=("safety classifier declined"
                            if response.stop_reason == "refusal" else ""),
        )

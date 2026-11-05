"""The contract every scoring provider satisfies.

Addendum §2 asks for "a thin provider abstraction so a model swap is a config
change". The interface is deliberately narrow — one scoring call, one token
count, one cache-floor number — because everything above it (the rubric,
escalation, retries, token accounting) is provider-independent and should
only be written once.

The two providers differ in ways this interface hides:

  * Anthropic caches via an explicit `cache_control` breakpoint with a TTL.
    Gemini caches implicitly on any sufficiently long shared prefix. Both are
    fed the same system prompt string and decide for themselves.
  * Anthropic rejects non-default `temperature` on claude-sonnet-5; Gemini
    accepts `temperature=0` everywhere. Determinism is therefore requested
    per-provider rather than by a shared parameter.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ProviderResponse:
    """One scoring call's result, normalised across providers."""

    text: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    refused: bool = False
    refusal_reason: str = ""
    # The model hit the output ceiling and `text` is cut off. Not a refusal
    # (a parse failure downstream must stay visible as one), but a caller
    # that asked for a ceiling needs to know it was reached: the grounding
    # check once produced ~285k characters of claims (HANDOFF §8), and a
    # truncated JSON object looks like any other parse error without this.
    truncated: bool = False


class ProviderError(RuntimeError):
    """The provider could not be used at all — bad key, unknown model."""


class ScoringProvider(ABC):
    """A model backend that can score one posting against a cached prefix."""

    name: str = "base"

    #: Models this provider is known to serve, best first. Advisory only —
    #: used for error messages, not enforced, since catalogues change.
    known_models: tuple[str, ...] = ()

    @abstractmethod
    def count_tokens(self, model: str, text: str) -> int:
        """Token count for `text` under `model`."""

    @abstractmethod
    def cache_minimum_tokens(self, model: str) -> int:
        """Smallest prefix this model will cache.

        Below it, caching silently does not happen on either provider — no
        error, just a bill roughly ten times larger than expected. This number
        is what makes that failure detectable at startup instead of never.
        """

    @abstractmethod
    def score(self, *, model: str, system_prompt: str, user_text: str,
              schema: dict) -> ProviderResponse:
        """Run one scoring call constrained to `schema`, deterministically."""

    def score_capped(self, *, max_output_tokens: int, model: str,
                     system_prompt: str, user_text: str,
                     schema: dict) -> ProviderResponse:
        """`score`, with a ceiling on the output.

        The grounding check needs one (2026-11-05: Flash Lite looped and
        produced ~285k characters of claims, identically twice). The ceiling
        is a separate method rather than a parameter on `score` so that a
        provider - or a test double - that knows nothing of ceilings still
        works: this default ignores the cap and calls `score`. The real
        providers override it and set `truncated` on the response when the
        ceiling was hit, which is what the caller checks for.
        """
        return self.score(model=model, system_prompt=system_prompt,
                          user_text=user_text, schema=schema)
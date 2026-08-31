"""Google Gemini scoring provider (addendum §2).

Request shape verified against the live API on 2026-08-12, because the docs
and the SDK disagreed on two points:

  * The SDK moved to the Interactions API — `client.interactions.create(...)`,
    not `generate_content`.
  * `response_format` IS the JSON schema, with `type` taking a JSON-schema
    type such as "object". The API rejects `type: "json_schema"` and lists the
    values it accepts. Setting `response_mime_type` without `response_format`
    is also a 400.

**Caching is implicit.** Unlike Anthropic there is no breakpoint to place and
no TTL to set: Gemini caches any sufficiently long shared prefix on 2.5+
models automatically. Keeping the master profile at the front of
`system_instruction`, byte-identical across calls, is the whole technique.
Cache hits show up as `usage.total_cached_tokens`.
"""
from __future__ import annotations

import logging
import os

from .base import ProviderError, ProviderResponse, ScoringProvider

log = logging.getLogger(__name__)

# Implicit-caching minimums from Google's caching documentation. Gemini 2.5
# Flash/Pro cache from 2,048 tokens; the 3.x generation needs 4,096. The
# larger figure is the default for anything unrecognised, so an unknown model
# fails loudly rather than silently costing 10x.
_CACHE_MINIMUMS = {
    "gemini-2.5-flash": 2048,
    "gemini-2.5-flash-lite": 2048,
    "gemini-2.5-pro": 2048,
}
_DEFAULT_CACHE_MINIMUM = 4096


class GeminiProvider(ScoringProvider):
    name = "gemini"

    # Verified live: gemini-2.5-flash-lite — the model addendum §2 names — is
    # "no longer available to new users", so a fresh key cannot use it. The
    # addendum's first escalation step is the effective primary.
    known_models = (
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
    )

    def __init__(self, api_key: str | None = None):
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - import guard
            raise ProviderError(
                "google-genai is not installed. pip install -U google-genai"
            ) from exc

        # The SDK reads GEMINI_API_KEY; .env may reasonably use GOOGLE_API_KEY
        # instead, so both are accepted and passed explicitly.
        key = (api_key or os.environ.get("GEMINI_API_KEY")
               or os.environ.get("GOOGLE_API_KEY"))
        if not key:
            raise ProviderError(
                "no Gemini API key found. Set GEMINI_API_KEY (or "
                "GOOGLE_API_KEY) in .env — and make sure the key belongs to a "
                "billing-enabled project, since the free tier's terms permit "
                "training on submitted prompts and the prompt here is a full "
                "personal career profile.")
        self._client = genai.Client(api_key=key)

    def count_tokens(self, model: str, text: str) -> int:
        return self._client.models.count_tokens(
            model=model, contents=text).total_tokens

    def cache_minimum_tokens(self, model: str) -> int:
        for prefix, minimum in _CACHE_MINIMUMS.items():
            if model.startswith(prefix):
                return minimum
        return _DEFAULT_CACHE_MINIMUM

    def score(self, *, model: str, system_prompt: str, user_text: str,
              schema: dict) -> ProviderResponse:
        response = self._client.interactions.create(
            model=model,
            # The profile sits here, unchanged on every call, which is what
            # makes implicit caching fire.
            system_instruction=system_prompt,
            input=user_text,
            # response_format is the schema itself; `type` is a JSON-schema
            # type. Verified against the live API.
            response_format=schema,
            generation_config={"temperature": 0},
        )

        usage = getattr(response, "usage", None)
        return ProviderResponse(
            text=response.output_text,
            input_tokens=getattr(usage, "total_input_tokens", 0) or 0,
            output_tokens=getattr(usage, "total_output_tokens", 0) or 0,
            cached_tokens=getattr(usage, "total_cached_tokens", 0) or 0,
        )
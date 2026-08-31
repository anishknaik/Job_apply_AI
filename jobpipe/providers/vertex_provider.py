"""Gemini on Vertex AI (PHASE5_PLAN Track A step 2).

Same models, different billing door. The Developer API key in `.env` bills to
its own track, which Google Cloud promotional credits are excluded from; Vertex
bills to the GCP billing account where the credits actually live. Vertex also
carries enterprise data-governance terms, which matters independently of money
for a prompt that is a complete personal career profile.

WHY THIS IS A SEPARATE CLASS RATHER THAN A FLAG

`interactions.create` is Developer-API only — Vertex answers `Unsupported model
interaction`. So the request shape genuinely differs, and so do the usage field
names:

    Developer API                  Vertex
    interactions.create            models.generate_content
    system_instruction=            config.system_instruction
    input=                         contents=
    response_format=schema         config.response_schema + response_mime_type
    usage.total_input_tokens       usage_metadata.prompt_token_count
    usage.total_output_tokens      usage_metadata.candidates_token_count
    usage.total_cached_tokens      usage_metadata.cached_content_token_count

Everything above `providers/` — the rubric, escalation, retries, token
accounting — is untouched. That is what the abstraction was for.

DELIBERATELY MODEL-AGNOSTIC. This class does not change which model runs, and
`DEFAULT_MODELS` is unchanged. Flipping `JOBPIPE_GEMINI_BACKEND=vertex` moves
the same model to a different backend, so the backend change can be verified on
its own before the model changes underneath it. Two variables moving at once is
how a regression becomes unattributable — and Track A already has a confound to
diagnose if its gate trips.

Verified live 2026-08-30 (`scripts/probe_vertex.py`): `gemini-2.5-flash`
resolves at both `global` and `us-central1`, implicit caching fires on the real
profile prefix at 98% when it lands, and `thinking_budget=0` is accepted.
"""
from __future__ import annotations

import logging
import os

from .base import ProviderError, ProviderResponse, ScoringProvider
from .gemini_provider import _CACHE_MINIMUMS, _DEFAULT_CACHE_MINIMUM

log = logging.getLogger(__name__)

# Vertex serves 2.5 and 3.x; unlike a fresh Developer API key it still serves
# gemini-2.5-flash-lite. The constraint that pushed this project off 2.5 and
# the constraint limiting the credit to 2.x cancel out here.
KNOWN_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "gemini-3.1-flash-lite",
)

# `global` by default because gemini-3.1-flash-lite 404s regionally and is
# still the configured default model. 2.5 Flash resolves in both, so this stops
# being a constraint once the model moves — but it costs nothing to keep.
DEFAULT_LOCATION = "global"


class VertexGeminiProvider(ScoringProvider):
    name = "gemini"          # the rubric and escalation ladder key off this
    known_models = KNOWN_MODELS

    def __init__(self, project: str | None = None, location: str | None = None,
                 credentials_path: str | None = None):
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - import guard
            raise ProviderError(
                "google-genai is not installed. pip install -U google-genai"
            ) from exc

        project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise ProviderError(
                "no GCP project. Set GOOGLE_CLOUD_PROJECT in .env — it must be "
                "the project whose billing account holds the credits, which is "
                "not necessarily the one that issued the API key.")

        location = (location or os.environ.get("GOOGLE_CLOUD_LOCATION")
                    or DEFAULT_LOCATION)

        # The same service account the Sheets client uses, which already holds
        # roles/aiplatform.user. A service account rather than ADC for the
        # reason sheets.py documents: the scheduled run has no browser, no
        # consent screen, and no refresh token to expire at 06:00.
        credentials = None
        path = (credentials_path
                or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
                or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
        if path:
            try:
                from google.oauth2 import service_account
                credentials = service_account.Credentials.from_service_account_file(
                    path, scopes=["https://www.googleapis.com/auth/cloud-platform"])
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(
                    f"could not load the service account at {path}: {exc}. "
                    f"It needs roles/aiplatform.user on {project}."
                ) from exc

        self._project, self._location = project, location
        self._client = genai.Client(vertexai=True, project=project,
                                    location=location, credentials=credentials)
        log.debug("vertex backend: project=%s location=%s", project, location)

    def count_tokens(self, model: str, text: str) -> int:
        return self._client.models.count_tokens(
            model=model, contents=text).total_tokens

    def cache_minimum_tokens(self, model: str) -> int:
        """Shared with the Developer-API provider rather than restated.

        A caveat the number cannot express: 2.5 Flash Lite meets its 2,048
        floor and still caches NOTHING on Vertex — zero across eleven calls
        over two probes. The floor is necessary, not sufficient. It does not
        matter in practice because Flash Lite's job here is grounding, whose
        prompts are short and carry no long shared prefix.
        """
        for prefix, minimum in _CACHE_MINIMUMS.items():
            if model.startswith(prefix):
                return minimum
        return _DEFAULT_CACHE_MINIMUM

    def score(self, *, model: str, system_prompt: str, user_text: str,
              schema: dict) -> ProviderResponse:
        from google.genai import types

        config = types.GenerateContentConfig(
            # The profile sits here, byte-identical on every call, which is
            # what makes implicit caching fire.
            system_instruction=system_prompt,
            temperature=0,
            response_mime_type="application/json",
            response_schema=schema,
            # Off deliberately, not left at whatever the default is: thinking
            # output is a distinct and more expensive SKU, and scoring emits a
            # small structured object. Verified accepted on 2.5 Flash and
            # Flash Lite, with thoughts_token_count coming back 0.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

        try:
            response = self._client.models.generate_content(
                model=model, contents=user_text, config=config)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"vertex call failed for {model} at {self._location} "
                f"(project {self._project}): {exc}") from exc

        blocked = _refusal_reason(response)
        if blocked:
            return ProviderResponse(text=None, refused=True,
                                    refusal_reason=blocked)

        usage = getattr(response, "usage_metadata", None)
        return ProviderResponse(
            text=response.text,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            cached_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
        )


def _refusal_reason(response) -> str:
    """A blocked response, in the two shapes Vertex reports it.

    Returned as a refusal rather than raised: `scoring` already treats a
    refusal as a scoreable outcome, while an exception would abort the run and
    lose the postings after it. A safety block on one job description must not
    cost the other nine.
    """
    feedback = getattr(response, "prompt_feedback", None)
    block = getattr(feedback, "block_reason", None)
    if block:
        return f"prompt blocked: {block}"

    for candidate in getattr(response, "candidates", None) or []:
        reason = getattr(candidate, "finish_reason", None)
        if reason is None:
            continue
        name = getattr(reason, "name", str(reason))
        if name not in ("STOP", "MAX_TOKENS"):
            return f"generation stopped: {name}"
    return ""

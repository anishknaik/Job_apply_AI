"""Provider registry. Swapping models is a config change, not a code change.

    JOBPIPE_PROVIDER=gemini
    JOBPIPE_SCORING_MODEL=gemini-3.1-flash-lite
"""
from __future__ import annotations

import os

from .base import ProviderError, ProviderResponse, ScoringProvider

DEFAULT_PROVIDER = "gemini"

# Sensible primary model per provider, used when JOBPIPE_SCORING_MODEL is
# unset. Note gemini-2.5-flash-lite is deliberately NOT here: it is no longer
# available to new API keys (verified live 2026-08-12), despite being the
# model addendum §2 names.
DEFAULT_MODELS = {
    "gemini": "gemini-3.1-flash-lite",
    "anthropic": "claude-haiku-4-5",
}

# Addendum §2's escalation ladder: escalate the model, not the provider.
DEFAULT_ESCALATION_MODELS = {
    "gemini": "gemini-3.5-flash-lite",
    "anthropic": "claude-sonnet-5",
}

# Phase 2 §5: cover letter prose is quality-sensitive and explicitly must not
# be cost-optimised, so it gets its own tier rather than inheriting the
# scoring model. Selection and the grounding check stay on Flash-Lite; only
# the ~30 letters a month that follow a human's decision to apply run here.
#
# §5 names `gemini-3.1-flash`. It does not exist — verified against
# ListModels, the 3.1 generation ships only flash-lite (plus image and TTS
# variants). Same trap as addendum §2's `gemini-2.5-flash-lite` in Phase 1:
# the spec named a plausible model that the API does not have. The nearest
# faithful reading of "one tier above Flash-Lite for prose" is the cheapest
# non-lite flash on offer.
#
# Deliberately NOT `gemini-flash-latest`: an alias silently changes model
# underneath a calibrated prompt, and this is the one call whose output
# quality is the entire deliverable.
# Keyed by BACKEND as well as provider, for the same reason grounding is: this
# default was provider-keyed only, so it returned gemini-3.5-flash on Vertex -
# a model Vertex does not serve and one the credit would not cover if it did.
# Third instance of one defect. Grounding was the first (574fdd4), the rubric's
# field descriptions the second; each time a value was resolved without asking
# which backend was about to receive it.
#
# The Vertex entry is the faithful port of the reasoning above: the cheapest
# non-lite flash on offer, one tier above the flash-lite used for selection and
# grounding. gemini-2.5-pro is also credit-eligible and would be the choice if
# letter quality ever justifies the thinking-token SKU - a deliberate decision
# to make on its own, not a default to drift into.
DEFAULT_PROSE_MODELS = {
    ("gemini", "api"): "gemini-3.5-flash",
    ("gemini", "vertex"): "gemini-2.5-flash",
    ("anthropic", "api"): "claude-sonnet-5",
    ("anthropic", "vertex"): "claude-sonnet-5",
}


# Grounding is a short, structured check with no long shared prefix, so it
# wants the cheapest capable model rather than the scoring model. It used to
# inherit `default_model()`, which ignores JOBPIPE_SCORING_MODEL entirely -
# so moving scoring to 2.5 Flash would have silently left grounding on
# 3.1-flash-lite, a model the credit does not cover.
#
# Keyed by BACKEND as well as provider, because gemini-2.5-flash-lite is
# served on Vertex but is unavailable to new Developer API keys (verified
# live 2026-08-12). One default cannot be right for both.
DEFAULT_GROUNDING_MODELS = {
    ("gemini", "api"): "gemini-3.1-flash-lite",
    ("gemini", "vertex"): "gemini-2.5-flash-lite",
    ("anthropic", "api"): "claude-haiku-4-5",
    ("anthropic", "vertex"): "claude-haiku-4-5",
}


def default_grounding_model(provider: str | None = None) -> str:
    provider = (provider or os.environ.get("JOBPIPE_PROVIDER")
                or DEFAULT_PROVIDER).strip().lower()
    # Override first, backend second. Resolving the backend up front made an
    # explicitly chosen model depend on the billing switch being set, which is
    # backwards: naming a model is the one case where no default is needed.
    override = os.environ.get("JOBPIPE_GROUNDING_MODEL")
    if override:
        return override
    backend = gemini_backend() if provider == "gemini" else "api"
    return (DEFAULT_GROUNDING_MODELS.get((provider, backend))
            or DEFAULT_MODELS.get(provider, DEFAULT_MODELS[DEFAULT_PROVIDER]))


def default_prose_model(provider: str | None = None) -> str:
    provider = (provider or os.environ.get("JOBPIPE_PROVIDER")
                or DEFAULT_PROVIDER).strip().lower()
    override = os.environ.get("JOBPIPE_PROSE_MODEL")
    if override:
        return override
    backend = gemini_backend() if provider == "gemini" else "api"
    return (DEFAULT_PROSE_MODELS.get((provider, backend))
            or DEFAULT_MODELS.get(provider, DEFAULT_MODELS[DEFAULT_PROVIDER]))


def get_provider(name: str | None = None, **kwargs) -> ScoringProvider:
    name = (name or os.environ.get("JOBPIPE_PROVIDER")
            or DEFAULT_PROVIDER).strip().lower()

    if name == "gemini":
        # Same provider name, two backends. The Developer API key bills to a
        # track that Google Cloud promotional credits are excluded from;
        # Vertex bills to the account where the credits live, and carries
        # enterprise data terms for a prompt that is a full personal profile.
        #
        # A backend rather than a third provider name because everything above
        # here keys off `gemini` — the rubric, the escalation ladder, the
        # model defaults. Rollback is unsetting one variable.
        if gemini_backend() == "vertex":
            from .vertex_provider import VertexGeminiProvider
            return VertexGeminiProvider(**kwargs)
        from .gemini_provider import GeminiProvider
        return GeminiProvider(**kwargs)
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(**kwargs)

    raise ProviderError(
        f"unknown provider {name!r}. Set JOBPIPE_PROVIDER to one of: "
        f"{', '.join(sorted(DEFAULT_MODELS))}")


def gemini_backend() -> str:
    """Which Gemini backend to use: `api` (the Developer API) or `vertex`.

    Deliberately does NOT change which model runs. Flipping the backend moves
    the same model to a different billing door, so the backend can be verified
    on its own before the model changes underneath it — two variables moving
    at once is how a regression becomes unattributable.
    """
    raw = os.environ.get("JOBPIPE_GEMINI_BACKEND")
    if raw is None or not raw.strip():
        # Deliberately no default. This variable decides which account is
        # billed, and defaulting to `api` made that decision silently: a
        # process that fails to load .env - a script run from outside the repo
        # root, a cron entry with the wrong working directory - fell back to
        # the Developer API. With a key exported in a shell profile that is a
        # silent charge to the prepaid balance rather than the GCP credit.
        # Refusing costs one explicit line in .env and turns an invisible
        # billing event into a startup error.
        raise ProviderError(
            "JOBPIPE_GEMINI_BACKEND is not set. It decides which account is "
            "billed, so it has no default: set it to 'vertex' (bills the GCP "
            "billing account, where the credits live) or 'api' (bills the "
            "prepaid Developer API balance). If you expected .env to supply "
            "it, the process did not load .env - python-dotenv searches "
            "upward from the calling file, not the working directory.")
    value = raw.strip().lower()
    if value not in ("api", "vertex"):
        raise ProviderError(
            f"JOBPIPE_GEMINI_BACKEND must be 'api' or 'vertex', not {value!r}")
    return value


def default_model(provider: str | None = None) -> str:
    """The scoring model: .env first, then the built-in default.

    Reading the environment here rather than only in `cli._resolve_model` is
    deliberate. Every caller that resolved a model WITHOUT going through the
    CLI - grounding, the escalation ladder, calibrate - silently ignored
    .env and used the hardcoded default. That is invisible: the run
    succeeds, on a model nobody chose. It bit twice, both found by reading
    rather than by a failure.

    `_resolve_model` still layers a CLI flag on top, and its precedence is
    unchanged: flag beats .env beats this.
    """
    provider = (provider or os.environ.get("JOBPIPE_PROVIDER")
                or DEFAULT_PROVIDER).strip().lower()
    return (os.environ.get("JOBPIPE_SCORING_MODEL")
            or DEFAULT_MODELS.get(provider, DEFAULT_MODELS[DEFAULT_PROVIDER]))


def default_escalation_model(provider: str | None = None) -> str:
    """The escalation model: .env first, then the built-in default.

    Same reasoning as `default_model`. This one mattered concretely: with
    JOBPIPE_ESCALATION_MODEL=gemini-2.5-pro set, `calibrate` would still have
    escalated to gemini-3.5-flash-lite - a 3.x model the GCP credit does not
    cover - and nothing would have said so.
    """
    provider = (provider or os.environ.get("JOBPIPE_PROVIDER")
                or DEFAULT_PROVIDER).strip().lower()
    return (os.environ.get("JOBPIPE_ESCALATION_MODEL")
            or DEFAULT_ESCALATION_MODELS.get(
                provider, DEFAULT_ESCALATION_MODELS[DEFAULT_PROVIDER]))


__all__ = ["ProviderError", "ProviderResponse", "ScoringProvider",
           "get_provider", "default_model", "default_escalation_model",
           "gemini_backend", "default_grounding_model",
           "DEFAULT_GROUNDING_MODELS",
           "DEFAULT_MODELS", "DEFAULT_ESCALATION_MODELS",
           "DEFAULT_PROSE_MODELS", "default_prose_model"]

"""The seven resume variants (PHASE5_PLAN §4.2).

A variant is an ORDERING over the whole bank, plus a headline and a
skill-section order. It is never a subset, and the distinction is the whole
design: the bank holds 21 bullets and roughly 14–16 fit one page, so any two
subsets must share at least 11. Seven "different" bullet lists would be seven
near-copies that drift apart under per-posting edits until they are seven
one-offs and the problem they were meant to solve is back.

Because the one-page loop drops from the bottom, an ordering *is* a selection,
and there stays exactly one copy of every bullet. What position 15 holds is
therefore as much a decision as what position 1 holds — it is the first thing
that will not render.

Reviewed and signed off 2026-09-01. Three of the orderings carry a decision
that is not obvious from the ranking alone:

  * `ds_analytics` leads with `dist-ablation`, not `bumi-models` as §4.2
    proposed. Anish's own letter for a data-science posting opened on the
    ablation and survived his edit there, and the work is recent where Bumi is
    two years old. Experimental rigor leads the DS lane.
  * `ai_ml_infra` puts the distillation and RAG projects above all paid work,
    so `gt-nbs7` is not reached until position 9. Deliberate: the NBS 7 work
    was a group proof of concept, the projects are solo and carry results.
  * `ai_ml_cv` runs out of computer vision at position 3, because the bank
    holds exactly three CV bullets. Kept anyway — those three are cut by every
    other ordering, so without this lane they never render at all.

A wrong drop list is either a permanent edit to a variant or a signal that the
posting needs a different one. It is never a per-application adjustment: that
is what pulls the seven apart. Variants are versioned artifacts.
"""
from __future__ import annotations

# Ordering per variant, strongest first. Every ordering is a permutation of
# the whole bank — `tests/test_variants.py` fails if one drifts into a subset
# or picks up an id the bank no longer has.
ORDERINGS: dict[str, tuple[str, ...]] = {
    # AI infrastructure — what developers build on. Compute, serving, vector
    # stores, orchestration, eval. Leads with the distillation project because
    # fine-tuning plus a real eval harness is the closest thing in the bank to
    # the work, and keeps all three of its bullets together: every extra role
    # costs a heading and a stack line before a single bullet lands.
    "ai_ml_infra": (
        "dist-distill", "dist-ablation", "dist-harness", "rag-agentic",
        "rag-fullstack", "spot-gpt", "gt-lesson-plan", "newsletter-pipeline",
        "gt-nbs7", "gt-remittance", "gt-ownership", "bumi-etl", "bumi-models",
        "stock-ensemble", "capstone-quality", "capstone-hypothesis",
        "nec-yolo", "nec-tracking", "spot-cv", "brain-survey",
        "brain-dashboards",
    ),
    # Data infrastructure — pipelines, ingestion, storage, quality. The only
    # ordering where `capstone-quality` renders high, because data validation
    # is the job rather than a footnote.
    "ai_ml_data": (
        "gt-nbs7", "bumi-etl", "gt-remittance", "capstone-quality",
        "rag-fullstack", "dist-harness", "newsletter-pipeline",
        "gt-lesson-plan", "dist-distill", "rag-agentic", "gt-ownership",
        "dist-ablation", "bumi-models", "stock-ensemble",
        "capstone-hypothesis", "spot-gpt", "brain-survey", "brain-dashboards",
        "nec-yolo", "nec-tracking", "spot-cv",
    ),
    # AI-native and general product SaaS. Tuned for `ai_saas`, which outnumbers
    # `product_saas` 17 to 9 in the priority list; generic product rides along
    # rather than the reverse. `gt-ownership` is high here and nowhere else —
    # scoping with a client is the differentiator for a product team.
    "ai_ml_product": (
        "gt-lesson-plan", "rag-agentic", "newsletter-pipeline", "gt-ownership",
        "rag-fullstack", "spot-gpt", "dist-distill", "dist-ablation",
        "gt-remittance", "gt-nbs7", "dist-harness", "bumi-models", "bumi-etl",
        "stock-ensemble", "capstone-quality", "capstone-hypothesis",
        "brain-dashboards", "brain-survey", "nec-yolo", "spot-cv",
        "nec-tracking",
    ),
    # Healthcare, biotech, gov-adjacent and research institutions. `gt-nbs7`
    # leads on domain rather than depth — HL7 and federal disease surveillance
    # is experience almost nobody at this level has — and `capstone-quality`
    # rises because the capstone dataset was global COVID-19 data.
    "ai_ml_health": (
        "gt-nbs7", "dist-distill", "dist-ablation", "dist-harness",
        "rag-agentic", "rag-fullstack", "gt-lesson-plan", "bumi-models",
        "capstone-quality", "capstone-hypothesis", "gt-ownership", "bumi-etl",
        "spot-gpt", "newsletter-pipeline", "gt-remittance", "stock-ensemble",
        "brain-survey", "brain-dashboards", "nec-yolo", "nec-tracking",
        "spot-cv",
    ),
    # Robotics and computer vision. Thin by construction — the bank has three
    # CV bullets and this ordering exhausts them by position 3, after which it
    # is the general AI/ML ordering. It exists so those three ever render.
    "ai_ml_cv": (
        "nec-yolo", "nec-tracking", "spot-cv", "dist-distill", "dist-harness",
        "dist-ablation", "rag-agentic", "bumi-models", "gt-nbs7",
        "rag-fullstack", "gt-lesson-plan", "spot-gpt", "stock-ensemble",
        "gt-ownership", "newsletter-pipeline", "bumi-etl", "gt-remittance",
        "capstone-quality", "capstone-hypothesis", "brain-survey",
        "brain-dashboards",
    ),
    # The data-science lane, any domain. Both Brainayan bullets render only
    # here, which is the point of the lane rather than a gap in it: survey
    # analysis and BI dashboards are the least transferable work in the bank
    # and they are what makes this an analytics résumé rather than an
    # engineering one with statistics bolted on.
    "ds_analytics": (
        "dist-ablation", "bumi-models", "capstone-hypothesis",
        "stock-ensemble", "capstone-quality", "brain-survey",
        "brain-dashboards", "bumi-etl", "dist-harness", "dist-distill",
        "rag-agentic", "gt-lesson-plan", "gt-nbs7", "nec-yolo",
        "rag-fullstack", "gt-ownership", "spot-gpt", "gt-remittance",
        "newsletter-pipeline", "nec-tracking", "spot-cv",
    ),
    # The SWE lane, any domain, and where fintech lands. Fintech gets no
    # variant of its own: the drop order is already relevance-aware, so a
    # posting naming payments and PostgreSQL promotes `gt-remittance` on its
    # own. A variant earns its place when the whole ordering differs, not when
    # one bullet should move.
    "swe_backend": (
        "gt-remittance", "gt-nbs7", "rag-fullstack", "newsletter-pipeline",
        "gt-lesson-plan", "gt-ownership", "rag-agentic", "dist-distill",
        "bumi-etl", "dist-harness", "spot-gpt", "dist-ablation",
        "bumi-models", "stock-ensemble", "capstone-quality",
        "capstone-hypothesis", "nec-yolo", "brain-survey", "nec-tracking",
        "brain-dashboards", "spot-cv",
    ),
}

# Seven variants, four headlines. `meta.headline` in the bank holds the text;
# this says which key each variant reads. Keyed here rather than duplicated in
# `bullets.yaml` so a headline edit lands in one place.
HEADLINE_KEY: dict[str, str] = {
    "ai_ml_infra": "ai_ml",
    "ai_ml_data": "ai_ml",
    "ai_ml_product": "ai_ml",
    "ai_ml_health": "ai_ml",
    "ai_ml_cv": "cv",
    "ds_analytics": "data_science",
    "swe_backend": "swe",
}

# Which categories the Skills section front-loads. Sections absent from a list
# still render, appended in bank order — a new skill category cannot silently
# disappear, which is the rule the two legacy variants already followed.
SKILL_ORDER: dict[str, list[str]] = {
    "ai_ml_infra": ["GenAI & LLM", "ML & Frameworks", "Cloud & Infra",
                    "Data & Backend", "Languages", "Statistics & Analysis",
                    "Analytics & BI"],
    "ai_ml_data": ["Data & Backend", "Cloud & Infra", "ML & Frameworks",
                   "GenAI & LLM", "Languages", "Statistics & Analysis",
                   "Analytics & BI"],
    "ai_ml_product": ["GenAI & LLM", "ML & Frameworks", "Data & Backend",
                      "Languages", "Cloud & Infra", "Statistics & Analysis",
                      "Analytics & BI"],
    "ai_ml_health": ["ML & Frameworks", "GenAI & LLM", "Statistics & Analysis",
                     "Cloud & Infra", "Data & Backend", "Languages",
                     "Analytics & BI"],
    "ai_ml_cv": ["ML & Frameworks", "Languages", "GenAI & LLM",
                 "Cloud & Infra", "Data & Backend", "Statistics & Analysis",
                 "Analytics & BI"],
    "ds_analytics": ["Statistics & Analysis", "ML & Frameworks", "Languages",
                     "Analytics & BI", "Data & Backend", "GenAI & LLM",
                     "Cloud & Infra"],
    "swe_backend": ["Data & Backend", "Cloud & Infra", "Languages",
                    "GenAI & LLM", "ML & Frameworks", "Statistics & Analysis",
                    "Analytics & BI"],
}

# Phase 1 archetypes collapse to three lanes. `archetype` is filled at scoring
# time and is the only lane signal that exists before a session starts.
LANE_FOR_ARCHETYPE: dict[str, str] = {
    "ai_ml_engineer": "ai_ml",
    "ml_platform": "ai_ml",
    "swe_generalist": "swe",
    "data_scientist": "ds",
    "analytics": "ds",
}

# The AI/ML lane is the only one that splits by domain — the other two are one
# ordering each, because a data-science posting wants the same résumé whether
# the company sells to developers or to hospitals.
_AI_ML_BY_DOMAIN: dict[str, str] = {
    "ai_infra": "ai_ml_infra",
    "data_infra": "ai_ml_data",
    "ai_saas": "ai_ml_product",
    "product_saas": "ai_ml_product",
    "healthcare": "ai_ml_health",
    "robotics_cv": "ai_ml_cv",
    # Fintech is 1% of the priority list and 21 of its 22 companies are tier
    # 3. It reads as backend work here rather than earning an ordering.
    "fintech": "swe_backend",
}

# What an unknown or missing domain falls back to. `ai_infra` is the largest
# share of the priority list and the queue, so it is the least wrong guess —
# but the fallback firing at all is worth noticing, which is why `resolve`
# reports it rather than swallowing it.
DEFAULT_VARIANT = "ai_ml_infra"


def resolve(archetype: str | None, domain: str | None) -> tuple[str, str]:
    """Pick a variant from `(lane, domain)`. Returns (variant, why).

    The `why` is not decoration. Roughly 565 postings were scored before
    `domain` existed and carry NULL, so the fallback is a live path rather
    than a defensive one, and a caller — or Anish reading a tool result —
    needs to know whether the grid resolved or guessed. PHASE5_PLAN §4.2 has
    Claude proposing and Anish confirming precisely for these.
    """
    lane = LANE_FOR_ARCHETYPE.get(archetype or "")
    if lane == "ds":
        return "ds_analytics", f"data-science lane ({archetype})"
    if lane == "swe":
        return "swe_backend", f"software-engineering lane ({archetype})"

    if lane == "ai_ml":
        variant = _AI_ML_BY_DOMAIN.get(domain or "")
        if variant:
            return variant, f"AI/ML lane, domain {domain}"
        return DEFAULT_VARIANT, (
            f"AI/ML lane but domain {domain!r} is unknown or unscored; "
            f"defaulted to {DEFAULT_VARIANT} — confirm before rendering")

    return DEFAULT_VARIANT, (
        f"archetype {archetype!r} maps to no lane; defaulted to "
        f"{DEFAULT_VARIANT} — confirm before rendering")


def headline_key(variant: str) -> str:
    """Which `meta.headline` key a variant reads. Unknown variants pass
    through unchanged, so the two legacy names still resolve to themselves."""
    return HEADLINE_KEY.get(variant, variant)


def ordering(variant: str) -> tuple[str, ...] | None:
    return ORDERINGS.get(variant)

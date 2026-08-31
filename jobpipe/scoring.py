"""Stage B: LLM fit scoring against the master profile.

Provider-agnostic (addendum §2): everything here — the rubric, escalation,
retries, token accounting — is written once, and `jobpipe.providers` handles
the per-vendor request shape. Gemini is the default; Anthropic is the last
rung of the escalation ladder.

Design notes, several of which deviate from the original spec for verified
reasons:

* **Determinism comes from the schema, not from `temperature`.** The spec asks
  for `temperature = 0`; Gemini accepts it and Anthropic rejects it on
  `claude-sonnet-5`. Constraining the response to a JSON schema is a stronger
  guarantee than low temperature anyway, and it is the one mechanism both
  providers support, so it is the one this module relies on. Each provider
  still requests determinism its own way.

* **The master profile is a cached prefix.** Both providers charge roughly a
  tenth for a cache hit, and both fail *silently* when the prefix is too
  short — no error, the cache counter simply stays at 0 and the bill is ten
  times what you expected, forever. The prefix is therefore measured at
  startup and the run refuses to begin if it sits under the model's floor.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import db
from .providers import ScoringProvider, default_escalation_model, default_model

log = logging.getLogger(__name__)

# A score in this band means the model is unsure; those get a second opinion
# from the next model up. Escalate the model, not the provider (§2).
ESCALATION_BAND = (5, 7)

MAX_DESCRIPTION_CHARS = 12_000

ARCHETYPES = ["ai_ml_engineer", "data_scientist", "ml_platform",
              "swe_generalist", "analytics", "non_technical"]

# What the EMPLOYER does, orthogonal to what the ROLE is. It selects the
# resume variant downstream (PHASE5_PLAN 4.2), which is why it is worth a
# field of its own rather than being inferred from the company name later.
#
# Sized against the real target list, not guessed: all 143 companies in
# targets.csv were classified (scripts/classify_targets.py). ai_infra and
# data_infra are 29% each of the tier-1/2 companies, ai_saas 17%, healthcare
# 10%, product_saas 9%, robotics_cv 6%, fintech 1%.
#
# NOT to be confused with `domain_relevance`, which is a SCORE about fit.
# This is a label about the employer.
DOMAINS = ["ai_infra", "ai_saas", "data_infra", "product_saas",
           "healthcare", "robotics_cv", "fintech", "other"]

# Bump whenever RUBRIC changes. A score is only comparable to another score
# produced under the same rubric, and a corpus that mixes them silently is a
# corpus whose calibration numbers mean nothing. test_scoring pins the rubric
# hash against this, so changing the text without bumping fails the suite.
RUBRIC_VERSION = "2026-08-30.1"

LEGITIMACY = ["ok", "suspicious", "likely_ghost"]

# Orthogonal to archetype, deliberately. A consulting role can be genuine ML
# engineering, so folding "consultant" into ARCHETYPES would force a choice
# between recording the lane and recording the arrangement, and lose whichever
# lost. Same reasoning as keeping legitimacy on its own axis (3.2).
ENGAGEMENT = ["direct", "consulting"]

SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "archetype": {
            "type": "string",
            "enum": ARCHETYPES,
            "description": "The lane this posting belongs to. Decided first, "
                           "because the rubric branches on it.",
        },
        "domain": {
            "type": "string",
            "enum": DOMAINS,
            "description": "What the EMPLOYER does, not what the role is. "
                           "This is a label about the company's field; it is "
                           "not `domain_relevance`, which is a score about "
                           "fit. Use `other` rather than forcing a bad fit.",
        },
        "engagement": {
            "type": "string",
            "enum": ENGAGEMENT,
            "description": "consulting ONLY when the employer's business is "
                           "supplying people or services to other companies: "
                           "a consultancy, systems integrator, IT services "
                           "firm, staffing or contract agency. direct for any "
                           "role building the employer's own product, "
                           "including forward-deployed and deployed engineer "
                           "roles that install it inside customers.",
        },
        "legitimacy": {
            "type": "string",
            "enum": LEGITIMACY,
            "description": "Whether this looks like a real open req. Never "
                           "contributes to overall or apply_score.",
        },
        "legitimacy_reason": {
            "type": "string",
            "description": "One sentence. Empty when legitimacy is ok.",
        },
        "reasoning": {
            "type": "string",
            "description": "Two sentences justifying the scores. Written "
                           "before the numbers, and referring to specifics "
                           "from both the profile and the posting.",
        },
        "skills_match": {"type": "integer", "minimum": 0, "maximum": 10,
                         "description": "0-10"},
        "seniority_fit": {"type": "integer", "minimum": 0, "maximum": 10,
                          "description": "0-10"},
        "domain_relevance": {"type": "integer", "minimum": 0, "maximum": 10,
                             "description": "0-10"},
        "dealbreakers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Hard blockers stated in the posting: clearance "
                           "requirements, mandatory relocation, fluency in a "
                           "language the candidate does not speak, years of "
                           "experience far beyond the profile. Empty if none.",
        },
        "overall": {"type": "integer",
                    "minimum": 0, "maximum": 10,
            "description": "0-10 fit on paper, judged strictly"},
        "apply_score": {
            "type": "integer",
            "minimum": 0, "maximum": 10,
            "description": "0-10 whether it is worth sending an application "
                           "anyway. Diverges upward from `overall` when the "
                           "skills match but the years do not.",
        },
        "apply_reason": {
            "type": "string",
            "description": "One sentence. If apply_score differs from overall "
                           "by 2 or more, say what drives the gap.",
        },
    },
    # archetype first: the rubric branches on it, and the schema order is the
    # order the model fills the fields in.
    "required": ["archetype", "domain", "engagement", "legitimacy", "legitimacy_reason",
                 "reasoning", "skills_match", "seniority_fit",
                 "domain_relevance", "dealbreakers", "overall",
                 "apply_score", "apply_reason"],
    "additionalProperties": False,
}

# The scoring rubric is deliberately explicit about what each band means.
# A first-pass prompt without worked anchors clusters everything at 6-8, which
# carries no information and makes the ranking useless.
RUBRIC = """\
You are scoring job postings for fit against one candidate's profile, which \
follows below. You will be given one posting at a time.

=== STEP 1: ARCHETYPE — decide this before anything else ===

Classify the posting into exactly one lane, then score against that lane's \
criteria. A single rubric spanning AI/ML engineering and data science is why \
scores drift toward the middle; the lane is what makes the numbers mean \
something.

  ai_ml_engineer  building with models — LLM and agent pipelines, RAG,
                  fine-tuning, evaluation harnesses, applied AI product work
  data_scientist  experimentation and inference — A/B testing, causal
                  analysis, metrics, statistical modelling
  ml_platform     infrastructure FOR ML — training and serving infra, GPU
                  orchestration, feature stores, deployment plumbing
  swe_generalist  general software engineering with no ML core
  analytics       BI, dashboards, reporting, SQL-first analyst work
  non_technical   the work itself is not engineering or analysis — sales,
                  marketing, recruiting, operations, legal, finance, IT
                  support, program and project management

**Classify on the work described, never on a domain word in the title.** \
`non_technical` is a FLAG, not a verdict: it lowers the ceiling but it does \
not stop the analysis, and every axis is still scored on its merits. Apply it \
only when the day-to-day work is genuinely not engineering or analysis. A \
business word in the title is not sufficient evidence:

  - "Marketing Engineer" that builds internal tooling and data pipelines is
    swe_generalist or ai_ml_engineer, NOT non_technical. It is an engineering
    role that happens to serve the marketing function.
  - "AI Advisory Consultant" that scopes and builds ML solutions for clients
    is ai_ml_engineer with engagement=consulting, NOT non_technical.
  - "Analytics Engineer" building dbt models is analytics, not non_technical.

Conversely a technical-sounding title with no technical work is \
non_technical: a "Solutions Architect" who only runs demos and writes \
proposals belongs there whatever the title says.

Read the responsibilities before deciding. If the role involves writing code, \
building pipelines, or analysing data as its core, it is NOT non_technical.

The candidate's profile declares **ai_ml_engineer and data_scientist as \
co-primary lanes**. Apply the lane you assigned:

  ai_ml_engineer / data_scientist — primary. Judge on the merits.
  ml_platform    — adjacent. The candidate has built pipelines but not
                   operated them at platform scale. Cap domain_relevance at 7.
  swe_generalist — adjacent. Judge on merit when the role is AI-flavoured;
                   otherwise cap domain_relevance at 6.
  analytics      — weak overlap. Cap domain_relevance at 5.
  non_technical  — weakest overlap. Cap domain_relevance at 3 and judge
                   every other axis on its merits. Do NOT force the scores to
                   1, and do NOT stop analysing. This label used to
                   short-circuit to 1 across the board; it cost a real role
                   (an "AI Advisory Consultant" that scoped and built ML
                   solutions, human-scored 6, model-scored 1) and the loss was
                   invisible because the score carried no trace of why.

Set `domain` from **what the employer does**, independently of the lane. It \
decides which resume variant the candidate sends, so it is about the company, \
not the role. Do not confuse it with `domain_relevance`, which is a score.

  ai_infra      what developers BUILD ON: model providers and APIs, compute
                and GPUs, model hosting and serving, vector databases,
                orchestration frameworks, eval/observability/governance
  ai_saas       an AI-native APPLICATION sold to end users or businesses —
                the customer uses the thing itself rather than building on it
  data_infra    warehouses, ETL and pipelines, streaming, data quality,
                product analytics, general observability
  product_saas  software product, developer tooling, collaboration or
                consumer, where AI is not the product
  healthcare    clinical, biotech, pharma, health data
  robotics_cv   autonomous vehicles, robotics, physical AI, vision hardware
  fintech       payments, banking, lending, crypto, risk and identity
  other         none of the above fits

Where a company applies AI to a vertical, **the vertical wins**: a clinical \
documentation AI company is `healthcare`, not `ai_saas`, because the vertical \
is what decides which evidence the candidate should lead with.

Set `engagement` at the same time as the archetype. It is a separate question \
from the lane, and it turns on **what the employer sells** — not on whether \
the role talks to customers.

  consulting  the employer's business IS supplying people or services to
              other companies: a consultancy, systems integrator, IT services
              or outsourcing firm, staffing or contract/C2C agency. What is
              being sold is the labour.
  direct      the employer sells its own product and this role builds,
              deploys or supports THAT product — even when the work happens
              largely inside customer organisations.

Customer-facing is NOT consulting. Forward-deployed engineer, deployed \
engineer, solutions engineer, applied AI engineer, enterprise engineer and \
similar titles at a product company are all `direct`: the person is employed \
by the company whose product they are deploying, and the job is a normal \
engineering hire that happens to sit close to customers.

  - "Deployed Engineer" at an LLM tooling company -> direct
  - "Applied AI Engineer, Enterprise" at an AI lab -> direct
  - "Forward Deployed Engineer" at an agent startup -> direct
  - "Consultant" or "Advisory" at a firm whose product IS advisory
    delivery -> consulting
  - any staffing, contract-placement or systems-integration firm -> consulting

When unsure, ask: if this company stopped selling consulting hours tomorrow, \
would it still have a product? If yes, the role is `direct`.

=== STEP 2: LEGITIMACY — tracked separately, never affects any score ===

legitimacy: ok | suspicious | likely_ghost

A legitimate poor-fit role and a fake perfect-fit role need different \
handling, so this rides on its own axis. It must NOT influence overall or \
apply_score in either direction.

  ok            a specific, concrete req — named team, real responsibilities,
                a scope you could describe back
  suspicious    boilerplate with no specifics; a requirements list so broad it
                matches no real person; evergreen "always hiring" phrasing; a
                salary band spanning several levels
  likely_ghost  reads as pipeline-building rather than hiring — no team named,
                no concrete work described, identical across many locations

legitimacy_reason: one sentence, or an empty string when legitimacy is ok.

=== STEP 3: SCORE ===

Score four dimensions from 0 to 10. Reason before you assign numbers.

skills_match — how well the candidate's demonstrated skills cover what the \
posting actually requires.
  9-10  covers essentially everything required, with direct evidence
  6-8   covers the core requirements; gaps are secondary or learnable
  3-5   covers some requirements but misses something central
  0-2   little overlap

**Weigh where the skill was demonstrated.** Much of this candidate's \
experience comes from personal and academic projects rather than production \
systems. A posting that names a tool the candidate has used in a project is a \
partial match, not a full one, when the posting expects that tool operated at \
production scale, on-call, or across a large team. Score the overlap the \
posting actually asks for, not the presence of a keyword in the profile.

seniority_fit — how far the posting's stated experience bar sits above the \
candidate's. This is a DISCOUNT, not a gate. The candidate is early-career \
and expects to stretch; a posting asking for a few more years than they have \
is still worth applying to when the skills line up.
  9-10  the bar is at or below the candidate's experience AND the work
        described is well-bounded feature work
  6-8   bar is 1-4 years above the candidate — reachable
  3-5   bar is 5-7 years above
  0-2   bar is 8+ years above, or the title itself is senior/staff/lead/\
principal/manager

**A posting that states no years bar has NOT thereby earned a 9-10.** This is \
the single most common way to get this score wrong. Senior roles routinely \
omit a number — the scope of the work is what gives them away, and a missing \
bar means you must read the scope, not that the bar is low. When no years \
requirement appears anywhere in the posting, ignore the band table above and \
score from scope alone:

  - one strong scope signal below  ->  seniority_fit at most 6
  - two or more                    ->  seniority_fit at most 5
  - none, and the work is bounded feature work  ->  8-10 is fair

**Weigh scope, not only the stated years.** Treat all of the following as \
evidence of a high bar, whether or not a number appears anywhere in the \
posting:

  - owning a product area, service or system end to end
  - defining strategy, roadmap or technical direction
  - leading, mentoring or setting standards for other engineers
  - representing the company externally to customers, partners or startups
  - "founding", "lead", "principal" or "head" framing in the responsibilities
    even when absent from the title
  - being the first hire for a function, or standing something up from nothing

**Hard rule: if two or more of those signals are present, seniority_fit must \
not exceed 5, whatever the posting says about years.** Absence of a stated \
number is not evidence of a low bar — senior roles frequently omit one. \
Conversely, a posting asking for a few years but describing well-bounded \
feature work is a 6-8: the number matters less than what you are asked to own.

Research roles deserve particular care. "Research Engineer" or "Research \
Scientist" at a lab implies published or industrial research experience the \
candidate does not have, even when the listed tooling matches theirs exactly. \
A matching stack does not substitute for a research track record; cap \
seniority_fit at 5 for these unless the posting explicitly invites \
early-career or new-graduate applicants.

domain_relevance — how relevant the problem space and industry are to the \
candidate's background and stated interests.
  9-10  squarely in the candidate's strongest domain
  6-8   adjacent; the experience transfers
  3-5   unrelated but not disqualifying
  0-2   unrelated and requires domain knowledge the candidate lacks

overall — how well the candidate matches this posting ON PAPER, judged \
strictly and honestly. This is a fit assessment, not a recommendation. A \
posting the candidate is not yet qualified for scores low here even if it \
would be worth a speculative application — that judgement belongs in \
apply_score below.

  9-10  meets essentially everything the posting asks for
  7-8   meets the core asks; the gaps are secondary
  5-6   meets some asks, missing something the posting treats as required
  3-4   substantial gaps in skills or required experience
  0-2   not a plausible candidate on paper

**9 and 10 are reserved.** Give them only when the posting is explicitly \
aimed at early-career, new-grad or campus candidates AND the stack overlaps \
strongly. A role that merely matches the candidate's interests well, however \
exciting, tops out at 8 — it still expects experience they do not have.

Be discriminating. If most postings score 6-8 the ranking is useless. A \
genuinely average posting should score 5.

apply_score — whether it is worth spending an application on, given that fit.

`overall` answers "does this match on paper". This answers "should they send \
one anyway". The two diverge in one direction constantly, and capturing that \
gap is the entire reason this field exists: **a posting whose listed skills \
line up closely but whose stated experience bar sits above the candidate is a \
MODERATE fit and a STRONG apply.** Years-of-experience bars are routinely \
soft and frequently negotiated. A gap in the skills themselves is not.

  9-10  skills line up closely — apply regardless of the stated years
  7-8   skills mostly line up; the bar is a stretch, the application is still \
worth writing
  5-6   partial skill overlap; apply if the week is quiet
  3-4   the gap is in the skills themselves, not merely the years
  0-2   hard dealbreaker, or a function the candidate is not pursuing

Three rules, in order of precedence:
  1. A hard dealbreaker caps apply_score at 2 no matter how well the skills \
fit — no sponsorship, security clearance, mandatory relocation the candidate \
has ruled out, professional fluency in a language the candidate does not \
speak, or a stated bar of 8+ years.
  2. A wrong-function role caps apply_score at 3 regardless of general \
engineering overlap: frontend-only, IT support and helpdesk, hardware and \
silicon, physical security, sales engineering, marketing operations.

     Consulting is NOT a wrong function and rule 2 does not apply to it. The \
candidate is on a consulting firm's roster between client placements and has \
already delivered into a client environment, including a federal one, so \
agency and staffing work is a real option for him. Score such a role on its \
merits, then subtract 1 from apply_score because he would rather have a \
direct role that sponsors — a preference, not a barrier. Never take \
apply_score below 5 for this reason alone.

     Apply that subtraction ONLY when engagement is `consulting` as defined \
in step 1 — an agency, staffing or services firm selling labour. A \
forward-deployed or deployed engineer role at a product company is `direct` \
and takes no discount at all, however customer-facing it is.
  3. Otherwise, when a stated years figure is the ONLY thing between the \
candidate and the role, apply_score must not fall below 6 if skills_match is \
7 or higher. Right skills, not enough years is the single most common shape \
of a role worth applying to, and treating it as a rejection is the costliest \
mistake this system can make.

     Before applying rule 3, check WHY seniority_fit is low. Rule 3 covers a \
stated years figure and nothing else. If seniority_fit is 5 or below because \
of SCOPE — ownership of an area, strategy, leading others, external \
representation, or a research track record — then **rule 3 does not apply and \
there is no floor at all**. Let apply_score fall to 3-5, or below that when \
the scope gap is wide. A role that asks you to own a domain is not one you \
grow into by applying to it, however well the stack matches. Do not invoke \
rule 3 and then note the scope gap in apply_reason as a caveat; if the gap is \
real, it belongs in the number.

apply_score is usually equal to or higher than overall. It is lower when a \
dealbreaker applies, when the function is wrong, or when the seniority gap is \
one of scope rather than years.

**Calibration note.** Measured against the candidate's own scores, this axis \
runs about 0.7 too high and never goes below 6, while their scores span 2 to \
10. A set of postings should produce apply_scores across the whole range: if \
nothing in a batch scores below 6, the scope check above is not being \
applied. Scoring 2 or 3 is a normal outcome, not a failure of nerve.

=== WORKED EXAMPLES ===

Calibrated against the candidate's own judgement on real postings. Note that \
the two scores come apart in the middle rows — that divergence is the point.

archetype ai_ml_engineer, overall 9 / apply 9 — "Software Engineer, New Grad \
(2027)" at an infrastructure company. Explicitly early-career, no experience \
bar, listed stack is one the candidate has shipped projects in. Nothing to \
discount on either axis.

archetype ai_ml_engineer, overall 4 / apply 4 — "Demo Experience Engineer, \
Technical Success". The stack is a direct match and the posting states no \
years requirement at all, but the role owns end-to-end demos for customers \
and represents the company externally. That scope is the seniority signal; \
the absence of a number is not evidence of a low bar. Scoring this 9 on stack \
alone was a real error.

archetype ai_ml_engineer, overall 5 / apply 6 — "Applied AI Engineer, Agents \
& Automations" at an LLM company. The stack is an excellent match and the \
posting names no years requirement anywhere. It is still not a 9: the role \
owns an automation surface end to end and partners directly with customer \
teams, which is two scope signals, so seniority_fit caps at 5. The candidate \
read this one as "mostly a senior role" and was right. A strong stack with an \
unstated bar is the most over-scored shape there is — the missing number is \
the thing that should make you read the responsibilities harder, not relax.

overall 5 / apply 8 — "Deployed Engineer (Early Career)" at an LLM tooling \
company. Says "3+ years", which the candidate does not have, so the paper fit \
is middling. But the framework is the exact one behind their own projects, so \
the application is well worth writing. This is the canonical shape rule 3 \
protects.

overall 4 / apply 7 — "Software Engineer, Product Analytics Platform", asking \
3+ years. The candidate has not held the title and the posting names some \
tooling they lack, so it is a weak paper fit — but the data-pipeline work \
described is squarely what they have built. Worth an application.

overall 4 / apply 4 — "Research Engineer, Domain Scaling" at a frontier lab. \
Strong domain overlap, but the work assumes years of research experience and \
the posting targets established researchers. The gap is in the substance of \
the work, not just the years, so apply_score does not rise.

overall 3 / apply 2 — "Software Engineer, Frontend". Ordinary engineering \
overlap, but the function is one the candidate is not pursuing. Rule 2 caps it.

overall 2 / apply 1 — "Data Scientist, B2B Demand Generation" requiring 10+ \
years. Rule 1: an extreme bar is a dealbreaker on its own.

dealbreakers must list only hard blockers stated in the posting itself — \
security clearance, mandatory relocation to a city the candidate has ruled \
out, a years-of-experience floor far above the profile. Do not invent them, \
and do not list mere preferences.

Language requirements turn entirely on WHICH SECTION they appear in, so read \
the heading above the bullet before deciding:

  * Under a requirements heading ("What you'll bring", "Requirements", \
"Qualifications"), "professional fluency in X and English" is a HARD \
dealbreaker for any language the profile does not list. Rule 1 applies and \
apply_score caps at 2, however well the engineering work fits. A role the \
candidate cannot do the daily communication for is not a candidate role.
  * Under a preferences heading ("Even better", "Nice to have", "Bonus", \
"Preferred"), the same sentence is a preference. Do NOT treat it as a \
dealbreaker and do not reduce any score for it.

The same company often posts one role each way, so the section heading is the \
only thing separating them.

=== CANDIDATE MASTER PROFILE ===
"""


@dataclass
class ScoreRun:
    scored: int = 0
    escalated: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    errors: list[str] = field(default_factory=list)


class ProfileTooShort(RuntimeError):
    """The cached prefix is below the model's minimum cacheable size."""


def load_profile(path: str | Path | None = None) -> str:
    path = Path(path or os.environ.get("JOBPIPE_PROFILE_PATH",
                                       "config/master_profile.md"))
    if not path.exists():
        raise FileNotFoundError(
            f"master profile not found at {path}. Stage B cannot run without "
            f"it; everything downstream inherits its quality.")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"{path} is empty")
    return text


CALIBRATION_EXAMPLES_PATH = Path("config/calibration_examples.md")


def load_calibration_examples(path: str | Path | None = None) -> str:
    """Few-shot examples built from Anish's own hand scores, or "" if absent.

    Optional by design. It is generated from gitignored CSVs, so a fresh
    checkout will not have it, and scoring must still run — degraded, not
    broken.

    Deliberately NOT part of `master_profile.md`. The profile is also read by
    the letter writer, the grounding check and the MCP session; scoring
    calibration is noise in all three. Keeping it separate means this prompt
    can grow without widening the others.

    Regenerate with scripts/build_calibration_examples.py after hand-scoring
    more postings, and bump RUBRIC_VERSION when you do — the examples are part
    of the prompt, so a score produced with a different set is a score under a
    different rubric.
    """
    # OFF by default, on the evidence. Measured on the n=19 holdout
    # 2026-08-31: adding 13 examples built from Anish's own scores halved the
    # bias (+1.21 -> +0.89 fit, +1.11 -> +0.63 apply) and simultaneously
    # destroyed rank agreement, Spearman +0.346 -> -0.016. Rank is what the
    # sheet actually uses - it takes the top 25, it does not threshold - so a
    # change that improves absolute scores while randomising their order is a
    # net loss. Opt in with JOBPIPE_CALIBRATION_EXAMPLES=<path> to re-run the
    # experiment; the generator is scripts/build_calibration_examples.py.
    explicit = path or os.environ.get("JOBPIPE_CALIBRATION_EXAMPLES")
    if not explicit:
        return ""
    path = Path(explicit)
    if not path.exists():
        log.info("no calibration examples at %s; scoring without few-shot "
                 "anchors", path)
        return ""
    return path.read_text(encoding="utf-8").strip()


def build_system_prompt(profile: str, examples: str | None = None) -> str:
    """Rubric + profile + calibrated examples, as one string.

    Byte-identical on every call, which is what makes implicit caching fire.

    Returned as plain text rather than provider-shaped blocks: Anthropic wraps
    it in a cache_control block, Gemini passes it as system_instruction, and
    that difference is the provider's business, not this module's.

    Examples go last: they are few-shot anchors on the judgement the rules
    above only approximate, and the closer they sit to the posting being
    scored the more weight they carry.
    """
    if examples is None:
        examples = load_calibration_examples()
    if not examples:
        return RUBRIC + profile
    return RUBRIC + profile + "\n\n---\n\n" + examples + "\n"


def check_cacheable(provider: ScoringProvider, system_prompt: str,
                    model: str) -> int:
    """Measure the cached prefix and refuse to run if it will not cache.

    A prefix under the model's floor produces no error and no cache entry —
    the bill is simply ~10x higher than expected, forever, with nothing in the
    logs to say why. Measuring once at startup is the whole defence, and it is
    why `cache_minimum_tokens` is part of the provider interface.
    """
    tokens = provider.count_tokens(model, system_prompt)
    minimum = provider.cache_minimum_tokens(model)
    if tokens < minimum:
        shortfall = minimum - tokens
        raise ProfileTooShort(
            f"cached prefix is {tokens} tokens; {model} will not cache below "
            f"{minimum} and gives no error when it declines. Lengthen "
            f"config/master_profile.md by roughly {shortfall} tokens "
            f"(~{int(shortfall * 0.75)} words), or choose a model with a "
            f"lower floor.")
    return tokens


def _posting_message(row) -> str:
    description = (row["description"] or "")[:MAX_DESCRIPTION_CHARS]
    return (
        f"Company: {row['company']}\n"
        f"Title: {row['title']}\n"
        f"Location: {row['location'] or 'not stated'}\n"
        f"Remote: {'yes' if row['remote_flag'] else 'not stated'}\n"
        f"Posted: {row['posted_at'] or 'not stated'}\n\n"
        f"--- job description ---\n{description}"
    )


def score_posting(provider: ScoringProvider, row, system_prompt: str,
                  run: ScoreRun, model: str | None = None,
                  escalation_model: str | None = None) -> dict | None:
    """Score one posting, escalating borderline results. Never raises."""
    model = model or default_model(provider.name)
    escalation_model = escalation_model or default_escalation_model(provider.name)
    user_text = _posting_message(row)

    def attempt(active_model: str) -> dict | None:
        for tries in range(2):          # one retry, then give up and move on
            try:
                response = provider.score(
                    model=active_model, system_prompt=system_prompt,
                    user_text=user_text, schema=SCORE_SCHEMA)
                run.input_tokens += response.input_tokens
                run.output_tokens += response.output_tokens
                run.cache_read_tokens += response.cached_tokens

                if response.refused:
                    run.errors.append(
                        f"posting {row['id']}: refused by {active_model}"
                        f" ({response.refusal_reason})")
                    return None
                return json.loads(response.text or "")
            except json.JSONDecodeError as exc:
                if tries == 0:
                    log.warning("  posting %s: unparseable JSON, retrying (%s)",
                                row["id"], exc)
                    continue
                run.errors.append(f"posting {row['id']}: bad JSON after retry")
                return None
            except Exception as exc:  # noqa: BLE001 - one posting is not the run
                if tries == 0:
                    log.warning("  posting %s: %s, retrying",
                                row["id"], type(exc).__name__)
                    continue
                run.errors.append(
                    f"posting {row['id']}: {type(exc).__name__}: {exc}")
                return None
        return None

    score = attempt(model)
    if score is None:
        run.failed += 1
        return None
    score["model"] = model

    low, high = ESCALATION_BAND
    if low <= score.get("overall", 0) <= high:
        escalated = attempt(escalation_model)
        if escalated is not None:
            escalated["model"] = escalation_model
            run.escalated += 1
            score = escalated

    run.scored += 1
    return score


def scorer_id(provider: ScoringProvider) -> str:
    """Who produced the score: provider and backend, e.g. `gemini/vertex`.

    The backend matters as much as the provider here. The same model id can be
    served by the Developer API or by Vertex, and they are different billing
    tracks, different data-governance terms, and - as the 2026-08-30 probe
    found - different caching behaviour. A score that records only the model
    cannot tell those apart afterwards.
    """
    from .providers import gemini_backend
    backend = gemini_backend() if provider.name == "gemini" else "api"
    return f"{provider.name}/{backend}"


def score_pending(conn, provider: ScoringProvider, system_prompt: str,
                  limit: int, model: str | None = None,
                  escalation_model: str | None = None) -> ScoreRun:
    """Score the Stage B queue, committing as it goes.

    Committing per posting rather than at the end means an interrupted run
    keeps the work it already paid for.
    """
    run = ScoreRun()
    model = model or default_model(provider.name)
    rows = db.get_unscored(conn, limit=limit)
    log.info("scoring %d posting(s) with %s via %s",
             len(rows), model, provider.name)

    for index, row in enumerate(rows, start=1):
        score = score_posting(provider, row, system_prompt, run,
                              model=model, escalation_model=escalation_model)
        if score is not None:
            # Provenance, per PHASE5_PLAN 3.5. `model` alone does not say
            # which backend served the call or which rubric produced the
            # judgement, and the corpus is about to mix 3.1-on-the-API scores
            # with 2.5-on-Vertex ones. Without these the mixture is invisible
            # and every calibration figure computed over it is meaningless.
            db.insert_score(conn, row["id"], score, score["model"],
                            scorer=scorer_id(provider),
                            rubric_version=RUBRIC_VERSION)
            conn.commit()
        if index % 25 == 0:
            log.info("  %d/%d scored", index, len(rows))

    return run

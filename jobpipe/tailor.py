"""Bullet selection (Phase 2 spec §9.6, §3.1a).

One constrained call. The model reads a posting and returns bullet IDs, an
ordering, and the posting's skill vocabulary. It never writes prose, and it
cannot invent a bullet: the valid IDs are compiled into the response schema as
an `enum`, so an unknown ID is rejected by the API rather than by us.

That is the whole design. §2.1 asks for fabrication to be structurally
difficult rather than discouraged, and a schema the model cannot step outside
of is stronger than any instruction. `Bank.select` re-checks anyway, because a
provider that ignores enums should fail loudly rather than silently.

Keyword alignment (§3.1a) is selection and reordering only: matching bullets
are preferred and the Skills section is reordered to front-load relevant
categories. No bullet text changes, and no skill is ever added.
"""
from __future__ import annotations

import json
import logging

from . import variants
from .bullets import Bank, Bullet, match_score
from .providers import ScoringProvider, default_model

log = logging.getLogger(__name__)

VARIANTS = ("ai_ml", "data_science")

# Which resume layout each Phase 1 lane maps to. Only two variants ship
# (§3.1); the others pick whichever is closer rather than failing.
VARIANT_FOR_ARCHETYPE = {
    "ai_ml_engineer": "ai_ml",
    "ml_platform": "ai_ml",
    "swe_generalist": "ai_ml",
    "data_scientist": "data_science",
    "analytics": "data_science",
}

# Every additional role costs roughly a heading's worth of vertical space
# before a single bullet lands, so a selection spread across nine roles fits
# nine bullets while the same count concentrated in five fits twelve. Measured
# on the real template, not assumed.
TARGET_BULLETS = 16

# A floor, enforced by the schema rather than requested in prose. The renderer
# already drops whatever will not fit and reports it, so over-selecting is
# free and under-selecting is invisible.
MIN_BULLETS = 14


def build_selection_schema(bank: Bank) -> dict:
    """Response schema with the bank's IDs baked in as an enum."""
    ids = sorted(bank.bullets)
    sections = list(bank.surfaceable_skills())
    return {
        "type": "object",
        "properties": {
            "posting_skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Skills and technologies the posting actually "
                               "asks for, lowercased. Extracted verbatim from "
                               "the posting, not inferred.",
            },
            "variant": {"type": "string", "enum": list(VARIANTS)},
            "reasoning": {
                "type": "string",
                "description": "Two sentences: what this posting is really "
                               "asking for, and why these bullets answer it.",
            },
            "selected": {
                "type": "array",
                "items": {"type": "string", "enum": ids},
                # Asking for ~16 in the prompt produced 9. The renderer drops
                # what will not fit, so under-selecting is the costlier error:
                # it silently produces a thin resume with no signal that
                # anything was left out. A floor makes the ask structural.
                "minItems": min(MIN_BULLETS, len(ids)),
                "maxItems": len(ids),
                "description": f"Bullet IDs, strongest first. Select at least "
                               f"{MIN_BULLETS} and ideally {TARGET_BULLETS}, "
                               f"concentrated in as few roles as possible. "
                               f"The renderer drops any that do not fit, so "
                               f"selecting generously costs nothing.",
            },
            "skill_order": {
                "type": "array",
                "items": {"type": "string", "enum": sections},
                "description": "Skill section names, most relevant to this "
                               "posting first.",
            },
        },
        "required": ["posting_skills", "variant", "reasoning", "selected",
                     "skill_order"],
        "additionalProperties": False,
    }


def build_system_prompt(bank: Bank, profile: str = "") -> str:
    """The cacheable static prefix: rules, the whole bank, then the profile.

    §5 asks for the master profile AND the bank to be cached together, and
    there is a hard reason beyond convention: the bank alone measures ~2,400
    tokens, below Flash-Lite's 4,096 cache floor, and a provider that declines
    to cache says nothing about it. Prefix caching would silently never fire.

    Including the profile is safe as well as necessary. The model can only
    emit bullet IDs from the schema's enum, so extra context improves which
    honest evidence it picks without widening what it can claim — it learns
    the candidate's positioning and skill tiers, not new material.

    Byte-stable across calls, exactly as in Phase 1.
    """
    lines = [
        "You are selecting which pre-written resume bullets best answer a "
        "specific job posting.",
        "",
        "=== WHAT YOU MAY AND MAY NOT DO ===",
        "",
        "You SELECT and ORDER. You do not write.",
        "",
        "  * Return bullet IDs from the bank below. You cannot return "
        "anything else — the schema will reject it.",
        "  * You may not author, edit, paraphrase or 'improve' bullet text. "
        "The text is fixed and will be rendered exactly as written.",
        "  * If the posting asks for something the bank does not evidence, "
        "the correct answer is a resume without it. Absence is honest; "
        "there is no bullet to add and you must not imply one.",
        "",
        "=== HOW TO CHOOSE ===",
        "",
        f"1. Extract what the posting actually asks for into "
        f"`posting_skills` — terms present in the posting, not inferred.",
        "2. Pick the variant: `ai_ml` for AI/ML/GenAI engineering and "
        "platform roles, `data_science` for experimentation, statistics, "
        "analytics and modelling roles.",
        f"3. Select about {TARGET_BULLETS} bullets, strongest first.",
        "",
        "**Concentrate.** Every role you draw from costs a heading and a "
        "stack line before any bullet appears, so two bullets from five "
        "roles fits comfortably where one bullet from ten roles does not. "
        "Prefer taking several bullets from a strongly relevant role over "
        "one bullet from many weakly relevant ones. Selections that sample "
        "one bullet from everywhere lose roughly a third of their content "
        "to the one-page limit.",
        "",
        "**Prefer bullets whose `skills` overlap `posting_skills`.** This is "
        "preference between honest options, never a reason to stretch what a "
        "bullet says.",
        "",
        "**Keep the strongest evidence even when it is not a keyword match.** "
        "A metric-bearing bullet about experimental rigor is worth more than "
        "a keyword hit, for most readers.",
        "",
        "4. Order `skill_order` so the categories this posting screens on "
        "come first.",
        "",
        "=== THE BULLET BANK ===",
        "",
    ]

    for role_id, role in bank.roles.items():
        header = role.get("company", role_id)
        if role.get("title"):
            header += f" · {role['title']}"
        lines.append(f"[{role.get('kind', 'role')}] {header}")
        for bullet in bank.bullets.values():
            if bullet.role != role_id:
                continue
            skills = ", ".join(bullet.skills)
            lines.append(f"  {bullet.id}")
            lines.append(f"    text   : {bullet.render_text()}")
            lines.append(f"    skills : {skills}")
        lines.append("")

    lines += ["=== SKILL SECTIONS AVAILABLE FOR ORDERING ===", ""]
    for section, entries in bank.surfaceable_skills().items():
        lines.append(f"  {section}: {', '.join(entries)}")

    if profile:
        lines += [
            "",
            "=== CANDIDATE MASTER PROFILE (context for choosing) ===",
            "",
            "Background for judging which bullets answer a posting. It is NOT "
            "a source of resume content: anything here that is not in the "
            "bank above cannot go on the resume, and you have no way to put "
            "it there. Use it to understand depth and positioning.",
            "",
            profile,
        ]
    return "\n".join(lines)


def posting_message(row) -> str:
    """The posting, trimmed. Mirrors Phase 1's shape."""
    def field(name, default=""):
        try:
            value = row[name]
        except (KeyError, IndexError):
            return default
        return default if value is None else value

    description = str(field("description"))[:12_000]
    return (f"Company: {field('company')}\n"
            f"Title: {field('title')}\n"
            f"Location: {field('location')}\n\n"
            f"{description}")


def rank_selection(bank: Bank, ids: list[str],
                   vocabulary: list[str]) -> list[Bullet]:
    """Resolve IDs and apply the §3.1a preference as a stable tiebreak.

    The model's order is respected first — it read the posting. Keyword
    overlap and the bank's own rank only break ties, so this can reorder
    honest evidence but never introduce or alter any.
    """
    bullets = bank.select(ids)
    order = {bullet.id: index for index, bullet in enumerate(bullets)}
    return sorted(
        bullets,
        key=lambda b: (order[b.id], -match_score(b, vocabulary), b.rank))


def bullets_for_variant(bank: Bank, variant: str) -> list[Bullet]:
    """A variant's ordering, resolved against the bank.

    No provider and no posting: the ordering is fixed per lane, which is the
    point of §4.2. Relevance still enters at render time, where the drop order
    promotes a bullet whose `skills` overlap the posting vocabulary before
    cutting it — so the same ordering yields a different tail per posting
    without the variants drifting apart.

    `Bank.select` raises on an unknown id, so an ordering that has drifted
    from the bank fails loudly here rather than rendering a short resume.
    """
    ids = variants.ordering(variant)
    if ids is None:
        raise KeyError(
            f"unknown variant {variant!r}; known: "
            f"{', '.join(sorted(variants.ORDERINGS))}")
    return bank.select(list(ids))


def select_bullets(provider: ScoringProvider, bank: Bank, row,
                   system_prompt: str, model: str | None = None) -> dict:
    """One selection call. Raises on failure — a caller batching over a queue
    decides whether to continue, and a silently empty selection would render
    an almost-blank resume."""
    model = model or default_model(provider.name)
    schema = build_selection_schema(bank)

    response = provider.score(model=model, system_prompt=system_prompt,
                              user_text=posting_message(row), schema=schema)
    if response.refused:
        raise RuntimeError(
            f"selection refused by {model}: {response.refusal_reason}")

    payload = json.loads(response.text or "")
    vocabulary = payload.get("posting_skills") or []
    selected = rank_selection(bank, payload.get("selected") or [], vocabulary)
    if not selected:
        raise RuntimeError("selection returned no bullets")

    variant = payload.get("variant")
    if variant not in VARIANTS:
        log.warning("unknown variant %r, defaulting to ai_ml", variant)
        variant = "ai_ml"

    return {
        "variant": variant,
        "bullets": selected,
        "bullet_ids": [b.id for b in selected],
        "posting_skills": vocabulary,
        "skill_order": payload.get("skill_order") or None,
        "reasoning": payload.get("reasoning", ""),
        "usage": {"input": response.input_tokens,
                  "output": response.output_tokens,
                  "cached": response.cached_tokens},
        "model": model,
    }

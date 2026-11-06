"""The bullet bank — Phase 2's fabrication firewall (spec §2.1).

Resume tailoring is selection and ordering, never writing. Everything in this
module exists to make that structurally true rather than merely instructed:

  * bullet text is returned immutable, and `render_text` will only ever hand
    back a string that already exists in the bank;
  * a selection naming an unknown id is rejected outright rather than silently
    dropped, because a silent drop looks identical to the model choosing fewer
    bullets;
  * Exposure-tier skills are filtered out at load, so no downstream caller can
    surface one even by mistake (§2.3, §3.1a).

The load-time validation is deliberately noisy. This file is hand-edited, and
a typo in an archetype tag would otherwise show up as a bullet that silently
never gets selected for the lane it was written for.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PATH = Path("config/bullets.yaml")

# Must match jobpipe.scoring.ARCHETYPES. Checked at load rather than trusted,
# so a renamed lane in Phase 1 surfaces here as an error instead of as bullets
# that quietly stop matching.
VALID_ARCHETYPES = {"ai_ml_engineer", "data_scientist", "ml_platform",
                    "swe_generalist", "analytics", "non_technical"}

SURFACEABLE_TIERS = ("core", "working")


def _key(text) -> str:
    """Normalise a skill term for comparison.

    One home, used by both the skills reorder and `match_score`. They were
    written separately with the same three transformations inline, which is
    how "engagement defined in three places, written in one" happened in
    Phase 1.
    """
    return (str(text or "").strip().lower()
            .replace("-", "_").replace(" ", "_"))


class BankError(ValueError):
    """The bank is malformed. Always fatal — this file is the source of truth."""


@dataclass(frozen=True)
class Bullet:
    id: str
    role: str
    text: str
    source: str
    archetypes: tuple[str, ...]
    skills: tuple[str, ...]
    metrics: bool
    rank: int
    variants: dict = field(default_factory=dict)
    # Named workstream inside a role, e.g. the three projects under Gradient
    # Tensor. None means the bullet hangs directly off the role.
    group: str | None = None

    def render_text(self, prefer_short: bool = False) -> str:
        """Immutable text, optionally the pre-written short variant.

        The ONLY two strings this can return are ones a human wrote into the
        bank. There is no path here that composes or edits text.
        """
        if prefer_short and self.variants.get("short"):
            return " ".join(self.variants["short"].split())
        return " ".join(self.text.split())


@dataclass(frozen=True)
class Bank:
    meta: dict
    education: dict
    roles: dict          # id -> role metadata
    bullets: dict        # id -> Bullet
    skills: dict         # section -> {core, working, exposure}

    def for_archetype(self, archetype: str) -> list[Bullet]:
        """Bullets tagged for this lane, best first."""
        return sorted(
            (b for b in self.bullets.values() if archetype in b.archetypes),
            key=lambda b: (b.rank, b.id))

    def select(self, ids) -> list[Bullet]:
        """Resolve ids to bullets, in the order given.

        An unknown id raises. The model returning `dist-ablations` instead of
        `dist-ablation` must not read as "chose one fewer bullet" — that is a
        silent quality regression with no error attached, and it is exactly the
        class of failure §8 warns about.
        """
        unknown = [i for i in ids if i not in self.bullets]
        if unknown:
            raise BankError(
                f"selection names {len(unknown)} unknown bullet id(s): "
                f"{', '.join(unknown[:5])}. Known ids: "
                f"{', '.join(sorted(self.bullets)[:8])}...")
        return [self.bullets[i] for i in ids]

    def surfaceable_skills(self, emphasise=None) -> dict[str, list[str]]:
        """Core + Working only, per section. Exposure is never returned.

        §3.1a: an Exposure skill is never added to a resume to match a
        keyword, and this is where that becomes impossible rather than
        discouraged.

        `emphasise` is a posting's vocabulary. Entries matching it move to the
        front of their section — "front-load matching entries" in §3.1a step 3.
        This is a permutation and nothing else: the same entries come back in
        a different order, so a recruiter scanning the first few items of a
        line sees the relevant ones without anything being added or dropped.
        Verified by test, because a reorder that quietly lost an entry would
        look exactly like a reorder that worked.
        """
        out = {}
        wanted = {_key(v) for v in (emphasise or []) if v}
        for section, tiers in self.skills.items():
            entries = []
            for tier in SURFACEABLE_TIERS:
                entries.extend(tiers.get(tier) or [])
            if not entries:
                continue
            if wanted:
                def hit(entry: str) -> int:
                    # Substring both ways so "AWS (SageMaker, Bedrock, S3)"
                    # matches a posting asking for "sagemaker", and "RAG"
                    # matches "rag pipelines". Word-boundary matching would
                    # miss the parenthesised compounds this file is full of.
                    token = _key(entry)
                    return 0 if any(w in token or token in w
                                    for w in wanted) else 1
                # `sorted` is stable, so within the matched and unmatched
                # groups the hand-written order survives untouched.
                entries = sorted(entries, key=hit)
            out[section] = entries
        return out

    def exposure_skills(self) -> set[str]:
        """Everything that must never appear. Used by the tests and the audit."""
        out = set()
        for tiers in self.skills.values():
            out.update(tiers.get("exposure") or [])
        return out


def _require(condition, message):
    if not condition:
        raise BankError(message)


@lru_cache(maxsize=4)
def load_bank(path: str | Path = DEFAULT_PATH) -> Bank:
    import yaml

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    _require(isinstance(raw, dict), f"{path} did not parse to a mapping")

    # The header block -- name, contact, degree, and each role's company,
    # title and dates -- is generated from master_profile.md §12 and overlaid
    # here. Nothing is written back into bullets.yaml, which stays entirely
    # hand-edited; the generated file is separate so that regenerating can
    # never touch a human's bullet text. Absent file, no overlay: a fresh
    # checkout has no gitignored profile to generate from, and the bank's own
    # hand-written header still renders.
    from . import header as header_mod
    try:
        raw = header_mod.merge_into(raw, header_mod.load())
    except Exception as exc:                       # pragma: no cover - guard
        log.warning("generated header not applied (%s); using the bank's own "
                    "values", exc)
    for key in ("meta", "education", "roles", "bullets", "skills"):
        _require(key in raw, f"{path} is missing the {key!r} section")

    roles = {}
    for entry in raw["roles"]:
        _require("id" in entry, f"a role in {path} has no id")
        _require(entry["id"] not in roles,
                 f"duplicate role id {entry['id']!r} in {path}")
        roles[entry["id"]] = entry

    bullets = {}
    for entry in raw["bullets"]:
        bid = entry.get("id")
        _require(bid, f"a bullet in {path} has no id")
        _require(bid not in bullets, f"duplicate bullet id {bid!r} in {path}")
        _require(entry.get("text", "").strip(), f"bullet {bid!r} has no text")
        _require(entry.get("role") in roles,
                 f"bullet {bid!r} names unknown role {entry.get('role')!r}")
        _require(entry.get("source"), f"bullet {bid!r} has no source; every "
                                      f"bullet must trace to a document")

        archetypes = tuple(entry.get("archetypes") or ())
        _require(archetypes, f"bullet {bid!r} is tagged for no archetype and "
                             f"could never be selected")
        bad = set(archetypes) - VALID_ARCHETYPES
        _require(not bad, f"bullet {bid!r} names unknown archetype(s) "
                          f"{sorted(bad)}; valid: {sorted(VALID_ARCHETYPES)}")

        group = entry.get("group")
        if group is not None:
            known = {g["id"] for g in (roles[entry["role"]].get("groups") or [])}
            _require(group in known,
                     f"bullet {bid!r} names group {group!r}, which role "
                     f"{entry['role']!r} does not define. Known: "
                     f"{sorted(known) or 'none'}")

        variants = entry.get("variants") or {}
        bullets[bid] = Bullet(
            id=bid, role=entry["role"], text=entry["text"],
            source=entry["source"], archetypes=archetypes,
            skills=tuple(entry.get("skills") or ()),
            metrics=bool(entry.get("metrics")),
            rank=int(entry.get("rank", 5)), variants=variants, group=group)

    skills = {}
    for section, tiers in (raw["skills"] or {}).items():
        _require(isinstance(tiers, dict),
                 f"skills section {section!r} is not a mapping of tiers")
        unknown = set(tiers) - {"core", "working", "exposure"}
        _require(not unknown, f"skills section {section!r} has unknown "
                              f"tier(s) {sorted(unknown)}")
        skills[section] = {k: list(v or []) for k, v in tiers.items()}

    bank = Bank(meta=raw["meta"], education=raw["education"], roles=roles,
                bullets=bullets, skills=skills)
    log.info("loaded %d bullets across %d roles from %s",
             len(bullets), len(roles), path)
    return bank


def match_score(bullet: Bullet, vocabulary) -> int:
    """How many of a posting's skill terms this bullet already evidences.

    Used to PREFER bullets (§3.1a step 2). It cannot alter a bullet, add a
    skill, or invent anything — the worst it can do is order honest evidence
    differently.
    """
    wanted = {_key(v) for v in vocabulary if v and str(v).strip()}
    return sum(1 for s in bullet.skills if _key(s) in wanted)


# ---------------------------------------------------------------------------
# A posting's vocabulary, read deterministically (Phase 7A slice 5)
# ---------------------------------------------------------------------------
#
# Under MCP a session names `posting_skills` after reading the posting. The
# apply session renders unattended, so the vocabulary is read from the text
# instead: the bank's own `skills` tags that the posting names. Only the
# bank's tags, because `match_score` compares against nothing else - a term
# no bullet carries could never promote one.
#
# Tags the bank uses as ordinary English ("testing", "ownership",
# "performance") appear in nearly every posting, so a match on them says
# nothing about what THIS posting asks for. They are left out of the
# vocabulary entirely - neither promoting a bullet nor flagging one - so a
# drop flag means a specific technology or method the posting names.
# Add to this set, not to a posting, when a tag turns out to be noise.
GENERIC_TAGS = frozenset({
    "backend", "client_delivery", "end_to_end", "evaluation", "founding",
    "infrastructure", "integration", "ownership", "performance", "pipelines",
    "prototyping", "regression", "routing", "scoping", "scoring",
    "stakeholder_communication", "startup", "testing", "tracking",
    "transactions",
    # Nearly every bullet in the bank is Python work, so a dropped bullet
    # tagged python says nothing; on the first live session (2026-11-06) it
    # flagged brain-survey and capstone-quality on a posting naming Python once.
    "python",
})

# Other spellings postings use for a tag. A tag's own forms - its parts
# joined by a space, a hyphen or nothing, with an optional plural - are
# matched without listing them here.
TAG_ALIASES = {
    "aws_bedrock": ("bedrock",),
    "aws_sagemaker": ("sagemaker",),
    "fine_tuning": ("fine-tune", "fine tune", "finetune"),
    "huggingface": ("hugging face",),
    "kubernetes": ("k8s",),
    "llm": ("large language model",),
    "mcp": ("model context protocol",),
    "postgresql": ("postgres",),
    "rag": ("retrieval augmented generation", "retrieval-augmented generation"),
    "rest_api": ("restful",),
    "scikit_learn": ("sklearn",),
    "tool_use": ("tool calling", "function calling"),
    "vector_database": ("vector db", "vector store"),
}

_EDGE = r"[a-z0-9+#]"


def _tag_pattern(tag: str):
    import re
    parts = [re.escape(p) for p in str(tag).lower().split("_") if p]
    forms = [r"[\s\-]?".join(parts)]
    forms += [re.escape(a.lower()) for a in TAG_ALIASES.get(tag, ())]
    return re.compile(rf"(?<!{_EDGE})(?:{'|'.join(forms)})(?:e?s)?(?!{_EDGE})")


def _plain_text(text: str | None) -> str:
    """Posting text with markup removed: some boards store HTML."""
    import html
    import re
    text = re.sub(r"<[^>]+>", " ", text or "")
    # \s is Unicode-aware, so &nbsp; (U+00A0) folds to a space here and a
    # spaced alias like "retrieval augmented generation" still matches.
    return re.sub(r"\s+", " ", html.unescape(text)).lower()


def posting_vocabulary(bank: Bank, text: str | None) -> dict[str, int]:
    """The bank's specific skill tags this posting names, with how often.

    Most-named first. Deterministic and free: a word-boundary match per tag,
    over the posting's own text, which stays data - nothing here acts on it.
    """
    plain = _plain_text(text)
    tags = sorted({s for b in bank.bullets.values() for s in b.skills}
                  - GENERIC_TAGS)
    counts = {}
    for tag in tags:
        n = len(_tag_pattern(tag).findall(plain))
        if n:
            counts[tag] = n
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def flag_dropped(bank: Bank, dropped, vocabulary: dict[str, int]) -> list[dict]:
    """Dropped bullets carrying a tag the posting names - CLAUDE.md's
    "a concrete defect". Most-named first; an unknown id is ignored, since
    the drop list came from this bank's own render."""
    flags = []
    for bid in dropped or []:
        bullet = bank.bullets.get(bid)
        if bullet is None:
            continue
        hits = sorted({s for s in bullet.skills if s in vocabulary},
                      key=lambda s: (-vocabulary[s], s))
        if hits:
            flags.append({"bullet": bid, "skills": hits,
                          "mentions": {s: vocabulary[s] for s in hits}})
    flags.sort(key=lambda f: -sum(f["mentions"].values()))
    return flags

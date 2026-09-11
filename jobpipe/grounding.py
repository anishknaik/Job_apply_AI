"""Grounding check for generated letters (Phase 2 spec §2.2, §9.9).

The resume cannot fabricate — its text is selected from a fixed bank. A cover
letter is written, so this is the only place in the system where an untrue
sentence can reach an employer. This module is the check on that.

Three rules from §2.2, in order of importance:

  * **Surface and stop. Never auto-correct.** A silently repaired letter looks
    identical to a letter that was right the first time, so the failure mode
    disappears from view and the measurement in §7 becomes meaningless. An
    unsupported claim blocks the draft and is shown to the human verbatim.
  * Every factual claim is extracted and judged individually, not the letter
    as a whole. "Does this letter seem accurate" is a question a model will
    almost always answer yes to.
  * Claims are routed to the evidence that can actually settle them. A claim
    about the candidate is checked against the profile and bank; a claim about
    the employer is checked against the posting. Checking "Abridge maps
    summaries to ground truth via Linked Evidence" against a personal profile
    would flag a true sentence, and checking it against nothing would let an
    invented product name through.

Runs on the cheap model (§5). It is a second pass over ~350 words, so it is
the least expensive call in the phase and the one least worth skipping.
"""
from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field

from .bullets import Bank
from .providers import ScoringProvider, default_grounding_model

log = logging.getLogger(__name__)

VERDICTS = ("supported", "unsupported", "partial")
SUBJECTS = ("candidate", "employer")

GROUNDING_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {
                        "type": "string",
                        "description": "The factual assertion, quoted or "
                                       "closely paraphrased from the letter.",
                    },
                    "about": {
                        "type": "string",
                        "enum": list(SUBJECTS),
                        "description": "candidate = a claim about what he has "
                                       "done, built, measured or knows. "
                                       "employer = a claim about the company, "
                                       "its product or its problems.",
                    },
                    "supported": {
                        "type": "string",
                        "enum": list(VERDICTS),
                        "description": "supported = the evidence states this. "
                                       "partial = the evidence says something "
                                       "weaker, narrower, or adjacent. "
                                       "unsupported = the evidence does not "
                                       "say it at all.",
                    },
                    "source": {
                        "type": "string",
                        "description": "The sentence or bullet id the claim "
                                       "rests on. Empty when unsupported.",
                    },
                },
                "required": ["claim", "about", "supported", "source"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claims"],
    "additionalProperties": False,
}

# What is under test, in the words each part of the request uses for it. The
# system prompt, the `claim` field and the section heading have to agree. A
# prompt about "a cover letter", a field saying "from the letter" and a
# section headed APPLICATION ANSWER left the checker unsure which text it was
# checking: on LangChain's form answer it took 38 of 45 claims from the job
# posting's requirement list and judged them as the candidate's (2026-09-10).
# The letter's wording is the original, and its request stays byte-identical.
KINDS = {
    "letter": {"what": "a cover letter", "a_noun": "A letter",
               "noun": "letter", "heading": "THE LETTER TO CHECK"},
    "answer": {"what": "an answer written for a job application form",
               "a_noun": "An answer", "noun": "answer",
               "heading": "THE APPLICATION ANSWER TO CHECK"},
}

# Only an answer carries this. It sits beside a posting several times its own
# length, and the posting's requirement list reads like claims.
_ANSWER_SCOPE = (
    f"Claims come ONLY from the section headed "
    f"'=== {KINDS['answer']['heading']} ==='. The job posting is evidence for "
    f"employer claims and nothing else: a requirement it lists is what the "
    f"employer wants, not something the candidate claimed, so never extract "
    f"a claim from it.")


def _kind(kind: str) -> dict:
    try:
        return KINDS[kind]
    except KeyError:
        raise ValueError(f"unknown grounding kind {kind!r}; expected one of "
                         f"{sorted(KINDS)}") from None


def schema_for(kind: str = "letter") -> dict:
    """The response schema for `kind`. A letter gets GROUNDING_SCHEMA itself.

    An answer's `claim` field carries the scope rule, because the field the
    model fills is where a rule has to be for it to hold — HANDOFF §5 found
    a rule stated in prose and missing at the point of use four times.
    """
    k = _kind(kind)
    if kind == "letter":
        return GROUNDING_SCHEMA
    schema = copy.deepcopy(GROUNDING_SCHEMA)
    claim = schema["properties"]["claims"]["items"]["properties"]["claim"]
    claim["description"] = (f"The factual assertion, quoted or closely "
                            f"paraphrased from the {k['noun']}. "
                            f"{_ANSWER_SCOPE}")
    return schema


@dataclass
class GroundingResult:
    claims: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""

    @property
    def problems(self) -> list[dict]:
        """Everything not fully supported. These block the draft."""
        return [c for c in self.claims
                if c.get("supported") in ("unsupported", "partial")]

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def accuracy(self) -> float:
        """§7's metric: share of claims marked supported. Target 100%."""
        if not self.claims:
            return 0.0
        supported = sum(1 for c in self.claims
                        if c.get("supported") == "supported")
        return supported / len(self.claims)

    def report(self) -> str:
        if self.ok:
            return (f"grounding OK — {len(self.claims)} claim(s) all "
                    f"supported")
        lines = [f"grounding FAILED — {len(self.problems)} of "
                 f"{len(self.claims)} claim(s) not supported:"]
        for problem in self.problems:
            lines.append(f"  [{problem.get('supported')}] "
                         f"{problem.get('claim')}")
            if problem.get("source"):
                lines.append(f"      nearest evidence: {problem['source']}")
        return "\n".join(lines)


def build_system_prompt(bank: Bank, profile: str,
                        kind: str = "letter") -> str:
    """Cacheable prefix: the adversarial framing plus all candidate evidence.

    `kind` names what is under test and must match the `kind` given to
    `check`. A letter's prompt is byte-identical to before it existed.
    """
    k = _kind(kind)
    lines = [
        f"You are checking {k['what']} for claims its evidence does not "
        "support. You are not editing it and not judging whether it is good.",
        "",
        "Assume something is wrong until you have found the sentence that "
        f"supports it. {k['a_noun']} that reads plausibly is the normal "
        "case for a "
        "fabricated claim — plausibility is what generation optimises for. "
        "Your job is the part that plausibility does not cover.",
        "",
        "=== WHAT TO EXTRACT ===",
        "",
        "Every factual assertion, separately. A sentence containing three "
        "facts is three claims. Include:",
        "  * anything he did, built, measured, deployed or shipped",
        "  * every number, and what it is a number OF",
        "  * every named technology, employer, client or model",
        "  * statements about where he lives or can work",
        "  * statements about the employer, their product, or their problems",
        "",
        "Do NOT extract: opinions, motivation, interest in the role, or "
        "questions. 'I find evaluation design absorbing' is not a factual "
        "claim. 'I designed a controlled ablation' is.",
        "",
        *([_ANSWER_SCOPE, ""] if kind != "letter" else []),
        "=== HOW TO JUDGE ===",
        "",
        "Set `about` first, because it decides which evidence applies:",
        "",
        "  * `candidate` — check against the MASTER PROFILE and BULLET BANK "
        "below. Nothing else counts. If the profile does not say it, it is "
        "not supported, however reasonable it sounds.",
        "  * `employer` — check against the JOB POSTING supplied with the "
        f"{k['noun']}. A product name, team structure or technical "
        "detail not in "
        "the posting is unsupported even if it is probably true, because "
        "nothing here can confirm it.",
        "",
        "Verdicts:",
        "  * `supported` — the evidence states it. Numbers must match "
        "exactly. 7,210 is not 'over 7,000'.",
        "  * `partial` — the evidence says something weaker, narrower or "
        "adjacent. 'Built infrastructure for the CDC' where the evidence says "
        "'mirrored an existing AWS architecture on Azure' is partial: the "
        "work is real, the framing overstates it.",
        "  * `unsupported` — the evidence does not say it.",
        "",
        "Scale, team size, business impact and seniority are the claims most "
        "often inflated. Check them hardest.",
        "",
        "=== BULLET BANK ===",
        "",
    ]
    for bullet in bank.bullets.values():
        lines.append(f"  [{bullet.id}] {bullet.render_text()}")
    lines += ["", "=== MASTER PROFILE ===", "", profile]
    return "\n".join(lines)


def check(provider: ScoringProvider, bank: Bank, letter_text: str,
          posting_text: str, system_prompt: str,
          model: str | None = None,
          kind: str = "letter") -> GroundingResult:
    """Verify one letter or form answer. Never modifies it — §2.2 forbids
    auto-correction.

    `kind` (see KINDS) decides the section heading and the response schema,
    and `system_prompt` must have been built for the same kind. A short answer
    on an application form is checked by the same rules as a letter (§6.1).
    """
    k = _kind(kind)
    # Not the scoring model: this check is short and structured, and on the
    # Vertex backend it runs on 2.5 Flash Lite while scoring runs on Flash.
    model = model or default_grounding_model(provider.name)
    user = (f"=== JOB POSTING (the only evidence for `employer` claims) ===\n"
            f"{posting_text[:10_000]}\n\n"
            f"=== {k['heading']} ===\n{letter_text}")

    response = provider.score(model=model, system_prompt=system_prompt,
                              user_text=user, schema=schema_for(kind))
    if response.refused:
        raise RuntimeError(
            f"grounding check refused by {model}: {response.refusal_reason}")

    payload = json.loads(response.text or "")
    claims = payload.get("claims") or []
    if not claims:
        # An empty result is indistinguishable from a clean letter, and would
        # silently disable the only check on generated prose.
        raise RuntimeError(
            "grounding check extracted no claims from a non-empty letter; "
            "treating as a failure rather than a pass")

    return GroundingResult(claims=claims, model=model,
                           usage={"input": response.input_tokens,
                                  "output": response.output_tokens,
                                  "cached": response.cached_tokens})

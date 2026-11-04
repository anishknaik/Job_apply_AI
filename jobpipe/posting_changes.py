"""What an employer's edit to a live posting changed, and whether it matters.

Posting text used to be frozen at first sight (HANDOFF §4): LangChain moved a
role from "3+ years" to "1-3 years" and every score, `get_posting` and the
grounding evidence kept reading the old version. A live check on 2026-11-04
found 30-40% of stored Anthropic, OpenAI and Scale AI postings had since been
edited - titles renamed, pay ranges filled in, "8+ years" lowered to "6+" -
and nearly every Cursor posting carrying new company boilerplate.

Anish's call, 2026-11-04: keep one version, the current one. Any change
updates the stored posting; a change to the years asked for or to the skills
named also sends it back to be scored. A boilerplate rewrite changes neither,
so it costs nothing.

Deterministic on purpose. Deciding "did the requirements change" with a model
would bill a call for every edit of every posting on every board, to save a
scoring call on the few that matter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .filters import minimum_years_required

# The skills vocabulary a rescore keys on. Deliberately concrete: tools,
# languages, frameworks and named techniques that a scorer weighs in
# `skills_match`. Soft skills and generic words ("communication", "data")
# are left out, because a rewritten paragraph shuffles those constantly and
# each false trigger is a paid rescore.
#
# Words that are also plain English (go, rest, spring, rails, agents, r)
# are left out or spelled in their unambiguous form (golang, rest api,
# spring boot), since a rewritten paragraph trips them.
#
# Matched case-insensitively on word boundaries. Terms with punctuation
# (c++, c#, node.js, .net) are matched literally.
SKILL_TERMS: tuple[str, ...] = (
    # languages
    "python", "java", "javascript", "typescript", "golang", "rust",
    "c++", "c#", "scala", "kotlin", "ruby", "php", "sql", "bash",
    "matlab", "elixir", "haskell", "solidity",
    # ML / AI
    "pytorch", "tensorflow", "jax", "keras", "scikit-learn", "sklearn",
    "xgboost", "lightgbm", "hugging face", "huggingface", "transformers",
    "llm", "llms", "rag", "langchain", "langgraph", "llamaindex", "openai",
    "fine-tuning", "fine tuning", "rlhf", "reinforcement learning",
    "computer vision", "nlp", "natural language processing", "deep learning",
    "machine learning", "mlops", "vllm", "triton", "cuda", "onnx",
    "tensorrt", "embeddings", "vector database", "pinecone", "weaviate",
    "prompt engineering", "diffusion models", "recommendation systems",
    "time series", "causal inference", "a/b testing", "bayesian",
    # data
    "spark", "pyspark", "hadoop", "kafka", "flink", "airflow", "dagster",
    "dbt", "snowflake", "databricks", "bigquery", "redshift", "postgresql",
    "postgres", "mysql", "mongodb", "redis", "elasticsearch", "cassandra",
    "dynamodb", "clickhouse", "pandas", "numpy", "polars", "tableau",
    "looker", "power bi", "etl", "data modeling", "data warehouse",
    # infra / platform
    "aws", "gcp", "azure", "kubernetes", "k8s", "docker", "terraform",
    "helm", "ansible", "linux", "ci/cd", "github actions", "jenkins",
    "prometheus", "grafana", "datadog", "microservices",
    "distributed systems", "grpc", "graphql", "rest api", "serverless",
    # web / app
    "react", "next.js", "node.js", "vue.js", "angular", "django",
    "flask", "fastapi", "spring boot", "ruby on rails", ".net", "html",
    "css",
    "ios", "android", "react native",
    # robotics / systems
    "ros2", "slam", "embedded systems", "fpga", "verilog", "opencv",
)

_WORD = r"[a-z0-9]"


def _pattern(term: str) -> re.Pattern:
    # Word-ish boundaries that work for terms ending in punctuation: "c++"
    # must match in "C++," and "go" must not match inside "google".
    return re.compile(rf"(?<!{_WORD}){re.escape(term)}(?!{_WORD})")


_PATTERNS = tuple((term, _pattern(term)) for term in SKILL_TERMS)


def skills_named(text: str | None) -> frozenset[str]:
    """The SKILL_TERMS a posting's text names."""
    lowered = (text or "").lower()
    return frozenset(term for term, pattern in _PATTERNS
                     if pattern.search(lowered))


def _squash(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# The posting fields an upsert compares. `content_hash` and
# `normalized_title` follow `title`, so they are not compared on their own.
COMPARED = ("title", "location", "remote_flag", "description", "url",
            "posted_at")


@dataclass(frozen=True)
class Change:
    """One posting's edit, as seen by the fetch that noticed it."""
    fields: tuple[str, ...]
    years_before: int | None = None
    years_after: int | None = None
    skills_added: frozenset[str] = frozenset()
    skills_removed: frozenset[str] = frozenset()

    @property
    def years_changed(self) -> bool:
        return self.years_before != self.years_after

    @property
    def needs_rescore(self) -> bool:
        """Anish's rule: the years asked for, or the skills named, moved."""
        return self.years_changed or bool(self.skills_added
                                          or self.skills_removed)


def _value(field: str, value):
    if field == "remote_flag":
        return int(bool(value))
    if field in ("title", "description", "location"):
        # Whitespace-only differences are not edits.
        return _squash(value)
    return value or None


def diff(stored, posting) -> Change | None:
    """Compare a stored row with a freshly fetched posting.

    `stored` is anything keyed by column name (an sqlite3.Row); `posting` is
    a normalize.Posting. Returns None when nothing an employer could have
    edited differs.
    """
    fields = tuple(f for f in COMPARED
                   if _value(f, stored[f]) != _value(f, getattr(posting, f)))
    if not fields:
        return None

    years_before = years_after = None
    added = removed = frozenset()
    if "description" in fields or "title" in fields:
        old = f"{stored['title'] or ''}\n{stored['description'] or ''}"
        new = f"{posting.title or ''}\n{posting.description or ''}"
        years_before = minimum_years_required(stored["description"] or "")
        years_after = minimum_years_required(posting.description or "")
        before, after = skills_named(old), skills_named(new)
        added, removed = after - before, before - after

    return Change(fields=fields, years_before=years_before,
                  years_after=years_after, skills_added=added,
                  skills_removed=removed)

"""Calibration: does the model's scoring agree with your own judgement?

Required before scaling past the current company list. The first version of
any scoring prompt clusters everything at 6-8, which looks fine in the sheet
and carries no information. The only way to see that is to score postings you
have already judged yourself and compare.

Workflow:

    python -m jobpipe.cli calibrate --sample 20   # writes a blank sheet
    # ... fill in the my_score column by hand ...
    python -m jobpipe.cli calibrate               # scores them and compares

Re-run monthly, and after any change to the rubric or the master profile.
"""
from __future__ import annotations

import csv
import json
import logging
import random
import re
from pathlib import Path

from . import db, scoring

log = logging.getLogger(__name__)

DEFAULT_PATH = (Path(__file__).resolve().parent.parent
                / "config" / "calibration.csv")

COLUMNS = ["posting_id", "company", "title", "lane", "url",
           "my_fit", "my_apply", "notes"]


# Strata for calibration sampling, most in-lane first. Defined on the title
# alone because anything score-derived would be circular — the set exists to
# measure the scorer.
STRATA = [
    ("early-career", re.compile(
        r"\b(new grad|early career|graduate|university)\b", re.I)),
    ("strong-lane", re.compile(
        r"\b(machine learning|ml|ai|llm|nlp|deep learning|data scientist|"
        r"research engineer|applied ai|mlops|data engineer|"
        r"analytics engineer)\b", re.I)),
    ("generic-swe", re.compile(
        r"\b(software engineer|backend|back-end|full[- ]?stack|platform|"
        r"infrastructure|developer|swe)\b", re.I)),
    ("other", re.compile(r"")),
]

# Roughly even coverage rather than proportional representation. See the
# docstring below for why this is deliberate.
STRATUM_SHARE = {"early-career": 0.20, "strong-lane": 0.30,
                 "generic-swe": 0.30, "other": 0.20}


def _classify(title: str) -> str:
    for name, pattern in STRATA:
        if name == "other" or pattern.search(title or ""):
            return name
    return "other"


def write_template(conn, path: Path, sample: int, stratified: bool = True,
                   exclude_ids: set[int] | None = None) -> int:
    """Emit a blank calibration sheet drawn from Stage A survivors.

    **Stratified by default, and that is a considered choice with a cost.**

    The first calibration set was drawn uniformly and came back with 14 of 20
    scored at or below 3 and nothing in the 8 band. A rubric fitted to that
    set learns to separate a 1 from a 2, when the pipeline's real job is
    separating a 6 from an 8.

    Uniform sampling would repeat it: 46% of the current pool falls in the
    "other" stratum (Silicon Engineer, Red Team, Technical Documentation) and
    only 2% is early-career, so a random 20 would likely contain no
    high-scoring postings at all.

    The cost: MAE measured on a stratified set is NOT an unbiased estimate of
    MAE over the whole pool — it deliberately over-weights the top of the
    distribution. That is the right trade for calibrating a ranking, but it
    means the number answers "can it tell a 6 from an 8", not "how wrong is
    it on average". Pass stratified=False for a uniform draw.
    """
    survivors = conn.execute("""
        SELECT id, company, title, url FROM postings WHERE stage_a_passed = 1
    """).fetchall()
    if not survivors:
        raise RuntimeError("no Stage A survivors to sample; run `filter` first")

    # A holdout set must not overlap the set the rubric was tuned against,
    # or the second run measures fit rather than accuracy.
    if exclude_ids:
        survivors = [r for r in survivors if r["id"] not in exclude_ids]
        if not survivors:
            raise RuntimeError(
                "every survivor is already in the excluded set; nothing left "
                "to sample for a holdout")

    if not stratified:
        rows = random.sample(list(survivors), min(sample, len(survivors)))
    else:
        buckets: dict[str, list] = {name: [] for name, _ in STRATA}
        for row in survivors:
            buckets[_classify(row["title"])].append(row)
        for bucket in buckets.values():
            random.shuffle(bucket)

        rows, shortfall = [], 0
        for name, _ in STRATA:
            want = round(sample * STRATUM_SHARE[name]) + shortfall
            take = buckets[name][:want]
            # A thin stratum (only 3 early-career postings exist) passes its
            # unmet quota along rather than shrinking the sample.
            shortfall = want - len(take)
            rows.extend(take)

        if len(rows) < sample:
            already = {r["id"] for r in rows}
            spare = [r for r in survivors if r["id"] not in already]
            random.shuffle(spare)
            rows.extend(spare[:sample - len(rows)])
        rows = rows[:sample]

        counts: dict[str, int] = {}
        for row in rows:
            key = _classify(row["title"])
            counts[key] = counts.get(key, 0) + 1
        log.info("stratified sample: %s",
                 ", ".join(f"{k}={v}" for k, v in counts.items()))

    # Never destroy hand-scored work. Overwriting a scored sheet loses both
    # the scores and the record of which postings were used — which is how a
    # later "holdout" silently re-drew postings from an earlier set, because
    # --exclude had nothing left to read.
    if path.exists():
        try:
            existing = [r for r in read_rows(path)
                        if (r.get("my_fit") or r.get("my_apply")
                            or r.get("my_score") or "").strip()]
        except Exception:
            existing = []
        if existing:
            raise RuntimeError(
                f"{path} already holds {len(existing)} hand-scored row(s). "
                f"Refusing to overwrite them — write to a new path instead, "
                f"and pass the old one to --exclude so its postings are not "
                f"re-drawn.")

    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: the BOM makes Excel open it as UTF-8 and save it back the
    # same way, instead of silently rewriting the file as cp1252.
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "posting_id": row["id"], "company": row["company"],
                "title": row["title"], "lane": _classify(row["title"]),
                "url": row["url"], "my_fit": "", "my_apply": "", "notes": "",
            })
    return len(rows)


def read_rows(path: Path) -> list[dict]:
    """Read the sheet, tolerating whatever encoding the editor wrote.

    This file is meant to be hand-edited, and Excel and most Windows editors
    save CSV as cp1252 rather than UTF-8. A curly apostrophe in a note is
    enough to make a UTF-8-only read raise UnicodeDecodeError, which is a
    ridiculous way to lose an afternoon of hand-scoring.
    """
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with path.open(newline="", encoding=encoding) as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError as exc:
            last_error = exc
    raise RuntimeError(f"could not decode {path}: {last_error}")


def load_hand_scores(path: Path) -> list[dict]:
    """Read hand scores, accepting both the one- and two-column layouts.

    Older sheets have a single `my_score`. Those were scored on a
    "should I apply" basis, so they map to my_apply, and my_fit is left
    unknown rather than guessed.
    """
    def as_score(value: str):
        """Parse a hand-entered cell, tolerating anything that isn't a number.

        A hand-edited sheet legitimately contains "n/a", "?", "-" or a note
        where a number was expected — a dead link, or a posting the scorer
        could not judge. Those mean "unscored", not "crash the run after an
        afternoon of scoring".
        """
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    rows, unparsed = [], []
    for row in read_rows(path):
        fit = (row.get("my_fit") or "").strip()
        apply_ = (row.get("my_apply") or "").strip()
        legacy = (row.get("my_score") or "").strip()
        if legacy and not (fit or apply_):
            apply_ = legacy

        row["my_fit"] = as_score(fit)
        row["my_apply"] = as_score(apply_)
        if row["my_fit"] is None and row["my_apply"] is None:
            if fit or apply_:
                unparsed.append((row.get("company", "?"),
                                 row.get("title", "?"), fit or apply_))
            continue
        rows.append(row)

    for company, title, raw in unparsed:
        log.info("skipping %s | %s — score %r is not a number",
                 company[:16], title[:40], raw)

    if not rows:
        raise RuntimeError(
            f"{path} has no filled-in scores. Fill my_fit and my_apply (0-10) "
            f"by hand first — that judgement is the whole point of the "
            f"exercise.")
    return rows


def effective_cutoff(conn, limit: int = 20, fallback: int = 6) -> int:
    """The apply_score the sheet's top-N rule actually cuts at, right now.

    The sheet surfaces by *rank*, not by threshold, so a hardcoded 6 grades a
    decision rule the pipeline does not use — it reports misses for roles that
    would in fact have been surfaced, and vice versa. A 19-row calibration
    sample cannot express "top 20 of 209" directly, but it can be scored
    against the cutoff that rule currently produces on the live corpus.

    Recomputed per run, because it moves as the corpus grows.
    """
    row = conn.execute(
        "SELECT apply_score FROM scores WHERE apply_score IS NOT NULL "
        "ORDER BY apply_score DESC LIMIT 1 OFFSET ?", (limit - 1,)).fetchone()
    return fallback if row is None else int(row["apply_score"])


def run(conn, provider, path: Path = DEFAULT_PATH,
        model: str | None = None, threshold: int | None = None) -> int:
    """Score the hand-scored set fresh and print a side-by-side comparison."""
    derived = threshold is None
    if derived:
        threshold = effective_cutoff(conn)
    hand = load_hand_scores(path)
    profile = scoring.load_profile()
    system_prompt = scoring.build_system_prompt(profile)
    prefix_tokens = scoring.check_cacheable(provider, system_prompt, model)
    print(f"provider: {provider.name}  model: {model}")
    print(f"cached prefix: {prefix_tokens} tokens\n")

    stats = scoring.ScoreRun()
    comparisons = []
    skipped = []

    for entry in hand:
        row = db.get_posting(conn, int(entry["posting_id"]))
        if row is None:
            log.warning("posting %s no longer in the database",
                        entry["posting_id"])
            continue
        # Stage A decides what the scorer ever sees. Measuring it on postings
        # the pipeline now discards flatters or penalises it for work it will
        # never do.
        if row["stage_a_passed"] != 1:
            skipped.append((row, entry))
            continue
        score = scoring.score_posting(provider, row, system_prompt, stats,
                                      model=model)
        if score is None:
            continue
        comparisons.append({
            "posting_id": row["id"],
            "company": row["company"], "title": row["title"],
            "my_fit": entry["my_fit"], "my_apply": entry["my_apply"],
            "model_fit": score["overall"],
            "model_apply": score.get("apply_score", score["overall"]),
            "which": score["model"], "reasoning": score.get("reasoning", ""),
            "apply_reason": score.get("apply_reason", ""),
            "skills_match": score.get("skills_match"),
            "seniority_fit": score.get("seniority_fit"),
            "domain_relevance": score.get("domain_relevance"),
            "dealbreakers": score.get("dealbreakers", []),
        })

    if skipped:
        print(f"skipped {len(skipped)} posting(s) that Stage A now discards:")
        for row, entry in skipped:
            print(f"  {row['company'][:16]:16s} | {row['title'][:38]:38s} "
                  f"(you scored {entry['my_score']}) — {row['stage_a_reason']}")
        print()

    if not comparisons:
        print("nothing scored — check the errors above")
        return 1

    def axis(mine_key, model_key, label):
        """Report one axis. Returns its summary dict, or None if unscored."""
        pairs = [(c[mine_key], c[model_key]) for c in comparisons
                 if c[mine_key] is not None]
        if not pairs:
            print(f"{label}: no hand scores supplied, skipping")
            return None

        n = len(pairs)
        deltas = [m - h for h, m in pairs]
        mae = sum(abs(d) for d in deltas) / n
        bias = sum(deltas) / n
        within_1 = sum(1 for d in deltas if abs(d) <= 1)
        model_vals = [m for _, m in pairs]
        mine_vals = [h for h, _ in pairs]

        print()
        print(f"=== {label} (n={n}) ===")
        print(f"  MAE                 {mae:.2f}")
        print(f"  bias (model-you)    {bias:+.2f}")
        print(f"  within 1 point      {within_1}/{n} ({within_1/n:.0%})")
        print(f"  model range/stdev   {min(model_vals)}-{max(model_vals)} "
              f"/ {_stdev(model_vals):.2f}")
        print(f"  your  range/stdev   {min(mine_vals)}-{max(mine_vals)} "
              f"/ {_stdev(mine_vals):.2f}")
        return {"n": n, "mae": round(mae, 3), "bias": round(bias, 3),
                "within_1": within_1, "stdev": round(_stdev(model_vals), 3)}

    fit = axis("my_fit", "model_fit", "FIT  (overall — match on paper)")
    app = axis("my_apply", "model_apply", "APPLY (worth an application)")

    # The sheet surfaces on apply_score, so that is the axis whose threshold
    # behaviour decides whether the pipeline is useful.
    pairs = [(c["my_apply"], c["model_apply"]) for c in comparisons
             if c["my_apply"] is not None]
    tp = sum(1 for h, m in pairs if h >= threshold and m >= threshold)
    fn = sum(1 for h, m in pairs if h >= threshold and m < threshold)
    fp = sum(1 for h, m in pairs if h < threshold and m >= threshold)
    wanted = tp + fn

    print()
    source = ("the live top-20 cutoff" if derived else "--threshold")
    print(f"=== sheet decision, on APPLY at >= {threshold} ({source}) ===")
    print(f"  you would want        {wanted}")
    print(f"  model surfaces        {tp + fp}")
    print(f"  correctly surfaced    {tp}")
    print(f"  MISSED (false neg)    {fn}"
          + ("   <-- invisible and permanent" if fn else ""))
    print(f"  wrongly surfaced      {fp}")
    if wanted:
        print(f"  recall                {tp / wanted:.0%}")
    if tp + fp:
        print(f"  precision             {tp / (tp + fp):.0%}")

    # The whole reason the two columns exist: the model must reproduce the
    # gap, not just the levels.
    gaps = [(c["my_apply"] - c["my_fit"], c["model_apply"] - c["model_fit"])
            for c in comparisons
            if c["my_fit"] is not None and c["my_apply"] is not None]
    if gaps:
        yours = sum(g for g, _ in gaps) / len(gaps)
        theirs = sum(m for _, m in gaps) / len(gaps)
        print()
        print(f"=== apply-minus-fit gap ===")
        print(f"  your mean gap         {yours:+.2f}")
        print(f"  model mean gap        {theirs:+.2f}")
        if theirs < yours - 1:
            print("  The model is not opening the gap enough — it is still "
                  "treating an experience shortfall as a rejection.")
        elif theirs > yours + 1:
            print("  The model opens the gap too readily — it is excusing "
                  "skills gaps, not just years gaps.")

    print()
    if wanted and tp / wanted < 0.7:
        print(f"Recall below 70%: {fn} of {wanted} roles you would apply to "
              f"never reach the sheet. Fix this before worrying about MAE.")
    elif app and app["stdev"] < 1.5:
        print("Apply scores are clustered, which carries no information even "
              "if MAE looks fine.")
    else:
        print("Calibration looks usable. Re-run monthly and after any change "
              "to the rubric or the master profile.")

    print()
    print("largest apply disagreements:")
    scored = [c for c in comparisons if c["my_apply"] is not None]
    for c in sorted(scored, key=lambda c: -abs(c["model_apply"] - c["my_apply"]))[:3]:
        if abs(c["model_apply"] - c["my_apply"]) < 2:
            break
        print(f"  {c['company'][:16]} — {c['title'][:44]}")
        print(f"    you fit={c['my_fit']} apply={c['my_apply']}  |  "
              f"model fit={c['model_fit']} apply={c['model_apply']} "
              f"(sk={c['skills_match']} sen={c['seniority_fit']} "
              f"dom={c['domain_relevance']})")
        print(f"    {(c['apply_reason'] or c['reasoning'])[:180]}")

    _write_result(path, comparisons, {
        "fit": fit, "apply": app, "threshold": threshold,
        "true_pos": tp, "false_neg": fn, "false_pos": fp,
        "recall": (tp / wanted) if wanted else None,
        "precision": (tp / (tp + fp)) if (tp + fp) else None,
        "model": model, "provider": provider.name,
    })
    return 0


def _stdev(values: list[int]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def _write_result(path: Path, comparisons: list[dict], summary: dict) -> None:
    """Persist the run so diagnosing it does not mean paying to re-score.

    Deliberately a sidecar file rather than the `scores` table: a calibration
    run is often made with an experimental rubric, and those scores must not
    leak into the sheet.
    """
    out = path.with_name(path.stem + "_result.json")
    out.write_text(json.dumps({"summary": summary, "postings": comparisons},
                              indent=2), encoding="utf-8")
    print(f"\nfull results written to {out.name}")

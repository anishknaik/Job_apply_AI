"""Command line interface.

    python -m jobpipe.cli init            create the database
    python -m jobpipe.cli fetch           fetch all boards into SQLite
    python -m jobpipe.cli filter          run Stage A over unfiltered postings
    python -m jobpipe.cli audit           inspect what Stage A threw away
    python -m jobpipe.cli stats           current state of the database
    python -m jobpipe.cli targets         validate config/targets.csv

Stage B (score, calibrate) and the Sheets sync are added once the master
profile exists.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import db, filters, pipeline
from .targets import load_targets


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )


def cmd_init(args) -> int:
    conn = db.connect(args.db)
    db.init_schema(conn)
    print(f"schema ready at {args.db}")
    return 0


def cmd_targets(args) -> int:
    targets = load_targets(args.targets)
    by_ats: dict[str, int] = {}
    for target in targets:
        by_ats[target.ats_type] = by_ats.get(target.ats_type, 0) + 1
    print(f"{len(targets)} targets, all valid")
    for ats, count in sorted(by_ats.items()):
        print(f"  {ats:16s} {count}")
    by_tier: dict[int, int] = {}
    for target in targets:
        by_tier[target.tier] = by_tier.get(target.tier, 0) + 1
    for tier, count in sorted(by_tier.items()):
        print(f"  tier {tier}          {count}")
    return 0


def cmd_verify(args) -> int:
    """Probe every board token: HTTP status and row count, no parsing.

    Run this after any edit to targets.csv. A wrong token does not raise —
    Ashby and Greenhouse both return an empty list — so without this the
    company silently never appears and nothing says why.
    """
    from .verify import probe

    targets = load_targets(args.targets)
    print(f"probing {len(targets)} boards\n")
    print(f"{'company':24s} {'ats':12s} {'token':22s} {'status':>7s} {'rows':>6s}")
    print("-" * 84)

    suspicious = []
    for target in targets:
        result = probe(target)
        rows = "-" if result["rows"] is None else str(result["rows"])
        print(f"{target.company[:23]:24s} {target.ats_type:12s} "
              f"{target.board_token[:21]:22s} {str(result['status']):>7s} "
              f"{rows:>6s}  {result['note']}")
        if result["rows"] is None or result["rows"] == 0:
            suspicious.append((target, result))

    print()
    if suspicious:
        print(f"{len(suspicious)} board(s) need attention:")
        for target, result in suspicious:
            print(f"  ! {target.company} ({target.ats_type}/"
                  f"{target.board_token}): {result['note']}")
        return 1

    print(f"all {len(targets)} boards responded with postings")
    return 0


def cmd_fetch(args) -> int:
    conn = db.connect(args.db)
    db.init_schema(conn)
    targets = load_targets(args.targets)
    if args.company:
        wanted = args.company.lower()
        targets = [t for t in targets if wanted in t.company.lower()]
        if not targets:
            print(f"no target matches {args.company!r}")
            return 1

    print(f"fetching {len(targets)} boards\n")
    result = pipeline.fetch_all(conn, targets)

    print(f"\nfetched      {result.fetched}")
    print(f"new          {result.new_postings}")
    print(f"reposts      {result.reposts}")
    print(f"errors       {len(result.errors)}")
    for message in result.errors:
        print(f"  ! {message}")
    return 0


def cmd_filter(args) -> int:
    conn = db.connect(args.db)
    db.init_schema(conn)
    rules = filters.load_rules(args.filters)

    if args.refilter:
        # Re-evaluate everything after a rules change. Duplicate markings are
        # left alone: they are a fact about the data, not a filter verdict,
        # and clearing them would re-admit duplicates for scoring.
        cur = conn.execute("""
            UPDATE postings SET stage_a_passed = NULL, stage_a_reason = NULL
            WHERE stage_a_reason IS NULL OR stage_a_reason NOT LIKE 'duplicate:%'
        """)
        conn.commit()
        print(f"re-filtering {cur.rowcount} postings (duplicates preserved)\n")

    result = pipeline.filter_all(conn, rules)

    print(f"evaluated    {result.evaluated}")
    print(f"passed       {result.passed}")
    if result.evaluated:
        print(f"kill rate    {result.kill_rate:.1%}")
    if result.reasons:
        print("\nkilled by rule:")
        for rule, count in sorted(result.reasons.items(),
                                  key=lambda kv: -kv[1]):
            print(f"  {rule:24s} {count}")
    return 0


def cmd_audit(args) -> int:
    """Inspect Stage A's decisions, so the filters can be tuned honestly.

    Reviewing the discard pile is how you find false negatives — the roles
    thrown away before scoring, which no amount of Stage B can recover.
    """
    conn = db.connect(args.db)
    want = 1 if args.passed else 0
    sql = """
        SELECT company, title, location, stage_a_reason
        FROM postings
        WHERE stage_a_passed = ?
    """
    params: list = [want]
    if args.rule:
        sql += " AND stage_a_reason LIKE ?"
        params.append(f"{args.rule}%")
    if args.company:
        sql += " AND company LIKE ?"
        params.append(f"%{args.company}%")
    sql += " ORDER BY company, title LIMIT ?"
    params.append(args.limit)

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("no matching postings (run `filter` first?)")
        return 0

    for row in rows:
        location = (row["location"] or "-")[:30]
        line = (f"{row['company'][:16]:16s} | {row['title'][:44]:44s} | "
                f"{location:30s}")
        if not args.passed:
            line += f" | {row['stage_a_reason']}"
        print(line)
    print(f"\n({len(rows)} shown)")
    return 0


def _load_env() -> None:
    """Load .env so no key needs to live in the shell.

    Keeping ANTHROPIC_API_KEY in particular out of the interactive
    environment matters: an exported key silently causes Claude Code to bill
    the pay-as-you-go API instead of the Pro subscription.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def _build_provider(name: str | None = None):
    from .providers import ProviderError, get_provider

    _load_env()
    try:
        return get_provider(name)
    except ProviderError as exc:
        raise SystemExit(str(exc))


def _resolve_model(provider, explicit: str | None,
                   escalation: bool = False) -> str:
    """CLI flag beats .env beats the provider's own default.

    A model name from the wrong provider is caught here rather than as a
    confusing 404 mid-run — the failure mode when .env still held Anthropic
    model names after the provider was switched to Gemini.
    """
    from .providers import default_escalation_model, default_model

    env_var = ("JOBPIPE_ESCALATION_MODEL" if escalation
               else "JOBPIPE_SCORING_MODEL")
    fallback = (default_escalation_model(provider.name) if escalation
                else default_model(provider.name))
    model = explicit or os.environ.get(env_var) or fallback

    family = model.split("-")[0].lower()
    expected = {"gemini": "gemini", "anthropic": "claude"}[provider.name]
    if family != expected:
        raise SystemExit(
            f"{env_var}={model!r} is not a {provider.name} model. Either set "
            f"JOBPIPE_PROVIDER to match, or set {env_var} to one of: "
            f"{', '.join(provider.known_models)}")
    return model


def cmd_score(args) -> int:
    from . import scoring

    conn = db.connect(args.db)
    db.init_schema(conn)

    # Raised from 200 (addendum 2 §5). The old ceiling was sized against 36
    # companies; at 142 a single backfill queues ~250 and Phase 2 adds resume
    # generation across the queue on top. Raise it deliberately here rather
    # than discover it mid-batch — the `--rescore` failure, where the
    # guardrail caused the outage it exists to prevent.
    #
    # Still a real ceiling: 600 postings is roughly $1.10, so a runaway fetch
    # or a filters.yaml mistake refuses instead of billing.
    cap = int(os.environ.get("JOBPIPE_MAX_SCORES_PER_RUN", "600"))

    if args.rescore:
        # Scores are derived data, so clearing them is recoverable by paying
        # again — unlike `postings`, which is never deleted from. Needed after
        # any rubric change, since existing rows would otherwise be skipped.
        existing = conn.execute("SELECT COUNT(*) n FROM scores").fetchone()["n"]
        queued = conn.execute(
            "SELECT COUNT(*) n FROM postings WHERE stage_a_passed = 1"
        ).fetchone()["n"]

        # Check the cap BEFORE deleting. Deleting first meant a run that the
        # cap then refused left the database with no scores at all — the
        # guardrail causing the outage it exists to prevent.
        if queued > cap:
            print(f"--rescore would queue {queued} postings, above the {cap} "
                  f"cap. Nothing has been deleted. Raise "
                  f"JOBPIPE_MAX_SCORES_PER_RUN deliberately, or narrow "
                  f"filters.yaml first.")
            return 1
        if not args.yes:
            print(f"--rescore would discard {existing} score(s) and re-run "
                  f"{queued}. Add --yes to confirm the spend.")
            return 1

        conn.execute("DELETE FROM scores")
        conn.commit()
        print(f"cleared {existing} score(s) for a full re-score")

    gate = pipeline.check_scoring_gate(conn, cap=cap, enabled=args.yes)
    if not gate.allowed:
        print(gate.reason)
        if not args.yes:
            print("\nRe-run with --yes once you have reviewed the queue.")
        return 1

    provider = _build_provider(args.provider)
    args.model = _resolve_model(provider, args.model)
    args.escalation_model = _resolve_model(provider, args.escalation_model,
                                           escalation=True)
    profile = scoring.load_profile()
    system_prompt = scoring.build_system_prompt(profile)

    try:
        prefix_tokens = scoring.check_cacheable(provider, system_prompt,
                                                args.model)
    except scoring.ProfileTooShort as exc:
        print(f"refusing to run: {exc}")
        return 1
    print(f"provider: {provider.name}  model: {args.model}")
    print(f"cached prefix: {prefix_tokens} tokens "
          f"(floor {provider.cache_minimum_tokens(args.model)})")

    run = scoring.score_pending(conn, provider, system_prompt, limit=cap,
                                model=args.model,
                                escalation_model=args.escalation_model)

    print(f"\nscored           {run.scored}")
    print(f"escalated        {run.escalated}")
    print(f"failed           {run.failed}")
    print(f"input tokens     {run.input_tokens}")
    print(f"output tokens    {run.output_tokens}")
    print(f"cache read       {run.cache_read_tokens}")
    if run.scored > 1 and run.cache_read_tokens == 0:
        print("\n  WARNING: zero cache reads across multiple calls. The "
              "profile is not being cached — check the prefix is byte-stable "
              "and above the model's floor. Silent, and roughly 10x the "
              "expected bill.")
    for message in run.errors[:10]:
        print(f"  ! {message}")
    return 0


def cmd_calibrate(args) -> int:
    from . import calibrate as calibration
    from pathlib import Path

    conn = db.connect(args.db)
    db.init_schema(conn)
    path = Path(args.path) if args.path else calibration.DEFAULT_PATH

    if args.sample:
        exclude: set[int] = set()
        for other in args.exclude or []:
            other_path = Path(other)
            if not other_path.exists():
                raise SystemExit(f"--exclude file not found: {other_path}")
            exclude |= {int(r["posting_id"])
                        for r in calibration.read_rows(other_path)
                        if (r.get("posting_id") or "").strip()}
        if exclude:
            print(f"excluding {len(exclude)} already-scored posting(s)")

        written = calibration.write_template(conn, path, args.sample,
                                             exclude_ids=exclude)
        print(f"wrote {written} postings to {path}\n"
              f"Fill in the my_score column (0-10) by hand, then run "
              f"`calibrate --path {path}`.")
        return 0

    if not path.exists():
        print(f"{path} not found. Create it first:\n"
              f"  python -m jobpipe.cli calibrate --sample 20")
        return 1

    # Validate the sheet before constructing the client. Building it first
    # meant a blank sheet failed with an API-key error, which points at
    # entirely the wrong problem.
    try:
        hand = calibration.load_hand_scores(path)
    except RuntimeError as exc:
        print(exc)
        return 1
    print(f"{len(hand)} hand-scored posting(s) in {path.name}\n")

    provider = _build_provider(getattr(args, "provider", None))
    return calibration.run(conn, provider, path,
                           model=_resolve_model(provider, None),
                           threshold=args.threshold)


def cmd_sheet(args) -> int:
    """Print exactly what the Google Sheet would contain.

    Same query the sync uses, so what you see here is what lands in the sheet.
    """
    from .targets import tier_thresholds

    conn = db.connect(args.db)
    db.init_schema(conn)
    targets = load_targets(args.targets)

    rows = db.get_sheet_rows(conn, min_score=args.min_score,
                             tier_thresholds=tier_thresholds(targets),
                             limit=args.limit,
                             per_company=args.per_company or None)
    if not rows:
        print("nothing to surface — run `score --yes` first?")
        return 0

    print(f"top {len(rows)} by apply_score (floor {args.min_score})\n")
    print(f"{'apply':>5} {'fit':>4}  {'company':16s} {'role':40s} location")
    print("-" * 96)
    for row in rows:
        print(f"{row['apply_score']:>5} {row['overall']:>4}  "
              f"{row['company'][:15]:16s} {row['title'][:39]:40s} "
              f"{(row['location'] or '-')[:26]}")

    scored = conn.execute("SELECT COUNT(*) n FROM scores").fetchone()["n"]
    print(f"\n{len(rows)} shown of {scored} scored")
    if args.verbose:
        print()
        for row in rows:
            print(f"  {row['company']} — {row['title']}")
            print(f"    {row['url']}")
            print(f"    {(row['reasoning'] or '')[:200]}")
            if row["apply_reason"]:
                print(f"    apply: {row['apply_reason'][:180]}")
            print()
    return 0


def cmd_sync(args) -> int:
    """Push the ranked shortlist to the Google Sheet."""
    from . import sheets
    from .targets import tier_thresholds

    _load_env()
    conn = db.connect(args.db)
    db.init_schema(conn)
    targets = load_targets(args.targets)

    rows = db.get_sheet_rows(conn, min_score=args.min_score,
                             tier_thresholds=tier_thresholds(targets),
                             limit=args.limit,
                             per_company=args.per_company or None)
    if not rows:
        print("nothing to sync — run `score --yes` first?")
        return 0

    def mirror_status(rows_by_url: dict) -> None:
        """Copy the sheet's status column into the DB (addendum 4.3).

        Strictly one-way. The sheet remains authoritative for status and the
        pipeline never writes that column; this only reads it so advance rates
        have transitions to count. Reading for analytics is not the same as
        letting the sheet drive the pipeline, which the spec forbids.
        """
        from . import states

        known = db.current_statuses(conn)
        id_by_url = {r["url"]: r["id"] for r in conn.execute(
            "SELECT id, url FROM postings").fetchall()}
        changed, unknown, backwards = 0, [], []

        for url, raw in rows_by_url.items():
            posting_id = id_by_url.get(url)
            if posting_id is None:
                continue
            try:
                status = states.normalise(raw)
            except states.UnknownStatus:
                unknown.append(raw)
                continue
            previous = known.get(posting_id)
            if previous and states.is_regression(previous, status):
                backwards.append(f"{raw} (was {previous})")
            if db.record_status(conn, posting_id, status, previous):
                changed += 1
        conn.commit()

        if changed:
            print(f"  mirrored {changed} status change(s) into the database")
        if unknown:
            print(f"  ! unrecognised status value(s): "
                  f"{', '.join(sorted(set(unknown))[:5])} — add an alias in "
                  f"config/states.yml or these rows stay uncounted")
        if backwards:
            print(f"  ! status moved backwards: {'; '.join(backwards[:5])} "
                  f"— usually a typo")

    def refresh(urls):
        """Bring rows that have dropped out of the top N up to date.

        They are never deleted — they may carry hand-typed notes — so without
        this their scores would stay frozen at the last rank they held.
        """
        return [sheets.build_row(r) for r in db.get_rows_by_url(conn, urls)]

    try:
        worksheet = sheets.open_worksheet()
        result = sheets.sync(worksheet, [sheets.build_row(r) for r in rows],
                             refresh_by_url=refresh)
        # After the write, so a status typed in this morning is picked up in
        # the same run rather than a day late.
        mirror_status(sheets.read_statuses(worksheet))
        sheets.ensure_status_dropdown(worksheet)
        if not args.no_sort:
            sheets.sort_by_score(worksheet)
        # Last, so the colours are derived from the rows in their final
        # positions rather than from where they were before the sort.
        coloured = sheets.color_by_tier(
            worksheet, {t.company: t.tier for t in targets})
    except sheets.SheetError as exc:
        print(f"sheet sync failed:\n  {exc}")
        return 1

    total = (result.appended + result.updated + result.refreshed
             + result.unchanged)
    print(f"synced {total} row(s) ({len(rows)} in the top {args.limit}): "
          f"{result.appended} added, {result.updated} updated, "
          f"{result.unchanged} unchanged")
    if result.refreshed:
        print(f"  refreshed {result.refreshed} row(s) that have dropped out "
              f"of the top {args.limit} — their scores were stale")
    if coloured:
        print(f"  coloured {coloured} row(s) by tier "
              f"(1 blue, 2 yellow, 3 red)")
    for message in result.errors:
        print(f"  ! {message}")
    return 0


def cmd_tailor(args) -> int:
    """Generate tailored documents for roles you have queued (Phase 2 §1).

    Two stages, split by a human decision:

      --queued          resume for every role at status=queued, then advance
                        each to `drafted` and write `resume_version`.
      --cover-letter N  letter for one posting, verified against the profile.

    Resumes are batched and speculative because they are cheap and reusable.
    Letters are one at a time because they cost ~10x and are worthless until
    you have actually decided to apply.
    """
    from . import pipeline_tailor as pt
    from . import sheets
    from .providers import default_prose_model
    from .targets import load_targets

    _load_env()
    conn = db.connect(args.db)
    db.init_schema(conn)

    try:
        bank = pt.load_bank()
    except Exception as exc:  # noqa: BLE001 - the bank is hand-edited
        print(f"bullet bank is unusable, refusing to run:\n  {exc}")
        return 1

    provider = _build_provider(args.provider)
    run = pt.TailorRun()
    root = Path(args.out) if args.out else pt.OUTPUT_ROOT

    # --- one letter, on demand ------------------------------------------
    if args.cover_letter:
        row = db.get_posting(conn, int(args.cover_letter))
        if row is None:
            print(f"no posting with id {args.cover_letter}")
            return 1
        if not args.yes:
            print(f"would generate a cover letter for {row['company']} — "
                  f"{row['title']} on {default_prose_model(provider.name)}. "
                  f"Add --yes to confirm the spend.")
            return 1

        prompts = pt.build_prompts(bank)
        current = db.current_document(conn, row["id"], "resume")
        selected = None
        if current and current["bullet_ids"]:
            import json as _json
            try:
                selected = bank.select(_json.loads(current["bullet_ids"]))
            except Exception:  # noqa: BLE001 - fall back to no hint
                selected = None

        out = pt.generate_cover_letter(
            provider, bank, row, prompts["letter"], prompts["grounding"],
            conn, default_prose_model(provider.name), root=root,
            selected=selected)
        conn.commit()

        letter, check = out["letter"], out["grounding"]
        print(f"{row['company']} — {row['title']}")
        print(f"  {letter['words']} words, {letter['attempts']} attempt(s)")
        if letter["banned_used"]:
            print(f"  ! banned phrase(s) survived: "
                  f"{', '.join(letter['banned_used'])}")
        print(f"  {check.report()}")
        print(f"  -> {out['path']}")
        # Printed at the moment of the mistake rather than left to the docs.
        # The first real edit renamed this file instead of copying it, which
        # destroyed the baseline `measure` compares against — unrecoverably,
        # since prose regenerates at 0.53 distance from itself.
        sent = Path(out["path"]).parent / "cover_letter.sent.md"
        print(f"\n  To edit: COPY first, then edit the copy.")
        print(f"    copy \"{out['path']}\" \"{sent}\"")
        print("  Renaming or editing in place destroys the edit-distance "
              "baseline and cannot be undone.")
        if not check.ok:
            print("\n  The draft is on disk but NOT ready to send. Fix the "
                  "claims above by hand, or correct the master profile if "
                  "the claim is true and simply unrecorded.")
            return 1
        return 0

    # --- the queued batch -------------------------------------------------
    tiers = {t.company: t.tier for t in load_targets(args.targets)}
    queue = db.postings_with_status(conn, "queued", tiers=tiers,
                                    limit=args.limit)
    if not queue:
        print("nothing queued.\n\n"
              "Set a row's `status` to Queued in the sheet, run `sync` to "
              "mirror it, then run this again.")
        return 0

    cap = int(os.environ.get("JOBPIPE_MAX_DOCS_PER_RUN", "40"))
    if len(queue) > cap:
        print(f"{len(queue)} queued, above the {cap} document cap. Nothing "
              f"generated. Raise JOBPIPE_MAX_DOCS_PER_RUN deliberately, or "
              f"use --limit.")
        return 1

    print(f"{len(queue)} queued, in tier then apply_score order\n")
    if not args.yes:
        for row in queue:
            print(f"  tier {tiers.get(row['company'], '?')}  "
                  f"apply {row['apply_score']}  {row['company']} — "
                  f"{row['title'][:52]}")
        print(f"\nWould generate {len(queue)} resume(s). Add --yes to run.")
        return 0

    prompts = pt.build_prompts(bank)
    worksheet = None
    try:
        worksheet = sheets.open_worksheet()
    except sheets.SheetError as exc:
        print(f"  ! sheet unavailable, will generate but not advance status:"
              f"\n    {exc}\n")

    for row in queue:
        label = f"{row['company']} — {row['title'][:48]}"
        try:
            out = pt.generate_resume(provider, bank, row, prompts["selection"],
                                     conn, root=root)
        except Exception as exc:  # noqa: BLE001 - one role is not the batch
            run.failed += 1
            run.errors.append(f"{label}: {type(exc).__name__}: {exc}")
            print(f"  ! {label}: {type(exc).__name__}: {exc}")
            continue

        run.generated += 1
        run.add_usage(out["selection"]["usage"])
        rendered = out["rendered"]
        print(f"  {label}")
        print(f"      {out['selection']['variant']}, "
              f"{len(rendered.bullet_ids)} bullets at {rendered.font_pt}pt, "
              f"parse {'OK' if rendered.parse['ok'] else 'FAILED'}")
        # Surfaced rather than buried in the log. What was cut to fit one page
        # is the most concrete review signal available: if the posting asked
        # for something and its bullet is on this list, that is a specific
        # defect, where "the resume looks about right" is an impression.
        if rendered.dropped:
            print(f"      dropped to fit: {', '.join(rendered.dropped)}")

        # Status is advanced only after the document exists on disk. The
        # reverse order would mark a role `drafted` that has no draft.
        if worksheet is not None:
            advanced = sheets.set_cells(
                worksheet, row["url"],
                {"status": "Drafted", "resume_version": out["version"]},
                expect={"status": "Queued"})
            if advanced:
                db.record_status(conn, row["id"], "drafted",
                                 previous="queued", source="tailor")
            else:
                run.skipped += 1
                print("      status not advanced — the sheet no longer says "
                      "Queued (edited since?)")
        conn.commit()

    print(f"\ngenerated   {run.generated}")
    print(f"failed      {run.failed}")
    if run.skipped:
        print(f"not advanced {run.skipped}")
    print(f"tokens      in={run.usage['input']} out={run.usage['output']} "
          f"cached={run.usage['cached']}")
    print(f"\nDrafts are in {root}. Review, then set status to Applying and "
          f"run `tailor --cover-letter <id> --yes` for the ones you send.")
    return 1 if run.failed else 0


def cmd_ground(args) -> int:
    """Re-run the grounding check on an existing draft and record the verdict.

    Separate from generation for two reasons, both of which bite right now.

    The 52 documents already in the database predate the grounding columns, so
    every one carries a NULL verdict and `copy_for_editing` refuses all of
    them. An absent check is not a pass, so the fix is to run the check rather
    than waive it.

    And a verdict goes stale. Grounding is judged against `bullets.yaml` and
    `master_profile.md`; when the profile changes - as it did in Phase 5 slice
    0 - every stored verdict describes a comparison against a profile that no
    longer exists.

    Never rewrites the letter. §2.2 forbids auto-correction: an unsupported
    claim is fixed by hand, or by correcting the profile when the claim is
    true and merely unrecorded.
    """
    from . import grounding
    from . import pipeline_tailor as pt
    from .providers import default_model

    _load_env()
    conn = db.connect(args.db)
    db.init_schema(conn)

    row = db.get_posting(conn, int(args.posting_id))
    if row is None:
        print(f"no posting with id {args.posting_id}")
        return 1

    doc = db.current_document(conn, row["id"], "cover_letter")
    if doc is None:
        print(f"{row['company']} - {row['title']} has no cover letter.\n"
              f"  python -m jobpipe.cli tailor --cover-letter {row['id']} "
              f"--yes")
        return 1

    # The database first, the file only as a fallback - a row cannot be
    # renamed, and the file is the one a human works on.
    text = doc["content"]
    if not text:
        path = Path(doc["generated_path"] or "")
        if not path.name or not path.exists():
            print(f"draft {doc['id']} has neither stored content nor a file "
                  f"at {path}; there is nothing to check")
            return 1
        text = path.read_text(encoding="utf-8")

    try:
        bank = pt.load_bank()
    except Exception as exc:  # noqa: BLE001 - the bank is hand-edited
        print(f"bullet bank is unusable, refusing to run:\n  {exc}")
        return 1

    provider = _build_provider(args.provider)
    if not args.yes:
        print(f"would check draft {doc['id']} ({row['company']} - "
              f"{row['title']}) on {default_model(provider.name)}. "
              f"Add --yes to confirm the spend.")
        return 1

    prompts = pt.build_prompts(bank)
    check = grounding.check(provider, bank, text, row["description"] or "",
                            prompts["grounding"])
    db.record_grounding(conn, doc["id"], check)

    print(f"{row['company']} - {row['title']}")
    print(f"  draft {doc['id']}, revision {doc['revision']}, "
          f"checked by {check.model}")
    print(f"  {check.report()}")
    if check.ok:
        print("\n  Recorded. copy_for_editing will now release this draft.")
        return 0
    print("\n  Recorded as FAILED, and copy_for_editing will refuse it. Fix "
          "the claims above in the draft by hand, or correct the master "
          "profile if a claim is true and simply unrecorded.")
    return 1


def cmd_measure(args) -> int:
    """What you changed in the drafts you actually sent (Phase 2 §7).

    Reads every `cover_letter.sent.md` sitting beside its generated original,
    records the distance, and reports which paragraph slot you rewrite most.
    The aggregate is the headline; the per-slot number is the actionable part.
    """
    from . import measure

    conn = db.connect(args.db)
    db.init_schema(conn)
    measured = measure.collect(conn)

    if not measured:
        print("nothing to measure yet.\n\n"
              "COPY a generated cover_letter.md to cover_letter.sent.md, then "
              "edit the copy and run this again. Copy, do not rename — the "
              "original is the baseline this measures against, and a fresh "
              "generation is not a substitute for it.\n\n"
              "The resume is not measured: it is assembled from the bank and "
              "should be sent as generated.")
        return 0

    print(f"{'company':<14}{'variant':<14}{'words':<12}{'distance'}")
    for entry in measured:
        words = f"{entry['words_generated']}->{entry['words_sent']}"
        print(f"{entry['company'][:13]:<14}"
              f"{(entry['archetype'] or '?')[:13]:<14}{words:<12}"
              f"{entry['distance']:.3f}")

    summary = measure.summarise(measured)
    print(f"\nmean {summary['mean']:.3f}   best {summary['best']:.3f}   "
          f"worst {summary['worst']:.3f}   n={summary['n']}")

    print("\nby paragraph slot — the number that says which instruction is "
          "wrong:")
    for slot, value in summary["by_slot"].items():
        name = (measure.SLOT_NAMES[slot] if slot < len(measure.SLOT_NAMES)
                else f"slot {slot}")
        bar = "#" * min(40, round(value * 100))
        print(f"  {name:<20} {value:.3f}  {bar}")

    if len(summary["by_archetype"]) > 1:
        print("\nby variant:")
        for variant, value in summary["by_archetype"].items():
            print(f"  {variant:<20} {value:.3f}")

    if summary["n"] < 15:
        print(f"\n  {summary['n']} letter(s) measured. §7 expects the trend to "
              f"fall over the first ~15; below that this is a smoke test that "
              f"the plumbing works, not evidence about the prompt.")

    if args.detail:
        print("\n--- paragraph detail ---")
        for entry in measured:
            print(f"\n{entry['company']} — {entry['title'][:50]}")
            for change in entry["paragraphs"]:
                name = (measure.SLOT_NAMES[change.index]
                        if change.index < len(measure.SLOT_NAMES)
                        else f"slot {change.index}")
                print(f"  [{change.verdict:<9}] {name} ({change.distance:.3f})")
                if change.distance > 0:
                    print(f"      was : {change.generated[:150]}")
                    print(f"      sent: {change.sent[:150]}")
    return 0


def cmd_status(args) -> int:
    """Where applications stand, and which ATS actually produces interviews.

    The per-channel table is addendum 4.3. It will read all zeros until the
    status column has been filled in for a while — that is expected, and the
    schema exists now precisely so no migration is needed when it does.
    """
    from . import states

    conn = db.connect(args.db)
    db.init_schema(conn)

    current = db.current_statuses(conn)
    if not current:
        print("no statuses recorded yet.\n\n"
              "Fill in the `status` column in the sheet, then run `sync` — it "
              "mirrors the column into the database on every run. Values are "
              "constrained to a dropdown; see config/states.yml.")
        return 0

    counts: dict[str, int] = {}
    for status in current.values():
        counts[status] = counts.get(status, 0) + 1

    print("current status")
    for state in states.load_states():
        if counts.get(state.id):
            print(f"  {state.label:<14}{counts[state.id]}")

    rows = db.advance_rates(conn, reached=args.reached)
    print(f"\nper-channel advance to {args.reached} (addendum 4.3)")
    print(f"  {'ats':<14}{'tracked':<10}{'applied':<10}{args.reached}")
    for row in rows:
        print(f"  {row['ats_type']:<14}{row['surfaced']:<10}"
              f"{row['applied']:<10}{row['reached']}")

    tracked = sum(r["surfaced"] for r in rows)
    if tracked < 30:
        print(f"\n  Only {tracked} posting(s) have a status. These rates are "
              f"not yet meaningful — treat them as a smoke test that the "
              f"plumbing works, not as evidence about any channel.")
    return 0


def cmd_daily(args) -> int:
    """One unattended run: fetch, filter, score, sync.

    Written for Task Scheduler, which means three things differ from running
    the steps by hand:

      * **No step aborts the run.** A board that 500s, a rubric that rejects a
        posting, a Sheets outage — each is reported and the rest continues.
        The spec's rule that one company failing must never abort a run
        applies to one *stage* failing too.
      * **The spend cap is not optional.** Nobody is watching at 07:00, so a
        day that suddenly queues 400 postings must refuse rather than bill.
        `score` already enforces JOBPIPE_MAX_SCORES_PER_RUN; `daily` inherits
        it and reports when it bites instead of silently truncating.
      * **The exit code means something.** Task Scheduler shows it in the
        history column, so it is the only signal available without opening a
        log: 0 clean, 1 finished with at least one stage degraded.
    """
    started = db.utcnow()
    print(f"=== jobpipe daily {started} ===\n")
    degraded: list[str] = []

    # `runs` was empty and `cli stats`' recent-runs section permanently blank.
    # run_daily.py is the only caller of start_run/finish_run, but daily.ps1
    # invokes `cli daily`, so run_daily.py is orphaned and nothing ever
    # recorded a run. That matters more once the cadence stops being uniform:
    # with scoring moving off the schedule, "when did the last fetch actually
    # run" becomes a question with no other answer.
    run_conn = db.connect(args.db)
    db.init_schema(run_conn)
    run_id = db.start_run(run_conn)

    def stage(name: str, fn) -> None:
        print(f"--- {name} ---")
        try:
            if fn() not in (0, None):
                degraded.append(name)
        except Exception as exc:                          # noqa: BLE001
            # Deliberately broad: an unhandled error in one stage must not
            # cost the run the stages that follow it.
            degraded.append(name)
            print(f"  ! {name} failed: {type(exc).__name__}: {exc}")
        print()

    stage("fetch", lambda: cmd_fetch(args))
    stage("filter", lambda: cmd_filter(args))
    if args.yes:
        stage("score", lambda: cmd_score(args))
    else:
        print("--- score ---\n  skipped: pass --yes to authorise spending\n")
    stage("sync", lambda: cmd_sync(args))

    # Counts come from timestamps, not from the stage functions, which return
    # exit codes. `last_seen_at` is rewritten for every posting the fetch saw,
    # so counting rows touched since `started` measures the run without
    # threading a stats object through four separate commands.
    def since(sql: str) -> int:
        try:
            return run_conn.execute(sql, (started,)).fetchone()[0]
        except Exception:                                 # noqa: BLE001
            return 0

    db.finish_run(
        run_conn, run_id,
        fetched=since("SELECT COUNT(*) FROM postings WHERE last_seen_at >= ?"),
        new_postings=since(
            "SELECT COUNT(*) FROM postings WHERE first_seen_at >= ?"),
        stage_a_pass=since(
            "SELECT COUNT(*) FROM postings WHERE stage_a_passed = 1 "
            "AND last_seen_at >= ?"),
        scored=since("SELECT COUNT(*) FROM scores WHERE scored_at >= ?"),
        errors=", ".join(degraded))
    run_conn.close()

    print(f"=== finished {db.utcnow()} ===")
    if degraded:
        print(f"degraded stage(s): {', '.join(degraded)}")
        return 1
    print("all stages clean")
    return 0


def cmd_histogram(args) -> int:
    """Group every Stage A rejection by the exact term that triggered it.

    The per-rule totals in `filter` say "title_exclude killed 3,146" without
    saying which word did it. This says `'manager'` killed 400 — which is the
    number that tells you whether a rule is earning its place.
    """
    import re as _re

    conn = db.connect(args.db)
    rows = conn.execute("""
        SELECT stage_a_reason, COUNT(*) n
        FROM postings WHERE stage_a_passed = 0 AND stage_a_reason IS NOT NULL
        GROUP BY stage_a_reason
    """).fetchall()

    tally: dict[tuple[str, str], int] = {}
    for row in rows:
        reason = row["stage_a_reason"]
        rule = reason.split(":", 1)[0]
        quoted = _re.search(r"'([^']*)'", reason)
        term = quoted.group(1) if quoted else ""
        if rule in ("max_age_days", "duplicate", "location_allow",
                    "max_years_experience"):
            term = ""          # tails are per-posting, not per-rule
        key = (rule, term[:40])
        tally[key] = tally.get(key, 0) + row["n"]

    total = sum(tally.values())
    print(f"{total} postings rejected by Stage A\n")
    print(f"{'count':>7s}  {'rule':22s} term")
    print("-" * 62)
    for (rule, term), count in sorted(tally.items(), key=lambda kv: -kv[1]):
        if count < args.min_count:
            continue
        print(f"{count:7d}  {rule:22s} {term}")
    return 0


def cmd_stats(args) -> int:
    conn = db.connect(args.db)
    db.init_schema(conn)

    def scalar(sql: str, params=()):
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else 0

    total = scalar("SELECT COUNT(*) FROM postings")
    filtered = scalar("SELECT COUNT(*) FROM postings WHERE stage_a_passed IS NOT NULL")
    passed = scalar("SELECT COUNT(*) FROM postings WHERE stage_a_passed = 1")
    scored = scalar("SELECT COUNT(*) FROM scores")
    unscored = scalar("""
        SELECT COUNT(*) FROM postings p
        LEFT JOIN scores s ON s.posting_id = p.id
        WHERE p.stage_a_passed = 1 AND s.posting_id IS NULL
    """)

    print(f"postings         {total}")
    print(f"  stage A run    {filtered}")
    print(f"  stage A passed {passed}"
          + (f"  ({passed / filtered:.1%} of filtered)" if filtered else ""))
    print(f"  awaiting score {unscored}")
    print(f"scores           {scored}")

    print("\nby company:")
    rows = conn.execute("""
        SELECT company,
               COUNT(*) AS total,
               SUM(CASE WHEN stage_a_passed = 1 THEN 1 ELSE 0 END) AS passed
        FROM postings GROUP BY company ORDER BY total DESC
    """).fetchall()
    for row in rows:
        print(f"  {row['company'][:24]:24s} {row['total']:5d} "
              f"-> {row['passed'] or 0:4d}")

    runs = conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT 5").fetchall()
    if runs:
        print("\nrecent runs:")
        for run in runs:
            print(f"  #{run['id']} {run['started_at']} "
                  f"fetched={run['fetched']} new={run['new_postings']} "
                  f"passed={run['stage_a_pass']} scored={run['scored']} "
                  f"errors={(run['errors'] or '').count(chr(10)) if run['errors'] else 0}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobpipe")
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH))
    parser.add_argument("--targets", default=None)
    parser.add_argument("--filters", default=None)
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("targets").set_defaults(func=cmd_targets)
    sub.add_parser("verify").set_defaults(func=cmd_verify)

    p_sheet = sub.add_parser("sheet",
                             help="preview what the Google Sheet would hold")
    p_sheet.add_argument("--limit", type=int, default=25)
    p_sheet.add_argument("--per-company", type=int, default=3,
                         help="max postings per company in the sheet, applied "
                              "during selection so the list still fills; "
                              "0 disables the cap")
    p_sheet.add_argument("--min-score", type=int, default=4,
                         help="hard floor; nothing below this surfaces")
    p_sheet.set_defaults(func=cmd_sheet)

    p_sync = sub.add_parser("sync", help="push the shortlist to Google Sheets")
    p_sync.add_argument("--limit", type=int, default=25)
    p_sync.add_argument("--per-company", type=int, default=3,
                         help="max postings per company in the sheet, applied "
                              "during selection so the list still fills; "
                              "0 disables the cap")
    p_sync.add_argument("--min-score", type=int, default=4)
    p_sync.add_argument("--no-sort", action="store_true")
    p_sync.set_defaults(func=cmd_sync)

    p_daily = sub.add_parser(
        "daily", help="one unattended run: fetch, filter, score, sync")
    p_daily.add_argument("--yes", action="store_true",
                         help="required before the score stage runs at all; "
                              "without it the run fetches and syncs for free")
    p_daily.add_argument("--limit", type=int, default=25)
    p_daily.add_argument("--per-company", type=int, default=3,
                         help="max postings per company in the sheet, applied "
                              "during selection so the list still fills; "
                              "0 disables the cap")
    p_daily.add_argument("--min-score", type=int, default=4)
    p_daily.add_argument("--no-sort", action="store_true")
    p_daily.add_argument("--company", default=None)
    p_daily.add_argument("--refilter", action="store_true")
    p_daily.add_argument("--provider", default=None)
    p_daily.add_argument("--model", default=None)
    p_daily.add_argument("--escalation-model", default=None)
    p_daily.set_defaults(func=cmd_daily, rescore=False)

    p_tailor = sub.add_parser(
        "tailor", help="generate tailored documents for queued roles")
    p_tailor.add_argument("--queued", action="store_true",
                          help="batch: a resume for every queued role")
    p_tailor.add_argument("--cover-letter", metavar="POSTING_ID",
                          help="generate one cover letter, verified")
    p_tailor.add_argument("--yes", action="store_true",
                          help="required — confirms the spend")
    p_tailor.add_argument("--limit", type=int, default=None,
                          help="process only the first N of the queue")
    p_tailor.add_argument("--out", default=None,
                          help="review folder (default output/applications)")
    p_tailor.add_argument("--provider", default=None)
    p_tailor.set_defaults(func=cmd_tailor)

    p_ground = sub.add_parser(
        "ground", help="re-check an existing draft against the bank and "
                       "profile, and record the verdict")
    p_ground.add_argument("posting_id", metavar="POSTING_ID")
    p_ground.add_argument("--yes", action="store_true",
                          help="required - confirms the spend")
    p_ground.add_argument("--provider", default=None)
    p_ground.set_defaults(func=cmd_ground)

    p_measure = sub.add_parser(
        "measure", help="edit distance between generated and sent letters")
    p_measure.add_argument("--detail", action="store_true",
                           help="show the before/after of each changed "
                                "paragraph")
    p_measure.set_defaults(func=cmd_measure)

    p_status = sub.add_parser(
        "status", help="application pipeline and per-channel advance rates")
    p_status.add_argument("--reached", default="interview",
                          help="the state to measure advancement to")
    p_status.set_defaults(func=cmd_status)

    p_hist = sub.add_parser("histogram")
    p_hist.add_argument("--min-count", type=int, default=1)
    p_hist.set_defaults(func=cmd_histogram)

    p_score = sub.add_parser("score")
    p_score.add_argument("--yes", action="store_true",
                         help="required — confirms you intend to spend money")
    p_score.add_argument("--provider", default=None,
                         help="gemini (default) or anthropic")
    p_score.add_argument("--model", default=None)
    p_score.add_argument("--escalation-model", default=None,
                         help="model used for borderline scores")
    p_score.add_argument("--rescore", action="store_true",
                         help="discard existing scores and score everything "
                              "again; needed after a rubric change")
    p_score.set_defaults(func=cmd_score)

    p_cal = sub.add_parser("calibrate")
    p_cal.add_argument("--sample", type=int,
                       help="write a blank calibration sheet of N postings")
    p_cal.add_argument("--path")
    p_cal.add_argument("--exclude", action="append",
                       help="a scored sheet whose postings must not be "
                            "re-sampled; repeatable. Use when building a "
                            "holdout set.")
    p_cal.add_argument("--threshold", type=int, default=None,
                       help="sheet cutoff used for the recall/precision "
                            "report. Defaults to the apply_score the live "
                            "top-20 rule currently cuts at, since that is the "
                            "decision the sheet actually makes.")
    p_cal.set_defaults(func=cmd_calibrate)
    sub.add_parser("stats").set_defaults(func=cmd_stats)

    p_fetch = sub.add_parser("fetch")
    p_fetch.add_argument("--company", help="fetch one company only")
    p_fetch.set_defaults(func=cmd_fetch)

    p_filter = sub.add_parser("filter")
    p_filter.add_argument("--refilter", action="store_true",
                          help="re-evaluate every posting after a rules change")
    p_filter.set_defaults(func=cmd_filter)

    p_audit = sub.add_parser("audit")
    p_audit.add_argument("--limit", type=int, default=30)
    p_audit.add_argument("--rule", help="only show this rule, e.g. title_exclude")
    p_audit.add_argument("--company", help="filter to one company")
    p_audit.add_argument("--passed", action="store_true",
                         help="show Stage A survivors instead of the discards")
    p_audit.set_defaults(func=cmd_audit)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)

    from .filters import DEFAULT_FILTERS_PATH
    from .targets import DEFAULT_TARGETS_PATH
    args.targets = args.targets or DEFAULT_TARGETS_PATH
    args.filters = args.filters or DEFAULT_FILTERS_PATH

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

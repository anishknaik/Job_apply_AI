"""Phase 6 - the apply runner (PHASE6_PLAN.md).

Fills a Greenhouse or Ashby application form in a browser of its own and then
either submits it or stops for Anish's review - decided by `decide.py`'s rule,
never by a model. The safety core is `resolve.py` (every value has a source or
is a blocker) and `decide.py` (auto-submit only when every condition holds);
both are pure, and both are tested a row at a time.

Nothing here is reachable from the MCP surface or from a web route that
submits: the runner is a local process, `python -m jobpipe.apply`.
"""

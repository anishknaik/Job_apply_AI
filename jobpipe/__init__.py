"""jobpipe — daily job discovery and scoring pipeline.

Phase 1 scope: discovery, deterministic filtering, LLM scoring, Google Sheet.
This is a script, not an agent: deterministic control flow, explicit error
handling, and it must run identically at 6am every day with no one watching.
"""

__version__ = "0.1.0"

"""Stage A rules added 2026-11-05: graduation windows that leave out the
candidate, and a bare "Remote" beside a non-US place. Every phrase below is
from a real posting, with the posting named; the ones that must NOT be
excluded matter as much as the ones that must."""
import dataclasses
from datetime import datetime, timezone

import pytest

from jobpipe import filters

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)
GRAD = "2025-05"


@pytest.mark.parametrize("sentence", [
    "This role is designed for candidates graduating between December 2026 and July 2027.",   # Decagon
    "You will graduate in fall 2026 or spring 2027 with a degree in Computer Science.",        # Databricks
    "A graduation date in Fall 2026 or Spring 2027 with a Bachelor's degree.",                # Scale AI
    "You're graduating in Spring 2027 (CS, software engineering, or a related field).",       # Cursor
    "We welcome candidates who have completed a Bachelor's or Master's degree within the last 6 months, or expect to complete one by Summer 2027.",  # Fireworks
    "To be eligible for this role, you need to graduate in December 2026 and be able to start full time.",  # Notion
    "Current Senior pursuing a Bachelor's degree in Computer Science, with an expected graduation date in 2027.",  # Samsara
    "This role is open to candidates who graduated in summer 2026 or will graduate by the end of 2026.",  # Applied Intuition
])
def test_windows_that_leave_out_a_2025_graduate(sentence):
    assert filters.graduation_window(sentence, GRAD, NOW)


@pytest.mark.parametrize("sentence", [
    # "by": a 2025 graduate satisfies it literally; left to the scorer.
    "We are looking for engineers who are graduating by December 2026.",                       # Nuro
    "Bachelor's degree in Computer Science, Computer Engineering, or a related field, graduating by December 2026.",
    "You will complete your most recent degree program by August 2027 or earlier.",
    # A window that includes him, or an inclusive alternative.
    "Candidates who graduated within the last 2 years are encouraged to apply.",
    "Recent graduates, or students graduating in Spring 2027, are welcome.",
    "Graduated between 2023 and 2025 with a degree in Computer Science.",
    # Not about graduation at all.
    "We moved into our new office in 2026 and grew the team in 2027.",
    "Bachelor's degree in Computer Science or equivalent experience.",
])
def test_phrases_that_must_not_exclude(sentence):
    assert filters.graduation_window(sentence, GRAD, NOW) is None


def test_the_months_window_moves_with_time():
    s = "Degree completed within the last 12 months."
    assert filters.graduation_window(s, GRAD, datetime(2026, 3, 1, tzinfo=timezone.utc)) is None
    assert filters.graduation_window(s, GRAD, NOW)


def test_disabled_without_a_graduation_date():
    assert filters.graduation_window("graduating between December 2026 and July 2027", None, NOW) is None


RULES = dataclasses.replace(filters.load_rules(), graduated=None)


@pytest.mark.parametrize("location,ok", [
    ("london; remote", False),                       # Harvey
    ("remote; london", False),
    ("remote; uk remote", False),                    # Synthesia
    ("belgrade; london; remote; berlin", False),     # Perplexity
    ("remote", True),
    ("remote, global", True),                        # Supabase
    ("san francisco; london; remote", True),         # a US city keeps it
    ("redwood city, ca; remote, eu; london, uk", True),  # Luma: a US state keeps it
    ("remote - us; london", True),
    ("london", False),
])
def test_remote_beside_a_non_us_place(location, ok):
    passed, reason = filters._location_verdict(location, True, RULES)
    assert passed is ok, reason
    if not ok and "remote" in location:
        assert reason.startswith("location_remote_abroad")

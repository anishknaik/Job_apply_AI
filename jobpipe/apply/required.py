"""The page's own required markers, checked against the spec.

Sierra, 2026-11-04: the parser missed five required radio groups, the
plan said every required field was planned, and nothing disagreed. A
parser that misses a widget fails open. This is the independent check:
read which questions the rendered page marks required, and refuse when any
of them is absent from the FormSpec. It shares no code with the parsers'
field discovery, which is the point.

Ashby marks a required question on its title label (a `_required` class);
the label's `for` is the field key, radio groups included. Greenhouse marks
the control with aria-required="true", by id, or - for the resume group -
by aria-labelledby="upload-label-resume". The phone widget's country
selector is aria-required and is part of the phone field, so it is allowed
by name below; nothing else is.
"""
from __future__ import annotations

import re

from . import fill as _fill, spec as _spec

# Page ids that are required but are not fields of their own. Each one is a
# deliberate exception with its reason; the check fails on anything else.
ALLOWED = {
    _spec.GREENHOUSE: {
        "country": "the phone widget's country selector, part of the phone field",
    },
    _spec.ASHBY: {},
}

_ASHBY_REQUIRED = 'label.ashby-application-form-question-title[class*="_required"]'
_GH_REQUIRED = '[aria-required="true"]'


def _gh_id(element) -> str | None:
    ident = element.get_attribute("id")
    if ident:
        return ident
    labelled = element.get_attribute("aria-labelledby") or ""
    for token in labelled.split():
        if token.startswith("upload-label-"):
            return token[len("upload-label-"):]
        if token.endswith("-label"):
            return token[:-len("-label")]
    return None


def markers_on_page(page, ats: str) -> list[str] | None:
    """Ids of the questions the rendered page marks required, or None when
    the form is not on the page or in any frame."""
    frame, root = _fill.form_root(page, ats)
    if frame is None:
        return None
    found: list[str] = []
    if ats == _spec.ASHBY:
        labels = root.locator(_ASHBY_REQUIRED)
        for i in range(labels.count()):
            key = labels.nth(i).get_attribute("for")
            if key:
                found.append(key)
    else:
        controls = root.locator(_GH_REQUIRED)
        for i in range(controls.count()):
            key = _gh_id(controls.nth(i))
            if key:
                found.append(key)
    return list(dict.fromkeys(found))


def missing_from_spec(markers: list[str] | None, form: _spec.FormSpec) -> list[str] | None:
    """Required ids on the page that the spec does not know. None when the
    page was not read; [] when every marker is a field or an allowed
    exception."""
    if markers is None:
        return None
    keys = {f.key for f in form.fields}
    keys |= {re.sub(r"\[\]$", "", k) for k in keys}
    allowed = ALLOWED.get(form.ats, {})
    return [m for m in markers if m not in keys and m not in allowed]


def check(page, form: _spec.FormSpec) -> tuple[list[str] | None, list[str] | None]:
    """(markers on the page, those missing from the spec)."""
    markers = markers_on_page(page, form.ats)
    return markers, missing_from_spec(markers, form)

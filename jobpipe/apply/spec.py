"""FormSpec: what an application form asks, in one shape for both ATSes.

Greenhouse publishes each form's questions as JSON
(`boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}?questions=true`):
`questions`, `location_questions`, `compliance` (EEOC) and
`demographic_questions`, each with labels, types, required flags and option
lists. That is the source here; the DOM is used only to find the widgets.

Ashby does not. PHASE6_PLAN §2 assumed the form rode in the job page's
`window.__appData`; measured 2026-11-04 on LangChain's board, `__appData`
carries the posting, the organisation and the reCAPTCHA site key and nothing
about the form. The form arrives by a POST to
`/api/non-user-graphql?op=ApiJobPostingForApplicationRequest`, which a runner
could call but a fixture cannot capture from here. What IS stable and
capturable is the rendered form: every question is a
`ashby-application-form-field-entry` div carrying `data-field-path` (the
field's key: `_systemfield_name`, `_systemfield_email`, `_systemfield_resume`,
`_systemfield_location`, or a UUID for a custom question), a label with a
`_required_` class marker, and one widget. So the Ashby parser reads the
rendered page. One consequence: an Ashby dropdown's options are not in the
DOM until it is opened, so `Field.options` is None for those and the filler
reads them from the open listbox before matching - exact text only, as for
every select.

Both parsers are pure: text in, dataclasses out, no network, no browser.
"""
from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from typing import Any

GREENHOUSE = "greenhouse"
ASHBY = "ashby"

# Field kinds. `unknown` is a kind on purpose: resolve.py treats it as a
# blocker, so a widget nobody has seen cannot be filled by accident.
TEXT, EMAIL, PHONE, TEXTAREA, FILE, SELECT, MULTISELECT, YESNO, CHECKBOX, \
    HIDDEN, LOCATION, UNKNOWN = (
        "text", "email", "phone", "textarea", "file", "select", "multiselect",
        "yesno", "checkbox", "hidden", "location", "unknown")

# Sections: where a question came from. Self-ID questions are resolved
# differently (exact option text from answers.yaml, blank otherwise), so the
# section is part of the field, not inferred from its label.
APPLICATION, LOCATION_SECTION, EEOC, DEMOGRAPHIC = (
    "application", "location", "eeoc", "demographic")


@dataclass(frozen=True)
class Option:
    label: str
    value: str


@dataclass(frozen=True)
class Field:
    key: str                    # the DOM id / name the filler locates by
    label: str
    kind: str
    required: bool
    section: str = APPLICATION
    options: tuple[Option, ...] | None = None   # None: read from the live widget
    text_key: str | None = None  # Greenhouse: the "enter manually" twin of a file field
    max_length: int | None = None
    description: str = ""

    @property
    def multiline(self) -> bool:
        return self.kind == TEXTAREA


@dataclass(frozen=True)
class FormSpec:
    ats: str
    job_id: str
    company: str
    title: str
    fields: tuple[Field, ...]
    apply_url: str | None = None
    bot_check_site_key: str | None = None   # Ashby publishes its reCAPTCHA key
    notes: tuple[str, ...] = field(default_factory=tuple)

    def field_by_key(self, key: str) -> Field | None:
        return next((f for f in self.fields if f.key == key), None)

    @property
    def required(self) -> tuple[Field, ...]:
        return tuple(f for f in self.fields if f.required)


class SpecError(ValueError):
    """The payload is not the shape the parser was written against."""


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------

_GH_KINDS = {
    "input_text": TEXT,
    "textarea": TEXTAREA,
    "input_file": FILE,
    "multi_value_single_select": SELECT,
    "multi_value_multi_select": MULTISELECT,
    "input_hidden": HIDDEN,
}

# Greenhouse's own names for the identity fields. The rendered page uses the
# same ids (verified on the Alloy page, 2026-11-04), except `location`, which
# renders as `candidate-location`.
_GH_DOM_IDS = {"location": "candidate-location"}


def _gh_options(values: list) -> tuple[Option, ...]:
    return tuple(Option(label=str(v.get("label", "")), value=str(v.get("value", "")))
                 for v in values or [])


def _gh_question(q: dict, section: str) -> Field:
    fields = q.get("fields") or []
    if not fields:
        raise SpecError(f"question {q.get('label')!r} has no fields")
    primary = fields[0]
    name = str(primary.get("name") or "")
    kind = _GH_KINDS.get(str(primary.get("type")), UNKNOWN)
    text_key = None
    if kind == FILE:
        twin = next((f for f in fields[1:] if f.get("type") == "textarea"), None)
        text_key = str(twin["name"]) if twin else None
    key = _GH_DOM_IDS.get(name, name)
    options = _gh_options(primary.get("values")) if kind in (SELECT, MULTISELECT) else None
    if kind == MULTISELECT and key.endswith("[]"):
        key = key[:-2]
    return Field(key=key, label=str(q.get("label") or "").strip(), kind=kind,
                 required=bool(q.get("required")), section=section,
                 options=options, text_key=text_key,
                 description=_strip_tags(q.get("description") or ""))


def _gh_demographic(q: dict) -> Field:
    """`demographic_questions.questions[]` as Greenhouse documents it:
    id, label, required, type, answer_options[{id, label}]. UNVERIFIED
    against a live board - no sampled posting carried one (2026-11-04)."""
    kind = {"multi_value_single_select": SELECT,
            "multi_value_multi_select": MULTISELECT,
            "input_text": TEXT, "textarea": TEXTAREA}.get(str(q.get("type")), UNKNOWN)
    options = tuple(Option(label=str(o.get("label", "")), value=str(o.get("id", "")))
                    for o in q.get("answer_options") or []) or None
    return Field(key=f"demographic_question_{q.get('id')}",
                 label=str(q.get("label") or "").strip(), kind=kind,
                 required=bool(q.get("required")), section=DEMOGRAPHIC,
                 options=options)


def parse_greenhouse(payload: dict[str, Any], company: str | None = None) -> FormSpec:
    """The `?questions=true` job JSON -> FormSpec."""
    if "questions" not in payload:
        raise SpecError("not a Greenhouse job payload with questions=true")
    fields: list[Field] = []
    for q in payload.get("questions") or []:
        fields.append(_gh_question(q, APPLICATION))
    for q in payload.get("location_questions") or []:
        fields.append(_gh_question(q, LOCATION_SECTION))
    for block in payload.get("compliance") or []:
        for q in block.get("questions") or []:
            fields.append(_gh_question(q, EEOC))
    demographic = payload.get("demographic_questions")
    if isinstance(demographic, dict):
        for q in demographic.get("questions") or []:
            fields.append(_gh_demographic(q))
    return FormSpec(
        ats=GREENHOUSE,
        job_id=str(payload.get("id")),
        company=company or str(payload.get("company_name") or ""),
        title=str(payload.get("title") or ""),
        fields=tuple(fields),
        apply_url=payload.get("absolute_url"),
    )


def greenhouse_questions_url(board_token: str, job_id: str | int) -> str:
    return (f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/"
            f"{job_id}?questions=true")


def greenhouse_form_url(board_token: str, job_id: str | int) -> str:
    """Greenhouse's own hosting of the form. A board may redirect it to the
    company's site, where the same form is embedded; fill.py looks for
    `#application-form` in the page and in its frames either way."""
    return f"https://job-boards.greenhouse.io/{board_token}/jobs/{job_id}"


# ---------------------------------------------------------------------------
# Ashby
# ---------------------------------------------------------------------------

_ASHBY_ENTRY = re.compile(
    r'<div\b[^>]*\bashby-application-form-field-entry\b[^>]*>', re.I)
_ASHBY_PATH = re.compile(r'data-field-path="([^"]*)"')
_ASHBY_LABEL = re.compile(
    r'<label\b([^>]*)\bashby-application-form-question-title\b[^>]*>(.*?)</label>',
    re.I | re.S)
_ASHBY_END = re.compile(
    r'<div\b[^>]*\bashby-application-form-field-entry\b|'
    r'<button\b[^>]*\bashby-application-form-submit-button\b|'
    r'<div\b[^>]*\bashby-application-form-section-container\b', re.I)
_TAG = re.compile(r"<[^>]+>")


def _strip_tags(text: str) -> str:
    return " ".join(_html.unescape(_TAG.sub(" ", text)).split())


def _ashby_widget(segment: str, path: str) -> tuple[str, tuple[Option, ...] | None]:
    s = segment
    if re.search(r'<input\b[^>]*type="file"', s, re.I):
        return FILE, None
    if "ashby-application-form-input-yesno" in s:
        return YESNO, (Option("Yes", "yes"), Option("No", "no"))
    if re.search(r"<textarea\b", s, re.I):
        return TEXTAREA, None
    if "ashby-application-form-input-autocomplete" in s:
        return (LOCATION if path == "_systemfield_location" else SELECT), None
    if "ashby-application-form-input-select" in s or 'role="combobox"' in s:
        return SELECT, None
    checks = re.findall(r'<input\b[^>]*type="checkbox"[^>]*>', s, re.I)
    if checks:
        labels = re.findall(r'<label\b[^>]*>(.*?)</label>', s, re.S)
        opts = tuple(Option(_strip_tags(t), _strip_tags(t)) for t in labels[1:])
        return (MULTISELECT if len(checks) > 1 else CHECKBOX), (opts or None)
    m = re.search(r'<input\b[^>]*\btype="([^"]+)"', s, re.I)
    if m:
        kind = {"email": EMAIL, "tel": PHONE, "text": TEXT,
                "hidden": HIDDEN}.get(m.group(1).lower(), UNKNOWN)
        if kind == TEXT and path == "_systemfield_phone":
            kind = PHONE
        return kind, None
    return UNKNOWN, None


def parse_ashby_form(html_text: str, app_data: dict | None = None,
                     apply_url: str | None = None) -> FormSpec:
    """The rendered Ashby application page -> FormSpec.

    `app_data` is `window.__appData` from the same page, for the posting's
    title, the company and the reCAPTCHA site key; the form itself comes
    from the field-entry divs.
    """
    starts = [m.start() for m in _ASHBY_ENTRY.finditer(html_text)]
    if not starts:
        raise SpecError("no ashby-application-form-field-entry in the page")
    fields: list[Field] = []
    for start in starts:
        tag_end = html_text.find(">", start)
        attrs = html_text[start:tag_end]
        path_m = _ASHBY_PATH.search(attrs)
        path = path_m.group(1) if path_m else ""
        nxt = _ASHBY_END.search(html_text, tag_end + 1)
        segment = html_text[tag_end + 1:nxt.start() if nxt else len(html_text)]
        label_m = _ASHBY_LABEL.search(segment)
        label = _strip_tags(label_m.group(2)) if label_m else ""
        required = bool(label_m and "_required" in label_m.group(1))
        if not required:
            required = bool(re.search(r'<input\b[^>]*\brequired\b', segment))
        kind, options = _ashby_widget(segment, path)
        fields.append(Field(key=path or label, label=label, kind=kind,
                            required=required, options=options))

    posting = (app_data or {}).get("posting") or {}
    organisation = (app_data or {}).get("organization") or {}
    return FormSpec(
        ats=ASHBY,
        job_id=str(posting.get("id") or ""),
        company=str(organisation.get("name") or ""),
        title=str(posting.get("title") or "").strip(),
        fields=tuple(fields),
        apply_url=apply_url,
        bot_check_site_key=(app_data or {}).get("recaptchaUniversalPublicSiteKey"),
        notes=tuple(n for n in [
            "Ashby select options are read from the open listbox at fill time"
            if any(f.kind in (SELECT, LOCATION) and f.options is None for f in fields) else "",
        ] if n),
    )


_APPDATA = re.compile(r"window\.__appData\s*=\s*")


def extract_app_data(html_text: str) -> dict | None:
    """`window.__appData` from an Ashby page, or None. The object is a JS
    literal followed by more script, so decode the first object only."""
    import json
    m = _APPDATA.search(html_text)
    if not m:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(html_text[m.end():].lstrip())
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def ashby_application_url(job_url: str) -> str:
    """`jobs.ashbyhq.com/{org}/{id}` -> the application page. The board JSON
    also carries `applyUrl`, which is this."""
    return job_url.rstrip("/") + ("" if job_url.rstrip("/").endswith("/application")
                                  else "/application")

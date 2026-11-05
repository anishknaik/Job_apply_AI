"""Playwright: fill, upload, read back, screenshot. Never submit.

Takes a Resolution (what to put where, and from which source) and drives
the page. Every typed value is read back and both sides hashed, because a
field can silently reshape what was put in it - a single-line box that
strips paragraph breaks, a phone widget that reformats. Every option is
chosen by its exact text from the widget's own listbox; a widget that
offers no exact match is left alone and reported. Every upload goes
through the field's own file input, never an "autofill from resume" box.
Hidden fields are never touched, and there is no submit here at all.

The page is untrusted. Nothing read from it is treated as an instruction;
what is read - values, option texts, frame URLs, globals - is data for the
report and for signals.detect.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import resolve as _resolve, signals as _signals, spec as _spec

OK, MISMATCH, UNMATCHED, NOT_FOUND, ERROR, BLANK, SKIPPED, BLOCKED = (
    "ok", "mismatch", "unmatched_option", "not_found", "error", "blank",
    "skipped", "blocked")

FORM_ROOTS = {
    _spec.GREENHOUSE: "#application-form",
    _spec.ASHBY: ".ashby-application-form-container",
}


def sha256(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


@dataclass
class FieldResult:
    key: str
    label: str
    action: str
    status: str
    source: str | None = None
    meaning: str | None = None
    value_sha256: str | None = None
    readback_sha256: str | None = None
    note: str = ""

    @property
    def verified(self) -> bool:
        return self.status == OK and self.value_sha256 is not None \
            and self.value_sha256 == self.readback_sha256


@dataclass
class FillReport:
    ats: str
    url: str
    fields: list[FieldResult] = field(default_factory=list)
    signals_before: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    form_found: bool = True
    error: str | None = None

    @property
    def unverified(self) -> list[FieldResult]:
        """Anything typed, chosen or uploaded that did not read back equal."""
        return [f for f in self.fields
                if f.action in (_resolve.FILL, _resolve.UPLOAD, _resolve.SELECT,
                                _resolve.AUTOCOMPLETE) and not f.verified]

    def to_dict(self) -> dict:
        return {"ats": self.ats, "url": self.url, "form_found": self.form_found,
                "error": self.error, "signals_before": list(self.signals_before),
                "screenshots": list(self.screenshots),
                "fields": [asdict(f) for f in self.fields]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=1)


# ---------------------------------------------------------------------------
# Locating things on the page, in any frame
# ---------------------------------------------------------------------------

def form_root(page, ats: str):
    """The frame holding the form, and the root locator in it. A Greenhouse
    board may redirect to the company's site with the form embedded, so the
    page's own frames are searched too."""
    selector = FORM_ROOTS[ats]
    for frame in [page.main_frame] + [f for f in page.frames if f != page.main_frame]:
        try:
            if frame.locator(selector).count():
                return frame, frame.locator(selector).first
        except Exception:                                    # noqa: BLE001
            continue
    return None, None


def collect_signals(page) -> _signals.Verdict:
    """Everything signals.detect wants, read without touching anything."""
    html = page.content()
    # Child frames only: the page's own URL is where the form is, not a
    # challenge, whatever words it carries.
    frame_urls = [f.url for f in page.frames if f != page.main_frame]
    try:
        globals_ = page.evaluate(
            "names => names.filter(n => typeof window[n] !== 'undefined')",
            list(_signals.PROBE_GLOBALS))
    except Exception:                                        # noqa: BLE001
        globals_ = []
    try:
        text = page.inner_text("body")
    except Exception:                                        # noqa: BLE001
        text = None
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            html += frame.content()
        except Exception:                                    # noqa: BLE001
            pass
    return _signals.detect(html, frame_urls, globals_, text)


def _control(frame, f: _spec.Field):
    """The input, textarea or button group for a field."""
    key = f.key
    if f.kind == _spec.FILE:
        candidates = [f'input[type="file"]#{_css(key)}',
                      f'input[type="file"][name="{key}"]']
    elif f.kind in (_spec.YESNO, _spec.RADIO):
        candidates = [f'[data-field-path="{key}"]']
    elif _spec.page_id(key) != key:
        bare = _spec.page_id(key)
        # A bare numeric id is not a valid #selector; match the attribute.
        candidates = [f'[id="{bare}"]', f'[name="{bare}"]', f'[id="{key}"]']
    else:
        candidates = [f'#{_css(key)}', f'[name="{key}"]',
                      f'[data-field-path="{key}"] input',
                      f'[data-field-path="{key}"] textarea']
    for sel in candidates:
        loc = frame.locator(sel)
        try:
            if loc.count():
                return loc.first
        except Exception:                                    # noqa: BLE001
            continue
    return None


def _css(key: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else f"\\{c}" for c in key)


# ---------------------------------------------------------------------------
# Doing one thing to one field
# ---------------------------------------------------------------------------

def _fill_text(control, value: str) -> str:
    control.fill(value)
    return control.input_value()


def _upload(control, path: Path) -> tuple[str, str]:
    control.set_input_files(str(path))
    name, size = control.evaluate(
        "el => el.files && el.files[0] ? [el.files[0].name, el.files[0].size] : ['', -1]")
    return name, str(size)


# Only the options on screen: the one open listbox, not every closed one.
_OPTIONS = '[role="option"]:visible, [id*="-option-"]:visible'


def _options_of(frame, control):
    """Option texts offered by the open listbox, in any shape the two ATSes
    render them: ARIA options, or react-select's ids."""
    opts = frame.locator(_OPTIONS)
    try:
        opts.first.wait_for(state="visible", timeout=4000)
    except Exception:                                        # noqa: BLE001
        return []
    return [(" ".join((opts.nth(i).inner_text() or "").split()), i)
            for i in range(opts.count())]


def _choose(frame, control, wanted: str) -> tuple[str, str | None]:
    """Open the widget, read its options, click the one whose text is
    exactly `wanted`. Anything else: close it and report."""
    control.click()
    try:
        control.fill("")
        control.type(wanted)
    except Exception:                                        # noqa: BLE001
        pass
    options = _options_of(frame, control)
    exact = [i for text, i in options if text == wanted.strip()]
    if not exact:
        try:
            control.press("Escape")
        except Exception:                                    # noqa: BLE001
            pass
        return UNMATCHED, None
    frame.locator(_OPTIONS).nth(exact[0]).click()
    return OK, _selected_text(frame, control)


def _selected_text(frame, control) -> str:
    """What the widget now shows as chosen. Ashby's autocomplete keeps it
    in the input; react-select keeps it in a sibling single-value element
    and the input goes empty."""
    try:
        value = control.input_value()
        if value.strip():
            return value.strip()
    except Exception:                                        # noqa: BLE001
        pass
    try:
        container = control.locator("xpath=ancestor::div[@data-value][1]")
        if container.count():
            dv = container.first.get_attribute("data-value") or ""
            text = (container.first.locator("xpath=..").inner_text() or "").strip()
            return text or dv
    except Exception:                                        # noqa: BLE001
        pass
    try:
        return (control.locator("xpath=ancestor::div[3]").inner_text() or "").strip()
    except Exception:                                        # noqa: BLE001
        return ""


def _yes_no(frame, entry, wanted: str) -> tuple[str, str]:
    option = entry.locator(f'button[data-option="{wanted.lower()}"]')
    if not option.count():
        return UNMATCHED, ""
    option.first.click()
    pressed = option.first.get_attribute("aria-pressed")
    return (OK if pressed == "true" else MISMATCH), (wanted if pressed == "true" else "")


_RADIO_LABEL = "label.ashby-application-form-input-radio-group-option-label"


def _radio(entry, wanted: str) -> tuple[str, str]:
    """Click the option whose label is exactly `wanted`, then read back the
    label of whichever radio is now checked."""
    labels = entry.locator(_RADIO_LABEL)
    texts = [" ".join((labels.nth(i).inner_text() or "").split())
             for i in range(labels.count())]
    exact = [i for i, t in enumerate(texts) if t == wanted.strip()]
    if not exact:
        return UNMATCHED, ""
    labels.nth(exact[0]).click()
    checked = entry.locator('input[type="radio"]:checked')
    if not checked.count():
        return MISMATCH, ""
    rid = checked.first.get_attribute("id") or ""
    back = entry.locator(f'label[for="{rid}"]')
    text = " ".join((back.first.inner_text() or "").split()) if back.count() else ""
    return (OK if text == wanted.strip() else MISMATCH), text


def read_choice(frame, f: _spec.Field) -> str | None:
    """What a choice widget currently shows as chosen, or None. The same
    reads the fillers verify with: a radio's checked label, a yes/no's
    pressed button, a select's shown value."""
    control = _control(frame, f)
    if control is None:
        return None
    try:
        if f.kind == _spec.RADIO:
            checked = control.locator('input[type="radio"]:checked')
            if not checked.count():
                return None
            rid = checked.first.get_attribute("id") or ""
            label = control.locator(f'label[for="{rid}"]')
            text = " ".join((label.first.inner_text() or "").split()) if label.count() else ""
            return text or None
        if f.kind == _spec.YESNO:
            pressed = control.locator('button[data-option][aria-pressed="true"]')
            if not pressed.count():
                return None
            return " ".join((pressed.first.inner_text() or "").split()) or None
        if f.kind in (_spec.SELECT, _spec.LOCATION):
            text = _selected_text(frame, control)
            if not text or text.lower().rstrip(".…") in ("select", "start typing", "type here", "choose"):
                return None
            if f.options is not None and not any(o.label.strip() == text for o in f.options):
                return None
            return text
    except Exception:                                        # noqa: BLE001
        return None
    return None


def read_choices(page, form: _spec.FormSpec) -> dict[str, str | None]:
    """A snapshot of every choice widget on the form: key -> chosen text or
    None. Empty when the form is no longer on the page."""
    frame, _root = form_root(page, form.ats)
    if frame is None:
        return {}
    return {f.key: read_choice(frame, f) for f in form.fields
            if f.kind in (_spec.SELECT, _spec.RADIO, _spec.YESNO)}


def apply_one(frame, item: _resolve.Resolved) -> FieldResult:
    f = item.field
    result = FieldResult(key=f.key, label=f.label, action=item.action, status=OK,
                         source=item.source, meaning=item.meaning, note=item.note)
    if item.action in (_resolve.SKIP, _resolve.BLANK):
        result.status = SKIPPED if item.action == _resolve.SKIP else BLANK
        return result
    if item.action == _resolve.BLOCKER:
        result.status = BLOCKED
        return result
    control = _control(frame, f)
    if control is None:
        result.status = NOT_FOUND
        result.note = f"no control for {f.key!r} on the page"
        return result
    try:
        if item.action == _resolve.FILL:
            result.value_sha256 = sha256(item.value)
            back = _fill_text(control, item.value)
            result.readback_sha256 = sha256(back)
            result.status = OK if back == item.value else MISMATCH
        elif item.action == _resolve.UPLOAD:
            path = Path(item.value)
            if not path.is_file():
                result.status = ERROR
                result.note = f"{path} is not a file"
                return result
            result.value_sha256 = sha256(f"{path.name}:{path.stat().st_size}")
            name, size = _upload(control, path)
            result.readback_sha256 = sha256(f"{name}:{size}")
            result.status = OK if result.value_sha256 == result.readback_sha256 else MISMATCH
        elif item.action == _resolve.SELECT and f.kind == _spec.RADIO:
            result.value_sha256 = sha256(item.value)
            status, back = _radio(control, item.value)
            if status == UNMATCHED:
                result.status = UNMATCHED
                result.note = "no option on the page exactly equals the value; left unset"
                return result
            result.readback_sha256 = sha256(back)
            result.status = status
        elif item.action == _resolve.SELECT and f.kind == _spec.YESNO:
            result.value_sha256 = sha256(item.value)
            status, back = _yes_no(frame, control, item.value)
            result.readback_sha256 = sha256(back)
            result.status = status
        elif item.action in (_resolve.SELECT, _resolve.AUTOCOMPLETE):
            result.value_sha256 = sha256(item.value)
            status, back = _choose(frame, control, item.value)
            if status == UNMATCHED:
                result.status = UNMATCHED
                result.note = "no option on the page exactly equals the value; left unset"
                return result
            result.readback_sha256 = sha256(back or "")
            result.status = OK if back == item.value else MISMATCH
        else:
            result.status = ERROR
            result.note = f"no handler for action {item.action!r}"
    except Exception as exc:                                 # noqa: BLE001
        result.status = ERROR
        result.note = f"{type(exc).__name__}: {exc}"[:300]
    return result


def fill_form(page, form: _spec.FormSpec, resolution: _resolve.Resolution,
              screenshot_dir: Path | None = None, tag: str = "filled") -> FillReport:
    """Fill the form on an already-loaded page. Stops before any submit."""
    report = FillReport(ats=form.ats, url=page.url)
    report.signals_before = list(collect_signals(page).kinds)
    frame, root = form_root(page, form.ats)
    if frame is None:
        report.form_found = False
        report.error = f"no {FORM_ROOTS[form.ats]} on the page or in its frames"
        _shoot(page, screenshot_dir, f"{tag}-no-form", report)
        return report
    for item in resolution.items:
        report.fields.append(apply_one(frame, item))
    _shoot(page, screenshot_dir, tag, report)
    return report


def _shoot(page, directory: Path | None, name: str, report: FillReport) -> None:
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        report.screenshots.append(str(path))
    except Exception as exc:                                 # noqa: BLE001
        report.error = (report.error or "") + f" screenshot failed: {exc}"

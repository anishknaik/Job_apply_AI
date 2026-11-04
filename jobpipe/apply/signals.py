"""Bot-check detection on the rendered page. Stop, never evade.

A hit means one thing: the runner does not submit. It is checked after the
page loads and again just before a submit click, and the confirmation step
treats a challenge that appears afterwards the same way. A miss is not proof
of absence, which is why the decision also depends on everything else.

Pure: the page's HTML, its frame URLs, and the globals the page defines come
in; a verdict comes out. fill.py collects the inputs from Playwright. Nothing
here touches a challenge element, reads a token, or changes how the browser
looks to the page.

Measured 2026-11-04: both ATSes load reCAPTCHA Enterprise on the rendered
form page (Greenhouse's job-boards app and Ashby's application page), so
this is expected to fire on nearly every real form, and the review path is
the main product.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Each pattern names what it finds, so a report can say which check fired.
_HTML_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(pattern, re.I)) for name, pattern in (
        ("recaptcha", r"recaptcha/(?:api|enterprise)\.js|g-recaptcha(?:-response)?|data-sitekey=\"6L|grecaptcha"),
        ("hcaptcha", r"hcaptcha\.com|h-captcha|data-hcaptcha"),
        ("turnstile", r"challenges\.cloudflare\.com/turnstile|cf-turnstile|turnstile\.render"),
        ("arkose", r"arkoselabs|funcaptcha|arkose-?labs"),
        ("challenge_iframe", r"<iframe\b[^>]*\bsrc=\"[^\"]*(?:recaptcha|hcaptcha|turnstile|arkose|challenge)[^\"]*\""),
        ("verify_human_text", r"verify (?:that )?you(?:'| a)re (?:a )?human|are you a robot|i'm not a robot|prove you(?:'| a)re not a robot|complete the (?:security )?check"),
    ))
_FRAME_PATTERN = re.compile(r"recaptcha|hcaptcha|turnstile|arkose|funcaptcha|challenge", re.I)
_GLOBALS = ("grecaptcha", "hcaptcha", "turnstile", "ArkoseEnforcement", "___grecaptcha_cfg")


@dataclass(frozen=True)
class Signal:
    kind: str
    where: str     # html | frame | global | text
    detail: str


@dataclass(frozen=True)
class Verdict:
    signals: tuple[Signal, ...] = field(default_factory=tuple)

    @property
    def bot_check(self) -> bool:
        return bool(self.signals)

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted({s.kind for s in self.signals}))

    def describe(self) -> str:
        if not self.signals:
            return "no bot check seen"
        return "; ".join(f"{s.kind} ({s.where}: {s.detail})" for s in self.signals)


def detect(html: str, frame_urls: tuple[str, ...] | list[str] = (),
           window_globals: tuple[str, ...] | list[str] = (),
           visible_text: str | None = None) -> Verdict:
    """What on this page checks for bots.

    `html` is the rendered document; `frame_urls` every frame's URL;
    `window_globals` the names defined on `window` that fill.py probed for
    (read-only: `typeof window.x`); `visible_text` the page's inner text,
    for the "verify you are human" family. Any hit is a hit.
    """
    found: list[Signal] = []
    for name, pattern in _HTML_PATTERNS:
        if name == "verify_human_text":
            continue
        m = pattern.search(html or "")
        if m:
            found.append(Signal(name, "html", m.group(0)[:80]))
    for url in frame_urls:
        m = _FRAME_PATTERN.search(url or "")
        if m:
            found.append(Signal("challenge_iframe", "frame", url[:120]))
    for name in window_globals:
        if name in _GLOBALS:
            found.append(Signal(_kind_for_global(name), "global", name))
    text = visible_text if visible_text is not None else _strip(html or "")
    m = _HTML_PATTERNS[-1][1].search(text)
    if m:
        found.append(Signal("verify_human_text", "text", m.group(0)))
    return Verdict(tuple(found))


def _kind_for_global(name: str) -> str:
    return {"grecaptcha": "recaptcha", "___grecaptcha_cfg": "recaptcha",
            "hcaptcha": "hcaptcha", "turnstile": "turnstile",
            "ArkoseEnforcement": "arkose"}[name]


_SCRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")


def _strip(html: str) -> str:
    return " ".join(_TAG.sub(" ", _SCRIPT.sub(" ", html)).split())


PROBE_GLOBALS = _GLOBALS

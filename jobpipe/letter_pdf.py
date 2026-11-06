"""The approved cover letter as a PDF, for a form that wants a file.

Anish's decision (2026-09-10): a letter goes into a form's text box as text
where there is one, and as a PDF rendered at fill time where the form wants a
file. Either way the source is `cover_letter.sent.md` - the copy he reviewed
and edited - never the draft, and never a copy older than the current draft.
`mcp_server.prepare_letter` enforces that; this module only renders.

Rendered on the resume's Chromium path under the same ATS rules, then read
back. The PDF's text must be the approved letter's, word for word, or the file
is refused: a PDF that looks right and extracts to something else is worse
than none, because the human reviews the look and the ATS reads the text.
"""
from __future__ import annotations

import html
import re
import threading
from pathlib import Path

from . import resume

TEMPLATE_PATH = Path("templates/letter.html")

# Largest first. His letters run 360-431 words and fit at 11pt with room to
# spare; the smaller sizes are for an unusually long one, not a target.
FONT_LADDER = (11.0, 10.5, 10.0)


class LetterRenderError(RuntimeError):
    pass


def build_html(text: str, font_pt: float = FONT_LADDER[0]) -> str:
    """Blank-line blocks become paragraphs. A line break inside a block - the
    name, contact and date lines at the top - stays a line break."""
    blocks = [b for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    body = "\n".join(
        "<p>" + "<br>".join(html.escape(line.strip())
                            for line in block.splitlines()) + "</p>"
        for block in blocks)
    return (TEMPLATE_PATH.read_text(encoding="utf-8")
            .replace("{{FONT_SIZE}}", f"{font_pt:g}")
            .replace("{{BODY}}", body))


def _words(text: str) -> str:
    """Letters and digits only, after the resume's cosmetic folding.

    What survives any extractor's handling of spacing, of a hyphen broken
    across a line, and of ligatures - and still changes when a word is lost,
    added or altered. That is the property the check needs.
    """
    return re.sub(r"[\W_]+", "", resume._normalise(text))


def matches(pdf_text: str, source: str) -> bool:
    """Does the PDF's extracted text say exactly what the letter says?"""
    return _words(pdf_text) == _words(source)


def render(text: str, out: Path | str) -> dict:
    """Render `text` to `out`, on one page if any size on the ladder allows.

    Refuses - and removes the file, so it cannot be uploaded by mistake - a
    PDF whose text does not read back as the letter.

    Runs on a thread of its own. The apply runner asks for the letter PDF
    while its own form browser is open, and Playwright's sync API refuses a
    second instance on a thread that already drives one ("using Playwright
    Sync API inside the asyncio loop") - the 2026-11-06 Arize run failed on
    exactly that. A fresh thread has no loop, and `resume`'s browser state is
    per thread, so the render cannot see or disturb the caller's.
    """
    result: dict = {}

    def work():
        try:
            result["value"] = _render(text, Path(out))
        except BaseException as exc:                         # noqa: BLE001
            result["error"] = exc

    worker = threading.Thread(target=work, name="letter-pdf", daemon=True)
    worker.start()
    worker.join()
    if "error" in result:
        raise result["error"]
    return result["value"]


def _render(text: str, out: Path) -> dict:
    pages, used = None, FONT_LADDER[0]
    for used in FONT_LADDER:
        pages = resume._render_pdf(build_html(text, used), out)
        if pages == 1:
            break
    if not matches(resume.extract_text(out), text):
        out.unlink(missing_ok=True)
        raise LetterRenderError(
            "the PDF rendered, but its text did not read back as the approved "
            "letter, so it was discarded rather than handed over")
    return {"path": str(out), "pages": pages, "font_pt": used}

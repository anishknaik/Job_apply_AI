"""The approved cover letter as a PDF (Anish's decision, 2026-09-10).

The property that matters is fidelity: the file an ATS reads must carry the
letter he approved, word for word. So the read-back check is pinned both
ways - it tolerates what extractors do to spacing, hyphens and ligatures, and
it catches a word that went missing.
"""
import shutil

import pytest

from jobpipe import letter_pdf, resume

LETTER = """Anish Kumar Naik
jane@example.com · (555) 010-0000
September 10, 2026

Dear Example Hiring Team,

{body}

Sincerely,
Anish Kumar Naik
"""

PARAGRAPH = ("The evaluation harness mattered more than the model: held-out "
             "template splits separated generalization from memorization, "
             "and the ablation isolated the traces as the causal driver. ")


def sample(paragraphs=4, sentences=3):
    """About 380 words - inside his own 360-431 range."""
    body = "\n\n".join((PARAGRAPH * sentences).strip()
                        for _ in range(paragraphs))
    return LETTER.format(body=body)


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False
    return bool(shutil.which("python"))


needs_chromium = pytest.mark.skipif(
    not _chromium_available(),
    reason="playwright/chromium not installed; HTML and matching still covered")


class TestBuildHtml:
    def test_blocks_become_paragraphs(self):
        html = letter_pdf.build_html(sample(paragraphs=4))
        # header, salutation, four body paragraphs, sign-off
        assert html.count("<p>") == 7

    def test_the_header_lines_stay_separate_lines(self):
        html = letter_pdf.build_html(sample())
        assert "Anish Kumar Naik<br>jane@example.com" in html

    def test_text_is_escaped(self):
        html = letter_pdf.build_html("R&D <team>\n\nBody.")
        assert "R&amp;D &lt;team&gt;" in html

    def test_no_placeholder_survives(self):
        assert "{{" not in letter_pdf.build_html(sample(), 10.5)

    def test_ligatures_are_off(self):
        """An fi ligature extracts as one codepoint; a parser sees another
        word."""
        assert "font-variant-ligatures: none" in letter_pdf.build_html("x")


class TestMatches:
    def test_extractor_quirks_are_tolerated(self):
        source = "I built the ﬁrst harness,\nend to end - content-creation."
        extracted = "I built the first harness, end to end — contentcreation."
        assert letter_pdf.matches(extracted, source)

    def test_a_missing_word_is_caught(self):
        assert not letter_pdf.matches("I built the harness.",
                                      "I built the full harness.")

    def test_an_added_word_is_caught(self):
        assert not letter_pdf.matches("I built and shipped the harness.",
                                      "I built the harness.")


@needs_chromium
class TestRender:
    def test_a_normal_letter_is_one_page_and_reads_back(self, tmp_path):
        out = tmp_path / "cover_letter.pdf"
        result = letter_pdf.render(sample(), out)
        assert result["pages"] == 1
        assert result["font_pt"] == letter_pdf.FONT_LADDER[0]
        assert letter_pdf.matches(resume.extract_text(out), sample())

    def test_a_pdf_that_does_not_read_back_is_discarded(self, tmp_path,
                                                        monkeypatch):
        out = tmp_path / "cover_letter.pdf"
        monkeypatch.setattr(resume, "extract_text",
                            lambda path: "something else entirely")
        with pytest.raises(letter_pdf.LetterRenderError):
            letter_pdf.render(sample(), out)
        assert not out.exists(), "a bad file must not be left to upload"

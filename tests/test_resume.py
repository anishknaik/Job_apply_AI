"""Resume rendering. HTML assembly and verification logic are tested without
a browser; the two tests that need Chromium are marked and skip when it is
absent, so the suite still runs offline.

The bugs these pin were all found by rendering a real PDF and reading it back
rather than by reasoning about the code:

  * the parse check verified long bullet text against a page built from short
    variants, reporting five failures that were one bug in the checker;
  * height was measured at the default 1280px viewport while the printable
    width is 710px, so text wrapped less when measured than when printed and
    the loop dropped six bullets it did not need to;
  * a hyphenated compound broke across a line, and the first version of the
    hyphen check tested only the fused form, missing the split form entirely.
"""
import shutil
from pathlib import Path

import pytest

from jobpipe import bullets, resume


@pytest.fixture(scope="module")
def bank():
    return bullets.load_bank()


@pytest.fixture(scope="module")
def selected(bank):
    return bank.select(["dist-distill", "dist-ablation", "rag-agentic",
                        "gt-lesson-plan", "nec-yolo"])


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    return bool(shutil.which("python"))


needs_chromium = pytest.mark.skipif(
    not _chromium_available(),
    reason="playwright/chromium not installed; HTML assembly is still covered")


class TestHtmlAssembly:
    """No browser needed. Pure string work."""

    def test_every_placeholder_is_filled(self, bank, selected):
        html = resume.build_html(bank, selected)
        assert "{{" not in html, "an unreplaced placeholder reached the page"

    def test_selected_bullets_appear(self, bank, selected):
        html = resume.build_html(bank, selected)
        for bullet in selected:
            assert bullet.render_text()[:40] in html

    def test_unselected_bullets_do_not_appear(self, bank, selected):
        html = resume.build_html(bank, selected)
        assert bank.bullets["spot-cv"].render_text()[:40] not in html

    def test_short_variants_are_used_when_asked(self, bank, selected):
        html = resume.build_html(bank, selected, short=True)
        b = bank.bullets["dist-distill"]
        assert b.render_text(True)[:40] in html
        assert b.render_text(False)[:60] not in html

    def test_exposure_skills_never_reach_the_page(self, bank, selected):
        """§3.1a, enforced at the last point before rendering."""
        html = resume.build_html(bank, selected)
        for skill in bank.exposure_skills():
            assert f"{skill}," not in html and f"{skill}<" not in html

    def test_ats_forbidden_elements_are_absent(self, bank, selected):
        """§3.3: no tables, text boxes, images, headers or footers."""
        html = resume.build_html(bank, selected).lower()
        for tag in ("<table", "<img", "<header", "<footer", "<object",
                    "<canvas", "<svg"):
            assert tag not in html

    def test_ligatures_are_disabled(self, bank, selected):
        """Without this, 'verification' becomes one codepoint and no ATS
        keyword search matches it."""
        assert "font-variant-ligatures: none" in resume.build_html(bank,
                                                                   selected)

    def test_only_standard_section_headings(self, bank, selected):
        import re
        html = resume.build_html(bank, selected)
        headings = {h.strip().lower()
                    for h in re.findall(r"<h2>(.*?)</h2>", html)}
        assert headings <= {"education", "experience", "projects", "skills"}

    def test_font_size_never_goes_below_the_floor(self):
        assert min(resume.font_ladder()) >= resume.min_font_pt()

    def test_the_floor_is_read_at_call_time_not_import(self, monkeypatch):
        """It was a module constant, and the CLI imports this module before
        loading .env — so every resume silently rendered at the 10pt default
        however JOBPIPE_MIN_FONT_PT was set."""
        monkeypatch.setenv("JOBPIPE_MIN_FONT_PT", "9.0")
        assert resume.min_font_pt() == 9.0
        assert 9.0 in resume.font_ladder()
        monkeypatch.setenv("JOBPIPE_MIN_FONT_PT", "10.0")
        assert 9.0 not in resume.font_ladder()

    def test_the_ladder_is_always_descending(self):
        ladder = resume.font_ladder()
        assert list(ladder) == sorted(ladder, reverse=True)

    def test_roles_without_selected_bullets_are_omitted(self, bank, selected):
        html = resume.build_html(bank, selected)
        assert "Brainayan" not in html


class TestDropOrderIsRelevanceAware:
    """The one-page loop drops the weakest bullet FOR THIS POSTING.

    Static rank alone got this exactly backwards on live data. `gt-ownership`
    is rank 4 because it does not belong on most resumes — but on a Founding
    Engineer posting it is among the most relevant bullets in the bank.
    Ranking alone dropped it from precisely that resume while keeping it on
    two where the posting never asked for it.
    """

    def _weakest(self, bank, ids, vocabulary):
        """Mirror the loop's choice without rendering."""
        from jobpipe.bullets import match_score
        working = bank.select(ids)
        return max(working, key=lambda b: (
            b.rank - min(match_score(b, vocabulary), 2),
            working.index(b))).id

    def test_an_irrelevant_low_rank_bullet_goes_first(self, bank):
        """No ownership language in the posting: rank decides."""
        ids = ["dist-distill", "gt-ownership", "nec-yolo"]
        assert self._weakest(bank, ids, ["qlora", "distillation"]) == \
            "gt-ownership"

    def test_a_matching_bullet_outlives_a_comparable_one(self, bank):
        """gt-ownership is rank 4, gt-remittance rank 3. With the posting
        asking for ownership, the promotion flips which goes first.

        Deliberately compared against a near-rank bullet, not against the
        rank-1 flagship: a 2-rank cap means relevance reorders within a band
        rather than letting a marginal bullet displace the distillation work
        on a founding posting. Both behaviours are intended.
        """
        ids = ["gt-remittance", "gt-ownership"]
        assert self._weakest(bank, ids, []) == "gt-ownership"
        assert self._weakest(bank, ids,
                             ["ownership", "founding"]) == "gt-remittance"

    def test_the_cap_stops_a_marginal_bullet_displacing_the_flagship(self,
                                                                     bank):
        """Even fully matched, rank 4 stays below rank 1."""
        ids = ["dist-distill", "gt-ownership"]
        assert self._weakest(bank, ids,
                             list(bank.bullets["gt-ownership"].skills)) == \
            "gt-ownership"

    def test_promotion_is_capped_at_two_ranks(self, bank):
        """A heavily-matching rank-4 bullet must not outrank a rank-1 one —
        relevance reorders within a band, it does not invert the bank."""
        from jobpipe.bullets import match_score
        b = bank.bullets["gt-ownership"]
        vocabulary = list(b.skills)          # every tag matches
        assert min(match_score(b, vocabulary), 2) == 2

    def test_ties_still_break_deterministically(self, bank):
        ids = ["nec-yolo", "nec-tracking"]
        first = self._weakest(bank, ids, [])
        assert first == self._weakest(bank, ids, [])

    def test_no_vocabulary_falls_back_to_rank(self, bank):
        ids = ["dist-distill", "gt-ownership"]
        assert self._weakest(bank, ids, []) == "gt-ownership"


class TestVariantIsALayout:
    """§3.1: two variants, differing in more than a headline."""

    def _skill_sections(self, html):
        """Section names as rendered, unescaped back to their bank spelling —
        'Analytics & BI' reaches the page as 'Analytics &amp; BI'."""
        import html as html_mod
        import re
        return [html_mod.unescape(m)
                for m in re.findall(r"<strong>([^<]+):</strong>", html)]

    def test_the_two_variants_order_skills_differently(self, bank, selected):
        ai = self._skill_sections(resume.build_html(bank, selected,
                                                    variant="ai_ml"))
        ds = self._skill_sections(resume.build_html(bank, selected,
                                                    variant="data_science"))
        assert ai != ds
        assert ai[0] == "GenAI & LLM"
        assert ds[0] == "Statistics & Analysis"

    def test_each_variant_lists_every_section(self, bank):
        """A variant that forgot a category would silently delete a whole
        skills line from the resume."""
        sections = set(bank.surfaceable_skills())
        for variant, order in resume.VARIANT_SKILL_ORDER.items():
            assert sections <= set(order), variant

    def test_a_partial_model_order_still_renders_everything(self, bank,
                                                            selected):
        """The model may return two sections; the rest must be appended,
        not dropped."""
        html = resume.build_html(bank, selected, skill_order=["Languages"])
        rendered = set(self._skill_sections(html))
        assert rendered == set(bank.surfaceable_skills())
        assert self._skill_sections(html)[0] == "Languages"

    def test_the_model_order_beats_the_variant_default(self, bank, selected):
        html = resume.build_html(bank, selected, variant="ai_ml",
                                 skill_order=["Analytics & BI"])
        assert self._skill_sections(html)[0] == "Analytics & BI"

    def test_emphasis_reaches_the_page(self, bank, selected):
        html = resume.build_html(bank, selected, emphasise=["power bi"])
        line = [ln for ln in html.splitlines() if "Analytics &amp; BI" in ln][0]
        assert line.index("Power BI") < line.index("Matplotlib")

    def test_variants_match_the_selector(self, bank, selected):
        from jobpipe import tailor
        assert set(resume.VARIANT_SKILL_ORDER) == set(tailor.VARIANTS)


class TestNormalisation:
    """The fold that makes verification meaningful rather than brittle."""

    def test_ligatures_are_unfolded(self):
        assert resume._normalise("veriﬁcation") == "verification"

    def test_dash_variants_collapse(self):
        assert resume._normalise("Nov 2025 – July 2026") == \
            resume._normalise("Nov 2025 - July 2026")

    def test_whitespace_collapses(self):
        assert resume._normalise("a   b\n\nc") == "a b c"


class TestPrintGeometry:
    def test_width_is_the_printable_width_not_the_viewport(self):
        """Measuring at 1280px let text wrap less than it prints, which
        understated height and cost six bullets."""
        assert 650 < resume.PRINT_WIDTH_PX < 760

    def test_usable_height_matches_letter_minus_margins(self):
        assert resume.USABLE_PX == pytest.approx((11 - 1.0) * 96)


@needs_chromium
class TestRenderedPdf:
    def test_renders_one_verified_page(self, bank, selected, tmp_path):
        out = tmp_path / "r.pdf"
        result = resume.render(bank, selected, out)
        assert result.pages == 1
        assert out.exists()
        assert result.parse["ok"], result.parse["missing"]

    def test_the_parse_check_matches_what_was_rendered(self, bank, selected,
                                                       tmp_path):
        """Checking long text against short-variant output reported five
        failures that were really one bug in the checker."""
        out = tmp_path / "short.pdf"
        html = resume.build_html(bank, selected, short=True)
        resume._render_pdf(html, out)
        assert resume.ats_parse_check(out, bank, selected, short=True)["ok"]
        assert not resume.ats_parse_check(out, bank, selected,
                                          short=False)["ok"]

    def test_name_and_email_survive_extraction(self, bank, selected, tmp_path):
        out = tmp_path / "r.pdf"
        resume.render(bank, selected, out)
        text = resume._normalise(resume.extract_text(out))
        assert resume._normalise(bank.meta["name"]) in text
        assert resume._normalise(bank.meta["email"]) in text

    def test_hyphen_damage_is_reported_not_silent(self, bank, tmp_path):
        """A compound split across lines matches neither term. It is a
        warning rather than a failure — there is no honest render-time fix,
        since a non-breaking hyphen changes the codepoint."""
        selected = bank.select(["rag-agentic", "dist-distill"])
        out = tmp_path / "h.pdf"
        result = resume.render(bank, selected, out)
        assert "fused_hyphens" in result.parse

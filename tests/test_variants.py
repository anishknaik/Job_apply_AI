"""The seven variants (PHASE5_PLAN §4.2).

The orderings are data, and data drifts silently. Nothing at render time can
tell that a variant lost a bullet — a 20-entry ordering renders 14 bullets
exactly as a 21-entry one does, and the missing one just never appears again
in that lane. These tests are the only thing that reports it.
"""
from __future__ import annotations

import pytest

from jobpipe import bullets, resume, tailor, variants


@pytest.fixture(scope="module")
def bank():
    return bullets.load_bank()


class TestEveryOrderingCoversTheBank:
    """A variant is an ORDERING, never a subset. This is the invariant."""

    def test_every_ordering_is_a_permutation_of_the_bank(self, bank):
        expected = set(bank.bullets)
        for name, order in variants.ORDERINGS.items():
            assert set(order) == expected, (
                f"{name} is not a permutation of the bank: "
                f"missing {sorted(expected - set(order))}, "
                f"unknown {sorted(set(order) - expected)}")

    def test_no_ordering_repeats_a_bullet(self):
        for name, order in variants.ORDERINGS.items():
            assert len(order) == len(set(order)), f"{name} repeats a bullet"

    def test_orderings_are_actually_different(self):
        """Seven identical orderings would pass every other test here."""
        seen = {name: order for name, order in variants.ORDERINGS.items()}
        for a in seen:
            for b in seen:
                if a < b:
                    assert seen[a] != seen[b], f"{a} and {b} are identical"


class TestTheDecisionsThatWereSignedOff:
    """Three orderings carry a reviewed decision. A silent revert is a
    regression even though nothing would fail without these."""

    def test_ds_analytics_leads_with_the_ablation(self):
        assert variants.ORDERINGS["ds_analytics"][0] == "dist-ablation"

    def test_ai_ml_infra_leads_with_projects_not_employment(self):
        """Signed off deliberately: the NBS 7 work was a group proof of
        concept, the projects are solo and carry results."""
        order = variants.ORDERINGS["ai_ml_infra"]
        assert order.index("dist-distill") < order.index("gt-nbs7")

    def test_the_cv_bullets_lead_only_the_cv_lane(self):
        """They sit at or below the one-page boundary everywhere else, which
        is why ai_ml_cv exists at all — without it they never render.

        The boundary rather than well below it: `nec-yolo` is position 14 in
        ds_analytics, the last slot that fits, on the grounds that it is paid
        work with a model-evaluation component. That placement was reviewed."""
        cv = ("nec-yolo", "nec-tracking", "spot-cv")
        assert variants.ORDERINGS["ai_ml_cv"][:3] == cv
        for name, order in variants.ORDERINGS.items():
            if name == "ai_ml_cv":
                continue
            first = min(order.index(b) for b in cv) + 1   # index -> position
            assert first >= tailor.MIN_BULLETS, (
                f"{name} leads with a CV bullet at position {first}")


class TestVariantsResolveToALayout:
    def test_every_ordering_has_a_headline_key(self, bank):
        available = set(bank.meta.get("headline") or {})
        for name in variants.ORDERINGS:
            key = variants.headline_key(name)
            assert key in available, f"{name} -> {key} is not in the bank"

    def test_every_ordering_has_a_skill_order(self):
        for name in variants.ORDERINGS:
            assert name in variants.SKILL_ORDER

    def test_skill_orders_name_real_sections(self, bank):
        sections = set(bank.surfaceable_skills())
        for name, order in variants.SKILL_ORDER.items():
            assert set(order) <= sections, f"{name} names a missing section"

    def test_legacy_variants_still_resolve_to_themselves(self):
        assert variants.headline_key("ai_ml") == "ai_ml"
        assert variants.headline_key("data_science") == "data_science"


class TestResolution:
    def test_the_data_science_lane_ignores_domain(self):
        for domain in ("ai_infra", "healthcare", None):
            assert variants.resolve("data_scientist", domain)[0] == \
                "ds_analytics"

    def test_the_swe_lane_ignores_domain(self):
        assert variants.resolve("swe_generalist", "ai_saas")[0] == \
            "swe_backend"

    @pytest.mark.parametrize("domain,expected", [
        ("ai_infra", "ai_ml_infra"),
        ("data_infra", "ai_ml_data"),
        ("ai_saas", "ai_ml_product"),
        ("product_saas", "ai_ml_product"),
        ("healthcare", "ai_ml_health"),
        ("robotics_cv", "ai_ml_cv"),
        ("fintech", "swe_backend"),
    ])
    def test_the_ai_ml_lane_splits_by_domain(self, domain, expected):
        assert variants.resolve("ai_ml_engineer", domain)[0] == expected

    def test_an_unscored_domain_falls_back_and_says_so(self):
        """~565 postings predate the `domain` field, so this is a live path.
        The caller has to be able to tell a resolution from a guess."""
        variant, why = variants.resolve("ai_ml_engineer", None)
        assert variant == variants.DEFAULT_VARIANT
        assert "confirm" in why

    def test_an_unknown_archetype_falls_back_and_says_so(self):
        variant, why = variants.resolve("astronaut", "ai_infra")
        assert variant == variants.DEFAULT_VARIANT
        assert "confirm" in why

    def test_a_resolved_grid_hit_does_not_ask_for_confirmation(self):
        assert "confirm" not in variants.resolve("ai_ml_engineer",
                                                 "healthcare")[1]

    def test_every_grid_target_is_a_real_ordering(self):
        for variant in variants._AI_ML_BY_DOMAIN.values():
            assert variant in variants.ORDERINGS
        assert variants.DEFAULT_VARIANT in variants.ORDERINGS


class TestBulletsForVariant:
    def test_it_returns_the_whole_bank_in_variant_order(self, bank):
        got = tailor.bullets_for_variant(bank, "ds_analytics")
        assert [b.id for b in got] == \
            list(variants.ORDERINGS["ds_analytics"])

    def test_an_unknown_variant_names_the_ones_that_exist(self, bank):
        with pytest.raises(KeyError) as excinfo:
            tailor.bullets_for_variant(bank, "ai_ml_quantum")
        assert "ds_analytics" in str(excinfo.value)

    def test_the_renderer_accepts_every_variant(self, bank):
        """Cheap end-to-end: build_html is pure string work, no PDF.

        The headline is escaped on the way into the page, so compare against
        the escaped form — three of the four contain an ampersand and would
        pass vacuously against the raw string on a page that dropped them."""
        import html as html_mod
        for name in variants.ORDERINGS:
            selected = tailor.bullets_for_variant(bank, name)[:6]
            page = resume.build_html(bank, selected, variant=name)
            expected = bank.meta["headline"][variants.headline_key(name)]
            assert html_mod.escape(expected) in page, name

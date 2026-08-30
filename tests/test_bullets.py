"""The bullet bank. No network.

This is the fabrication firewall (Phase 2 spec §2.1), so the tests are about
what the system CANNOT do, not what it does: cannot emit text a human did not
write, cannot surface an Exposure-tier skill, cannot silently drop a bullet the
model asked for.
"""
import pytest

from jobpipe import bullets


@pytest.fixture(scope="module")
def bank():
    return bullets.load_bank()


class TestBankIntegrity:
    """Every guard here has a corresponding hand-edit that would trip it."""

    def test_it_loads(self, bank):
        assert bank.bullets and bank.roles

    def test_every_bullet_traces_to_a_source(self, bank):
        """§2.1: nothing enters the bank that is not already evidenced."""
        for b in bank.bullets.values():
            assert b.source, f"{b.id} has no source document"

    def test_every_bullet_belongs_to_a_known_role(self, bank):
        for b in bank.bullets.values():
            assert b.role in bank.roles

    def test_every_bullet_is_reachable_by_some_archetype(self, bank):
        """A bullet tagged for no lane is dead weight nobody would notice."""
        reachable = set()
        for archetype in bullets.VALID_ARCHETYPES:
            reachable.update(b.id for b in bank.for_archetype(archetype))
        assert set(bank.bullets) == reachable

    def test_archetypes_match_phase_1(self, bank):
        """A lane renamed in scoring.py must fail here, not silently stop
        matching."""
        from jobpipe import scoring
        assert bullets.VALID_ARCHETYPES == set(scoring.ARCHETYPES)

    def test_both_resume_variants_have_enough_material(self, bank):
        """§3.1 ships two layouts; each needs bullets to fill a page."""
        for archetype in ("ai_ml_engineer", "data_scientist"):
            assert len(bank.for_archetype(archetype)) >= 8, archetype

    def test_a_malformed_bank_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "meta: {}\neducation: {}\nroles: [{id: r}]\nskills: {}\n"
            "bullets:\n  - id: x\n    role: nope\n    text: t\n"
            "    source: s\n    archetypes: [ai_ml_engineer]\n",
            encoding="utf-8")
        with pytest.raises(bullets.BankError) as exc:
            bullets.load_bank(str(bad))
        assert "unknown role" in str(exc.value)

    def test_an_unknown_archetype_tag_raises(self, tmp_path):
        """The typo that would otherwise make a bullet permanently invisible."""
        bad = tmp_path / "bad2.yaml"
        bad.write_text(
            "meta: {}\neducation: {}\nroles: [{id: r}]\nskills: {}\n"
            "bullets:\n  - id: x\n    role: r\n    text: t\n"
            "    source: s\n    archetypes: [ai_ml_enginer]\n",
            encoding="utf-8")
        with pytest.raises(bullets.BankError) as exc:
            bullets.load_bank(str(bad))
        assert "unknown archetype" in str(exc.value)


class TestTextIsImmutable:
    """The model selects. It does not write."""

    def test_render_returns_bank_text_verbatim(self, bank):
        for b in bank.bullets.values():
            assert b.render_text() == " ".join(b.text.split())

    def test_the_short_variant_is_also_hand_written(self, bank):
        """`short` must be a string a human put in the file, not a truncation."""
        for b in bank.bullets.values():
            if b.variants.get("short"):
                assert b.render_text(prefer_short=True) == \
                    " ".join(b.variants["short"].split())

    def test_render_falls_back_when_no_short_exists(self, bank):
        b = next(iter(bank.bullets.values()))
        stripped = bullets.Bullet(
            id=b.id, role=b.role, text=b.text, source=b.source,
            archetypes=b.archetypes, skills=b.skills, metrics=b.metrics,
            rank=b.rank, variants={})
        assert stripped.render_text(prefer_short=True) == stripped.render_text()


class TestSelection:
    def test_selection_preserves_the_requested_order(self, bank):
        ids = ["dist-ablation", "dist-distill"]
        assert [b.id for b in bank.select(ids)] == ids

    def test_an_unknown_id_raises_rather_than_dropping(self, bank):
        """A silent drop is indistinguishable from the model choosing fewer
        bullets — a quality regression with no error attached."""
        with pytest.raises(bullets.BankError) as exc:
            bank.select(["dist-ablation", "dist-ablations"])
        assert "unknown bullet id" in str(exc.value)

    def test_the_error_names_the_offender(self, bank):
        with pytest.raises(bullets.BankError) as exc:
            bank.select(["totally-made-up"])
        assert "totally-made-up" in str(exc.value)


class TestExposureTierNeverSurfaces:
    """§2.3 / §3.1a — the hard rule, tested as an impossibility."""

    def test_tableau_is_graded_exposure(self, bank):
        """Sanity anchor: it is on the current resume but Exposure in the
        profile, so the generated resume must drop it. Without an anchor like
        this, an empty `exposure` list everywhere would make the whole class
        pass vacuously."""
        assert "Tableau" in bank.exposure_skills()

    def test_surfaceable_skills_exclude_every_exposure_entry(self, bank):
        surfaced = {s for entries in bank.surfaceable_skills().values()
                    for s in entries}
        assert not (surfaced & bank.exposure_skills())

    @pytest.mark.parametrize("skill", ["Tableau", "C", "ARM Assembly"])
    def test_named_exposure_skills_are_absent(self, bank, skill):
        surfaced = {s for entries in bank.surfaceable_skills().values()
                    for s in entries}
        assert skill not in surfaced

    def test_the_bank_tiers_match_the_profile(self, bank):
        """The profile is the source of truth for tiers. A bank that
        disagreed would let Phase 1 score a posting on one grading while
        Phase 2 wrote a resume claiming another.

        Checked by regrade rather than by parsing the profile's prose: these
        three moved Exposure -> Working, and the profile must agree.
        """
        from pathlib import Path
        profile = Path("config/master_profile.md").read_text(encoding="utf-8")
        surfaced = {s for entries in bank.surfaceable_skills().values()
                    for s in entries}
        assert "MLflow" in surfaced
        assert "**Exposure** — MLflow." not in profile
        assert "**Exposure** — AWS IAM, AWS EC2." not in profile

    def test_no_section_surfaces_an_empty_list(self, bank):
        for section, entries in bank.surfaceable_skills().items():
            assert entries, f"{section} would render as an empty heading"


class TestSkillEmphasis:
    """§3.1a step 3: front-load matching entries. A permutation, nothing more.

    The dangerous failure is silent: a reorder that drops an entry looks
    exactly like a reorder that worked, so every test here compares the
    multiset before and after rather than eyeballing the front of the list.
    """

    def test_reordering_preserves_every_entry(self, bank):
        plain = bank.surfaceable_skills()
        moved = bank.surfaceable_skills(emphasise=["sql", "pandas", "rag"])
        assert set(plain) == set(moved)
        for section in plain:
            assert sorted(plain[section]) == sorted(moved[section])

    def test_matching_entries_move_to_the_front(self, bank):
        moved = bank.surfaceable_skills(emphasise=["power bi"])
        assert moved["Analytics & BI"][0] == "Power BI"

    def test_a_parenthesised_compound_still_matches(self, bank):
        """'AWS (SageMaker, Bedrock, S3, IAM, EC2)' must match 'sagemaker'.
        Word-boundary matching would miss it — this file is full of them."""
        moved = bank.surfaceable_skills(emphasise=["sagemaker"])
        assert moved["Cloud & Infra"][0].startswith("AWS")

    def test_no_emphasis_leaves_hand_written_order_alone(self, bank):
        assert bank.surfaceable_skills(emphasise=[]) == \
            bank.surfaceable_skills()

    def test_unmatched_vocabulary_changes_nothing(self, bank):
        assert bank.surfaceable_skills(emphasise=["cobol", "fortran"]) == \
            bank.surfaceable_skills()

    def test_emphasis_can_never_surface_an_exposure_skill(self, bank):
        """The hard rule holds even when the posting asks for it by name."""
        moved = bank.surfaceable_skills(emphasise=["tableau", "c",
                                                   "arm assembly"])
        surfaced = {s for entries in moved.values() for s in entries}
        assert not (surfaced & bank.exposure_skills())

    def test_ordering_within_a_group_is_stable(self, bank):
        """Stable sort: unmatched entries keep their hand-written order."""
        moved = bank.surfaceable_skills(emphasise=["python"])
        plain = bank.surfaceable_skills()
        tail = [s for s in moved["Languages"] if "python" not in s.lower()]
        assert tail == [s for s in plain["Languages"]
                        if "python" not in s.lower()]


class TestKeywordMatching:
    """§3.1a: preference only. Cannot alter, add, or invent."""

    def test_overlap_is_counted(self, bank):
        b = bank.bullets["dist-ablation"]
        assert bullets.match_score(b, ["ablation", "experimental design"]) == 2

    def test_matching_is_case_and_separator_insensitive(self, bank):
        b = bank.bullets["dist-ablation"]
        assert bullets.match_score(b, ["Experimental-Design"]) == 1
        assert bullets.match_score(b, ["EXPERIMENTAL_DESIGN"]) == 1

    def test_an_unmatched_vocabulary_scores_zero(self, bank):
        b = bank.bullets["dist-ablation"]
        assert bullets.match_score(b, ["mlflow", "terraform"]) == 0

    def test_matching_never_mutates_the_bullet(self, bank):
        b = bank.bullets["dist-ablation"]
        before = b.render_text()
        bullets.match_score(b, ["ablation"])
        assert b.render_text() == before

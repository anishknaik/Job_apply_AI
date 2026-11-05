"""Phase 7A, slice 5: a posting's vocabulary read from its text, and the
drop flags it drives. Pure functions over a hand-built bank - no
gitignored files, no model."""
from jobpipe.bullets import (GENERIC_TAGS, Bank, Bullet, flag_dropped,
                             match_score, posting_vocabulary)


def bullet(bid, *skills):
    return Bullet(id=bid, role="r1", text=f"{bid} text.", source="profile §1",
                  archetypes=("ai_ml_engineer",), skills=tuple(skills),
                  metrics=False, rank=1)


def bank(*bullets):
    return Bank(meta={}, education={}, roles={"r1": {}},
                bullets={b.id: b for b in bullets}, skills={})


BANK = bank(bullet("pg", "postgresql", "schema_design", "backend"),
            bullet("rag", "rag", "retrieval", "embeddings"),
            bullet("sk", "scikit_learn", "xgboost"),
            bullet("cv", "computer_vision", "opencv"),
            bullet("gen", "testing", "ownership", "performance"),
            bullet("cpp", "c++"))


class TestPostingVocabulary:
    def test_only_the_banks_tags_the_text_names_most_named_first(self):
        text = ("We use PostgreSQL. Postgres is our main store; PostgreSQL "
                "schema design matters. Some retrieval work.")
        got = posting_vocabulary(BANK, text)
        assert list(got) == ["postgresql", "retrieval", "schema_design"]
        assert got["postgresql"] == 3, "the alias counts toward the tag"

    def test_underscore_parts_match_with_a_space_a_hyphen_or_nothing(self):
        for text in ("computer vision", "computer-vision", "computervision",
                     "Scikit-learn", "scikit learn", "sklearn"):
            got = posting_vocabulary(BANK, text)
            assert got, text

    def test_plurals_match_and_word_edges_hold(self):
        assert "embeddings" in posting_vocabulary(BANK, "vector embeddings")
        assert "rag" in posting_vocabulary(BANK, "RAG pipelines")
        assert "rag" not in posting_vocabulary(BANK, "storage and fragments")
        assert "opencv" not in posting_vocabulary(BANK, "opencvx")

    def test_html_is_stripped_and_entities_unescaped(self):
        got = posting_vocabulary(BANK, "<li>Retrieval&nbsp;augmented generation</li>"
                                       "<p>XGBoost &amp; friends</p>")
        assert set(got) == {"rag", "retrieval", "xgboost"}, "&nbsp; folds to a space"

    def test_generic_tags_never_enter_the_vocabulary(self):
        got = posting_vocabulary(BANK, "Testing, ownership and performance on the backend.")
        assert got == {}
        assert {"testing", "ownership", "performance", "backend"} <= GENERIC_TAGS

    def test_empty_and_missing_text(self):
        assert posting_vocabulary(BANK, None) == {}
        assert posting_vocabulary(BANK, "") == {}

    def test_the_vocabulary_is_what_match_score_compares_against(self):
        vocab = posting_vocabulary(BANK, "postgres and retrieval")
        assert match_score(BANK.bullets["pg"], vocab) == 1
        assert match_score(BANK.bullets["rag"], vocab) == 1
        assert match_score(BANK.bullets["sk"], vocab) == 0


class TestFlagDropped:
    def test_a_dropped_bullet_carrying_a_named_tag_is_flagged(self):
        vocab = {"postgresql": 3, "rag": 1}
        flags = flag_dropped(BANK, ["sk", "rag", "pg"], vocab)
        assert [f["bullet"] for f in flags] == ["pg", "rag"], "most-named first"
        assert flags[0] == {"bullet": "pg", "skills": ["postgresql"],
                            "mentions": {"postgresql": 3}}

    def test_nothing_named_nothing_flagged(self):
        assert flag_dropped(BANK, ["sk", "cv"], {"postgresql": 1}) == []
        assert flag_dropped(BANK, [], {"postgresql": 1}) == []
        assert flag_dropped(BANK, ["pg"], {}) == []

    def test_an_unknown_id_is_ignored(self):
        assert flag_dropped(BANK, ["nope"], {"postgresql": 1}) == []

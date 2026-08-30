from jobpipe.normalize import (Posting, content_hash, html_to_text,
                               looks_remote, merge_locations, normalize_title,
                               split_locations)


class TestNormalizeTitle:
    def test_canonicalises_seniority_spelling_without_removing_it(self):
        """Sr. and Senior are one req written two ways."""
        assert normalize_title("Sr. Software Engineer") == \
            normalize_title("Senior Software Engineer")
        assert normalize_title("Snr Software Engineer") == \
            "senior software engineer"
        assert normalize_title("Jr Data Scientist") == "junior data scientist"

    def test_seniority_bands_remain_distinct(self):
        """Senior X and X are two different open reqs, not one."""
        assert normalize_title("Senior Software Engineer") != \
            normalize_title("Software Engineer")
        assert normalize_title("Staff Software Engineer") != \
            normalize_title("Software Engineer")

    def test_strips_roman_and_numeric_levels(self):
        assert normalize_title("Software Engineer II") == "software engineer"
        assert normalize_title("Software Engineer III") == "software engineer"
        assert normalize_title("Software Engineer L4") == "software engineer"
        assert normalize_title("Software Engineer, Level 5") == "software engineer"

    def test_strips_level_and_employment_parentheticals(self):
        assert normalize_title("ML Engineer (L4) (Remote)") == "ml engineer"
        assert normalize_title("ML Engineer [Contract]") == "ml engineer"
        assert normalize_title("ML Engineer (US)") == "ml engineer"

    def test_strips_a_parenthetical_that_restates_the_location(self):
        assert normalize_title("Deployed Engineer (Amsterdam)",
                               "Amsterdam") == "deployed engineer"
        assert normalize_title("ML Engineer (New York)",
                               "New York, NY") == "ml engineer"

    def test_keeps_a_parenthetical_that_carries_the_role(self):
        """Perplexity puts the actual job inside the parentheses."""
        assert normalize_title(
            "Member of Technical Staff (AI Inference Engineer)",
            "San Francisco; New York City",
        ) == "member of technical staff ai inference engineer"

    def test_technical_staff_survives_intact(self):
        assert normalize_title("Member of Technical Staff") == \
            "member of technical staff"

    def test_strips_trailing_location_separators(self):
        assert normalize_title("Backend Engineer - Remote") == "backend engineer"
        assert normalize_title("Backend Engineer, New York") == "backend engineer"
        assert normalize_title("Backend Engineer | Full-Time") == "backend engineer"

    def test_the_same_req_relisted_normalizes_identically(self):
        """The whole point: one req listed twice must produce one hash."""
        variants = [
            "Senior Machine Learning Engineer II (New York)",
            "Sr. Machine Learning Engineer, New York",
            "Senior Machine Learning Engineer II",
            "senior machine learning engineer",
        ]
        assert len({normalize_title(v, "New York, NY") for v in variants}) == 1

    def test_early_career_is_not_treated_as_a_seniority_band(self):
        """The bug that lost a 9-day-old New York early-career role.

        It collapsed into a 68-day-old general listing of the same title,
        which was then discarded on posting age.
        """
        assert normalize_title("Deployed Engineer (Early Career-NYC)",
                               "New York, NY") != \
            normalize_title("Deployed Engineer (NYC)", "New York, NY")

    def test_an_unrecognised_parenthetical_is_kept_when_location_is_unknown(self):
        """Deliberate asymmetry.

        With no location to compare against, "(New York)" cannot be told apart
        from "(AI Inference Engineer)". Keeping it is the safe default:
        over-splitting costs one duplicate row, over-collapsing deletes a real
        job. Every ATS in the target list supplies a location, so the pipeline
        itself always takes the informed path.
        """
        assert normalize_title("ML Engineer (New York)") == \
            "ml engineer new york"
        assert normalize_title("ML Engineer (New York)", "New York, NY") == \
            "ml engineer"

    def test_handles_empty(self):
        assert normalize_title("") == ""


class TestContentHash:
    def test_is_stable_across_casing_and_whitespace(self):
        assert content_hash("Stripe", "software engineer") == \
            content_hash("  stripe ", "software engineer")

    def test_ignores_location(self):
        """One req listed in five cities must hash to one value."""
        assert len({content_hash("Harvey", "technology enablement analyst")
                    for _ in range(5)}) == 1

    def test_differs_on_role(self):
        assert content_hash("Stripe", "software engineer") != \
            content_hash("Stripe", "data scientist")

    def test_differs_on_company(self):
        assert content_hash("Stripe", "software engineer") != \
            content_hash("Figma", "software engineer")


class TestLocationMerging:
    def test_splits_multi_valued_strings(self):
        assert split_locations("San Francisco; New York") == \
            ["San Francisco", "New York"]
        assert split_locations("SF | NYC") == ["SF", "NYC"]
        assert split_locations(None) == []

    def test_keeps_comma_locations_intact(self):
        """"New York, NY" is one place, not two."""
        assert split_locations("New York, NY") == ["New York, NY"]

    def test_unions_without_duplicating(self):
        assert merge_locations("San Francisco", "New York") == \
            "San Francisco; New York"
        assert merge_locations("San Francisco", "san francisco") == \
            "San Francisco"
        assert merge_locations("SF; NYC", "NYC; Dallas") == "SF; NYC; Dallas"

    def test_handles_empty_sides(self):
        assert merge_locations(None, "New York") == "New York"
        assert merge_locations("New York", None) == "New York"


class TestHtmlToText:
    def test_unescapes_double_encoded_greenhouse_content(self):
        """Greenhouse ships `&lt;h2&gt;` rather than `<h2>`."""
        raw = "&lt;h2&gt;Who we are&lt;/h2&gt;&lt;p&gt;We build things.&lt;/p&gt;"
        text = html_to_text(raw)
        assert "<" not in text and ">" not in text
        assert "Who we are" in text
        assert "We build things." in text

    def test_strips_plain_html(self):
        assert html_to_text("<p>Hello <b>world</b></p>").strip() == "Hello world"

    def test_drops_script_and_style(self):
        raw = "<p>Real</p><script>var x = 1;</script><style>p{color:red}</style>"
        text = html_to_text(raw)
        assert "Real" in text
        assert "var x" not in text
        assert "color:red" not in text

    def test_preserves_paragraph_breaks(self):
        assert "\n" in html_to_text("<p>One</p><p>Two</p>")

    def test_handles_none_and_empty(self):
        assert html_to_text(None) == ""
        assert html_to_text("") == ""


class TestLooksRemote:
    def test_detects_remote(self):
        assert looks_remote("Remote - US")
        assert looks_remote(None, "Work from home")

    def test_ignores_non_remote(self):
        assert not looks_remote("New York, NY", "Software Engineer")

    def test_tolerates_all_none(self):
        assert not looks_remote(None, None)


class TestPosting:
    def test_derives_normalized_title_and_hash(self):
        posting = Posting(
            ats_type="greenhouse", source_id="1", company="Stripe",
            title="Senior Software Engineer II (New York)",
            location="New York, NY", remote_flag=False,
            description="", url="https://example.com", posted_at=None,
        )
        assert posting.normalized_title == "senior software engineer"
        assert len(posting.content_hash) == 64

    def test_the_same_req_in_five_cities_shares_one_hash(self):
        """The Harvey case: one opening returned once per location."""
        cities = ["New York", "Remote", "San Francisco", "Chicago", "Dallas"]
        hashes = {
            Posting(
                ats_type="ashby", source_id=f"id-{i}", company="Harvey",
                title="Technology Enablement Analyst", location=city,
                remote_flag=False, description="", url="https://example.com",
                posted_at=None,
            ).content_hash
            for i, city in enumerate(cities)
        }
        assert len(hashes) == 1

    def test_distinct_roles_under_one_umbrella_title_stay_distinct(self):
        """The Perplexity case: 42 different jobs all titled MTS."""
        titles = [
            "Member of Technical Staff (Software Engineer, GPU)",
            "Member of Technical Staff (AI Inference Engineer)",
            "Member of Technical Staff (AI Infrastructure Engineer)",
        ]
        hashes = {
            Posting(
                ats_type="ashby", source_id=f"id-{i}", company="Perplexity",
                title=title, location="San Francisco; New York City",
                remote_flag=False, description="", url="https://example.com",
                posted_at=None,
            ).content_hash
            for i, title in enumerate(titles)
        }
        assert len(hashes) == 3

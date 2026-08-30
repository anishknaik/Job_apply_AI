"""Fetcher parsing tests, using payload fixtures captured from the live APIs.

Each fixture preserves the traps confirmed against the real endpoints on
2026-08-11 — Greenhouse's double-escaped HTML and Ashby's `isListed` flag.

Lever is not covered because it is out of Phase 1 scope: the target list is 16
Greenhouse and 20 Ashby boards and no Lever ones.
"""
from jobpipe.fetchers import ashby, greenhouse


class TestGreenhouse:
    payload = {
        "jobs": [
            {
                "id": 8077887,
                "title": "Senior Software Engineer",
                "absolute_url": "https://stripe.com/jobs/search?gh_jid=8077887",
                "location": {"name": "New York, NY"},
                "content": "&lt;h2&gt;About&lt;/h2&gt;&lt;p&gt;Build things.&lt;/p&gt;",
                "first_published": "2026-07-22T13:15:53-04:00",
                "updated_at": "2026-08-06T12:10:17-04:00",
            }
        ],
        "meta": {"total": 1},
    }

    def test_parses_a_posting(self, monkeypatch):
        monkeypatch.setattr(greenhouse, "get_json", lambda url: self.payload)
        posting = next(greenhouse.fetch("Stripe", "stripe"))
        assert posting.ats_type == "greenhouse"
        assert posting.source_id == "8077887"
        assert posting.title == "Senior Software Engineer"
        assert posting.location == "New York, NY"

    def test_unescapes_and_strips_the_body(self, monkeypatch):
        monkeypatch.setattr(greenhouse, "get_json", lambda url: self.payload)
        posting = next(greenhouse.fetch("Stripe", "stripe"))
        assert "<" not in posting.description
        assert "&lt;" not in posting.description
        assert "Build things." in posting.description

    def test_prefers_first_published_over_updated_at(self, monkeypatch):
        monkeypatch.setattr(greenhouse, "get_json", lambda url: self.payload)
        posting = next(greenhouse.fetch("Stripe", "stripe"))
        assert posting.posted_at.startswith("2026-07-22")

    def test_skips_malformed_jobs(self, monkeypatch):
        monkeypatch.setattr(greenhouse, "get_json",
                            lambda url: {"jobs": [{"id": None, "title": ""}]})
        assert list(greenhouse.fetch("Stripe", "stripe")) == []

    def test_handles_empty_board(self, monkeypatch):
        monkeypatch.setattr(greenhouse, "get_json", lambda url: {"jobs": []})
        assert list(greenhouse.fetch("Stripe", "stripe")) == []


class TestAshby:
    payload = {
        "jobs": [
            {
                "id": "34413f8d-26bf-4bbc-8ade-eb309a0e2245",
                "title": "Security Engineer, Cloud",
                "location": "New York, NY (HQ)",
                "secondaryLocations": [{"location": "Remote (US)"}],
                "isListed": True,
                "isRemote": True,
                "publishedAt": "2026-04-07T17:12:35.753+00:00",
                "jobUrl": "https://jobs.ashbyhq.com/ramp/34413f8d",
                "descriptionPlain": "ABOUT RAMP\n\nWe build things.",
            },
            {
                "id": "unlisted-1",
                "title": "Hidden Role",
                "location": "New York",
                "isListed": False,
                "jobUrl": "https://jobs.ashbyhq.com/ramp/unlisted-1",
                "descriptionPlain": "Should never surface.",
            },
        ],
        "apiVersion": "1",
    }

    def test_drops_unlisted_postings(self, monkeypatch):
        monkeypatch.setattr(ashby, "get_json", lambda url: self.payload)
        postings = list(ashby.fetch("Ramp", "ramp"))
        assert len(postings) == 1
        assert postings[0].title == "Security Engineer, Cloud"

    def test_uses_the_native_remote_flag(self, monkeypatch):
        monkeypatch.setattr(ashby, "get_json", lambda url: self.payload)
        assert next(ashby.fetch("Ramp", "ramp")).remote_flag is True

    def test_merges_secondary_locations(self, monkeypatch):
        monkeypatch.setattr(ashby, "get_json", lambda url: self.payload)
        location = next(ashby.fetch("Ramp", "ramp")).location
        assert "New York" in location
        assert "Remote (US)" in location

    def test_prefers_description_plain(self, monkeypatch):
        monkeypatch.setattr(ashby, "get_json", lambda url: self.payload)
        assert "We build things." in next(ashby.fetch("Ramp", "ramp")).description

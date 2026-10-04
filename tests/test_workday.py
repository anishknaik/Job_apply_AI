"""Workday fetcher. No network - `get_json` is stubbed.

Fixtures mirror the live shapes recorded 2026-10-03 against Barclays and
Broadridge. What is guarded is what differs from every other fetcher: a host
per company, a POST for the list, and a `total` that is only true on the
first page.
"""
import pytest

from jobpipe.fetchers import workday as wd
from jobpipe.fetchers.http import (ALLOWED_HOSTS, FetchError, UnsafeURLError,
                                   assert_safe_url)

TOKEN = "broadridge/wd5/Careers"
BASE = "https://broadridge.wd5.myworkdayjobs.com/wday/cxs/broadridge/Careers"


def summary(n):
    return {"title": f"Member Technical {n}",
            "externalPath": f"/job/Bengaluru/Member-Technical_JR{n}",
            "locationsText": "Bengaluru-EPIP Industrial Area"}


def detail(n, location="Bengaluru-EPIP Industrial Area", country="India",
           additional=None):
    info = {
        "title": f"Member Technical {n}",
        "jobPostingId": f"Member-Technical_JR{n}",
        "jobReqId": f"JR{n}",
        "jobDescription": "<p>Build <b>things</b>.</p>",
        "location": location,
        "country": {"descriptor": country},
        "startDate": "2026-10-02",
        "externalUrl": f"https://broadridge.wd5.myworkdayjobs.com/Careers"
                       f"/job/Bengaluru/Member-Technical_JR{n}",
    }
    if additional:
        info["additionalLocations"] = additional
    return {"jobPostingInfo": info}


@pytest.fixture
def fake_api(monkeypatch):
    """Serve list pages and details from an in-memory board."""
    state = {"jobs": [], "details": {}, "calls": [], "fail": set(),
             "total_on_later_pages": 0, "total": None}

    def fake_get_json(url, body=None, **kwargs):
        state["calls"].append((url, body))
        if body is None:
            path = url[len(BASE):]
            if path in state["fail"]:
                raise FetchError(f"boom for {path}")
            return state["details"][path]
        offset = body["offset"]
        total = len(state["jobs"]) if state["total"] is None else state["total"]
        return {"total": total if offset == 0
                else state["total_on_later_pages"],
                "jobPostings": state["jobs"][offset:offset + body["limit"]]}

    monkeypatch.setattr(wd, "get_json", fake_get_json)
    return state


def board(state, n):
    state["jobs"] = [summary(i) for i in range(n)]
    state["details"] = {s["externalPath"]: detail(i)
                        for i, s in enumerate(state["jobs"])}


class TestToken:
    def test_the_three_parts_make_the_url(self):
        assert wd.base_url(TOKEN) == BASE

    def test_the_site_keeps_its_case(self):
        assert wd.base_url("barclays/wd3/External_Career_Site_Barclays") \
            .endswith("/barclays/External_Career_Site_Barclays")

    @pytest.mark.parametrize("token", ["broadridge", "broadridge/wd5", "a//b"])
    def test_a_malformed_token_is_refused(self, token):
        with pytest.raises(FetchError, match="tenant/wd/site"):
            wd.base_url(token)


class TestPagination:
    def test_the_list_is_a_post(self, fake_api):
        board(fake_api, 3)
        list(wd.fetch("Broadridge", TOKEN))
        url, body = fake_api["calls"][0]
        assert url == f"{BASE}/jobs"
        assert body["limit"] == wd.PAGE_SIZE == 20 and body["offset"] == 0

    def test_it_pages_to_the_end_when_later_pages_report_no_total(self,
                                                                 fake_api):
        """Broadridge: 284 at offset 0, then total 0 beside a full page."""
        board(fake_api, 45)
        assert len(list(wd.fetch("Broadridge", TOKEN))) == 45

    def test_an_empty_board_yields_nothing(self, fake_api):
        assert list(wd.fetch("Broadridge", TOKEN)) == []

    def test_a_board_past_the_result_cap_is_refused(self, fake_api):
        board(fake_api, 20)
        fake_api["total"] = 36099
        with pytest.raises(FetchError, match="cannot be fetched whole"):
            list(wd.fetch("Accenture", TOKEN))


class TestFieldMapping:
    def test_the_country_is_appended_to_the_location(self, fake_api):
        board(fake_api, 1)
        posting = next(wd.fetch("Broadridge", TOKEN))
        assert posting.location == "Bengaluru-EPIP Industrial Area, India"

    def test_additional_locations_are_kept(self, fake_api):
        board(fake_api, 1)
        fake_api["details"][summary(0)["externalPath"]] = detail(
            0, location="Newark, NJ", country="United States of America",
            additional=["Hyderabad-Hi-Tec City"])
        posting = next(wd.fetch("Broadridge", TOKEN))
        assert posting.location == ("Newark, NJ, United States of America; "
                                    "Hyderabad-Hi-Tec City")

    def test_the_rest_of_the_posting(self, fake_api):
        board(fake_api, 1)
        posting = next(wd.fetch("Broadridge", TOKEN))
        assert posting.ats_type == "workday"
        assert posting.source_id == "Member-Technical_JR0"
        assert "Build" in posting.description
        assert "<" not in posting.description
        assert posting.posted_at == "2026-10-02"
        assert posting.url.startswith(
            "https://broadridge.wd5.myworkdayjobs.com/Careers/job/")


class TestResilience:
    def test_one_failed_detail_does_not_lose_the_board(self, fake_api):
        board(fake_api, 3)
        fake_api["fail"].add(summary(1)["externalPath"])
        assert len(list(wd.fetch("Broadridge", TOKEN))) == 2

    def test_a_malformed_posting_is_skipped_not_fatal(self, fake_api):
        board(fake_api, 2)
        fake_api["details"][summary(0)["externalPath"]] = {"jobPostingInfo": {}}
        assert len(list(wd.fetch("Broadridge", TOKEN))) == 1


class TestHosts:
    def test_it_is_in_the_registry(self):
        from jobpipe.fetchers import FETCHERS
        assert "workday" in FETCHERS

    @pytest.mark.parametrize("token", [
        TOKEN, "barclays/wd3/External_Career_Site_Barclays"])
    def test_the_two_boards_are_allowlisted(self, token):
        assert_safe_url(wd.base_url(token) + "/jobs")

    def test_any_other_tenant_is_refused(self):
        """The allowlist stays closed: no wildcard over myworkdayjobs.com."""
        with pytest.raises(UnsafeURLError, match="allowlist"):
            assert_safe_url(wd.base_url("accenture/wd103/AccentureCareers")
                            + "/jobs")
        assert not any(host.startswith("*") for host in ALLOWED_HOSTS)

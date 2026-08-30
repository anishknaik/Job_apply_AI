"""SmartRecruiters fetcher. No network — `get_json` is stubbed.

Fixtures mirror the live shapes recorded 2026-08-14 against Canva's board.
The two behaviours worth guarding are the ones that differ from every other
fetcher: pagination that silently truncates, and a description that only
exists on a second request.
"""
import pytest

from jobpipe.fetchers import smartrecruiters as sr
from jobpipe.fetchers.http import FetchError


def summary(job_id="6000000001317240", name="Senior Frontend Engineer",
            city="Prague", country="cz", remote=False):
    return {
        "id": job_id, "name": name,
        "releasedDate": "2026-08-14T15:44:08.124Z",
        "location": {"city": city, "region": city, "country": country,
                     "remote": remote, "hybrid": True,
                     "fullLocation": f"{city}, {city}, {country.upper()}"},
    }


def detail(job_id="6000000001317240", description="<p>Build things.</p>",
           qualifications="<p>5+ years of experience.</p>", url=None):
    return {
        "id": job_id,
        "postingUrl": url or f"https://jobs.smartrecruiters.com/Canva/{job_id}",
        "location": {"city": "Prague", "country": "cz", "remote": False,
                     "fullLocation": "Prague, Prague, Czechia"},
        "jobAd": {"sections": {
            "companyDescription": {"title": "Company", "text": "<p>About us</p>"},
            "jobDescription": {"title": "Job", "text": description},
            "qualifications": {"title": "Quals", "text": qualifications},
            "additionalInformation": {"title": "More", "text": ""},
        }},
    }


@pytest.fixture
def fake_api(monkeypatch):
    """Serve list pages and details from an in-memory board."""
    state = {"pages": [], "details": {}, "calls": [], "fail": set()}

    def fake_get_json(url, **kwargs):
        state["calls"].append(url)
        if "/postings/" in url:
            job_id = url.rsplit("/", 1)[-1]
            if job_id in state["fail"]:
                raise FetchError(f"boom for {job_id}")
            return state["details"][job_id]
        offset = 0
        if "offset=" in url:
            offset = int(url.split("offset=")[1].split("&")[0])
        index = offset // sr.PAGE_SIZE
        return (state["pages"][index] if index < len(state["pages"])
                else {"content": [], "totalFound": 0})

    monkeypatch.setattr(sr, "get_json", fake_get_json)
    return state


def board(state, count, page_size=None):
    """Build `count` postings, paginated exactly as the API does."""
    page_size = page_size or sr.PAGE_SIZE
    jobs = [summary(job_id=str(1000 + i), name=f"Engineer {i}")
            for i in range(count)]
    for job in jobs:
        state["details"][job["id"]] = detail(job["id"])
    state["pages"] = [
        {"content": jobs[i:i + page_size], "totalFound": count,
         "offset": i, "limit": page_size}
        for i in range(0, max(count, 1), page_size)] or [
        {"content": [], "totalFound": 0}]
    return jobs


class TestPagination:
    """`limit` caps at 100 whatever you ask for. A single request would
    quietly collect the first 100 of 248 and report success — the same silent
    shape as a wrong board token."""

    def test_a_single_page_board(self, fake_api):
        board(fake_api, 5)
        assert len(list(sr.fetch("Canva", "canva"))) == 5

    def test_it_follows_pagination_to_the_end(self, fake_api):
        board(fake_api, 248)
        assert len(list(sr.fetch("Canva", "canva"))) == 248

    def test_it_requests_each_offset(self, fake_api):
        board(fake_api, 248)
        list(sr.fetch("Canva", "canva"))
        offsets = [c for c in fake_api["calls"] if "offset=" in c]
        assert "offset=0" in offsets[0]
        assert any("offset=100" in c for c in offsets)
        assert any("offset=200" in c for c in offsets)

    def test_it_stops_on_a_short_page(self, fake_api):
        """A board shrinking mid-pagination must end the loop, not spin."""
        board(fake_api, 150)
        assert len(list(sr.fetch("Canva", "canva"))) == 150

    def test_an_empty_board_yields_nothing(self, fake_api):
        board(fake_api, 0)
        assert list(sr.fetch("Canva", "canva")) == []

    def test_the_page_size_is_the_api_ceiling(self):
        assert sr.PAGE_SIZE == 100


class TestDescriptions:
    """The list endpoint has none. Stage A's years-of-experience,
    sponsorship and description rules all read the body, so a posting without
    one would be judged on nothing."""

    def test_the_description_comes_from_the_detail_call(self, fake_api):
        board(fake_api, 1)
        posting = next(iter(sr.fetch("Canva", "canva")))
        assert "Build things" in posting.description

    def test_qualifications_are_included(self, fake_api):
        """Where the years-of-experience and sponsorship language lives."""
        board(fake_api, 1)
        posting = next(iter(sr.fetch("Canva", "canva")))
        assert "5+ years" in posting.description

    def test_sections_appear_in_reading_order(self, fake_api):
        board(fake_api, 1)
        text = next(iter(sr.fetch("Canva", "canva"))).description
        assert text.index("Build things") < text.index("5+ years")
        assert text.index("5+ years") < text.index("About us")

    def test_empty_sections_are_skipped(self, fake_api):
        board(fake_api, 1)
        fake_api["details"]["1000"] = detail("1000", qualifications="")
        text = next(iter(sr.fetch("Canva", "canva"))).description
        assert "\n\n\n" not in text

    def test_html_is_stripped(self, fake_api):
        board(fake_api, 1)
        assert "<p>" not in next(iter(sr.fetch("Canva", "canva"))).description


class TestResilience:
    def test_one_failed_detail_does_not_lose_the_board(self, fake_api):
        """The same rule the pipeline applies to a failing company."""
        board(fake_api, 5)
        fake_api["fail"].add("1002")
        postings = list(sr.fetch("Canva", "canva"))
        assert len(postings) == 4

    def test_a_malformed_posting_is_skipped_not_fatal(self, fake_api):
        board(fake_api, 3)
        fake_api["details"]["1001"] = {"id": "1001"}   # no url, no jobAd
        assert len(list(sr.fetch("Canva", "canva"))) == 2


class TestFieldMapping:
    def test_structured_location_is_used(self, fake_api):
        board(fake_api, 1)
        assert next(iter(sr.fetch("Canva", "canva"))).location == \
            "Prague, Prague, Czechia"

    def test_the_explicit_remote_flag_is_respected(self, fake_api):
        """Richer than Greenhouse, which has to infer it from wording."""
        board(fake_api, 1)
        fake_api["details"]["1000"]["location"]["remote"] = True
        assert next(iter(sr.fetch("Canva", "canva"))).remote_flag is True

    def test_posted_at_comes_from_released_date(self, fake_api):
        board(fake_api, 1)
        assert next(iter(sr.fetch("Canva", "canva"))).posted_at == \
            "2026-08-14T15:44:08.124Z"

    def test_the_url_is_the_candidate_facing_one(self, fake_api):
        board(fake_api, 1)
        url = next(iter(sr.fetch("Canva", "canva"))).url
        assert url.startswith("https://jobs.smartrecruiters.com/")

    def test_ats_type_is_recorded(self, fake_api):
        board(fake_api, 1)
        assert next(iter(sr.fetch("Canva", "canva"))).ats_type == \
            "smartrecruiters"


class TestRegistration:
    def test_it_is_in_the_registry(self):
        from jobpipe.fetchers import FETCHERS, SUPPORTED
        assert "smartrecruiters" in FETCHERS
        assert "smartrecruiters" in SUPPORTED

    def test_the_host_is_on_the_ssrf_allowlist(self):
        from jobpipe.fetchers.http import ALLOWED_HOSTS
        assert "api.smartrecruiters.com" in ALLOWED_HOSTS

    def test_sessions_are_thread_local(self):
        """Detail requests fan out; `requests.Session` is not thread-safe.

        A barrier forces all three threads to be alive at once. Without it a
        pool finishes each task before starting the next, reuses one thread,
        and correctly returns one session — which looks like a failure.
        """
        import threading
        from jobpipe.fetchers.http import _get_session

        barrier = threading.Barrier(3)
        seen = []
        lock = threading.Lock()

        def grab():
            barrier.wait(timeout=5)
            session = _get_session()
            with lock:
                seen.append(session)

        threads = [threading.Thread(target=grab) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        assert len(seen) == 3
        assert len({id(s) for s in seen}) == 3, "threads shared a session"

    def test_the_same_thread_reuses_its_session(self):
        from jobpipe.fetchers.http import _get_session
        assert _get_session() is _get_session()

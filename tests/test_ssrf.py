"""SSRF hardening (addendum §5A.1).

career-ops shipped "providers: harden lever/ashby/workday against SSRF via
redirect". Every URL this pipeline fetches is built from a template, so the
legitimate host set is closed — which makes an allowlist the right control.
"""
import pytest
import requests

from jobpipe.fetchers.http import (ALLOWED_HOSTS, UnsafeURLError,
                                   _get_following_safe_redirects,
                                   _host_is_a_private_ip, assert_safe_url)


class TestURLValidation:
    def test_accepts_the_real_ats_endpoints(self):
        for url in [
            "https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true",
            "https://api.ashbyhq.com/posting-api/job-board/ramp",
            "https://api.smartrecruiters.com/v1/companies/Visa/postings",
        ]:
            assert_safe_url(url)

    def test_rejects_plain_http(self):
        with pytest.raises(UnsafeURLError, match="non-https"):
            assert_safe_url("http://boards-api.greenhouse.io/v1/boards/x/jobs")

    def test_rejects_a_host_outside_the_allowlist(self):
        with pytest.raises(UnsafeURLError, match="allowlist"):
            assert_safe_url("https://evil.example.com/jobs")

    def test_rejects_cloud_instance_metadata(self):
        """The canonical SSRF target."""
        with pytest.raises(UnsafeURLError):
            assert_safe_url("https://169.254.169.254/latest/meta-data/")

    def test_rejects_loopback_and_private_ranges(self):
        for host in ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1",
                     "[::1]"]:
            with pytest.raises(UnsafeURLError):
                assert_safe_url(f"https://{host}/jobs")

    def test_rejects_non_http_schemes(self):
        for url in ["file:///etc/passwd", "gopher://127.0.0.1:11211/"]:
            with pytest.raises(UnsafeURLError):
                assert_safe_url(url)

    def test_private_ip_detection(self):
        assert _host_is_a_private_ip("169.254.169.254")
        assert _host_is_a_private_ip("127.0.0.1")
        assert not _host_is_a_private_ip("api.ashbyhq.com")
        assert not _host_is_a_private_ip("8.8.8.8")


class FakeResponse:
    def __init__(self, status_code=200, location=None):
        self.status_code = status_code
        self.headers = {"Location": location} if location else {}
        self.is_redirect = location is not None
        self.is_permanent_redirect = status_code == 301 and location is not None


class FakeSession:
    """Records every URL actually requested."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requested = []

    def get(self, url, **kwargs):
        assert kwargs.get("allow_redirects") is False, \
            "redirects must never be followed automatically"
        self.requested.append(url)
        return self._responses.pop(0)


class TestRedirectHandling:
    def test_does_not_follow_a_redirect_off_the_allowlist(self):
        session = FakeSession([
            FakeResponse(302, "https://169.254.169.254/latest/meta-data/")])
        with pytest.raises(UnsafeURLError):
            _get_following_safe_redirects(
                session, "https://api.ashbyhq.com/posting-api/job-board/ramp")
        # The dangerous hop must never have been requested.
        assert len(session.requested) == 1

    def test_follows_a_same_host_redirect(self):
        session = FakeSession([
            FakeResponse(302, "https://api.ashbyhq.com/posting-api/v2/ramp"),
            FakeResponse(200)])
        response = _get_following_safe_redirects(
            session, "https://api.ashbyhq.com/posting-api/job-board/ramp")
        assert response.status_code == 200
        assert session.requested[-1].endswith("/v2/ramp")

    def test_caps_redirect_chains(self):
        loop = "https://api.ashbyhq.com/loop"
        session = FakeSession([FakeResponse(302, loop) for _ in range(10)])
        with pytest.raises(UnsafeURLError, match="too many redirects"):
            _get_following_safe_redirects(session, loop)

    def test_rejects_a_redirect_with_no_location(self):
        session = FakeSession([FakeResponse(200)])
        session._responses[0].is_redirect = True
        with pytest.raises(UnsafeURLError, match="no Location"):
            _get_following_safe_redirects(
                session, "https://api.ashbyhq.com/posting-api/job-board/ramp")

    def test_relative_redirects_resolve_against_the_current_host(self):
        session = FakeSession([FakeResponse(302, "/v2/ramp"),
                               FakeResponse(200)])
        _get_following_safe_redirects(
            session, "https://api.ashbyhq.com/posting-api/job-board/ramp")
        assert session.requested[-1] == "https://api.ashbyhq.com/v2/ramp"


class TestAllowlistContents:
    """Derived from the registry rather than hardcoded.

    This previously asserted `set(FETCHERS) == {"greenhouse", "ashby"}`, so
    adding SmartRecruiters failed the test and the fix was to edit the
    expectation — which is exactly the check being defeated. Now a new
    fetcher is covered automatically, and one whose host is missing from the
    allowlist fails here rather than at 10am in a cron run.
    """

    def _modules(self):
        import importlib
        from jobpipe.fetchers import FETCHERS
        return {name: importlib.import_module(f"jobpipe.fetchers.{name}")
                for name in FETCHERS}

    def test_every_registered_fetcher_declares_a_url(self):
        for name, module in self._modules().items():
            assert hasattr(module, "URL") or hasattr(module, "BASE"), \
                f"{name} exposes neither URL nor BASE; its host cannot be " \
                f"checked against the allowlist"

    def test_every_fetcher_host_is_allowlisted(self):
        from urllib.parse import urlparse

        for name, module in self._modules().items():
            template = getattr(module, "URL", None) or module.BASE
            host = urlparse(template.format(token="x")).hostname
            assert host in ALLOWED_HOSTS, \
                f"{name}: {host} missing from ALLOWED_HOSTS"

    def test_the_registry_matches_the_ats_types(self):
        from jobpipe.fetchers import FETCHERS
        for name, module in self._modules().items():
            assert module.ATS_TYPE == name

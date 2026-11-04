"""Shared by the Phase 6 browser tests: a local server for the fixture pages
and a Browser pointed at a temp profile. Nothing here reaches the network."""
from __future__ import annotations

import contextlib
import functools
import os
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from jobpipe.apply import browser as _browser
from jobpipe.apply.config import ApplyConfig, BrowserConfig

PAGES = Path(__file__).resolve().parent / "fixtures" / "apply" / "pages"

# The cloud session that built this has Chromium at a fixed path and no
# Playwright download; a developer machine has the download. Either works.
if not os.environ.get(_browser.EXECUTABLE_ENV) and Path("/opt/pw-browsers/chromium").exists():
    os.environ[_browser.EXECUTABLE_ENV] = "/opt/pw-browsers/chromium"


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False
    return bool(shutil.which("python"))


needs_chromium = pytest.mark.skipif(
    not _chromium_available(),
    reason="playwright/chromium not installed; the pure modules are still covered")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@contextlib.contextmanager
def serve(directory: Path = PAGES):
    """Serve a directory on 127.0.0.1 at a free port; yields the base URL."""
    handler = functools.partial(_Quiet, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def config_for(tmp_path: Path, **over) -> ApplyConfig:
    browser = BrowserConfig(profile_dir=tmp_path / "profile", headed=False,
                            page_timeout=10, confirmation_timeout=3)
    return ApplyConfig(browser=browser, output_dir=tmp_path / "out", **over)


@contextlib.contextmanager
def open_browser(tmp_path: Path):
    with _browser.Browser(config_for(tmp_path), headless=True) as b:
        yield b

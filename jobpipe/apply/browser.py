"""The runner's browser: its own Chromium profile, never Anish's Chrome.

`launch_persistent_context` so a review-mode run can leave the filled form
on screen for him in a window that survives the runner; the profile holds
no logins, because Greenhouse and Ashby forms need none. Nothing here
changes how the browser presents itself to a page - no stealth, no
fingerprint changes - because bot detection is for deciding to stop, not
for getting past.
"""
from __future__ import annotations

import os
from pathlib import Path

from .config import ApplyConfig

# A different Chromium than the one Playwright downloaded, when the machine
# has one: the cloud session that built this uses /opt/pw-browsers/chromium.
EXECUTABLE_ENV = "JOBPIPE_CHROMIUM"


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:                              # pragma: no cover
        raise RuntimeError(
            "playwright is required for the apply runner: "
            "pip install playwright && python -m playwright install chromium"
        ) from exc
    return sync_playwright


def executable_path() -> str | None:
    path = os.environ.get(EXECUTABLE_ENV)
    return path if path and Path(path).exists() else None


class Browser:
    """One persistent context, opened for a run and closed by the runner -
    or left open on purpose for review."""

    def __init__(self, config: ApplyConfig, headless: bool | None = None):
        self.config = config
        self.headless = (not config.browser.headed) if headless is None else headless
        self._pw = None
        self.context = None

    def __enter__(self):
        self._pw = _playwright()().start()
        profile = Path(self.config.browser.profile_dir)
        profile.mkdir(parents=True, exist_ok=True)
        kwargs = {"headless": self.headless}
        exe = executable_path()
        if exe:
            kwargs["executable_path"] = exe
        self.context = self._pw.chromium.launch_persistent_context(
            str(profile), **kwargs)
        self.context.set_default_timeout(self.config.browser.page_timeout * 1000)
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def new_page(self):
        return self.context.new_page()

    def close(self) -> None:
        if self.context is not None:
            try:
                self.context.close()
            finally:
                self.context = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

"""Playwright session management + network capture.

Uses a *persistent* browser context so a single manual login is reused on
every later run (cookies/profile live in `browser.session_dir`).
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext, Page, sync_playwright

from .config import Config


class Session:
    """A live persistent browser context + page, with optional net capture."""

    def __init__(self, context: BrowserContext, page: Page, capture_path: Path | None):
        self.context = context
        self.page = page
        self._capture_path = capture_path
        self._capture_fh = None
        if capture_path is not None:
            self._capture_fh = capture_path.open("w", encoding="utf-8")
            self._attach_capture()

    # -- network capture ---------------------------------------------------
    def _attach_capture(self) -> None:
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)

    def _write(self, record: dict) -> None:
        if self._capture_fh is None:
            return
        self._capture_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._capture_fh.flush()

    def _on_request(self, request) -> None:
        rt = request.resource_type
        if rt not in ("xhr", "fetch", "document"):
            return
        post = None
        try:
            post = request.post_data
        except Exception:
            pass
        self._write({
            "kind": "request",
            "method": request.method,
            "url": request.url,
            "resource_type": rt,
            "post_data": post,
        })

    def _on_response(self, response) -> None:
        req = response.request
        if req.resource_type not in ("xhr", "fetch"):
            return
        body_preview = None
        try:
            ct = response.headers.get("content-type", "")
            if "json" in ct or "text" in ct:
                body_preview = response.text()[:4000]
        except Exception:
            pass
        self._write({
            "kind": "response",
            "status": response.status,
            "url": response.url,
            "body_preview": body_preview,
        })

    def close(self) -> None:
        if self._capture_fh is not None:
            self._capture_fh.close()
            self._capture_fh = None
        try:
            self.context.close()
        except Exception:
            pass


@contextmanager
def open_session(
    cfg: Config,
    headless: bool | None = None,
    capture: bool = False,
) -> Iterator[Session]:
    """Open (and clean up) a persistent Playwright session."""
    is_headless = cfg.browser.headless if headless is None else headless
    cfg.browser.session_dir.mkdir(parents=True, exist_ok=True)

    capture_path = None
    if capture:
        capture_path = cfg.root / cfg.amazon.capture_file

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(cfg.browser.session_dir),
            headless=is_headless,
            slow_mo=cfg.browser.slow_mo,
            locale=cfg.browser.locale,
            timezone_id=cfg.browser.timezone,
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        # Reuse the first tab if one exists.
        page = context.pages[0] if context.pages else context.new_page()
        # Mild stealth: hide webdriver flag.
        context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        session = Session(context, page, capture_path)
        try:
            yield session
        finally:
            session.close()

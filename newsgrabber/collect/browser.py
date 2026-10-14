"""Optionales Rendern dynamischer Seiten mit Playwright (Chromium, headless).

Playwrights Sync-API ist nicht thread-sicher. Alle Browser-Aufrufe laufen deshalb
über einen eigenen Thread (Executor mit genau einem Worker); Aufrufer aus beliebigen
Threads warten auf das Ergebnis. robots.txt und Rate Limit werden vorab über den
:class:`~newsgrabber.collect.http.HttpClient` geprüft.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import urlsplit

from ..config import PlaywrightConfig
from .http import FetchError

log = logging.getLogger(__name__)

_BLOCKED_RESOURCES = {"image", "media", "font"}


def playwright_installed() -> bool:
    return importlib.util.find_spec("playwright") is not None


class BrowserRenderer:
    def __init__(self, config: PlaywrightConfig, http, user_agent: str):
        self.config = config
        self.http = http
        self.user_agent = user_agent
        self.available = config.enabled and playwright_installed()
        self.pages_rendered = 0
        self._error: Optional[str] = None
        self._executor: Optional[ThreadPoolExecutor] = None
        self._lock = threading.Lock()
        self._pw = self._browser = self._context = None
        if config.enabled and not self.available:
            log.info("Playwright ist nicht installiert – dynamische Seiten werden nur statisch abgerufen "
                     "(installieren: pip install playwright && python -m playwright install chromium)")

    @property
    def usable(self) -> bool:
        return self.available and self._error is None

    def render(self, url: str) -> str:
        """Lädt ``url`` im Browser und liefert das gerenderte HTML."""
        if not self.usable:
            raise FetchError(self._error or "Playwright ist nicht verfügbar")
        self.http.check_robots(url)
        self.http.limiter.wait(urlsplit(url).hostname or "")
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright")
        return self._executor.submit(self._render, url).result()

    # ---------------------------------------------------------- Browser-Thread
    def _start(self) -> None:
        from playwright.sync_api import sync_playwright

        try:
            self._pw = sync_playwright().start()
            options = {"headless": True}
            if self.config.channel:
                options["channel"] = self.config.channel
            if self.config.executable_path:
                options["executable_path"] = self.config.executable_path
            self._browser = self._pw.chromium.launch(**options)
        except Exception as exc:  # noqa: BLE001 – Playwright wirft eigene Fehlertypen
            self._error = ("Browser für Playwright konnte nicht gestartet werden ('python -m playwright install "
                           f"chromium' ausführen oder playwright.channel/executable_path setzen): "
                           f"{str(exc).splitlines()[0]}")
            log.warning(self._error)
            self._shutdown()
            raise FetchError(self._error) from exc
        self._context = self._browser.new_context(user_agent=self.user_agent, locale="de-DE",
                                                  timezone_id="Europe/Berlin")
        self._context.route("**/*", lambda route: route.abort()
                            if route.request.resource_type in _BLOCKED_RESOURCES else route.continue_())

    def _render(self, url: str) -> str:
        if self._browser is None:
            self._start()
        page = self._context.new_page()
        try:
            page.goto(url, wait_until=self.config.wait_until, timeout=self.config.timeout * 1000)
            if self.config.extra_wait_ms:
                page.wait_for_timeout(self.config.extra_wait_ms)
            self.pages_rendered += 1
            return page.content()
        except Exception as exc:  # noqa: BLE001
            raise FetchError(f"Playwright konnte {url} nicht laden: {str(exc).splitlines()[0]}") from exc
        finally:
            page.close()

    def _shutdown(self) -> None:
        for obj, method in ((self._context, "close"), (self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception:  # noqa: BLE001
                    pass
        self._pw = self._browser = self._context = None

    def close(self) -> None:
        if self._executor is not None:
            self._executor.submit(self._shutdown).result()
            self._executor.shutdown(wait=True)
            self._executor = None

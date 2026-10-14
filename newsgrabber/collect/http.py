"""HTTP-Zugriff mit robots.txt-Prüfung, Rate Limits pro Host und Wiederholungsversuchen.

Alle Netzwerkzugriffe der Datensammlung laufen über :class:`HttpClient` – auch
Playwright prüft vorab robots.txt und Rate Limit über diesen Client.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

import requests
from requests.structures import CaseInsensitiveDict

from ..config import CollectionConfig

log = logging.getLogger(__name__)

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class FetchError(Exception):
    """Abruf fehlgeschlagen (nach allen Wiederholungsversuchen)."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class RobotsDisallowed(FetchError):
    """robots.txt untersagt den Abruf."""


@dataclass
class Response:
    url: str  # nach Weiterleitungen
    status: int
    content: bytes
    headers: dict
    encoding: Optional[str] = None

    @property
    def content_type(self) -> str:
        return (self.headers.get("Content-Type") or "").lower()

    @property
    def text(self) -> str:
        encoding = self.encoding or "utf-8"
        try:
            return self.content.decode(encoding, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    def json(self):
        import json
        return json.loads(self.content.decode(self.encoding or "utf-8", errors="replace"))


class HostRateLimiter:
    """Mindestabstand zwischen zwei Anfragen an denselben Host (thread-sicher)."""

    def __init__(self, interval: float):
        self.interval = max(0.0, interval)
        self._next: dict = {}
        self._extra: dict = {}
        self._lock = threading.Lock()

    def set_host_interval(self, host: str, seconds: float) -> None:
        with self._lock:
            self._extra[host] = max(self._extra.get(host, 0.0), seconds)

    def wait(self, host: str) -> float:
        with self._lock:
            interval = max(self.interval, self._extra.get(host, 0.0))
            current = time.monotonic()
            slot = max(current, self._next.get(host, 0.0))
            self._next[host] = slot + interval
        delay = slot - current
        if delay > 0:
            time.sleep(delay)
        return delay


class RobotsCache:
    def __init__(self, client: "HttpClient"):
        self._client = client
        self._parsers: dict = {}
        self._unreachable: dict = {}
        self._locks: dict = {}
        self._lock = threading.Lock()

    def _host_lock(self, key: str) -> threading.Lock:
        with self._lock:
            return self._locks.setdefault(key, threading.Lock())

    def parser(self, url: str) -> Optional[urllib.robotparser.RobotFileParser]:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        with self._host_lock(key):
            if key in self._parsers:
                return self._parsers[key]
            parser = urllib.robotparser.RobotFileParser()
            robots_url = f"{key}/robots.txt"
            try:
                response = self._client.get(robots_url, check_robots=False, retries=1, accept="text/plain,*/*")
                parser.parse(response.text.splitlines())
            except FetchError as exc:
                # Verhalten nach RFC 9309: 4xx = keine Regeln, Server-/Netzwerkfehler = alles gesperrt
                if exc.status is not None and 400 <= exc.status < 500:
                    parser.parse([])
                else:
                    parser.parse(["User-agent: *", "Disallow: /"])
                    self._unreachable[key] = str(exc)
                    log.warning("robots.txt von %s nicht erreichbar (%s) – Host wird in diesem Lauf nicht abgerufen",
                                parts.netloc, exc)
            self._parsers[key] = parser
            if parser is not None:
                delay = parser.crawl_delay(self._client.agent_token)
                if delay:
                    delay = float(delay)
                    capped = min(delay, self._client.config.max_crawl_delay)
                    if delay > capped:
                        log.warning("Crawl-delay von %s (%.0f s) begrenzt auf %.0f s", parts.netloc, delay, capped)
                    self._client.limiter.set_host_interval(parts.hostname or "", capped)
            return parser

    def allowed(self, url: str) -> bool:
        parser = self.parser(url)
        if parser is None:
            return True
        return parser.can_fetch(self._client.agent_token, url)

    def check(self, url: str) -> None:
        if self.allowed(url):
            return
        parts = urlsplit(url)
        reason = self._unreachable.get(f"{parts.scheme}://{parts.netloc}")
        if reason:
            raise FetchError(f"robots.txt nicht erreichbar ({reason})")
        raise RobotsDisallowed(f"robots.txt untersagt den Abruf von {url}")


class HttpClient:
    def __init__(self, config: CollectionConfig):
        self.config = config
        self.limiter = HostRateLimiter(config.per_host_interval)
        self.robots = RobotsCache(self)
        self._local = threading.local()
        self.agent_token = (config.user_agent.split("/")[0].split()[0] or "*").strip()
        self.request_count = 0
        self._count_lock = threading.Lock()

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({
                "User-Agent": self.config.user_agent,
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
            })
            self._local.session = session
        return session

    def allowed(self, url: str) -> bool:
        if not self.config.respect_robots_txt:
            return True
        return self.robots.allowed(url)

    def check_robots(self, url: str) -> None:
        if self.config.respect_robots_txt:
            self.robots.check(url)

    def get(self, url: str, *, check_robots: bool = True, retries: Optional[int] = None,
            accept: str = "*/*", params: Optional[dict] = None, headers: Optional[dict] = None) -> Response:
        if check_robots:
            self.check_robots(url)
        host = urlsplit(url).hostname or ""
        attempts = 1 + (self.config.retries if retries is None else retries)
        max_bytes = int(self.config.max_response_mb * 1024 * 1024)
        last_error: Optional[FetchError] = None
        for attempt in range(1, attempts + 1):
            self.limiter.wait(host)
            with self._count_lock:
                self.request_count += 1
            retry_after: Optional[float] = None
            try:
                request_headers = {"Accept": accept}
                request_headers.update(headers or {})
                with self._session().get(url, params=params, headers=request_headers, timeout=self.config.timeout,
                                         stream=True, allow_redirects=True) as response:
                    status = response.status_code
                    if status in RETRY_STATUS:
                        retry_after = _retry_after(response.headers.get("Retry-After"))
                        raise FetchError(f"HTTP {status} für {url}", status)
                    if status >= 400:
                        # dauerhafte Fehler nicht wiederholen
                        raise _Permanent(FetchError(f"HTTP {status} für {url}", status))
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > max_bytes:
                            raise _Permanent(FetchError(
                                f"Antwort von {url} größer als {self.config.max_response_mb:g} MB – abgebrochen"))
                        chunks.append(chunk)
                    encoding = response.encoding if "charset" in response.headers.get("Content-Type", "").lower() else None
                    return Response(url=response.url, status=status, content=b"".join(chunks),
                                    headers=CaseInsensitiveDict(response.headers), encoding=encoding)
            except _Permanent as exc:
                raise exc.error from None
            except FetchError as exc:
                last_error = exc
            except requests.exceptions.SSLError as exc:
                raise FetchError(f"TLS-Fehler bei {url}: {exc}") from exc
            except requests.exceptions.InvalidURL as exc:
                raise FetchError(f"Ungültige URL {url}: {exc}") from exc
            except requests.exceptions.RequestException as exc:
                last_error = FetchError(f"{type(exc).__name__} bei {url}: {_short(exc)}")
            if attempt < attempts:
                delay = retry_after if retry_after is not None else \
                    self.config.backoff * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                delay = min(delay, 60.0)
                log.info("Abruf von %s fehlgeschlagen (%s) – neuer Versuch %d/%d in %.1f s",
                         url, last_error, attempt + 1, attempts, delay)
                time.sleep(delay)
        assert last_error is not None
        raise last_error


class _Permanent(Exception):
    def __init__(self, error: FetchError):
        super().__init__(str(error))
        self.error = error


def _retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        import email.utils
        try:
            moment = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        import datetime as _dt
        return max(0.0, (moment - _dt.datetime.now(moment.tzinfo)).total_seconds())


def _short(exc: Exception) -> str:
    text = str(exc)
    return text if len(text) <= 160 else text[:157] + "..."

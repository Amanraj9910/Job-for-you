"""Shared polite HTTP client: descriptive UA, per-host throttle, retries."""
from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential


def _user_agent() -> str:
    """Descriptive UA. Set JOBHUNT_CONTACT in .env (an email or URL) so site owners can reach you if needed."""
    import os
    contact = os.environ.get("JOBHUNT_CONTACT", "").strip()
    return "jobhunt/0.2 (personal job search tool; single user" + (f"; contact: {contact}" if contact else "") + ")"


USER_AGENT = _user_agent()
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

_lock = threading.Lock()
_last_hit: dict[str, float] = {}
DEFAULT_INTERVAL = 1.0  # seconds between requests to the same host
HOST_INTERVAL = {"remotive.com": 35.0, "remoteok.com": 5.0}


def _throttle(url: str) -> None:
    host = urlparse(url).hostname or ""
    gap = HOST_INTERVAL.get(host, DEFAULT_INTERVAL)
    with _lock:
        last = _last_hit.get(host, 0.0)
        wait = last + gap - time.monotonic()
        _last_hit[host] = max(time.monotonic(), last + gap)
    if wait > 0:
        time.sleep(wait)


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


class Http:
    def __init__(self, timeout: float = 30.0, browser_ua: bool = False):
        self.client = httpx.Client(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": BROWSER_UA if browser_ua else USER_AGENT,
                     "Accept": "application/json, text/html;q=0.9, */*;q=0.8"})

    @retry(retry=retry_if_exception(_retryable), stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=2, max=20), reraise=True)
    def request(self, method: str, url: str, *, throttle: bool = True, raise_for_status: bool = True,
                **kw) -> httpx.Response:
        if throttle:
            _throttle(url)
        r = self.client.request(method, url, **kw)
        if raise_for_status and r.status_code >= 400 and r.status_code != 404:
            r.raise_for_status()
        return r

    def get(self, url: str, **kw) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> httpx.Response:
        return self.request("POST", url, **kw)

    def get_json(self, url: str, **kw):
        r = self.get(url, **kw)
        if r.status_code == 404:
            return None
        return r.json()

    def close(self) -> None:
        self.client.close()


_default: Http | None = None


def http() -> Http:
    global _default
    if _default is None:
        _default = Http()
    return _default

"""Shared run controls and connection routes.

Control handles stopping and deadlines. Each ConnectionRoute owns one configured
connection route and a RateLimiter, shared by every session on that route.
"""

import ipaddress
import time
from threading import Event, Lock

from curl_cffi import requests

from common.helpers import SessionError
from common.settings import Proxy, Settings
from crawler.walmart_us import new_session

HTTP_PER_MINUTE = 55  # per route; highest rate validated in sustained runs
LOCAL_WORKERS = 4  # one session waits on a response while others send
REQUEST_TIMEOUT = 25
EXIT_IP_CHECK_SECONDS = 60


class Cancelled(Exception):
    """The run was interrupted or reached its time limit."""


class Control:
    """Stop flag and deadline shared by every worker."""

    def __init__(self, max_seconds: float) -> None:
        self.deadline = time.monotonic() + max_seconds
        self.stop = Event()
        self.reason: str | None = None

    def interrupt(self, reason: str) -> None:
        self.reason = self.reason or reason
        self.stop.set()

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def wait_or_stop(self, seconds: float = 0) -> None:
        """Wait; raise Cancelled once the run is stopped or out of time."""
        remaining = self.remaining()
        if remaining > 0:
            wait_seconds = max(0.0, min(seconds, remaining))
            stopped = self.stop.wait(wait_seconds)
            if not stopped and self.remaining() > 0:
                return
        if not self.stop.is_set():
            self.interrupt("Run time limit reached")
        raise Cancelled(self.reason)


class RateLimiter:
    """Evenly spaced request slots, without catch-up bursts."""

    def __init__(self, per_minute: float, control: Control) -> None:
        self.interval = 60 / per_minute
        self.control = control
        self._lock = Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            self.control.wait_or_stop(self._next - time.monotonic())
            self._next = time.monotonic() + self.interval


class ConnectionRoute:
    """One exit IP: its pacing and the sessions opened on it.

    A residential proxy port can rotate its exit IP. The IP is checked
    at most once a minute; when it changes, sessions created on the old
    IP are rebuilt instead of carrying their cookies to a new address.
    """

    def __init__(self, control: Control, proxy: Proxy | None = None):
        self.control = control
        self.proxy = proxy
        self.rate_limiter = RateLimiter(per_minute=HTTP_PER_MINUTE, control=control)
        self._lock = Lock()
        self._exit_ip: str | None = None
        self._checked_at = 0.0
        self._generation = 0

    def new_session(self) -> requests.Session:
        return new_session(self.proxy)

    def acquire(self) -> float:
        """Wait for the next request slot; return the request timeout."""
        self.rate_limiter.wait()
        return min(REQUEST_TIMEOUT, self.control.remaining())

    def exit_generation(self) -> int:
        if self.proxy is None:
            return 0
        with self._lock:
            now = time.monotonic()
            if self._exit_ip is None or (
                now - self._checked_at >= EXIT_IP_CHECK_SECONDS
            ):
                exit_ip = self._probe()
                if self._exit_ip not in (None, exit_ip):
                    self._generation += 1
                self._exit_ip, self._checked_at = exit_ip, now
            return self._generation

    def _probe(self) -> str:
        try:
            with new_session(self.proxy) as probe:
                response = probe.get("https://api.ipify.org?format=json", timeout=8)
                response.raise_for_status()
                return str(ipaddress.ip_address(response.json()["ip"]))
        except Exception as error:
            raise SessionError(
                f"Proxy exit IP check failed: {type(error).__name__}"
            ) from None


def create_connections(settings: Settings, control: Control) -> list[ConnectionRoute]:
    """Create one route per proxy, or one shared local route."""
    if settings.use_proxy:
        return [
            ConnectionRoute(control=control, proxy=proxy) for proxy in settings.proxies
        ]
    return [ConnectionRoute(control=control)]

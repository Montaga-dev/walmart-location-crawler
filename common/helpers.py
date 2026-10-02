"""Shared helpers: crawl errors, value parsing, files and time."""

import math
import re
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

__all__ = [
    "PRODUCT_ID",
    "BlockedError",
    "CrawlError",
    "LocationMismatch",
    "SessionError",
    "TransientError",
    "atomic_write",
    "clocks",
    "invalid_response",
    "to_price",
]

PRODUCT_ID = re.compile(r"[0-9]+")


# =========================
#  ERRORS
# =========================
class CrawlError(RuntimeError):
    """A failed page read; the retry policy decides what happens next."""


class TransientError(CrawlError):
    """Timeout, connection failure or 5xx: retry on the same session."""


class LocationMismatch(CrawlError):
    """The page was rendered for another ZIP or store."""


class SessionError(CrawlError):
    """An unexpected response: the session must be rebuilt."""


class BlockedError(SessionError):
    """A robot challenge or HTTP 429."""

    def __init__(self, message: str, *, captcha: bool = False) -> None:
        super().__init__(message)
        self.captcha = captcha


@contextmanager
def invalid_response(what: str) -> Generator[None]:
    """Missing or malformed fields mean Walmart sent another page."""
    try:
        yield
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise SessionError(
            f"Invalid {what} response: {type(error).__name__}: {error}"
        ) from error


# =========================
#  VALUES
# =========================
def to_price(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Invalid price: {value!r}")
    price = float(value)
    if not math.isfinite(price) or price < 0:
        raise ValueError(f"Invalid price: {value!r}")
    return price


# =========================
#  FILES AND TIME
# =========================
def atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    temporary.replace(path)


def clocks(moment: datetime) -> dict:
    return {
        "utc": moment.astimezone(UTC).isoformat(timespec="seconds"),
        "istanbul": moment.astimezone(ZoneInfo("Europe/Istanbul")).isoformat(
            timespec="seconds"
        ),
    }

"""What to crawl (config.json) and how to connect (.env)."""

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from common.helpers import PRODUCT_ID
from crawler.page_list import listing_url

MAX_SECONDS = 5 * 60 * 60
ENV_KEYS = ("USE_PROXY", "PROXIES")


@dataclass(frozen=True)
class Location:
    zip: str
    name: str | None
    product_requests: int
    category_requests: int


@dataclass(frozen=True)
class Config:
    path: Path
    locations: tuple[Location, ...]
    categories: tuple[str, ...]
    product_file: Path
    bootstrap_product: str
    max_seconds: int
    discovery_max_pages: int

    def product_ids(self) -> list[str]:
        """The shared product pool; every location reads its prefix."""
        ids = json.loads(self.product_file.read_text())
        if not isinstance(ids, list) or not all(
            isinstance(i, str) and PRODUCT_ID.fullmatch(i) for i in ids
        ):
            raise ValueError("product_file must be a JSON list of ID strings")
        if len(set(ids)) != len(ids):
            raise ValueError("product_file contains duplicate IDs")
        needed = max(loc.product_requests for loc in self.locations)
        if len(ids) < needed:
            raise ValueError(f"product_file has {len(ids)} IDs; need {needed}")
        return ids


@dataclass(frozen=True)
class Proxy:
    address: str
    username: str = field(repr=False)
    password: str = field(repr=False)


@dataclass(frozen=True)
class Settings:
    use_proxy: bool
    proxies: tuple[Proxy, ...] = ()

    def redact(self, text: str) -> str:
        """Mask credentials in proxy URLs."""
        return re.sub(r"(https?://)[^\s/]+@", r"\1[REDACTED]@", text)


def load_config(path: Path) -> Config:
    path = path.resolve()
    data = json.loads(path.read_text())
    _check(isinstance(data, dict), "config.json must be a JSON object")
    rows = data.get("locations")
    _check(isinstance(rows, list) and rows, "locations must be a list")
    locations = tuple(_location(row) for row in rows)
    zips = [loc.zip for loc in locations]
    _check(len(set(zips)) == len(zips), "Location ZIP codes must be distinct")
    categories = data.get("categories", [])
    _check(
        isinstance(categories, list)
        and all(isinstance(url, str) for url in categories),
        "categories must be a list of URLs",
    )
    categories = tuple(categories)
    for url in categories:
        listing_url(url, 1)
    _check(
        categories or not any(loc.category_requests for loc in locations),
        "category_requests need at least one category",
    )
    product_file = data.get("product_file")
    _check(
        isinstance(product_file, str) and product_file.strip(),
        "product_file is required",
    )
    bootstrap = data.get("bootstrap_product")
    _check(
        isinstance(bootstrap, str) and PRODUCT_ID.fullmatch(bootstrap),
        "bootstrap_product must be a product ID string",
    )
    seconds = data.get("max_seconds", MAX_SECONDS)
    _check(
        type(seconds) is int and 0 < seconds <= MAX_SECONDS,
        f"max_seconds must be 1..{MAX_SECONDS}",
    )
    pages = data.get("discovery_max_pages", 25)
    _check(type(pages) is int and pages > 0, "discovery_max_pages must be > 0")
    return Config(
        path=path,
        locations=locations,
        categories=categories,
        product_file=path.parent / product_file,
        bootstrap_product=bootstrap,
        max_seconds=seconds,
        discovery_max_pages=pages,
    )


def load_settings(path: Path, environ: Mapping[str, str] = os.environ) -> Settings:
    """Read .env; USE_PROXY and PROXIES in the environment win."""
    values = _read_env(path) if path.exists() else {}
    values.update({key: environ[key] for key in ENV_KEYS if key in environ})
    use_proxy = values.get("USE_PROXY", "false").strip().lower()
    _check(use_proxy in ("true", "false"), "USE_PROXY must be true or false")
    entries = [p.strip() for p in values.get("PROXIES", "").split(",")]
    proxies = tuple(
        _proxy(index, entry) for index, entry in enumerate(filter(None, entries), 1)
    )
    _check(
        use_proxy == "false" or proxies,
        "USE_PROXY=true needs at least one entry in PROXIES",
    )
    return Settings(use_proxy=use_proxy == "true", proxies=proxies)


def _check(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _location(row: object) -> Location:
    _check(isinstance(row, dict), "Each location must be a JSON object")
    zip_code = row.get("zip")
    _check(
        isinstance(zip_code, str) and re.fullmatch(r"[0-9]{5}", zip_code),
        f"Location needs a five-digit ZIP string: {row}",
    )
    product_requests = row.get("product_requests")
    category_requests = row.get("category_requests")
    _check(
        all(
            type(count) is int and count >= 0
            for count in (product_requests, category_requests)
        ),
        f"Request counts must be integers >= 0: {row}",
    )
    return Location(
        zip=zip_code,
        name=row.get("name"),
        product_requests=product_requests,
        category_requests=category_requests,
    )


def _read_env(path: Path) -> dict[str, str]:
    values = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = (part.strip() for part in line.partition("="))
        _check(
            separator and key in ENV_KEYS,
            f".env line {number}: expected USE_PROXY= or PROXIES=",
        )
        values[key] = value.strip("\"'")
    return values


def _proxy(index: int, entry: str) -> Proxy:
    parts = urlsplit(entry)
    try:
        port = parts.port
    except ValueError:
        port = None
    _check(
        parts.scheme == "http"
        and parts.hostname
        and ":" not in parts.hostname
        and port
        and parts.username
        and parts.password
        and parts.path in ("", "/")
        and not parts.query
        and not parts.fragment,
        f"PROXIES entry {index}: expected http://user:pass@host:port",
    )
    return Proxy(
        address=f"http://{parts.hostname}:{port}",
        username=unquote(parts.username),
        password=unquote(parts.password),
    )

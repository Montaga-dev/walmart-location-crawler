"""Walmart US over plain HTTP: guest sessions, location and page reads.

A session is a fresh guest. It gets its cookies from a product page,
stores the ZIP code through the ``UpdatePostalCode`` GraphQL mutation and
then reads server-rendered pages. Every page is checked against the
selected ZIP and store before it is accepted.
"""

import json
import re
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from threading import Event, Lock
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

from curl_cffi import requests

from common.helpers import (
    PRODUCT_ID,
    BlockedError,
    CrawlError,
    LocationMismatch,
    SessionError,
    TransientError,
    invalid_response,
)
from common.settings import Proxy
from crawler.page_list import extract_page_listing_details, listing_url
from crawler.page_product import extract_page_product_detail

if TYPE_CHECKING:
    from common.runtime import ConnectionRoute

# Observed in Chrome on 2026-10-03; Walmart changes them on deploys.
POSTAL_HASH = "0175f7072637b82757e89f50ed35676e89499341cd199d4032350ee9483cfc0e"
UPDATE_POSTAL_CODE_PATH = f"/orchestra/home/graphql/UpdatePostalCode/{POSTAL_HASH}"
PLATFORM_VERSION = "usweb-1.314.0-741096a6c7cee1e21437b39748ca40064a719a39-9301858r"
BLOCKED_PATH = "/blocked"
IMPERSONATE = "chrome150"
MAX_REDIRECTS = 2
REDIRECT_STATUSES = (301, 302, 307, 308)
TRANSIENT_STATUSES = (500, 502, 503, 504)
CHALLENGE_MARKERS = ("robot or human?", "px-captcha")
NEXT_DATA = re.compile(r"""id=["']__NEXT_DATA__["'][^>]*>(.*?)</script>""", re.DOTALL)


def new_session(proxy: Proxy | None = None) -> requests.Session:
    """curl_cffi gives the TLS and HTTP/2 fingerprint of real Chrome."""
    if proxy is None:
        return requests.Session(impersonate=IMPERSONATE, trust_env=False)
    return requests.Session(
        impersonate=IMPERSONATE,
        trust_env=False,
        proxy=proxy.address,
        proxy_auth=(proxy.username, proxy.password),
    )


def decode(text: str, status: int) -> dict:
    """Return the page data or raise the error the retry policy expects.

    HTML pages yield their ``__NEXT_DATA__`` ``initialData`` object.
    Challenge markers are checked first because Walmart can send them
    with HTTP 200 as well as 307 or 412.
    """
    lowered = text.lower()
    if any(marker in lowered for marker in CHALLENGE_MARKERS):
        raise BlockedError("Robot challenge returned", captcha=True)
    html = text.lstrip().startswith("<")
    data = None
    if not html:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
    if isinstance(data, dict) and (
        data.get("blockScript") or BLOCKED_PATH in str(data.get("redirectUrl"))
    ):
        raise BlockedError("Robot challenge returned", captcha=True)
    if status == 429:
        raise BlockedError("HTTP 429")
    if status in TRANSIENT_STATUSES:
        raise TransientError(f"HTTP {status}")
    if status != 200:
        raise SessionError(f"HTTP {status}")
    if html:
        match = NEXT_DATA.search(text)
        if not match:
            raise SessionError("Page data missing from HTML")
        with invalid_response("page"):
            return json.loads(match.group(1))["props"]["pageProps"]["initialData"]
    if not isinstance(data, dict):
        raise SessionError("Expected a JSON object")
    if data.get("errors"):
        raise SessionError("GraphQL returned errors")
    return data


class ProductUrlCache:
    """Canonical product URLs shared by all sessions.

    ``/ip/<id>`` redirects to ``/ip/<slug>/<id>``. The first session that
    reads a product follows the redirect; sessions reading the same
    product at the same time wait for it, so later locations save one
    HTTP request per product.
    """

    def __init__(self, stop: Event | None = None) -> None:
        self.urls: dict[str, str] = {}
        self.stop = stop or Event()
        self._locks: dict[str, Lock] = {}
        self._guard = Lock()

    @contextmanager
    def claim(self, product_id: str) -> Generator[None]:
        if product_id in self.urls:
            yield
            return
        with self._guard:
            lock = self._locks.setdefault(product_id, Lock())
        # Waiting is only an optimisation: give up on stop or after a minute.
        give_up = time.monotonic() + 60
        acquired = False
        while not acquired and not self.stop.is_set() and time.monotonic() < give_up:
            acquired = lock.acquire(timeout=0.2)
        try:
            yield
        finally:
            if acquired:
                lock.release()


class CrawlWalmartUs:
    """One guest session pinned to one ZIP code.

    ``connection`` is the exit IP the session uses: it paces requests, opens
    sessions and reports when a proxy's exit IP has changed.
    """

    BASE_URL = "https://www.walmart.com"  # no trailing slash

    def __init__(
        self,
        connection: "ConnectionRoute",
        product_urls: ProductUrlCache,
        record_http_event: Callable[[dict], None] | None = None,
    ) -> None:
        self.connection = connection
        self.product_urls = product_urls
        self.record_http_event = record_http_event
        self.session: requests.Session | None = None
        self.reset()

    def reset(self) -> None:
        """Drop all cookies and start as a new guest."""
        self.close()
        self.session = self.connection.new_session()
        self.exit_generation: int | None = None
        self.started = False
        self.location: dict = {}
        self.redirected_product_id: str | None = None

    def close(self) -> None:
        if self.session is not None:
            self.session.close()

    def set_location(
        self, postal_code: str, bootstrap_id: str, store_id: str | None = None
    ) -> dict:
        """Store the ZIP in the guest cookies and verify it on a page.

        With ``store_id``, the page must also show that store, so one ZIP
        never mixes results from two stores.
        """
        self.location = {}
        if not self.started:
            # A new guest gets its cookies from the short product URL.
            self._product_page(bootstrap_id, short=True)
            self.started = True
        address = {
            "postalCode": postal_code,
            "zipLocated": False,
            "stateOrProvinceCode": "",
            "stateOrProvinceName": "",
            "countryCode": "",
            "addressType": "",
            "isPoBox": False,
        }
        data = self._request(
            self.BASE_URL + UPDATE_POSTAL_CODE_PATH,
            operation="UpdatePostalCode",
            body={"variables": {"postalAddress": address}},
        )
        with invalid_response("UpdatePostalCode"):
            saved = data["data"]["updatePostalCode"]["postalCode"]
        if saved != postal_code:
            raise SessionError("ZIP update did not match")
        product = self._product_page(bootstrap_id)
        with invalid_response("location"):
            location = product["location"]
            store = str(location["pickupLocation"]["storeId"] or "")
            if (
                location["postalCode"] != postal_code
                or location["intent"] != "SHIPPING"
            ):
                raise LocationMismatch("Product location was not confirmed")
            if not store.isdigit() or not location["city"]:
                raise SessionError("Location store or city is missing")
            if store_id is not None and store != store_id:
                raise LocationMismatch(f"ZIP now maps to store {store}, not {store_id}")
        self.location = {
            "postal_code": postal_code,
            "city": location["city"],
            "store_id": store,
        }
        return dict(self.location)

    def get_page_product(self, product_id: str) -> dict:
        """Fetch a product, verify its location, then extract the case fields."""
        self._require_location()
        product = self._product_page(product_id)
        with invalid_response("product"):
            location = product["location"]
            self._check_location(
                location["postalCode"],
                location["pickupLocation"]["storeId"],
                location["intent"],
            )
            return extract_page_product_detail(
                product, product_id, self.location, self.BASE_URL
            )

    def get_page_listing(self, url: str, page: int) -> dict:
        """Fetch a listing, verify its location, then extract product cards."""
        self._require_location()
        data = self._request(listing_url(url, page))
        with invalid_response("listing"):
            location = data["pageMetadata"]["location"]
            stores = data["searchResult"]["paginationV2"]["pageProperties"]["stores"]
            self._check_location(
                location["postalCode"],
                location["storeId"],
                location["intent"],
            )
            if str(stores) != self.location["store_id"]:
                raise LocationMismatch(f"Listing is for store {stores}")
            return extract_page_listing_details(data, url, page, self.location)

    def _require_location(self) -> None:
        if not self.location:
            raise SessionError("Location is not set")

    def _check_location(self, postal: str, store: object, intent: str) -> None:
        expected = (
            self.location["postal_code"],
            self.location["store_id"],
        )
        actual = (postal, str(store))
        if actual != expected or intent != "SHIPPING":
            raise LocationMismatch(
                f"Page location {actual} ({intent}), expected {expected}"
            )

    def _product_page(self, product_id: str, short: bool = False) -> dict:
        """Read a product page and check that it is the one asked for."""
        self.redirected_product_id = None
        with self.product_urls.claim(product_id):
            cached = None if short else self.product_urls.urls.get(product_id)
            data = self._request(
                cached or f"{self.BASE_URL}/ip/{product_id}",
                product_id=product_id,
            )
            with invalid_response("product"):
                product = data["data"]["product"]
                actual = product["usItemId"]
            if not isinstance(actual, str) or not PRODUCT_ID.fullmatch(actual):
                raise SessionError("Invalid product ID in response")
            if actual == product_id:
                self.product_urls.urls[product_id] = self.page_url
            elif self.redirected_product_id != actual:
                raise SessionError("Product ID did not match")
            return product

    def _request(
        self,
        url: str,
        *,
        product_id: str | None = None,
        operation: str | None = None,
        body: dict | None = None,
    ) -> dict:
        """Send one logical request, following up to two redirects."""
        if operation is None:
            self.page_url = url
        for _ in range(MAX_REDIRECTS + 1):
            timeout = self.connection.acquire()
            self._check_exit_ip()
            event = {
                "operation": operation or "HTML",
                "path": urlsplit(url).path,
                "product_id": product_id,
                "status": None,
            }
            started = time.perf_counter()
            try:
                response = self._send(url, operation, body, timeout)
                event["status"] = response.status_code
                redirect = response.status_code in REDIRECT_STATUSES
                if operation or not redirect:
                    return decode(response.text, response.status_code)
                url = self._follow(url, response, product_id)
                event["redirect"] = urlsplit(url).path
            except CrawlError as error:
                event["error"] = str(error)
                if isinstance(error, BlockedError):
                    event["blocked"] = True
                    event["captcha"] = error.captcha
                raise
            finally:
                event["seconds"] = round(time.perf_counter() - started, 3)
                if self.record_http_event is not None:
                    self.record_http_event(event)
        raise SessionError("Too many redirects")

    def _send(
        self, url: str, operation: str | None, body: dict | None, timeout: float
    ) -> requests.Response:
        try:
            return self.session.request(
                "POST" if body is not None else "GET",
                url,
                headers=self._headers(operation),
                json=body,
                timeout=timeout,
                allow_redirects=False,
            )
        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        ) as error:
            raise TransientError(type(error).__name__) from error
        except requests.exceptions.RequestException as error:
            raise SessionError(type(error).__name__) from error

    def _follow(
        self, url: str, response: requests.Response, product_id: str | None
    ) -> str:
        target = urljoin(url, response.headers.get("location", ""))
        parts = urlsplit(target)
        host = urlsplit(self.BASE_URL).netloc
        if parts.netloc == host and parts.path == BLOCKED_PATH:
            raise BlockedError("Robot challenge returned", captcha=True)
        source = urlsplit(url).path
        listing = source == "/search" or source.startswith("/browse/")
        if listing:
            allowed = parts.path == "/search" or parts.path.startswith("/browse/")
        else:
            allowed = parts.path.startswith("/ip/")
        if parts.scheme != "https" or parts.netloc != host or not allowed:
            raise SessionError(f"Unexpected redirect to {parts.path}")
        if listing:
            with invalid_response("redirect"):
                listing_url(target, 1)
        elif product_id:
            redirected_id = parts.path.rsplit("/", 1)[-1]
            if PRODUCT_ID.fullmatch(redirected_id):
                self.redirected_product_id = redirected_id
        self.page_url = target
        return target

    def _check_exit_ip(self) -> None:
        generation = self.connection.exit_generation()
        if self.exit_generation is None:
            self.exit_generation = generation
        elif generation != self.exit_generation:
            raise SessionError("Proxy exit IP changed; new session needed")

    def _headers(self, operation: str | None) -> dict[str, str | None]:
        headers = {"accept-language": "en-US,en;q=0.9"}
        if operation is None:
            # curl_cffi adds Chrome's navigation headers to page loads.
            return headers
        return {
            **headers,
            "accept": "application/json",
            "content-type": "application/json",
            "origin": self.BASE_URL,
            "referer": self.page_url,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            # Remove the navigation defaults from API calls.
            "sec-fetch-user": None,
            "upgrade-insecure-requests": None,
            "tenant-id": "elh9ie",
            "wm_mp": "true",
            "x-o-bu": "WALMART-US",
            "x-o-mart": "B2C",
            "x-o-platform": "rweb",
            "x-o-platform-version": PLATFORM_VERSION,
            "x-o-segment": "oaoh",
            "x-o-ccm": "server",
            "x-apollo-operation-name": operation,
            "x-o-gql-query": f"mutation {operation}",
        }

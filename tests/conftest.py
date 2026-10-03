"""Offline fakes: no test sends a request to Walmart."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from crawler.walmart_us import UPDATE_POSTAL_CODE_PATH, CrawlWalmartUs, ProductUrlCache

FIXTURES = Path(__file__).parent / "fixtures"
PRODUCT_ID = "765224053"
CANONICAL = (
    "/ip/IronMax-13Amp-Corded-Scarifier-15-Electric-Lawn-Dethatcher"
    "-w-50L-Collection-Bag-Orange/765224053"
)
SEARCH_URL = "https://www.walmart.com/search?q=nike&facet=fulfillment_method%3AShipping"
MUTATION = UPDATE_POSTAL_CODE_PATH
NEW_YORK = {"postal_code": "10001", "city": "New York", "store_id": "3520"}
SACRAMENTO = {"postal_code": "95829", "city": "Sacramento", "store_id": "3081"}


@pytest.fixture
def product_page() -> dict:
    """Product page data for ZIP 10001 / store 3520."""
    return json.loads((FIXTURES / "product.json").read_text())


@pytest.fixture
def search_page() -> dict:
    """Search page data (q=nike) for ZIP 95829 / store 3081."""
    return json.loads((FIXTURES / "search_listing.json").read_text())


def html(page_data: dict) -> str:
    """Wrap page data the way Walmart renders it."""
    next_data = {"props": {"pageProps": {"initialData": page_data}}}
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(next_data)}</script></html>"
    )


def response(status: int = 200, text: str = "", location: str | None = None):
    headers = {"location": location} if location else {}
    return SimpleNamespace(status_code=status, text=text, headers=headers)


class FakeSession:
    """Answers requests from a URL path -> response list (FIFO)."""

    def __init__(self, routes: dict) -> None:
        self.routes = {path: list(answers) for path, answers in routes.items()}
        self.sent: list[SimpleNamespace] = []
        self.closed = False

    def request(self, method, url, headers=None, json=None, **kwargs):
        from urllib.parse import urlsplit

        path = urlsplit(url).path
        self.sent.append(SimpleNamespace(method=method, url=url, headers=headers))
        answers = self.routes[path]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def close(self) -> None:
        self.closed = True


class FakeConnectionRoute:
    def __init__(self, *sessions: FakeSession) -> None:
        self.sessions = list(sessions)
        self.generation = 0
        self.opened = 0

    def new_session(self):
        self.opened += 1
        return self.sessions.pop(0) if len(self.sessions) > 1 else self.sessions[0]

    def acquire(self) -> float:
        return 25

    def exit_generation(self) -> int:
        return self.generation


def zip_saved(zip_code: str):
    return response(
        200, json.dumps({"data": {"updatePostalCode": {"postalCode": zip_code}}})
    )


def walmart_routes(product_page: dict, *, zip_code: str = "10001") -> dict:
    """A healthy guest: redirect, ZIP update, product pages."""
    page = copy.deepcopy(product_page)
    page["data"]["product"]["location"]["postalCode"] = zip_code
    return {
        f"/ip/{PRODUCT_ID}": [response(301, location=CANONICAL)],
        CANONICAL: [response(200, html(page))],
        MUTATION: [zip_saved(zip_code)],
    }


def client_for(routes: dict, urls: ProductUrlCache | None = None) -> CrawlWalmartUs:
    return CrawlWalmartUs(
        FakeConnectionRoute(FakeSession(routes)), urls or ProductUrlCache()
    )


def located(crawl: CrawlWalmartUs, location: dict = NEW_YORK) -> CrawlWalmartUs:
    crawl.location = dict(location)
    crawl.started = True
    return crawl

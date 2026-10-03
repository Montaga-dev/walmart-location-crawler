"""Product page: case fields and reading a product for the current ZIP."""

import copy
import time

import pytest
from conftest import (
    CANONICAL,
    NEW_YORK,
    PRODUCT_ID,
    FakeConnectionRoute,
    FakeSession,
    client_for,
    html,
    located,
    response,
    walmart_routes,
)

from common.helpers import LocationMismatch, SessionError
from crawler.page_product import extract_page_product_detail
from crawler.walmart_us import CrawlWalmartUs, ProductUrlCache


def product_of(page: dict) -> dict:
    return page["data"]["product"]


def extract(page: dict) -> dict:
    return extract_page_product_detail(
        product_of(page), PRODUCT_ID, NEW_YORK, CrawlWalmartUs.BASE_URL
    )


def test_product_fields(product_page):
    record = extract(product_page)
    assert record == {
        "location_name": "New York",
        "location_id": "3520",
        "postal_code": "10001",
        "product_id": PRODUCT_ID,
        "product_title": product_of(product_page)["name"],
        "product_price": 139.99,
        "product_price_2": 229.0,
        "in_stock": True,
        "product_url": "https://www.walmart.com"
        + product_of(product_page)["canonicalUrl"],
    }


@pytest.mark.parametrize(
    ("options", "in_stock"),
    [
        ([{"type": "PICKUP", "availabilityStatus": "IN_STOCK"}], False),
        (
            [
                {"type": "SHIPPING", "availabilityStatus": "OUT_OF_STOCK"},
                {"type": "SHIPPING", "availabilityStatus": "IN_STOCK"},
            ],
            False,
        ),
        (None, False),
    ],
)
def test_stock_is_the_first_shipping_option(product_page, options, in_stock):
    product_of(product_page)["fulfillmentOptions"] = options
    record = extract(product_page)
    assert record["in_stock"] is in_stock


def test_missing_prices_are_null(product_page):
    product_of(product_page)["priceInfo"] = {"currentPrice": None, "wasPrice": ""}
    record = extract(product_page)
    assert record["product_price"] is None
    assert record["product_price_2"] is None


def test_variant_redirect_keeps_requested_id(product_page):
    product_of(product_page)["usItemId"] = "765224054"
    record = extract(product_page)
    assert record["product_id"] == PRODUCT_ID
    assert record["redirected_product_id"] == "765224054"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("priceInfo", None),
        ("canonicalUrl", "https://example.com/ip/1"),
        ("name", ""),
        ("fulfillmentOptions", [{"type": "SHIPPING", "availabilityStatus": "?"}]),
    ],
)
def test_unreadable_product_is_rejected(product_page, field, value):
    product_of(product_page)[field] = value
    with pytest.raises(ValueError):
        extract(product_page)


def test_product_is_checked_against_the_location(product_page):
    routes = walmart_routes(product_page)
    client = located(client_for(routes))
    assert client.get_page_product(PRODUCT_ID)["product_price"] == 139.99
    product_page["data"]["product"]["location"]["pickupLocation"]["storeId"] = "1"
    routes[CANONICAL] = [response(200, html(product_page))]
    with pytest.raises(LocationMismatch, match="expected"):
        located(client_for(routes)).get_page_product(PRODUCT_ID)


def test_canonical_url_is_shared_between_sessions(product_page):
    urls = ProductUrlCache()
    first = located(client_for(walmart_routes(product_page), urls))
    first.get_page_product(PRODUCT_ID)
    second_session = FakeSession(walmart_routes(product_page))
    second = located(CrawlWalmartUs(FakeConnectionRoute(second_session), urls))
    second.get_page_product(PRODUCT_ID)
    assert [r.url for r in second_session.sent] == [
        "https://www.walmart.com" + CANONICAL
    ]


def test_waiting_for_a_product_url_stops_with_the_run():
    urls = ProductUrlCache()
    with urls.claim(PRODUCT_ID):  # another session is resolving it
        urls.stop.set()
        started = time.monotonic()
        with urls.claim(PRODUCT_ID):
            pass
    assert time.monotonic() - started < 1


def test_variant_redirect_is_accepted_but_not_cached(product_page):
    variant = copy.deepcopy(product_page)
    variant["data"]["product"]["usItemId"] = "765224054"
    routes = {
        f"/ip/{PRODUCT_ID}": [response(301, location="/ip/Variant/765224054")],
        "/ip/Variant/765224054": [response(200, html(variant))],
        "/ip/1": [response(200, html(variant))],
    }
    urls = ProductUrlCache()
    client = located(client_for(routes, urls))
    record = client.get_page_product(PRODUCT_ID)
    assert record["redirected_product_id"] == "765224054"
    assert PRODUCT_ID not in urls.urls
    # The previous redirect must not authorize a different product's response.
    with pytest.raises(SessionError, match="did not match"):
        client.get_page_product("1")


def test_other_product_without_redirect_is_rejected(product_page):
    product_page["data"]["product"]["usItemId"] = "1"
    routes = {f"/ip/{PRODUCT_ID}": [response(200, html(product_page))]}
    with pytest.raises(SessionError, match="did not match"):
        located(client_for(routes)).get_page_product(PRODUCT_ID)

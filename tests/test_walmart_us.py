"""CrawlWalmartUs: response decoding, location setup, redirects, exit IP."""

import json
from urllib.parse import urlsplit

import pytest
from conftest import (
    CANONICAL,
    MUTATION,
    NEW_YORK,
    PRODUCT_ID,
    FakeConnectionRoute,
    FakeSession,
    client_for,
    html,
    located,
    response,
    walmart_routes,
    zip_saved,
)
from curl_cffi import requests

from common.helpers import BlockedError, LocationMismatch, SessionError, TransientError
from crawler.walmart_us import CrawlWalmartUs, ProductUrlCache, decode


@pytest.mark.parametrize(
    ("status", "body", "error", "captcha"),
    [
        (200, "<html>Robot or human?</html>", BlockedError, True),
        (307, '<div id="px-captcha"></div>', BlockedError, True),
        (412, '{"blockScript": "/px/captcha.js"}', BlockedError, True),
        (200, '{"redirectUrl": "/blocked?url=x"}', BlockedError, True),
        (429, "", BlockedError, False),
        (503, "", TransientError, None),
        (404, "<html></html>", SessionError, None),
        (200, "<html>no data</html>", SessionError, None),
        (200, '{"errors": [{"message": "x"}]}', SessionError, None),
    ],
)
def test_decode_classifies_failures(status, body, error, captcha):
    with pytest.raises(error) as raised:
        decode(body, status)
    if captcha is not None:
        assert raised.value.captcha is captcha


def test_decode_returns_page_data(product_page):
    assert decode(html(product_page), 200) == product_page


def test_set_location_uses_short_url_cookies_then_verifies(product_page):
    session = FakeSession(walmart_routes(product_page))
    client = CrawlWalmartUs(FakeConnectionRoute(session), ProductUrlCache())
    assert client.set_location("10001", PRODUCT_ID) == NEW_YORK
    assert [(r.method, urlsplit(r.url).path) for r in session.sent] == [
        ("GET", f"/ip/{PRODUCT_ID}"),
        ("GET", CANONICAL),
        ("POST", MUTATION),
        ("GET", CANONICAL),
    ]
    mutation = session.sent[2].headers
    assert mutation["x-apollo-operation-name"] == "UpdatePostalCode"
    assert mutation["referer"] == "https://www.walmart.com" + CANONICAL


def test_location_must_be_confirmed_on_the_page(product_page):
    routes = walmart_routes(product_page)  # pages keep rendering ZIP 10001
    routes[MUTATION] = [zip_saved("90001")]
    with pytest.raises(LocationMismatch):
        client_for(routes).set_location("90001", PRODUCT_ID)


def test_pinned_store_must_not_change(product_page):
    client = client_for(walmart_routes(product_page))
    with pytest.raises(LocationMismatch, match="store 3520, not 9999"):
        client.set_location("10001", PRODUCT_ID, store_id="9999")
    assert client.location == {}


@pytest.mark.parametrize(
    ("target", "error"),
    [
        ("https://www.walmart.com/blocked?url=x", BlockedError),
        ("https://example.com/ip/1", SessionError),
        ("/account/login", SessionError),
    ],
)
def test_redirects_outside_the_flow_are_rejected(target, error):
    routes = {f"/ip/{PRODUCT_ID}": [response(307, location=target)]}
    client = located(client_for(routes))
    with pytest.raises(error):
        client.get_page_product(PRODUCT_ID)
    assert len(client.connection.sessions[0].sent) == 1


def test_network_errors_are_transient():
    routes = {f"/ip/{PRODUCT_ID}": [requests.exceptions.Timeout("slow")]}
    with pytest.raises(TransientError):
        located(client_for(routes)).get_page_product(PRODUCT_ID)


def test_changed_exit_ip_stops_the_old_session(product_page):
    client = located(client_for(walmart_routes(product_page)))
    client.get_page_product(PRODUCT_ID)
    client.connection.generation += 1
    with pytest.raises(SessionError, match="exit IP changed"):
        client.get_page_product(PRODUCT_ID)
    assert len(client.connection.sessions[0].sent) == 2  # nothing sent the 2nd time


def test_http_events_are_observed(product_page):
    events = []
    client = CrawlWalmartUs(
        FakeConnectionRoute(FakeSession(walmart_routes(product_page))),
        ProductUrlCache(),
        events.append,
    )
    located(client).get_page_product(PRODUCT_ID)
    assert [(e["status"], e.get("redirect")) for e in events] == [
        (301, CANONICAL),
        (200, None),
    ]
    assert json.dumps(events)

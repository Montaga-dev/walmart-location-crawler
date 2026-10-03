"""Category and search pages: card fields, URL rules and listing reads."""

import pytest
from conftest import (
    SACRAMENTO,
    SEARCH_URL,
    client_for,
    html,
    located,
    response,
)

from common.helpers import LocationMismatch, SessionError
from crawler.page_list import extract_page_listing_details, listing_url


def extract(page: dict, url: str = SEARCH_URL) -> dict:
    return extract_page_listing_details(page, url, 1, SACRAMENTO)


def test_listing_cards_in_page_order(search_page):
    result = extract(search_page)
    records = result["records"]
    # Ads, the takeover tile and the secondary carousel are not cards.
    assert [r["product_id"] for r in records] == [
        "19288557285",
        "2624964791",
        "9861071882",
    ]
    assert [r["position"] for r in records] == [1, 2, 3]
    assert [r["product_price"] for r in records] == [69.99, 137.45, 69.87]
    assert {r["brand"] for r in records} == {"Nike"}
    assert records[0]["product_image_url"].startswith("https://i5.walmartimages")
    assert records[0]["location_id"] == "3081"
    assert result["max_page"] == 13


def test_listing_brand_alias_sponsored_flag_and_missing_price(search_page):
    first = search_page["searchResult"]["itemStacks"][0]["items"][0]
    first.update(brand=None, isSponsoredFlag=True, priceInfo={})
    record = extract(search_page)["records"][0]
    assert record["brand"] == "Nike"  # productBrand
    assert record["sponsored"] is True
    assert record["product_price"] is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("query", "adidas"), ("facet", "fulfillment_method:Pickup"), ("page", 2)],
)
def test_listing_must_be_the_requested_page(search_page, field, value):
    search_page["searchResult"]["paginationV2"]["pageProperties"][field] = value
    with pytest.raises(ValueError, match="mismatch"):
        extract(search_page)


@pytest.mark.parametrize("product_id", [19288557285, "1928855728a", ""])
def test_listing_product_ids_must_be_digit_strings(search_page, product_id):
    search_page["searchResult"]["itemStacks"][0]["items"][0]["usItemId"] = product_id
    with pytest.raises(ValueError, match="product ID"):
        extract(search_page)


def test_browse_listing_checks_category(search_page):
    url = "https://www.walmart.com/browse/garden/123_456"
    props = search_page["searchResult"]["paginationV2"]["pageProperties"]
    props.update(cat_id="123_456", query=None, facet=None)
    assert extract(search_page, url)["records"]
    props["cat_id"] = "789"
    with pytest.raises(ValueError, match="category mismatch"):
        extract(search_page, url)


def test_primary_stacks_keep_page_order(search_page):
    props = search_page["searchResult"]["paginationV2"]["pageProperties"]
    props["primaryStacksToMatch"].reverse()
    records = extract(search_page)["records"]
    assert records[0]["product_id"] == "19288557285"
    props["primaryStacksToMatch"][0]["matchIndex"] = 99
    with pytest.raises(ValueError, match="primary listing index"):
        extract(search_page)


def test_empty_listing_is_rejected(search_page):
    search_page["searchResult"]["itemStacks"] = []
    with pytest.raises(ValueError, match="Empty listing"):
        extract(search_page)


def test_listing_url_replaces_page_and_keeps_filters():
    assert listing_url(SEARCH_URL + "&page=7", 2) == (
        "https://www.walmart.com/search?q=nike"
        "&facet=fulfillment_method%3AShipping&page=2"
    )
    assert listing_url("https://www.walmart.com/browse/a/b/5428_1102182", 3) == (
        "https://www.walmart.com/browse/a/b/5428_1102182?page=3"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/search?q=nike",
        "http://www.walmart.com/search?q=nike",
        "https://www.walmart.com@evil.test/search?q=nike",
        "https://www.walmart.com/search?q=",
        "https://www.walmart.com/search?q=nike&q=adidas",
        "https://www.walmart.com/search?q=nike&unknown=1",
        "https://www.walmart.com/search?q=nike#top",
        "https://www.walmart.com/browse/garden/not-an-id",
        "https://www.walmart.com/account",
    ],
)
def test_listing_url_rejects_other_urls(url):
    with pytest.raises(ValueError):
        listing_url(url, 1)


def test_listing_is_checked_against_the_store(search_page):
    routes = {"/search": [response(200, html(search_page))]}
    client = located(client_for(routes), SACRAMENTO)
    assert len(client.get_page_listing(SEARCH_URL, 1)["records"]) == 3
    search_page["searchResult"]["paginationV2"]["pageProperties"]["stores"] = "1"
    routes = {"/search": [response(200, html(search_page))]}
    with pytest.raises(LocationMismatch):
        located(client_for(routes), SACRAMENTO).get_page_listing(SEARCH_URL, 1)


def test_malformed_page_needs_a_new_session(search_page):
    del search_page["searchResult"]["paginationV2"]
    routes = {"/search": [response(200, html(search_page))]}
    with pytest.raises(SessionError, match="Invalid listing"):
        located(client_for(routes), SACRAMENTO).get_page_listing(SEARCH_URL, 1)

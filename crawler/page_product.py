"""Product page: the case fields of one product for the selected ZIP.

The page data is the ``__NEXT_DATA__`` JSON of ``/ip/<slug>/<id>``.
"""

from urllib.parse import urljoin, urlsplit

from common.helpers import to_price

__all__ = ["extract_page_product_detail"]

FULFILLMENT = "SHIPPING"
STOCK_STATUSES = ("IN_STOCK", "OUT_OF_STOCK", "NOT_AVAILABLE")


def extract_page_product_detail(
    product: dict, product_id: str, location: dict, base_url: str
) -> dict:
    """Extract product fields using the supplied location, without a session."""
    shipping = [
        option
        for option in product.get("fulfillmentOptions") or []
        if option["type"] == FULFILLMENT
    ]
    stock = shipping[0]["availabilityStatus"] if shipping else "NOT_AVAILABLE"
    if stock not in STOCK_STATUSES:
        raise ValueError(f"Unknown stock status: {stock!r}")

    prices = product["priceInfo"]
    if not isinstance(prices, dict):
        raise ValueError("Missing priceInfo")
    current = prices.get("currentPrice") or {}
    was = prices.get("wasPrice")

    url = urlsplit(urljoin(base_url, product["canonicalUrl"]))
    if url.scheme != "https" or url.netloc != urlsplit(base_url).netloc:
        raise ValueError(f"Unexpected product URL: {url.geturl()}")
    if not product["name"]:
        raise ValueError("Product title is missing")

    record = {
        "location_name": location["city"],
        "location_id": location["store_id"],
        "postal_code": location["postal_code"],
        "product_id": product_id,
        "product_title": product["name"],
        "product_price": to_price(current.get("price")),
        "product_price_2": (
            to_price(was.get("price")) if isinstance(was, dict) else None
        ),
        "in_stock": stock == "IN_STOCK",
        "product_url": url._replace(query="", fragment="").geturl(),
    }
    if product["usItemId"] != product_id:
        record["redirected_product_id"] = product["usItemId"]
    return record

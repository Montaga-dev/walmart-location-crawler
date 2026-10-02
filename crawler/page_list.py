"""Category and search pages: product cards in on-page order.

The page data is the ``__NEXT_DATA__`` JSON of ``/browse/...`` or
``/search?q=...``. Parsing uses the supplied location and needs no session.
"""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from common.helpers import PRODUCT_ID, to_price

__all__ = ["extract_page_listing_details", "listing_url"]

HOST = "www.walmart.com"
BROWSE_PREFIX = "/browse/"
SEARCH_PATH = "/search"
URL_PARAMS = {"q", "facet", "sort", "page"}
# URL parameter -> pageProperties field that must echo it.
FILTERS = (("q", "query"), ("facet", "facet"), ("sort", "sort"))
AD_TYPES = ("AdPlaceholder", "TileTakeOverProductPlaceholder")
SPONSORED_FLAGS = ("sponsoredProduct", "isSponsored", "isSponsoredFlag")
PRICE_LINES = ("DISCOUNTED_PRICE", "CURRENT_PRICE")
CATEGORY_ID = re.compile(r"[0-9]+(?:_[0-9]+)*")


def listing_url(url: str, page: int) -> str:
    """Validate a browse or search URL and point it at ``page``."""
    parts = urlsplit(url)
    browse = parts.path.startswith(BROWSE_PREFIX) and CATEGORY_ID.fullmatch(
        parts.path.rsplit("/", 1)[-1]
    )
    if (
        parts.scheme != "https"
        or parts.netloc != HOST
        or parts.fragment
        or not (browse or parts.path == SEARCH_PATH)
    ):
        raise ValueError(f"Expected a Walmart browse or search URL: {url}")
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    params = dict(pairs)
    if len(params) != len(pairs) or set(params) - URL_PARAMS:
        raise ValueError(f"Unsupported or repeated URL parameter: {url}")
    if parts.path == SEARCH_PATH and not params.get("q", "").strip():
        raise ValueError(f"Search URL needs a q parameter: {url}")
    if type(page) is not int or page < 1:
        raise ValueError("Page number must be a positive integer")
    params["page"] = str(page)
    return parts._replace(query=urlencode(params)).geturl()


def _is_sponsored(item: dict) -> bool:
    """Only explicit ad markers count; titles never do."""
    return bool(
        any(item.get(flag) for flag in SPONSORED_FLAGS)
        or item.get("__typename") in AD_TYPES
    )


def _validate_listing(result: dict, url: str, page: int) -> tuple[dict, int]:
    """Confirm the requested category, page and filters before reading cards."""
    errors = result.get("errorResponse") or {}
    if errors.get("errors") or errors.get("errorCodes"):
        raise ValueError("Listing returned errors")
    pagination = result["paginationV2"]
    props = pagination["pageProperties"]
    if int(props["page"]) != page:
        raise ValueError("Listing page mismatch")
    requested = urlsplit(url)
    if requested.path.startswith(BROWSE_PREFIX) and (
        props["cat_id"] != requested.path.rsplit("/", 1)[-1]
    ):
        raise ValueError("Listing category mismatch")
    params = dict(parse_qsl(requested.query))
    for key, field in FILTERS:
        if key in params and props.get(field) != params[key]:
            raise ValueError("Listing query or filter mismatch")
    max_page = int(pagination["maxPage"])
    if max_page < 1:
        raise ValueError("Invalid maximum page")
    return props, max_page


def _parse_listing_card(item: dict, props: dict, location: dict) -> dict:
    """Validate one product card and extract its location and product fields."""
    if item["__typename"] != "Product":
        raise ValueError(f"Unknown listing item {item['__typename']}")
    product_id = item["usItemId"]
    if not isinstance(product_id, str) or not PRODUCT_ID.fullmatch(product_id):
        raise ValueError(f"Invalid product ID: {product_id!r}")
    if not item["name"]:
        raise ValueError("Product title is missing")
    brand = item.get("brand") or item.get("productBrand") or None
    if brand is not None and not isinstance(brand, str):
        raise ValueError(f"Invalid brand: {brand!r}")
    return {
        "category_id": props["cat_id"],
        "postal_code": location["postal_code"],
        "location_name": location["city"],
        "location_id": location["store_id"],
        "product_id": product_id,
        "product_title": item["name"],
        "product_image_url": (item.get("imageInfo") or {}).get("thumbnailUrl"),
        "brand": brand,
        "product_price": _listing_price(item),
    }


def _primary_stacks(result: dict) -> list[dict]:
    """The result grid, without carousels or secondary stacks."""
    stacks = result["itemStacks"]
    if not stacks:
        raise ValueError("Empty listing payload")
    primary = result["paginationV2"]["pageProperties"].get("primaryStacksToMatch")
    if primary is not None:
        indices = {e["matchIndex"] for e in primary if e["isPrimaryStack"]}
        if any(type(i) is not int or not 0 <= i < len(stacks) for i in indices):
            raise ValueError("Invalid primary listing index")
        stacks = [s for i, s in enumerate(stacks) if i in indices]
    else:
        stacks = [
            s
            for s in stacks
            if s["meta"]["layoutEnum"] == "GRID"
            and s["meta"].get("subType") != "SECONDARY_STACK"
        ]
    if not stacks or any(s["meta"]["layoutEnum"] != "GRID" for s in stacks):
        raise ValueError("Unsupported listing layout")
    return stacks


def _listing_price(item: dict) -> float | None:
    prices = item.get("priceInfo") or {}
    current = (prices.get("currentPrice") or {}).get("price")
    if current is not None:
        return to_price(current)
    lines = (prices.get("priceDetails") or {}).get("priceLines") or []
    for kind in PRICE_LINES:
        values = []
        for line in lines:
            if line["lineType"] != kind:
                continue
            for value in line["values"]:
                if value["key"] == "PRICE":
                    values.append(value["value"])
        if len(values) > 1:
            raise ValueError("Ambiguous listing price")
        if values:
            return to_price(values[0])
    return None


def extract_page_listing_details(
    data: dict, url: str, page: int, location: dict
) -> dict:
    """Extract product cards in page order using the supplied location."""
    result = data["searchResult"]
    props, max_page = _validate_listing(result, url, page)
    page_url = listing_url(url, page)
    records = []
    for stack in _primary_stacks(result):
        sponsored_stack = _is_sponsored(stack.get("meta") or {})
        for item in stack["items"]:
            if item["__typename"] in AD_TYPES:
                continue
            record = _parse_listing_card(item, props, location)
            record["position"] = len(records) + 1
            record["page_no"] = page
            record["sponsored"] = sponsored_stack or _is_sponsored(item)
            record["listing_url"] = page_url
            record["search_query"] = props.get("query") or None
            records.append(record)
    if not records:
        raise ValueError("No products in the listing")
    return {"records": records, "max_page": max_page}

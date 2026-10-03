"""Build a balanced product ID pool from category pages.

    python -m common.discovery                 uses config.json
    python -m common.discovery --config FILE   uses another config

Only needed for a new product sample; the repository already ships one.
"""

import argparse
import json
import logging
import signal
import sys
from pathlib import Path

from common.helpers import atomic_write
from common.journal import RunFolder
from common.location import LocationAbandoned, LocationCrawler
from common.runtime import HTTP_PER_MINUTE, Cancelled, Control, create_connections
from common.settings import Config, Settings, load_config, load_settings
from crawler.walmart_us import ProductUrlCache

log = logging.getLogger("crawler")
ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


def discover(
    config: Config, settings: Settings, run: RunFolder, control: Control
) -> bool:
    """Collect an equal share of unique organic IDs from every category."""
    location = config.locations[0]
    target = max(1, max(loc.product_requests for loc in config.locations))
    share, extra = divmod(target, len(config.categories))
    connection = create_connections(settings=settings, control=control)[0]
    crawler = LocationCrawler(
        location=location,
        connection=connection,
        run=run,
        product_urls=ProductUrlCache(stop=control.stop),
        bootstrap_product=config.bootstrap_product,
    )
    found: dict[str, None] = {}
    try:
        crawler.connect()
        for index, url in enumerate(config.categories):
            quota = share
            if index < extra:
                quota += 1
            if not quota:
                continue
            complete = _discover_category(
                crawler=crawler,
                url=url,
                quota=quota,
                found=found,
                max_pages=config.discovery_max_pages,
                total_target=target,
            )
            if not complete:
                return False
    except (Cancelled, LocationAbandoned) as stop:
        log.info(f"Discovery stopped: {stop}")
        return False
    finally:
        crawler.close()
    ids = list(found)[:target]
    run.write({"event": "discovery_end", "found": len(ids), "target": target})
    if len(ids) < target:
        log.info(f"Discovery found {len(ids)}/{target} IDs; file unchanged")
        return False
    atomic_write(config.product_file, json.dumps(ids, indent=2))
    log.info(f"Wrote {len(ids)} product IDs to {config.product_file}")
    return True


def _discover_category(
    crawler: LocationCrawler,
    url: str,
    quota: int,
    found: dict[str, None],
    max_pages: int,
    total_target: int,
) -> bool:
    """Fill one category's quota, preserving global discovery order and uniqueness."""
    selected = []
    for page in range(1, max_pages + 1):
        result = crawler.read(kind="category", target=url, page=page)
        if result is None:
            return False
        for record in result["records"]:
            product_id = record["product_id"]
            if record["sponsored"] or product_id in found:
                continue
            found[product_id] = None
            selected.append(product_id)
            if len(selected) == quota:
                break
        log.info(
            f"DISCOVER {url} page {page}: {len(selected)}/{quota} IDs "
            f"({len(found)}/{total_target} total)"
        )
        if len(selected) == quota or page >= result["max_page"]:
            break
    crawler.run.write(
        {
            "event": "discovery_category",
            "postal_code": crawler.zip,
            "category_url": url,
            "target": quota,
            "product_ids": selected,
        }
    )
    if len(selected) < quota:
        log.info(
            f"Discovery category has {len(selected)}/{quota} unique "
            f"organic IDs: {url}; file unchanged"
        )
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build product_ids.json.")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        settings = load_settings(ENV_FILE)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if not config.categories:
        parser.error("discovery needs categories in config.json")

    # Discovery reads at most this many category pages at the first ZIP.
    first_zip = config.locations[0].zip
    pages = len(config.categories) * config.discovery_max_pages
    targets = {first_zip: {"product": 0, "category": pages}}

    control = Control(max_seconds=config.max_seconds)
    stopped_by = []

    def stop(signum: int, frame: object) -> None:
        stopped_by.append(signum)
        control.interrupt(f"Stopped by {signal.Signals(signum).name}")

    run = RunFolder(
        results=config.path.parent / "results",
        command="discover",
        config={
            "locations": [
                {
                    "zip": location.zip,
                    "name": location.name,
                    "product_requests": location.product_requests,
                    "category_requests": location.category_requests,
                }
                for location in config.locations
            ],
            "categories": list(config.categories),
            "product_file": config.product_file.name,
            "use_proxy": settings.use_proxy,
            "proxy_count": len(settings.proxies) if settings.use_proxy else 0,
            "sessions_per_ip": 1,
            "http_per_minute_per_ip": HTTP_PER_MINUTE,
            "max_seconds": config.max_seconds,
        },
        targets=targets,
        redact=settings.redact,
    )
    previous_handlers = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, stop)
        with run:
            try:
                run.complete = discover(
                    config=config, settings=settings, run=run, control=control
                )
            finally:
                run.interrupted = bool(stopped_by)
                run.stop_reason = control.reason
    except Exception:
        return 1  # the run folder records the error and writes the summary
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    if stopped_by:
        return 128 + stopped_by[0]
    if run.complete:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

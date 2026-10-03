"""Distribute locations to workers and run each location's page reads.

Connection controls live in runtime.py; per-location retries in location.py.
Each worker connects, reads categories, reads products, then closes the session.
"""

import logging
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from queue import Empty, SimpleQueue

from common.journal import RunFolder
from common.location import LocationAbandoned, LocationCrawler
from common.runtime import (
    HTTP_PER_MINUTE,
    LOCAL_WORKERS,
    Cancelled,
    ConnectionRoute,
    Control,
    create_connections,
)
from common.settings import Config, Location, Settings
from crawler.walmart_us import ProductUrlCache

log = logging.getLogger("crawler")


def crawl(
    config: Config,
    settings: Settings,
    product_ids: list[str],
    run: RunFolder,
    control: Control,
) -> bool:
    """Read every location's categories, then its products."""
    product_urls = ProductUrlCache(stop=control.stop)
    connections = create_connections(settings=settings, control=control)
    workers_per_connection = 1 if settings.use_proxy else LOCAL_WORKERS
    total_workers = len(connections) * workers_per_connection
    queues = _queue_locations(
        locations=config.locations, connection_count=len(connections)
    )

    successful = 0
    with ThreadPoolExecutor(max_workers=total_workers) as pool:
        futures = []
        for connection, queue in zip(connections, queues, strict=True):
            for _ in range(workers_per_connection):
                future = pool.submit(
                    _process_location_queue,
                    connection=connection,
                    queue=queue,
                    config=config,
                    product_ids=product_ids,
                    run=run,
                    product_urls=product_urls,
                )
                futures.append(future)
        done, _ = wait(futures, return_when=FIRST_EXCEPTION)
        if any(future.exception() for future in done):
            control.interrupt("Unexpected error")
        for future in futures:
            successful += future.result()
    target = sum(
        loc.product_requests + loc.category_requests for loc in config.locations
    )
    return successful == target


def _queue_locations(
    locations: tuple[Location, ...], connection_count: int
) -> list[SimpleQueue]:
    """Assign locations to connection routes in round-robin order."""
    queues = [SimpleQueue() for _ in range(connection_count)]
    for index, location in enumerate(locations):
        connection_index = index % connection_count
        queues[connection_index].put(location)
    return queues


def _process_location_queue(
    connection: ConnectionRoute,
    queue: SimpleQueue,
    config: Config,
    product_ids: list[str],
    run: RunFolder,
    product_urls: ProductUrlCache,
) -> int:
    """One worker takes locations from its queue until empty or stopped."""
    successful = 0
    while not connection.control.stop.is_set():
        try:
            location = queue.get_nowait()
        except Empty:
            break
        crawler = LocationCrawler(
            location=location,
            connection=connection,
            run=run,
            product_urls=product_urls,
            bootstrap_product=config.bootstrap_product,
        )
        successful += _crawl_location(
            crawler=crawler,
            location=location,
            config=config,
            product_ids=product_ids,
        )
    return successful


def _crawl_location(
    crawler: LocationCrawler,
    location: Location,
    config: Config,
    product_ids: list[str],
) -> int:
    """Crawl one ZIP code; return its number of successful reads."""
    target = location.product_requests + location.category_requests
    error = None
    try:
        crawler.connect()
        _read_categories(
            crawler=crawler, categories=config.categories, location=location
        )
        for product_id in product_ids[: location.product_requests]:
            crawler.read(kind="product", target=product_id)
            if crawler.reads % 25 == 0:
                log.info(
                    f"ZIP {location.zip}: {crawler.reads}/{target} reads, "
                    f"{crawler.successes} successful"
                )
    except (Cancelled, LocationAbandoned) as stop:
        error = str(stop)
        log.info(f"ZIP {location.zip}: stopped: {error}")
    finally:
        crawler.close()
        crawler.run.write(
            {
                "event": "location_end",
                "postal_code": location.zip,
                "reads": crawler.reads,
                "successful": crawler.successes,
                "error": error,
            }
        )
    log.info(f"ZIP {location.zip}: {crawler.successes}/{target} reads successful")
    return crawler.successes


def _read_categories(
    crawler: LocationCrawler,
    categories: tuple[str, ...],
    location: Location,
) -> None:
    """Page through the categories in turn: page 1 of each, then 2..."""
    remaining = location.category_requests
    open_categories = list(categories)
    page = 0
    while remaining and open_categories:
        page += 1
        for url in list(open_categories):
            if not remaining:
                return
            remaining -= 1
            result = crawler.read(kind="category", target=url, page=page)
            if result is not None and page >= result["max_page"]:
                open_categories.remove(url)


def build_config_summary(config: Config, settings: Settings) -> dict:
    """The run configuration, without secrets, for the report."""
    return {
        "locations": [
            {
                "zip": loc.zip,
                "name": loc.name,
                "product_requests": loc.product_requests,
                "category_requests": loc.category_requests,
            }
            for loc in config.locations
        ],
        "categories": list(config.categories),
        "product_file": config.product_file.name,
        "use_proxy": settings.use_proxy,
        "proxy_count": len(settings.proxies) if settings.use_proxy else 0,
        "sessions_per_ip": 1 if settings.use_proxy else LOCAL_WORKERS,
        "http_per_minute_per_ip": HTTP_PER_MINUTE,
        "max_seconds": config.max_seconds,
    }


def run_crawl(
    config: Config,
    settings: Settings,
    targets: dict[str, dict[str, int]],
    product_ids: list[str],
) -> int:
    """Run the main crawl and finish with a summary and report.

    Returns 0 when the job is complete, 1 when it is incomplete or fails.
    """
    control = Control(max_seconds=config.max_seconds)
    run = RunFolder(
        results=config.path.parent / "results",
        command="run",
        config=build_config_summary(config, settings),
        targets=targets,
        redact=settings.redact,
    )
    try:
        # Entering opens the journal; leaving writes the summary and report.
        with run:
            try:
                run.complete = crawl(
                    config=config,
                    settings=settings,
                    product_ids=product_ids,
                    run=run,
                    control=control,
                )
            finally:
                run.stop_reason = control.reason
    except Exception:
        return 1  # the run folder already holds the error and the summary
    if run.complete:
        return 0
    return 1

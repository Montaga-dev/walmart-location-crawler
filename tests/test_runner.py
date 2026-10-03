import json
import time

import pytest

from common import location, runtime
from common.discovery import discover
from common.helpers import BlockedError, LocationMismatch, SessionError, TransientError
from common.journal import RunFolder, summarize
from common.location import LocationAbandoned, LocationCrawler
from common.runner import crawl
from common.runtime import Cancelled, ConnectionRoute, Control, RateLimiter
from common.settings import Config, Location, Proxy, Settings

CATEGORY_A = "https://www.walmart.com/browse/a/1"
CATEGORY_B = "https://www.walmart.com/browse/b/2"
DISCOVERY = {"10001": {"product": 0, "category": 10}}
LOCAL = Settings(use_proxy=False)


class FakeClient:
    """Stands in for CrawlWalmartUs; outcomes are scripted per read."""

    script: dict = {}
    created: list = []

    def __init__(self, connection, product_urls, record_http_event) -> None:
        self.connection = connection
        self.zip = None
        self.location: dict = {}
        self.resets = 0
        self.location_sets = 0
        FakeClient.created.append(self)

    def _next(self, key: tuple, default: object) -> object:
        outcomes = self.script.get(key)
        outcome = outcomes.pop(0) if outcomes else default
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def set_location(self, postal_code: str, bootstrap_id: str, store_id=None) -> dict:
        self.zip, self.location = postal_code, {}
        self.location_sets += 1
        self._next(("set", postal_code), None)
        store = self._next(("store", postal_code), "1")
        if store_id is not None and store != store_id:
            raise LocationMismatch(f"ZIP now maps to store {store}, not {store_id}")
        self.location = {
            "postal_code": postal_code,
            "city": f"City {postal_code}",
            "store_id": store,
        }
        return dict(self.location)

    def get_page_product(self, product_id: str) -> dict:
        record = {"product_id": product_id, "postal_code": self.zip}
        return self._next(("product", self.zip, product_id), record)

    def get_page_listing(self, url: str, page: int) -> dict:
        page_data = {
            "records": [{"product_id": f"{url[-1]}{page}", "sponsored": False}],
            "max_page": 99,
        }
        return self._next(("category", self.zip, url, page), page_data)

    def reset(self) -> None:
        self.resets += 1
        self.location = {}

    def close(self) -> None:
        pass


class Journal:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def write(self, event: dict) -> None:
        self.events.append(event)

    def kinds(self) -> list[str]:
        return [e.get("status", e["event"]) for e in self.events]


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    FakeClient.script, FakeClient.created = {}, []
    monkeypatch.setattr(location, "CrawlWalmartUs", FakeClient)
    return FakeClient


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    """Record waits instead of sleeping; stop and deadline still apply."""
    calls: list[float] = []
    original = Control.wait_or_stop

    def record(self, seconds=0):
        if seconds > 0:
            calls.append(seconds)
        original(self, 0)

    monkeypatch.setattr(Control, "wait_or_stop", record)
    return calls


def location_crawler(journal=None, zip_code="10001") -> LocationCrawler:
    connection = ConnectionRoute(Control(60))
    crawler = LocationCrawler(
        location=Location(zip_code, None, 0, 0),
        connection=connection,
        run=journal or Journal(),
        product_urls=None,
        bootstrap_product="1",
    )
    crawler.connect()
    return crawler


def test_transient_error_retries_on_the_same_session(sleeps):
    FakeClient.script[("product", "10001", "7")] = [TransientError("Timeout")]
    journal = Journal()
    crawler = location_crawler(journal)
    assert crawler.read("product", "7") == {"product_id": "7", "postal_code": "10001"}
    assert crawler.client.resets == 0
    assert sleeps == [2]
    assert journal.kinds() == ["started", "retry", "success"]


def test_blocked_response_gets_a_new_session(sleeps):
    FakeClient.script[("product", "10001", "7")] = [
        BlockedError("Robot challenge returned", captcha=True)
    ]
    journal = Journal()
    crawler = location_crawler(journal)
    assert crawler.read("product", "7")
    assert crawler.client.resets == 1
    assert journal.kinds() == ["started", "retry", "recovery", "success"]


def test_repeated_location_mismatch_reapplies_the_zip(sleeps):
    mismatch = LocationMismatch("Page location ('90001', '2')")
    FakeClient.script[("product", "10001", "7")] = [mismatch, mismatch]
    journal = Journal()
    crawler = location_crawler(journal)
    assert crawler.read("product", "7")
    assert crawler.client.resets == 0
    assert crawler.client.location_sets == 2  # connect + refresh
    assert sleeps == [2, 4]
    assert "location_refresh" in journal.kinds()


def test_the_first_store_stays_pinned_through_recovery(sleeps):
    FakeClient.script[("store", "10001")] = ["1", "2", "1"]
    FakeClient.script[("product", "10001", "7")] = [BlockedError("HTTP 429")]
    journal = Journal()
    crawler = location_crawler(journal)
    record = crawler.read("product", "7")
    # The rebuild that landed on store 2 was rejected; the next one is used.
    assert record and crawler.client.location["store_id"] == "1"
    assert journal.kinds().count("recovery") == 2


def test_failed_read_leaves_a_new_session_for_the_next_read(sleeps):
    FakeClient.script[("product", "10001", "7")] = [SessionError("HTTP 404")] * 3
    journal = Journal()
    crawler = location_crawler(journal)
    assert crawler.read("product", "7") is None
    assert crawler.client.resets == 2  # before attempts 2 and 3
    assert crawler.read("product", "8")
    assert crawler.client.resets == 3
    assert journal.kinds().count("failed") == 1


def test_three_failed_reads_in_a_row_reset_the_session(sleeps):
    for product_id in "123":
        FakeClient.script[("product", "10001", product_id)] = [
            TransientError("Timeout")
        ] * 3
    crawler = location_crawler()
    for product_id in "123":
        assert crawler.read("product", product_id) is None
    assert crawler.client.resets == 0
    assert crawler.read("product", "4")
    assert crawler.client.resets == 1


def test_three_failed_rebuilds_abandon_the_location(sleeps):
    journal = Journal()
    crawler = location_crawler(journal)
    FakeClient.script[("product", "10001", "7")] = [BlockedError("HTTP 429")]
    FakeClient.script[("set", "10001")] = [SessionError("HTTP 403")] * 3
    with pytest.raises(LocationAbandoned):
        crawler.read("product", "7")
    assert journal.kinds()[-1] == "failed"
    assert sleeps == [2, 4, 8]


def test_interrupt_cancels_the_read_in_flight(sleeps):
    journal = Journal()
    crawler = location_crawler(journal)
    crawler.control.interrupt("Stopped by SIGINT")
    FakeClient.script[("product", "10001", "7")] = [TransientError("Timeout")]
    with pytest.raises(Cancelled, match="SIGINT"):
        crawler.read("product", "7")
    assert journal.kinds()[-1] == "cancelled"


def config_for(tmp_path, locations, categories=(CATEGORY_A, CATEGORY_B)) -> Config:
    (tmp_path / "product_ids.json").write_text(json.dumps(["1", "2", "3"]))
    return Config(
        tmp_path / "config.json",
        tuple(locations),
        tuple(categories),
        tmp_path / "product_ids.json",
        "1",
        60,
        5,
    )


def run_crawl(tmp_path, config, settings=LOCAL):
    targets = {
        loc.zip: {"product": loc.product_requests, "category": loc.category_requests}
        for loc in config.locations
    }
    control = Control(60)
    with RunFolder(tmp_path / "results", "run", {}, targets) as run:
        run.complete = crawl(config, settings, ["1", "2", "3"], run, control)
    return run, summarize(run.folder / "journal.jsonl")


def test_categories_are_paged_in_turn_before_products(tmp_path, sleeps):
    FakeClient.script[("category", "10001", CATEGORY_A, 2)] = [
        {"records": [{"product_id": "x", "sponsored": False}], "max_page": 2}
    ]
    config = config_for(tmp_path, [Location("10001", None, 2, 5)])
    run, summary = run_crawl(tmp_path, config)
    reads = [
        (e["type"], e["id"][-1], e["page_no"])
        for e in map(json.loads, (run.folder / "journal.jsonl").open())
        if e.get("status") == "started"
    ]
    assert reads == [
        ("category", "1", 1),
        ("category", "2", 1),
        ("category", "1", 2),  # last page of A
        ("category", "2", 2),
        ("category", "2", 3),
        ("product", "1", None),
        ("product", "2", None),
    ]
    assert run.complete
    assert summary["status"] == "complete"
    assert summary["successful_requests"] == 7


def test_an_abandoned_location_does_not_stop_the_others(tmp_path, sleeps):
    FakeClient.script[("set", "90001")] = [SessionError("HTTP 403")] * 4
    config = config_for(
        tmp_path,
        [Location("90001", None, 3, 1), Location("10001", None, 3, 1)],
    )
    run, summary = run_crawl(tmp_path, config)
    assert not run.complete
    assert summary["status"] == "incomplete"
    by_zip = {row["postal_code"]: row for row in summary["locations"]}
    assert by_zip["10001"]["successful_requests"] == 4
    assert by_zip["90001"]["unattempted_requests"] == 4
    assert by_zip["90001"]["recoveries_count"] == 3
    assert "session rebuilds failed" in summary["errors"][0]["message"]


def test_each_proxy_connection_runs_its_own_locations(tmp_path, sleeps):
    proxies = (Proxy("http://p1:1", "u", "p"), Proxy("http://p2:2", "u", "p"))
    zips = ["10001", "90001", "60601"]
    config = config_for(tmp_path, [Location(z, None, 1, 0) for z in zips])
    run_crawl(tmp_path, config, Settings(True, proxies))
    connections = {client.zip: client.connection.proxy for client in FakeClient.created}
    assert connections == {
        "10001": proxies[0],
        "90001": proxies[1],
        "60601": proxies[0],
    }


def test_discover_balances_categories_and_deduplicates_pages(tmp_path, sleeps):
    def page(*ids, max_page=2):
        records = [{"product_id": i, "sponsored": i.startswith("ad")} for i in ids]
        return [{"records": records, "max_page": max_page}]

    script = FakeClient.script
    script[("category", "10001", CATEGORY_A, 1)] = page("1", "ad1", "2")
    script[("category", "10001", CATEGORY_A, 2)] = page("2", "3")
    script[("category", "10001", CATEGORY_B, 1)] = page("2", "4", "5")
    config = config_for(tmp_path, [Location("10001", None, 5, 0)])
    with RunFolder(tmp_path / "results", "discover", {}, DISCOVERY) as run:
        assert discover(config, Settings(False), run, Control(60))
    assert json.loads(config.product_file.read_text()) == ["1", "2", "3", "4", "5"]


def test_discover_takes_24_products_from_each_of_40_categories(tmp_path, sleeps):
    categories = [f"https://www.walmart.com/browse/category/{i}" for i in range(40)]
    for index, url in enumerate(categories):
        FakeClient.script[("category", "10001", url, 1)] = [
            {
                "records": [
                    {"product_id": str(index * 100 + i), "sponsored": False}
                    for i in range(48)
                ],
                "max_page": 25,
            }
        ]
    config = config_for(tmp_path, [Location("10001", None, 960, 40)], categories)
    journal = Journal()
    assert discover(config, LOCAL, journal, Control(60))
    ids = json.loads(config.product_file.read_text())
    assert len(ids) == len(set(ids)) == 960
    assert all(sum(int(i) // 100 == c for i in ids) == 24 for c in range(40))
    reads = [e for e in journal.events if e.get("status") == "success"]
    assert [(e["id"], e["page_no"]) for e in reads] == [(u, 1) for u in categories]
    coverage = [e for e in journal.events if e["event"] == "discovery_category"]
    assert len(coverage) == 40
    assert all(len(e["product_ids"]) == e["target"] == 24 for e in coverage)


def test_discover_does_not_replace_a_missing_category_with_others(tmp_path, sleeps):
    for url, ids in ((CATEGORY_A, ["1", "2", "3", "4"]), (CATEGORY_B, ["1", "2"])):
        FakeClient.script[("category", "10001", url, 1)] = [
            {
                "records": [{"product_id": i, "sponsored": False} for i in ids],
                "max_page": 1,
            }
        ]
    config = config_for(tmp_path, [Location("10001", None, 4, 0)])
    before = config.product_file.read_text()
    assert not discover(config, LOCAL, Journal(), Control(60))
    assert config.product_file.read_text() == before


def test_incomplete_discovery_keeps_the_product_file(tmp_path, sleeps):
    config = config_for(tmp_path, [Location("10001", None, 500, 0)], [CATEGORY_A])
    before = config.product_file.read_text()
    with RunFolder(tmp_path / "results", "discover", {}, DISCOVERY) as run:
        assert not discover(config, Settings(False), run, Control(60))
    assert config.product_file.read_text() == before


@pytest.mark.parametrize("products", [1, 2])
def test_discover_skips_categories_with_no_assigned_quota(tmp_path, sleeps, products):
    categories = [CATEGORY_A, CATEGORY_B, "https://www.walmart.com/browse/c/3"]
    config = config_for(tmp_path, [Location("10001", None, products, 0)], categories)
    journal = Journal()

    assert discover(config, LOCAL, journal, Control(60))

    assert json.loads(config.product_file.read_text()) == ["11", "21"][:products]
    reads = [event for event in journal.events if event.get("status") == "started"]
    assert [event["id"] for event in reads] == categories[:products]
    coverage = [
        event for event in journal.events if event["event"] == "discovery_category"
    ]
    assert [event["target"] for event in coverage] == [1] * products


def test_discovery_page_failure_keeps_the_previous_product_pool(tmp_path, sleeps):
    config = config_for(tmp_path, [Location("10001", None, 4, 0)])
    before = config.product_file.read_text()
    FakeClient.script[("category", "10001", CATEGORY_B, 1)] = [
        TransientError("Timeout")
    ] * 3
    journal = Journal()

    assert not discover(config, LOCAL, journal, Control(60))

    assert config.product_file.read_text() == before
    coverage = [
        event for event in journal.events if event["event"] == "discovery_category"
    ]
    assert [event["category_url"] for event in coverage] == [CATEGORY_A]
    assert journal.kinds()[-1] == "failed"


def test_control_raises_after_interrupt_or_deadline():
    control = Control(60)
    control.interrupt("Stopped by SIGTERM")
    with pytest.raises(Cancelled, match="SIGTERM"):
        control.wait_or_stop(5)
    late = Control(0.01)
    with pytest.raises(Cancelled, match="time limit"):
        late.wait_or_stop(5)


def test_rate_limiter_spaces_requests_evenly():
    rate_limiter = RateLimiter(per_minute=1200, control=Control(60))  # 50 ms apart
    started = time.monotonic()
    for _ in range(3):
        rate_limiter.wait()
    assert time.monotonic() - started >= 0.1


def test_proxy_exit_ip_change_starts_a_new_generation(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    connection = ConnectionRoute(Control(60), Proxy("http://p:1", "u", "p"))
    exits = iter(["1.1.1.1", "2.2.2.2"])
    monkeypatch.setattr(connection, "_probe", lambda: next(exits))
    assert connection.exit_generation() == 0
    clock[0] += 30  # checked at most once a minute
    assert connection.exit_generation() == 0
    clock[0] += 31
    assert connection.exit_generation() == 1

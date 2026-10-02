import csv
import json
import logging

import pytest

from common.journal import RunFolder, latest_run, read_events, write_report
from common.settings import Proxy, Settings

TARGETS = {"10001": {"product": 2, "category": 1}}


def request(request_id: str, status: str, kind: str = "product", **extra) -> dict:
    return {
        "event": "request",
        "request_id": request_id,
        "type": kind,
        "postal_code": "10001",
        "status": status,
        **extra,
    }


def test_summary_counts_each_request_once(tmp_path):
    with RunFolder(tmp_path, "run", {"use_proxy": False}, TARGETS) as run:
        run.write(request("a", "started"))
        run.write(request("a", "retry", error="Robot challenge returned"))
        run.write(
            {
                "event": "http",
                "postal_code": "10001",
                "status": 307,
                "error": "Robot challenge returned",
                "blocked": True,
                "captcha": True,
            }
        )
        run.write({"event": "recovery", "postal_code": "10001"})
        run.write(request("a", "success", location_name="New York", data={}))
        run.write(request("b", "started"))
        run.write(request("b", "failed", error="HTTP 404"))
        run.write(request("c", "started", "category"))
        cards = {"records": [{}, {}], "max_page": 3}
        run.write(request("c", "success", "category", data=cards))
    summary = json.loads((run.folder / "summary.json").read_text())
    assert summary["status"] == "incomplete"
    assert (summary["attempted_requests"], summary["successful_requests"]) == (3, 2)
    assert summary["success_rate_percent"] == 66.67
    assert summary["by_type"]["category"]["successful_requests"] == 1
    assert summary["category_product_cards"] == 2
    assert summary["retries_count"] == summary["recoveries_count"] == 1
    assert summary["blocked_count"] == summary["captcha_count"] == 1
    assert summary["locations"][0]["location_name"] == "New York"
    assert summary["errors"] == [{"message": "HTTP 404", "count": 1}]
    assert summary["started_at"]["istanbul"].endswith("+03:00")
    report = (run.folder / "report.md").read_text()
    assert "| Successful requests | 2 |" in report
    assert "| 10001 | New York | 3 | 3 | 2 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |" in report
    log = (run.folder / "run.log").read_text()
    assert "RUN START" in log and "RUN END" in log


def test_validated_records_are_exported_as_csv(tmp_path):
    product = {
        "location_name": "New York",
        "location_id": "3520",
        "postal_code": "10001",
        "product_id": "1",
        "product_title": "Mower, 16 inch",
        "product_price": 89.99,
        "product_price_2": None,
        "in_stock": True,
        "product_url": "https://www.walmart.com/ip/mower/1",
    }
    card = {"category_id": "5428", "product_id": "2", "brand": "Nike", "position": 1}
    with RunFolder(tmp_path, "run", {}, TARGETS) as run:
        run.write(request("a", "success", data=product))
        run.write(request("b", "failed", error="HTTP 404"))
        run.write(request("c", "success", "category", data={"records": [card]}))
    with (run.folder / "products.csv").open() as stream:
        [row] = list(csv.DictReader(stream))
    assert row["product_title"] == "Mower, 16 inch"
    assert (row["product_price"], row["product_price_2"], row["in_stock"]) == (
        "89.99",
        "",
        "True",
    )
    assert row["read_at"].startswith("20")
    with (run.folder / "categories.csv").open() as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
    assert reader.fieldnames[:2] == ["category_id", "postal_code"]
    assert [(r["product_id"], r["brand"], r["position"]) for r in rows] == [
        ("2", "Nike", "1")
    ]


def test_only_locations_with_reads_count_as_used(tmp_path):
    targets = {**TARGETS, "90001": {"product": 2, "category": 1}}
    with RunFolder(tmp_path, "run", {}, targets) as run:
        run.write(request("a", "success", data={"product_id": "1"}))
    summary = json.loads((run.folder / "summary.json").read_text())
    assert summary["locations_used"] == 1
    assert "| Locations used | 1 |" in (run.folder / "report.md").read_text()


def test_crash_is_recorded_without_secrets(tmp_path):
    def redact(text):
        return text.replace("hunter2", "[REDACTED]")

    with pytest.raises(RuntimeError):
        with RunFolder(tmp_path, "run", {}, TARGETS, redact) as run:
            run.write({"event": "http", "error": "proxy http://u:hunter2@h:1"})
            logging.getLogger("crawler").info("password hunter2")
            raise RuntimeError("boom hunter2")
    summary = json.loads((run.folder / "summary.json").read_text())
    assert summary["status"] == "crashed"
    log = (run.folder / "run.log").read_text()
    assert "RUN ERROR" in log and "RuntimeError: boom" in log
    for path in run.folder.iterdir():
        assert "hunter2" not in path.read_text()


@pytest.mark.parametrize("password", ['pa"ss"word', "pa\\ss\\word", "pässwörd"])
def test_proxy_urls_are_redacted_before_json_escaping(tmp_path, password):
    settings = Settings(True, (Proxy("http://h:1", "user0001", password),))
    proxy_url = f"http://user0001:{password}@h:1"
    with RunFolder(tmp_path, "run", {}, TARGETS, settings.redact) as run:
        run.write({"event": "http", "error": f"proxy {proxy_url} failed"})
        run.write({"event": "note", "detail": {"nested": [proxy_url]}})
    for path in run.folder.iterdir():
        text = path.read_text()
        assert password not in text
        assert json.dumps(password, ensure_ascii=False)[1:-1] not in text


def test_report_is_rebuilt_from_a_killed_run(tmp_path):
    folder = tmp_path / "20261003T000000Z-run-abc123"
    folder.mkdir()
    start = {
        "event": "run_start",
        "timestamp": "2026-10-03T10:00:00+00:00",
        "run_id": folder.name,
        "command": "run",
        "config": {},
        "targets": TARGETS,
    }
    events = [
        start,
        {
            **request("a", "success", data={"product_id": "1"}),
            "timestamp": "2026-10-03T10:00:30+00:00",
        },
        {**request("b", "started"), "timestamp": "2026-10-03T10:01:00+00:00"},
    ]
    lines = "".join(json.dumps(e) + "\n" for e in events)
    (folder / "journal.jsonl").write_text(lines + '{"event": "req')
    summary = write_report(folder)
    assert summary["status"] == "interrupted"
    assert summary["duration_seconds"] == 60
    assert summary["successful_requests"] == 1
    assert summary["cancelled_requests"] == 1
    assert latest_run(tmp_path) == folder


def test_a_broken_line_inside_the_journal_is_an_error(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text('{"event": "run_start"}\nnot json\n{"event": "x"}\n')
    with pytest.raises(ValueError, match="line 2"):
        read_events(path)

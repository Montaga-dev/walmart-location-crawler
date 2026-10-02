"""Run folders: an append-only journal and the reports built from it.

Every event is flushed to ``journal.jsonl`` when it happens, so the
summary can be rebuilt after any interruption:

    python -m common.journal                     the latest run in results/
    python -m common.journal results/<run_id>    a given run

    results/<run_id>/journal.jsonl   requests, HTTP calls, recoveries
    results/<run_id>/run.log         console output with timestamps
    results/<run_id>/summary.json    metrics computed from the journal
    results/<run_id>/report.md       the same metrics as tables
    results/<run_id>/products.csv    validated product records
    results/<run_id>/categories.csv  validated category cards
"""

import argparse
import csv
import io
import json
import logging
import os
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4

from common.helpers import atomic_write, clocks

log = logging.getLogger("crawler")
TYPES = ("product", "category")
COUNTERS = ("retries", "recoveries", "refreshes", "blocked", "captcha")
COLUMNS = (
    "target_requests",
    "attempted_requests",
    "successful_requests",
    "failed_requests",
)
PRODUCT_FIELDS = (
    "location_name",
    "location_id",
    "postal_code",
    "product_id",
    "product_title",
    "product_price",
    "product_price_2",
    "in_stock",
    "product_url",
    "redirected_product_id",
    "read_at",
)
CATEGORY_FIELDS = (
    "category_id",
    "postal_code",
    "location_name",
    "location_id",
    "product_id",
    "product_title",
    "product_image_url",
    "brand",
    "product_price",
    "position",
    "page_no",
    "sponsored",
    "listing_url",
    "search_query",
    "read_at",
)


class RunFolder:
    """Creates a run folder and always finishes it with a summary."""

    def __init__(
        self,
        results: Path,
        command: str,
        config: dict,
        targets: dict[str, dict[str, int]],
        redact: Callable[[str], str] = lambda text: text,
    ) -> None:
        self.started = datetime.now(UTC)
        self.run_id = f"{self.started:%Y%m%dT%H%M%SZ}-{command}-{uuid4().hex[:6]}"
        self.folder = results / self.run_id
        self.command = command
        self.config = config
        self.targets = targets
        self.redact = redact
        self.complete = False
        self.interrupted = False
        self.stop_reason: str | None = None
        self._lock = Lock()

    def __enter__(self) -> "RunFolder":
        self.folder.mkdir(parents=True)
        self._journal = (self.folder / "journal.jsonl").open("x", encoding="utf-8")
        self._handlers = _log_to(self.folder / "run.log", self.redact)
        self._clock = time.monotonic()
        self.write(
            {
                "event": "run_start",
                "run_id": self.run_id,
                "command": self.command,
                "config": self.config,
                "targets": self.targets,
            }
        )
        stamp = clocks(self.started)
        log.info(f"RUN START {self.run_id} {stamp['utc']} {stamp['istanbul']}")
        return self

    def write(self, event: dict) -> None:
        event = self._clean({"timestamp": datetime.now(UTC).isoformat(), **event})
        line = json.dumps(event, ensure_ascii=False)
        with self._lock:
            self._journal.write(line + "\n")
            self._journal.flush()

    def _clean(self, value: object) -> object:
        """Redact strings before JSON escaping can disguise a secret."""
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {self._clean(k): self._clean(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self._clean(item) for item in value]
        return value

    def __exit__(self, error_type, error, traceback) -> None:
        duration = time.monotonic() - self._clock
        try:
            if error is not None:
                log.error("RUN ERROR", exc_info=(error_type, error, traceback))
                status = "crashed"
            elif self.interrupted:
                status = "interrupted"
            else:
                status = "complete" if self.complete else "incomplete"
            self.write(
                {
                    "event": "run_end",
                    "status": status,
                    "stop_reason": self.stop_reason,
                    "duration_seconds": round(duration, 3),
                }
            )
            os.fsync(self._journal.fileno())
            self._journal.close()
            summary = write_report(self.folder)
            stamp = clocks(datetime.now(UTC))
            log.info(
                f"RUN END   {self.run_id} {stamp['utc']} "
                f"{stamp['istanbul']} {duration:.0f}s {status} "
                f"{summary['successful_requests']}/"
                f"{summary['target_requests']}"
            )
        finally:
            self._journal.close()
            for handler in self._handlers:
                log.removeHandler(handler)
                handler.close()


def _log_to(path: Path, redact: Callable[[str], str]) -> list:
    """Console output, mirrored to run.log, without proxy secrets."""

    class Redact(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            message = record.getMessage()
            if record.exc_info:
                trace = logging.Formatter().formatException(record.exc_info)
                message = f"{message}\n{trace}"
                record.exc_info = record.exc_text = None
            record.msg, record.args = redact(message), None
            return True

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(message)s"))
    file = logging.FileHandler(path, encoding="utf-8")
    file.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    for handler in (console, file):
        handler.addFilter(Redact())
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    return [console, file]


def latest_run(results: Path) -> Path:
    runs = sorted(path.parent for path in results.glob("*/journal.jsonl"))
    if not runs:
        raise ValueError(f"No runs in {results}")
    return runs[-1]


def write_report(folder: Path) -> dict:
    summary = summarize(folder / "journal.jsonl")
    atomic_write(
        folder / "summary.json",
        json.dumps(summary, indent=2, ensure_ascii=False),
    )
    atomic_write(folder / "report.md", render_report(summary))
    export_records(folder)
    return summary


def export_records(folder: Path) -> dict[str, int]:
    """Write validated records as CSV; failed reads and diagnostics are skipped."""
    products, cards = [], []
    for event in read_events(folder / "journal.jsonl"):
        if event["event"] != "request" or event["status"] != "success":
            continue
        read_at = {"read_at": event["timestamp"]}
        if event["type"] == "product":
            products.append({**event["data"], **read_at})
        else:
            cards.extend({**card, **read_at} for card in event["data"]["records"])
    _write_csv(folder / "products.csv", PRODUCT_FIELDS, products)
    _write_csv(folder / "categories.csv", CATEGORY_FIELDS, cards)
    return {"products": len(products), "categories": len(cards)}


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    atomic_write(path, buffer.getvalue())


def read_events(path: Path) -> list[dict]:
    """A torn last line from a killed process is ignored."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    events = []
    for number, line in enumerate(lines, 1):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as error:
            if number == len(lines) and not line.endswith("\n"):
                break
            raise ValueError(f"{path}: bad JSON on line {number}") from error
    return events


def summarize(path: Path) -> dict:
    """Case metrics from a journal; each request counts once."""
    events = read_events(path)
    if not events or events[0]["event"] != "run_start":
        raise ValueError(f"{path}: the journal must begin with run_start")
    start = events[0]
    end = next((e for e in events if e["event"] == "run_end"), None)
    targets = start["targets"]
    records, counters, http, errors = _collect_events(events)

    began = datetime.fromisoformat(start["timestamp"])
    if end is not None:
        finished = datetime.fromisoformat(end["timestamp"])
        duration = end["duration_seconds"]
    else:
        finished = datetime.fromisoformat(events[-1]["timestamp"])
        duration = (finished - began).total_seconds()
    minutes = duration / 60

    successful = [r for r in records if r["status"] == "success"]
    locations = _summarize_locations(targets, records, counters)
    totals = {}
    for kind in TYPES:
        totals[kind] = sum(target[kind] for target in targets.values())
    total_counters = Counter()
    for postal_code in targets:
        total_counters.update(counters[postal_code])

    return {
        "run_id": start["run_id"],
        "command": start["command"],
        "status": end["status"] if end else "interrupted",
        "stop_reason": end["stop_reason"] if end else "process was killed",
        "started_at": clocks(began),
        "finished_at": clocks(finished),
        "duration_seconds": round(duration, 3),
        **_tally(sum(totals.values()), records),
        "requests_per_minute": _rate(len(records), minutes),
        "successful_requests_per_minute": _rate(len(successful), minutes),
        "by_type": _counts_by_type(totals, records),
        "category_product_cards": sum(
            len(r["data"]["records"]) for r in successful if r["type"] == "category"
        ),
        "http_attempts": http["attempts"],
        "http_failures": http["failures"],
        **_counter_fields(total_counters),
        "locations_used": sum(1 for loc in locations if loc["attempted_requests"]),
        "locations": locations,
        "errors": [
            {"message": message, "count": count}
            for message, count in errors.most_common(20)
        ],
        "config": start["config"],
    }


def _collect_events(events: list[dict]) -> tuple[list[dict], dict, Counter, Counter]:
    """Keep each read's final state and count retries, HTTP attempts and errors."""
    reads: dict[str, dict] = {}
    counters: dict[str, Counter] = defaultdict(Counter)
    http = Counter()
    errors = Counter()
    for event in events:
        kind = event["event"]
        zip_counters = counters[event.get("postal_code")]
        if kind == "request":
            # Retries replace the same read; they do not create extra reads.
            reads[event["request_id"]] = event
            if event["status"] == "retry":
                zip_counters["retries"] += 1
            if event["status"] == "failed":
                errors[event["error"]] += 1
        elif kind == "http":
            http["attempts"] += 1
            if "error" in event:
                http["failures"] += 1
            if event.get("blocked"):
                zip_counters["blocked"] += 1
            if event.get("captcha"):
                zip_counters["captcha"] += 1
        elif kind == "recovery":
            zip_counters["recoveries"] += 1
        elif kind == "location_refresh":
            zip_counters["refreshes"] += 1
        elif kind == "location_end" and event.get("error"):
            errors[f"ZIP {event['postal_code']}: {event['error']}"] += 1
    return list(reads.values()), counters, http, errors


def _summarize_locations(
    targets: dict, records: list[dict], counters: dict
) -> list[dict]:
    """Build one report row per configured ZIP, including unattempted locations."""
    locations = []
    for postal_code, target in targets.items():
        rows = [r for r in records if r["postal_code"] == postal_code]
        names = [r["location_name"] for r in rows if r.get("location_name")]
        location = {
            "postal_code": postal_code,
            "location_name": names[0] if names else None,
            **_tally(sum(target.values()), rows),
            "by_type": _counts_by_type(target, rows),
            **_counter_fields(counters[postal_code]),
        }
        locations.append(location)
    return locations


def _counts_by_type(targets: dict, records: list[dict]) -> dict:
    """Count products and category pages separately."""
    counts = {}
    for kind in TYPES:
        rows = [record for record in records if record["type"] == kind]
        counts[kind] = _tally(targets[kind], rows)
    return counts


def _counter_fields(counters: Counter) -> dict:
    """Use the same counter names in location rows and the overall summary."""
    fields = {}
    for name in COUNTERS:
        fields[f"{name}_count"] = counters[name]
    return fields


def _tally(target: int, rows: list[dict]) -> dict:
    status_counts = Counter(row["status"] for row in rows)
    attempted = len(rows)
    successful = status_counts["success"]
    # Cancelled and in-flight reads count as failed attempts.
    failed = attempted - successful
    cancelled_or_in_flight = failed - status_counts["failed"]
    unattempted = max(0, target - attempted)
    success_rate = None
    if attempted:
        success_rate = round(successful * 100 / attempted, 2)
    return {
        "target_requests": target,
        "attempted_requests": attempted,
        "successful_requests": successful,
        "failed_requests": failed,
        "cancelled_requests": cancelled_or_in_flight,
        "unattempted_requests": unattempted,
        "success_rate_percent": success_rate,
    }


def _rate(count: int, minutes: float) -> float | None:
    return round(count / minutes, 2) if minutes > 0 else None


def render_report(summary: dict) -> str:
    def format_cell(value: object) -> str:
        text = "N/A" if value is None else str(value)
        return text.replace("|", "\\|").replace("\n", " ")

    def table(headers: list[str], rows: list[list]) -> list[str]:
        table_lines = [
            "| " + " | ".join(headers) + " |",
            "|" + " --- |" * len(headers),
        ]
        for row in rows:
            cells = []
            for value in row:
                cells.append(format_cell(value))
            table_lines.append("| " + " | ".join(cells) + " |")
        return table_lines

    metric_rows = [
        ["Status", summary["status"]],
        ["Stop reason", summary["stop_reason"]],
        ["Started (UTC)", summary["started_at"]["utc"]],
        ["Started (Istanbul)", summary["started_at"]["istanbul"]],
        ["Finished (UTC)", summary["finished_at"]["utc"]],
        ["Finished (Istanbul)", summary["finished_at"]["istanbul"]],
        ["Total execution time (s)", summary["duration_seconds"]],
        ["Target requests", summary["target_requests"]],
        ["Total attempted requests", summary["attempted_requests"]],
        ["Successful requests", summary["successful_requests"]],
        ["Failed requests", summary["failed_requests"]],
        ["Cancelled or in-flight requests", summary["cancelled_requests"]],
        ["Unattempted requests", summary["unattempted_requests"]],
        ["Success rate (%)", summary["success_rate_percent"]],
        ["Requests per minute", summary["requests_per_minute"]],
        [
            "Successful requests per minute",
            summary["successful_requests_per_minute"],
        ],
        ["Locations used", summary["locations_used"]],
        ["Retry count", summary["retries_count"]],
        ["Session recoveries", summary["recoveries_count"]],
        ["Location refreshes", summary["refreshes_count"]],
        ["Blocked responses", summary["blocked_count"]],
        ["CAPTCHA responses", summary["captcha_count"]],
        ["HTTP attempts (setup, redirects, retries)", summary["http_attempts"]],
        ["HTTP failures", summary["http_failures"]],
        ["Category product cards", summary["category_product_cards"]],
    ]
    lines = [f"# Run {summary['run_id']}", ""]
    lines += table(["Metric", "Value"], metric_rows)

    type_rows = []
    for kind, counts in summary["by_type"].items():
        row = [kind]
        for column in COLUMNS:
            row.append(counts[column])
        type_rows.append(row)
    lines += ["", "## Requests per type", ""]
    lines += table(
        ["Type", "Target", "Attempted", "Successful", "Failed"],
        type_rows,
    )

    location_rows = []
    for location in summary["locations"]:
        row = [location["postal_code"], location["location_name"]]
        for column in COLUMNS:
            row.append(location[column])
        row.extend(
            [
                location["by_type"]["product"]["successful_requests"],
                location["by_type"]["category"]["successful_requests"],
                location["retries_count"],
                location["recoveries_count"],
                location["blocked_count"],
                location["captcha_count"],
            ]
        )
        location_rows.append(row)
    lines += ["", "## Requests per location", ""]
    lines += table(
        ["ZIP", "City", "Target", "Attempted", "Successful", "Failed"]
        + ["Products OK", "Categories OK", "Retries", "Recoveries"]
        + ["Blocked", "CAPTCHA"],
        location_rows,
    )
    lines += [
        "",
        "A request is one product or category page read. Session setup,",
        "redirects and retries count as HTTP attempts, not as requests.",
    ]
    if summary["errors"]:
        error_rows = []
        for error in summary["errors"]:
            error_rows.append([error["count"], error["message"]])
        lines += ["", "## Most frequent errors", ""]
        lines += table(["Count", "Error"], error_rows)
    lines += [
        "",
        "## Configuration",
        "",
        "```json",
        json.dumps(summary["config"], indent=2, ensure_ascii=False),
        "```",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild a run's reports.")
    parser.add_argument("folder", nargs="?", type=Path)
    args = parser.parse_args(argv)

    try:
        results = Path(__file__).resolve().parents[1] / "results"
        folder = args.folder or latest_run(results)
        summary = write_report(folder)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    print(
        f"{folder / 'report.md'}: {summary['successful_requests']}/"
        f"{summary['target_requests']} successful"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

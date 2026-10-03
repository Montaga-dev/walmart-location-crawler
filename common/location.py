"""Read pages for one ZIP, recording results and handling retries.

A read has at most three attempts. Transient errors wait 2s then 4s;
repeated location mismatches refresh the ZIP. Session errors rebuild the
guest session and restore the pinned store. Failed rebuilds stop that ZIP.
"""

import logging

from common.helpers import CrawlError, LocationMismatch, SessionError, TransientError
from common.journal import RunFolder
from common.runtime import Cancelled, ConnectionRoute
from common.settings import Location
from crawler.walmart_us import CrawlWalmartUs, ProductUrlCache

log = logging.getLogger("crawler")

MAX_ATTEMPTS = 3
RETRY_WAIT = 2
MAX_RECOVERY_WAIT = 8
RECOVERY_ATTEMPTS = 3
FAILED_READS_BEFORE_RESET = 3


class LocationAbandoned(RuntimeError):
    """Session rebuilds keep failing for one location."""


class LocationCrawler:
    """A guest session for one ZIP code plus the retry policy."""

    def __init__(
        self,
        location: Location,
        connection: ConnectionRoute,
        run: RunFolder,
        product_urls: ProductUrlCache,
        bootstrap_product: str,
    ) -> None:
        self.zip = location.zip
        self.run = run
        self.control = connection.control
        self.bootstrap_product = bootstrap_product
        self.client = CrawlWalmartUs(
            connection=connection,
            product_urls=product_urls,
            record_http_event=self._record_http_event,
        )
        self.store_id: str | None = None
        self.reads = 0
        self.successes = 0
        self.consecutive_failed_reads = 0
        # A failed read can require a fresh session before the next read.
        self.needs_new_session = False

    def set_location(self) -> None:
        """Apply the ZIP; the first verified store is kept for the whole run."""
        location = self.client.set_location(
            postal_code=self.zip,
            bootstrap_id=self.bootstrap_product,
            store_id=self.store_id,
        )
        self.store_id = location["store_id"]

    def connect(self) -> None:
        try:
            self.set_location()
        except CrawlError as error:
            log.info(f"ZIP {self.zip}: setup failed ({error})")
            self.recover()
        location = self.client.location
        log.info(
            f"ZIP {self.zip}: location verified "
            f"({location['city']}, store {location['store_id']})"
        )

    def read(self, kind: str, target: str, page: int | None = None) -> dict | None:
        """One logical read with retries; None when it failed."""
        request = self._start_read(kind=kind, target=target, page=page)
        previous_error: CrawlError | None = None
        try:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    self._prepare_attempt(
                        previous_error=previous_error, attempt=attempt
                    )
                    if kind == "product":
                        data = self.client.get_page_product(product_id=target)
                    else:
                        data = self.client.get_page_listing(url=target, page=page)
                except CrawlError as failure:
                    previous_error = failure
                    if attempt < MAX_ATTEMPTS:
                        self.run.write(
                            {
                                **request,
                                "status": "retry",
                                "attempt": attempt,
                                "error": str(failure),
                            }
                        )
                    continue
                self._record_success(request=request, data=data, attempt=attempt)
                return data
        except Cancelled as stop:
            self.run.write({**request, "status": "cancelled", "error": str(stop)})
            raise
        except LocationAbandoned as abandoned:
            self.run.write({**request, "status": "failed", "error": str(abandoned)})
            raise
        self._record_failure(request=request, error=previous_error)
        return None

    def _start_read(self, kind: str, target: str, page: int | None) -> dict:
        """Give all attempts of this read the same journal ID."""
        self.reads += 1
        request = {
            "event": "request",
            "request_id": f"{self.zip}:{kind}:{self.reads}",
            "type": kind,
            "postal_code": self.zip,
            "id": target,
            "page_no": page,
        }
        self.run.write({**request, "status": "started"})
        return request

    def _record_success(self, request: dict, data: dict, attempt: int) -> None:
        self.consecutive_failed_reads = 0
        self.successes += 1
        self.run.write(
            {
                **request,
                "status": "success",
                "attempt": attempt,
                "location_name": self.client.location["city"],
                "location_id": self.client.location["store_id"],
                "data": data,
            }
        )

    def _record_failure(self, request: dict, error: CrawlError | None) -> None:
        """Record exhausted retries and decide whether the next read needs a reset."""
        self.run.write({**request, "status": "failed", "error": str(error)})
        log.info(f"ZIP {self.zip}: {request['type']} {request['id']} failed: {error}")
        self.consecutive_failed_reads += 1
        if (
            isinstance(error, SessionError)
            or self.consecutive_failed_reads >= FAILED_READS_BEFORE_RESET
        ):
            self.needs_new_session = True

    def _record_http_event(self, event: dict) -> None:
        self.run.write({"event": "http", "postal_code": self.zip, **event})

    def _prepare_attempt(self, previous_error: CrawlError | None, attempt: int) -> None:
        """Apply the previous error's retry policy, then any pending session reset."""
        # Attempt 2 is the first retry: wait 2s. Attempt 3 waits 4s.
        backoff_step = max(0, attempt - 2)
        if isinstance(previous_error, TransientError | LocationMismatch):
            wait_seconds = RETRY_WAIT * 2**backoff_step
            self.control.wait_or_stop(wait_seconds)
            if isinstance(previous_error, LocationMismatch) and attempt == MAX_ATTEMPTS:
                self.run.write({"event": "location_refresh", "postal_code": self.zip})
                self.set_location()
        elif previous_error is not None:
            self.needs_new_session = True
        if self.needs_new_session:
            self.recover(backoff_step=backoff_step)

    def recover(self, backoff_step: int = 0) -> None:
        """Start a new guest session; give up after repeated failures."""
        last_error = None
        for tries in range(RECOVERY_ATTEMPTS):
            wait_seconds = RETRY_WAIT * 2 ** (backoff_step + tries)
            wait_seconds = min(MAX_RECOVERY_WAIT, wait_seconds)
            self.control.wait_or_stop(wait_seconds)
            self.run.write({"event": "recovery", "postal_code": self.zip})
            self.client.reset()
            try:
                self.set_location()
            except CrawlError as error:
                last_error = error
                log.info(f"ZIP {self.zip}: new session failed: {error}")
                continue
            self.needs_new_session = False
            self.consecutive_failed_reads = 0
            return
        raise LocationAbandoned(
            f"{RECOVERY_ATTEMPTS} session rebuilds failed: {last_error}"
        )

    def close(self) -> None:
        self.client.close()

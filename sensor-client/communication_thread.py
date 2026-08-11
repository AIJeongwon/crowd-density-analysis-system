from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from environment_config import EnvironmentConfig
from shared_runtime import (
    CommunicationError,
    InferenceResult,
    LOGGER,
    ManagedWorker,
    ResultMailbox,
    ThreadFailure,
)


class CommunicationWorker(ManagedWorker):
    def __init__(
        self,
        *,
        environment: EnvironmentConfig,
        mailbox: ResultMailbox,
        stop_event: threading.Event,
        failure_queue: queue.Queue[ThreadFailure],
        verbose: bool,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        super().__init__(
            name="Communication",
            stop_event=stop_event,
            failure_queue=failure_queue,
            verbose=verbose,
        )
        self.config = environment.server
        self.mailbox = mailbox
        self.opener = opener
        self.consecutive_failures = 0
        self.health_url = f"{self.config.base_url}/health"
        self.result_url = f"{self.config.base_url}/api/inference-results"

    def run_worker(self) -> None:
        LOGGER.info("server communication started: %s", self.config.base_url)
        next_heartbeat = time.monotonic()
        try:
            while not self.stop_event.is_set():
                now = time.monotonic()
                wait_seconds = max(0.0, min(0.5, next_heartbeat - now))
                result = self.mailbox.take(wait_seconds, self.stop_event)
                if result is not None:
                    self._send_result_until_complete(result)

                now = time.monotonic()
                if now >= next_heartbeat and not self.stop_event.is_set():
                    self._heartbeat()
                    next_heartbeat = now + self.config.heartbeat_interval_seconds
        finally:
            LOGGER.info("server communication stopped")

    def _send_result_until_complete(self, result: InferenceResult) -> None:
        body = json.dumps(result.to_payload(), separators=(",", ":")).encode("utf-8")
        while not self.stop_event.is_set():
            request = Request(
                self.result_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            if self._perform_request(request, "inference result"):
                return
            self.stop_event.wait(min(1.0, self.config.heartbeat_interval_seconds))

    def _heartbeat(self) -> None:
        request = Request(self.health_url, method="GET")
        if self._perform_request(request, "heartbeat"):
            self.verbose_info("heartbeat is healthy")

    def _perform_request(self, request: Request, operation: str) -> bool:
        started_at = time.monotonic()
        try:
            with self.opener(
                request,
                timeout=self.config.request_timeout_seconds,
            ) as response:
                status = response.getcode()
                response.read()
            if not 200 <= status < 300:
                raise CommunicationError(f"server returned HTTP {status}")
        except HTTPError as exc:
            return self._record_failure(operation, f"HTTP {exc.code}")
        except (URLError, TimeoutError, OSError, CommunicationError) as exc:
            return self._record_failure(operation, str(exc))

        elapsed = time.monotonic() - started_at
        self.consecutive_failures = 0
        if elapsed >= self.config.heartbeat_warning_seconds:
            LOGGER.warning("%s was slow: %.3f seconds", operation, elapsed)
        return True

    def _record_failure(self, operation: str, detail: str) -> bool:
        self.consecutive_failures += 1
        LOGGER.warning(
            "%s failed (%d/%d): %s",
            operation,
            self.consecutive_failures,
            self.config.max_consecutive_failures,
            detail,
        )
        if self.consecutive_failures >= self.config.max_consecutive_failures:
            raise CommunicationError(
                f"{operation} failed {self.consecutive_failures} consecutive times"
            )
        return False

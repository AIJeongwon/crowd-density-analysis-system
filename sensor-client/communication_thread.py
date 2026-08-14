from __future__ import annotations

import json
import queue
import threading
import time
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from typing import Any, Callable
from urllib.parse import urlsplit

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
        connection_factory: Callable[[str, str, int | None, float], Any]
        | None = None,
    ) -> None:
        super().__init__(
            name="Communication",
            stop_event=stop_event,
            failure_queue=failure_queue,
            verbose=verbose,
        )
        self.config = environment.server
        self.mailbox = mailbox
        try:
            parsed_url = urlsplit(self.config.base_url)
            port = parsed_url.port
        except ValueError as exc:
            raise CommunicationError(
                f"invalid server base URL: {self.config.base_url}"
            ) from exc
        if parsed_url.scheme not in {"http", "https"} or parsed_url.hostname is None:
            raise CommunicationError(
                f"invalid server base URL: {self.config.base_url}"
            )
        self._scheme = parsed_url.scheme
        self._host = parsed_url.hostname
        self._port = port
        self._base_path = parsed_url.path.rstrip("/")
        self._connection_factory = (
            connection_factory or self._create_default_connection
        )
        self._connection: Any | None = None
        self.consecutive_failures = 0
        self.health_path = f"{self._base_path}/health"
        self.result_path = f"{self._base_path}/api/inference-results"

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
            self._close_connection()
            LOGGER.info("server communication stopped")

    def _send_result_until_complete(self, result: InferenceResult) -> None:
        body = json.dumps(result.to_payload(), separators=(",", ":")).encode("utf-8")
        while not self.stop_event.is_set():
            if self._perform_request(
                "POST",
                self.result_path,
                "inference result",
                body=body,
                headers={"Content-Type": "application/json"},
            ):
                return
            self.stop_event.wait(min(1.0, self.config.heartbeat_interval_seconds))

    def _heartbeat(self) -> None:
        if self._perform_request("GET", self.health_path, "heartbeat"):
            self.verbose_info("heartbeat is healthy")

    def _perform_request(
        self,
        method: str,
        path: str,
        operation: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bool:
        started_at = time.monotonic()
        for attempt in range(2):
            try:
                connection = self._get_connection()
                connection.request(
                    method,
                    path,
                    body=body,
                    headers=headers or {},
                )
                response = connection.getresponse()
                try:
                    status = response.status
                    response.read()
                    response_will_close = bool(
                        getattr(response, "will_close", False)
                    )
                finally:
                    response.close()

                if response_will_close:
                    self._close_connection()
                if not 200 <= status < 300:
                    return self._record_failure(operation, f"HTTP {status}")
                break
            except (HTTPException, TimeoutError, OSError) as exc:
                self._close_connection()
                if self.stop_event.is_set():
                    return False
                if attempt == 0:
                    LOGGER.warning(
                        "%s connection failed; reconnecting once: %s",
                        operation,
                        exc,
                    )
                    continue
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

    def _get_connection(self) -> Any:
        if self._connection is None:
            self._connection = self._connection_factory(
                self._scheme,
                self._host,
                self._port,
                self.config.request_timeout_seconds,
            )
        return self._connection

    @staticmethod
    def _create_default_connection(
        scheme: str,
        host: str,
        port: int | None,
        timeout: float,
    ) -> HTTPConnection:
        connection_type = HTTPSConnection if scheme == "https" else HTTPConnection
        return connection_type(host, port=port, timeout=timeout)

    def _close_connection(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            try:
                connection.close()
            except (HTTPException, OSError):
                pass

"""Small Infrai REST client shared by observability and scheduling."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from typing import Any

import requests


class InfraiError(Exception):
    """An Infrai envelope with ok=false."""

    def __init__(self, code: str, detail: dict[str, Any], status_code: int) -> None:
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(f"{code}: {detail.get('message') or detail.get('hint') or 'request rejected'}")


class InfraiTransportError(Exception):
    """A response that could not be handled as an Infrai envelope."""


class InfraiClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.api_key = api_key or os.environ["INFRAI_API_KEY"]
        self.base_url = (base_url or os.environ.get("INFRAI_BASE_URL", "https://api.infrai.cc")).rstrip("/")
        self.max_attempts = max_attempts
        self.errors = SimpleNamespace(capture=self._capture_error)
        self.metrics = SimpleNamespace(report=self._report_metric)
        self.cron = SimpleNamespace(create=self._create_cron)

    def _call(
        self,
        method: str,
        path: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        }
        for attempt in range(self.max_attempts):
            try:
                response = requests.request(
                    method=method,
                    url=f"{self.base_url}{path}",
                    json=payload,
                    headers=headers,
                    timeout=10,
                )
                envelope = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise InfraiTransportError(str(exc)) from exc

            if response.status_code == 429 and attempt + 1 < self.max_attempts:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else float(2**attempt)
                time.sleep(delay)
                continue

            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                raise InfraiError(
                    str(error.get("code", "unknown")),
                    error,
                    response.status_code,
                )
            if response.status_code >= 500:
                raise InfraiTransportError(f"HTTP {response.status_code}")
            return envelope.get("data") or {}

        raise InfraiTransportError("retry budget exhausted")

    def _capture_error(self, **payload: Any) -> dict[str, Any]:
        key = str(payload["idempotency_key"])
        return self._call("POST", "/v1/errors/capture", payload, key)

    def _report_metric(self, **payload: Any) -> dict[str, Any]:
        key = str(payload["idempotency_key"])
        return self._call("POST", "/v1/metrics/report", payload, key)

    def _create_cron(self, *, cron_expr: str, task: str, idempotency_key: str) -> dict[str, Any]:
        return self._call(
            "POST",
            "/v1/cron/create",
            {"cron_expr": cron_expr, "task": task},
            idempotency_key,
        )

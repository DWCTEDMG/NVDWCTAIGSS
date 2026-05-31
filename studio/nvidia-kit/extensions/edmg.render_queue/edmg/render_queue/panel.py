from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class RenderQueueBackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderQueueSummary:
    worker_text: str
    readiness_text: str
    status_text: str


class RenderQueueBackendClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout_s: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _json_request(self, path: str) -> dict[str, Any]:
        req = request.Request(f"{self.base_url}{path}", headers={"Accept": "application/json"}, method="GET")
        try:
            with request.urlopen(req, timeout=self.timeout_s) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RenderQueueBackendError(f"GET {path} failed with HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RenderQueueBackendError(f"GET {path} failed: {exc.reason}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RenderQueueBackendError(f"GET {path} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RenderQueueBackendError(f"GET {path} returned non-object JSON")
        return payload

    def worker_status(self) -> dict[str, Any]:
        return self._json_request("/v1/worker/status")

    def nvidia_diagnostics(self) -> dict[str, Any]:
        return self._json_request("/v1/nvidia/diagnostics")


def summarize_worker_status(payload: dict[str, Any]) -> str:
    running = bool(payload.get("running"))
    queue_size = payload.get("queue_size")
    active = payload.get("active")
    parts = [f"worker {'running' if running else 'stopped'}"]
    if queue_size is not None:
        parts.append(f"queue {queue_size}")
    if active:
        parts.append(f"active {active}")
    return ", ".join(parts)


def summarize_nvidia_readiness(payload: dict[str, Any]) -> str:
    nvidia = payload.get("nvidia") if isinstance(payload.get("nvidia"), dict) else {}
    readiness = nvidia.get("readiness") if isinstance(nvidia.get("readiness"), dict) else {}
    level = str(readiness.get("level") or "unknown")
    summary = str(readiness.get("summary") or "")
    return f"NVIDIA readiness {level}: {summary}".strip()


def summarize_render_queue(worker_payload: dict[str, Any], diagnostics_payload: dict[str, Any]) -> RenderQueueSummary:
    worker_text = summarize_worker_status(worker_payload)
    readiness_text = summarize_nvidia_readiness(diagnostics_payload)
    return RenderQueueSummary(
        worker_text=worker_text,
        readiness_text=readiness_text,
        status_text=f"{worker_text} | {readiness_text}",
    )

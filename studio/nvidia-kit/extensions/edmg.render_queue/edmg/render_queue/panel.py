from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from time import strftime
from typing import Any
from urllib import error, parse, request


class RenderQueueBackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class RenderQueueSummary:
    worker_text: str
    readiness_text: str
    status_text: str


@dataclass(frozen=True)
class RenderJobRecord:
    project_id: str
    project_root: Path
    job_path: Path
    payload: dict[str, Any]
    status: str
    stage_path: Path | None
    still_path: Path

    @property
    def job_relpath(self) -> str:
        return self.job_path.relative_to(self.project_root).as_posix()

    @property
    def still_relpath(self) -> str:
        return self.still_path.relative_to(self.project_root).as_posix()


class RenderQueueBackendClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout_s: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _json_request(self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout_s) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RenderQueueBackendError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RenderQueueBackendError(f"{method} {path} failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RenderQueueBackendError(f"{method} {path} timed out after {self.timeout_s:g}s") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RenderQueueBackendError(f"{method} {path} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RenderQueueBackendError(f"{method} {path} returned non-object JSON")
        return payload

    def worker_status(self) -> dict[str, Any]:
        return self._json_request("/v1/worker/status")

    def nvidia_diagnostics(self) -> dict[str, Any]:
        return self._json_request("/v1/nvidia/diagnostics")

    def report_render_job_result(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = parse.quote(str(project_id), safe="")
        return self._json_request(
            f"/v1/projects/{encoded}/nvidia/render-job/result",
            method="POST",
            payload=payload,
        )


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


def _safe_job_stem(path: Path) -> str:
    stem = path.stem.strip().replace(" ", "_")
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in stem)
    return cleaned.strip("_") or "rtx_render"


def _project_root_from_job(job_path: Path) -> Path:
    # <project>/outputs/nvidia/render_jobs/<job>.json
    return job_path.parents[3]


def _resolve_project_path(project_root: Path, value: object) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    return (project_root / raw).resolve()


def resolve_render_job_stage_path(project_root: Path, payload: dict[str, Any]) -> Path | None:
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    stage_path = _resolve_project_path(project_root, inputs.get("usda_path"))
    if stage_path is not None and stage_path.exists():
        return stage_path
    exports_root = project_root / "outputs" / "nvidia"
    try:
        exports = sorted(exports_root.glob("*.usda"), key=lambda path: path.stat().st_mtime, reverse=True)
    except Exception:
        exports = []
    return exports[0] if exports else stage_path


def resolve_render_job_still_path(project_root: Path, job_path: Path, payload: dict[str, Any]) -> Path:
    timeline = payload.get("timeline") if isinstance(payload.get("timeline"), list) else []
    first_scene = timeline[0] if timeline and isinstance(timeline[0], dict) else {}
    output = first_scene.get("output") if isinstance(first_scene.get("output"), dict) else {}
    sequence_dir = _resolve_project_path(project_root, output.get("sequence_dir"))
    if sequence_dir is None:
        sequence_dir = project_root / "outputs" / "nvidia" / "renders" / _safe_job_stem(job_path)
    return sequence_dir / "preview_0001.png"


def load_render_job_record(job_path: Path) -> RenderJobRecord:
    payload = json.loads(job_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{job_path} did not contain a JSON object")
    project_root = _project_root_from_job(job_path)
    project = payload.get("project") if isinstance(payload.get("project"), dict) else {}
    project_id = str(project.get("id") or project_root.name).strip() or project_root.name
    status = str(payload.get("status") or "ready_for_kit_worker").strip().lower()
    return RenderJobRecord(
        project_id=project_id,
        project_root=project_root,
        job_path=job_path,
        payload=payload,
        status=status,
        stage_path=resolve_render_job_stage_path(project_root, payload),
        still_path=resolve_render_job_still_path(project_root, job_path, payload),
    )


def discover_render_jobs(projects_root: Path, *, include_terminal: bool = False, limit: int = 20) -> list[RenderJobRecord]:
    terminal = {"completed", "failed", "canceled"}
    if not projects_root.exists():
        return []
    records: list[RenderJobRecord] = []
    candidates = sorted(
        projects_root.glob("*/outputs/nvidia/render_jobs/*.json"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        reverse=True,
    )
    for job_path in candidates:
        if job_path.name.endswith(".result.json") or job_path.name.endswith(".json.json"):
            continue
        try:
            record = load_render_job_record(job_path)
        except Exception:
            continue
        if not include_terminal and record.status in terminal:
            continue
        records.append(record)
        if len(records) >= limit:
            break
    return records


def summarize_render_jobs(records: list[RenderJobRecord]) -> str:
    if not records:
        return "no pending Kit RTX jobs"
    first = records[0]
    title = ""
    project = first.payload.get("project") if isinstance(first.payload.get("project"), dict) else {}
    if isinstance(project, dict):
        title = str(project.get("title") or project.get("name") or "")
    stage_state = "stage ready" if first.stage_path is not None and first.stage_path.exists() else "missing USDA stage"
    return f"{len(records)} pending Kit RTX job(s); next {title or first.job_path.name}: {stage_state}"


def build_render_job_result_payload(
    record: RenderJobRecord,
    *,
    status: str,
    message: str = "",
    outputs: dict[str, Any] | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    worker: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        stage_path = record.stage_path.relative_to(record.project_root).as_posix() if record.stage_path is not None else ""
    except Exception:
        stage_path = record.stage_path.as_posix() if record.stage_path is not None else ""
    base_outputs: dict[str, Any] = {
        "still": record.still_relpath,
        "stage": stage_path,
    }
    if outputs:
        base_outputs.update(outputs)
    payload: dict[str, Any] = {
        "job_path": record.job_relpath,
        "status": status,
        "message": message,
        "outputs": base_outputs,
        "worker": worker or {},
        "metrics": metrics or {},
    }
    if started_at:
        payload["started_at"] = started_at
    if completed_at:
        payload["completed_at"] = completed_at
    return payload


def timestamp() -> str:
    return strftime("%Y-%m-%d %H:%M:%S")

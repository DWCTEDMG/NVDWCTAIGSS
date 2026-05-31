from __future__ import annotations

import asyncio
from pathlib import Path

from .panel import (
    RenderJobRecord,
    RenderQueueBackendClient,
    RenderQueueBackendError,
    build_render_job_result_payload,
    discover_render_jobs,
    summarize_render_jobs,
    summarize_render_queue,
    timestamp,
)
from .worker import RenderJobExecutionError, execute_rtx_render_job, worker_identity

try:
    import carb.settings  # type: ignore
except Exception:
    carb = None  # type: ignore

try:
    import omni.ui as ui  # type: ignore
except Exception:
    ui = None  # type: ignore

try:
    import omni.ext  # type: ignore
except Exception:
    class _ExtBase:
        def on_startup(self, ext_id: str) -> None:
            return None

        def on_shutdown(self) -> None:
            return None

    class _OmniExt:
        IExt = _ExtBase

    class omni:  # type: ignore
        ext = _OmniExt()


def _setting(path: str, default: str) -> str:
    try:
        settings = carb.settings.get_settings()  # type: ignore[name-defined]
        value = settings.get(path)
        return str(value or default)
    except Exception:
        return default


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _float_setting(path: str, default: float) -> float:
    try:
        value = float(_setting(path, str(default)))
    except Exception:
        return default
    return max(0.25, value)


def _default_projects_root() -> str:
    try:
        repo_root = Path(__file__).resolve().parents[6]
        return str(repo_root / "studio" / "edmg-studio" / "python_backend" / "data" / "projects")
    except Exception:
        return ""


class EdmgRenderQueueExtension(omni.ext.IExt):  # type: ignore[name-defined]
    def on_startup(self, ext_id: str) -> None:
        self._ext_id = ext_id
        backend_url = _setting("/edmg/nvidia/backend_url", "http://127.0.0.1:8000")
        projects_root = _setting("/edmg/nvidia/projects_root", _default_projects_root())
        auto_run = _truthy(_setting("/edmg/nvidia/render_worker_autorun", "0"))
        poll_s = _float_setting("/edmg/nvidia/render_worker_poll_s", 5.0)
        self._backend = RenderQueueBackendClient(base_url=backend_url)
        self._projects_root = Path(projects_root) if projects_root else Path()
        self._auto_run = auto_run
        self._autorun_poll_s = poll_s
        self._window = None
        self._status_label = None
        self._worker_label = None
        self._readiness_label = None
        self._queue_label = None
        self._result_label = None
        self._queued_jobs: list[RenderJobRecord] = []
        self._running_task = None
        print(f"[edmg.render_queue] startup backend={backend_url}")
        print(f"[edmg.render_queue] projects_root={self._projects_root}")
        if self._auto_run:
            print(f"[edmg.render_queue] autorun polling every {self._autorun_poll_s:g}s")
        self._build_window()
        self._refresh()
        self._scan_jobs()
        if self._auto_run:
            try:
                self._running_task = asyncio.ensure_future(self._autorun_loop())
            except Exception as exc:
                print(f"[edmg.render_queue] could not schedule autorun: {exc}")

    def on_shutdown(self) -> None:
        task = getattr(self, "_running_task", None)
        if task is not None:
            try:
                task.cancel()
            except Exception:
                pass
        self._window = None
        print("[edmg.render_queue] shutdown")

    def _build_window(self) -> None:
        if ui is None:
            return
        try:
            self._window = ui.Window("EDMG Render Queue", width=420, height=320)
            with self._window.frame:
                with ui.VStack(spacing=8):
                    ui.Label("NVIDIA render queue readiness")
                    self._status_label = ui.Label("Checking backend...")
                    self._worker_label = ui.Label("")
                    self._readiness_label = ui.Label("")
                    self._queue_label = ui.Label("")
                    self._result_label = ui.Label("")
                    ui.Button("Refresh Queue Status", clicked_fn=self._refresh)
                    ui.Button("Scan RTX Jobs", clicked_fn=self._scan_jobs)
                    ui.Button("Run Next RTX Job", clicked_fn=self._run_next_job)
        except Exception as exc:
            print(f"[edmg.render_queue] window unavailable: {exc}")
            self._window = None

    def _set_label(self, attr: str, text: str) -> None:
        label = getattr(self, attr, None)
        if label is not None:
            try:
                label.text = text
            except Exception:
                pass

    def _refresh(self) -> None:
        try:
            summary = summarize_render_queue(
                self._backend.worker_status(),
                self._backend.nvidia_diagnostics(),
            )
            print(f"[edmg.render_queue] {summary.status_text}")
            self._set_label("_status_label", summary.status_text)
            self._set_label("_worker_label", summary.worker_text)
            self._set_label("_readiness_label", summary.readiness_text)
        except RenderQueueBackendError as exc:
            message = f"backend unavailable: {exc}"
            print(f"[edmg.render_queue] {message}")
            self._set_label("_status_label", message)

    def _scan_jobs(self, *, emit_log: bool = True) -> None:
        self._queued_jobs = discover_render_jobs(self._projects_root, include_terminal=False, limit=20)
        message = summarize_render_jobs(self._queued_jobs)
        if emit_log:
            print(f"[edmg.render_queue] {message}")
        self._set_label("_queue_label", message)

    def _run_next_job(self) -> None:
        task = getattr(self, "_running_task", None)
        if task is not None and not task.done():
            self._set_label("_result_label", "RTX worker is already running a job")
            return
        try:
            self._running_task = asyncio.ensure_future(self._run_next_job_async())
        except Exception as exc:
            message = f"could not schedule RTX worker: {exc}"
            print(f"[edmg.render_queue] {message}")
            self._set_label("_result_label", message)

    async def _autorun_loop(self) -> None:
        await asyncio.sleep(2.0)
        while True:
            ran_job = await self._run_next_job_async(emit_idle_log=False)
            await asyncio.sleep(0.25 if ran_job else self._autorun_poll_s)

    async def _run_next_job_async(self, *, emit_idle_log: bool = True) -> bool:
        self._scan_jobs(emit_log=emit_idle_log)
        if not self._queued_jobs:
            self._set_label("_result_label", "No pending Kit RTX jobs to run")
            return False
        record = self._queued_jobs[0]
        started_at = timestamp()
        worker = worker_identity()
        self._set_label("_result_label", f"Running RTX job {record.job_relpath}")
        print(f"[edmg.render_queue] starting RTX job {record.job_relpath}")
        try:
            self._backend.report_render_job_result(
                record.project_id,
                build_render_job_result_payload(
                    record,
                    status="running",
                    message="Kit RTX worker started.",
                    started_at=started_at,
                    worker=worker,
                ),
            )
        except RenderQueueBackendError as exc:
            print(f"[edmg.render_queue] could not report running status: {exc}")

        try:
            outputs = await execute_rtx_render_job(record)
            completed_at = timestamp()
            self._backend.report_render_job_result(
                record.project_id,
                build_render_job_result_payload(
                    record,
                    status="completed",
                    message="Kit RTX worker rendered viewport frames from the USDA stage.",
                    outputs=outputs,
                    started_at=started_at,
                    completed_at=completed_at,
                    worker=worker,
                    metrics={"rendered_frame_count": outputs.get("rendered_frame_count"), "elapsed_s": outputs.get("elapsed_s")},
                ),
            )
            message = f"Completed RTX job {record.job_relpath}: {outputs.get('rendered_frame_count')} frame(s)"
            print(f"[edmg.render_queue] {message}")
            self._set_label("_result_label", message)
        except (RenderJobExecutionError, Exception) as exc:
            completed_at = timestamp()
            try:
                self._backend.report_render_job_result(
                    record.project_id,
                    build_render_job_result_payload(
                        record,
                        status="failed",
                        message=str(exc),
                        started_at=started_at,
                        completed_at=completed_at,
                        worker=worker,
                    ),
                )
            except RenderQueueBackendError as report_exc:
                print(f"[edmg.render_queue] could not report failed status: {report_exc}")
            message = f"RTX job failed: {exc}"
            print(f"[edmg.render_queue] {message}")
            self._set_label("_result_label", message)
        finally:
            self._scan_jobs(emit_log=True)
        return True


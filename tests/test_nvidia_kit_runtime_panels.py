from __future__ import annotations

import importlib
import importlib.util
import asyncio
import json
import subprocess
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
KIT_ROOT = REPO_ROOT / "studio" / "nvidia-kit"


def _import_extension_module(ext_name: str, package_name: str, module_name: str):
    module_root = KIT_ROOT / "extensions" / ext_name / "edmg" / package_name
    test_package = f"edmg_kit_{package_name}_test"
    package = sys.modules.get(test_package)
    if package is None:
        package = types.ModuleType(test_package)
        package.__path__ = [str(module_root)]  # type: ignore[attr-defined]
        sys.modules[test_package] = package
    qualified = f"{test_package}.{module_name}"
    if qualified in sys.modules:
        return importlib.import_module(qualified)
    spec = importlib.util.spec_from_file_location(qualified, module_root / f"{module_name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


class _RenderHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return None

    def _send_json(self, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path == "/v1/worker/status":
            self._send_json({"ok": True, "running": True, "queue_size": 2, "active": "rtx-preview"})
            return
        if self.path == "/v1/nvidia/diagnostics":
            self._send_json(
                {
                    "ok": True,
                    "nvidia": {
                        "readiness": {
                            "level": "partial",
                            "summary": "NIM reachable; render worker pending.",
                        }
                    },
                }
            )
            return
        self.send_error(404)


def test_timeline_panel_reads_sample_scene_plan():
    panel = _import_extension_module("edmg.timeline", "timeline", "panel")
    sample_plan = KIT_ROOT / "sample_projects" / "audio_reactive_stage" / "scene_plan.json"

    summary = panel.load_scene_plan_summary(sample_plan)
    lines = panel.cue_lines(summary, limit=2)

    assert summary.scene_count > 0
    assert summary.duration_s > 0
    assert "cues" in summary.status_text
    assert 1 <= len(lines) <= 3


def test_timeline_extension_starts_headless():
    extension = _import_extension_module("edmg.timeline", "timeline", "extension")

    ext = extension.EdmgTimelineExtension()
    ext.on_startup("edmg.timeline-test")
    ext.on_shutdown()

    assert ext._ext_id == "edmg.timeline-test"


def test_render_queue_panel_client_and_summary():
    panel = _import_extension_module("edmg.render_queue", "render_queue", "panel")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RenderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = panel.RenderQueueBackendClient(base_url=f"http://127.0.0.1:{server.server_port}")
        summary = panel.summarize_render_queue(client.worker_status(), client.nvidia_diagnostics())

        assert summary.worker_text == "worker running, queue 2, active rtx-preview"
        assert "NVIDIA readiness partial" in summary.readiness_text
        assert "render worker pending" in summary.status_text
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_render_queue_discovers_pending_rtx_jobs(tmp_path):
    panel = _import_extension_module("edmg.render_queue", "render_queue", "panel")
    project_root = tmp_path / "project-one"
    render_jobs = project_root / "outputs" / "nvidia" / "render_jobs"
    render_jobs.mkdir(parents=True)
    stage = project_root / "outputs" / "nvidia" / "project.usda"
    stage.parent.mkdir(parents=True, exist_ok=True)
    stage.write_text("#usda 1.0\n", encoding="utf-8")
    job_path = render_jobs / "project_job.json"
    job_path.write_text(
        json.dumps(
            {
                "schema_version": "edmg.nvidia.render_job.v1",
                "kind": "nvidia_rtx_render_job",
                "project": {"id": "project-one", "title": "Project One"},
                "renderer": {"fps": 24, "resolution": {"width": 1280, "height": 720}, "output_format": "mp4"},
                "inputs": {"usda_path": "outputs/nvidia/project.usda"},
                "timeline": [
                    {
                        "id": "scene-1",
                        "start_frame": 0,
                        "output": {
                            "sequence_dir": "outputs/nvidia/renders/001_scene-1",
                            "movie": "outputs/nvidia/renders/001_scene-1.mp4",
                        },
                    }
                ],
                "status": "ready_for_kit_worker",
            }
        ),
        encoding="utf-8",
    )

    records = panel.discover_render_jobs(tmp_path)
    result_payload = panel.build_render_job_result_payload(records[0], status="completed", outputs={"frames": ["a.png"]})

    assert len(records) == 1
    assert records[0].project_id == "project-one"
    assert records[0].job_relpath == "outputs/nvidia/render_jobs/project_job.json"
    assert records[0].stage_path == stage
    assert records[0].still_relpath == "outputs/nvidia/renders/001_scene-1/preview_0001.png"
    assert "Project One" in panel.summarize_render_jobs(records)
    assert result_payload["job_path"] == "outputs/nvidia/render_jobs/project_job.json"
    assert result_payload["outputs"]["stage"] == "outputs/nvidia/project.usda"
    assert result_payload["outputs"]["frames"] == ["a.png"]


def test_render_queue_assembles_mp4_with_ffmpeg_concat(tmp_path, monkeypatch):
    panel = _import_extension_module("edmg.render_queue", "render_queue", "panel")
    worker = _import_extension_module("edmg.render_queue", "render_queue", "worker")
    project_root = tmp_path / "project-one"
    stage = project_root / "outputs" / "nvidia" / "project.usda"
    job_path = project_root / "outputs" / "nvidia" / "render_jobs" / "project_job.json"
    frames = [
        project_root / "outputs" / "nvidia" / "renders" / "001_scene-1" / "001_scene-1_000000.png",
        project_root / "outputs" / "nvidia" / "renders" / "002_scene-2" / "002_scene-2_000060.png",
    ]
    for path in [stage, job_path, *frames]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"frame")

    payload = {
        "renderer": {"fps": 24, "output_format": "mp4"},
        "outputs": {"movie": "outputs/nvidia/renders/final_timeline.mp4"},
        "timeline": [
            {
                "id": "scene-1",
                "start_s": 0,
                "end_s": 2.5,
                "output": {"movie": "outputs/nvidia/renders/001_scene-1.mp4"},
            },
            {"id": "scene-2", "start_frame": 60, "end_frame": 120, "output": {}},
        ],
    }
    record = panel.RenderJobRecord(
        project_id="project-one",
        project_root=project_root,
        job_path=job_path,
        payload=payload,
        status="ready_for_kit_worker",
        stage_path=stage,
        still_path=frames[0],
    )
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append((cmd, kwargs))
        Path(cmd[-1]).write_bytes(b"mp4")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(worker, "_ffmpeg_executable", lambda: r"C:\ffmpeg\bin\ffmpeg.exe")
    monkeypatch.setattr(worker.subprocess, "run", fake_run)

    movie_path, concat_path = worker._assemble_movie_from_frames(record, payload["timeline"], frames, 24)

    assert movie_path == project_root / "outputs" / "nvidia" / "renders" / "final_timeline.mp4"
    assert movie_path.read_bytes() == b"mp4"
    assert commands[0][0][0] == r"C:\ffmpeg\bin\ffmpeg.exe"
    vf_index = commands[0][0].index("-vf")
    assert commands[0][0][vf_index + 1] == "fps=24,format=yuv420p"
    concat_text = concat_path.read_text(encoding="utf-8")
    assert concat_text.count("duration 2.500000") == 2
    assert concat_text.count("file '") == 2


def test_render_queue_extension_starts_headless_with_fake_backend(monkeypatch):
    extension = _import_extension_module("edmg.render_queue", "render_queue", "extension")

    class FakeClient:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url

        def worker_status(self) -> dict:
            return {"ok": True, "running": False}

        def nvidia_diagnostics(self) -> dict:
            return {"nvidia": {"readiness": {"level": "disabled", "summary": "NVIDIA profile is disabled."}}}

    monkeypatch.setattr(extension, "RenderQueueBackendClient", FakeClient)

    ext = extension.EdmgRenderQueueExtension()
    ext.on_startup("edmg.render_queue-test")
    ext.on_shutdown()

    assert ext._backend.base_url == "http://127.0.0.1:8000"


def test_render_queue_autorun_loop_repeats_until_cancelled(monkeypatch):
    extension = _import_extension_module("edmg.render_queue", "render_queue", "extension")
    ext = extension.EdmgRenderQueueExtension()
    ext._autorun_poll_s = 0.5
    calls = []
    sleeps = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def fake_run_next_job_async(*, emit_idle_log: bool = True) -> bool:
        assert emit_idle_log is False
        calls.append("run")
        if len(calls) >= 2:
            raise asyncio.CancelledError()
        return False

    monkeypatch.setattr(extension.asyncio, "sleep", fake_sleep)
    ext._run_next_job_async = fake_run_next_job_async

    try:
        asyncio.run(ext._autorun_loop())
    except asyncio.CancelledError:
        pass

    assert calls == ["run", "run"]
    assert sleeps == [2.0, 0.5]

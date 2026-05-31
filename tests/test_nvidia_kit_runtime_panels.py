from __future__ import annotations

import importlib
import importlib.util
import json
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

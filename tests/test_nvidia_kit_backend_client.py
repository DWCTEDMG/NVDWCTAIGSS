from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_PATH = (
    REPO_ROOT
    / "studio"
    / "nvidia-kit"
    / "extensions"
    / "edmg.ai_director"
    / "edmg"
    / "ai_director"
    / "backend_client.py"
)
SMOKE_SCRIPT = REPO_ROOT / "studio" / "nvidia-kit" / "tools" / "smoke_ai_director_backend.py"
SAMPLE_PLAN = REPO_ROOT / "studio" / "nvidia-kit" / "sample_projects" / "audio_reactive_stage" / "scene_plan.json"


def _load_client_module():
    spec = importlib.util.spec_from_file_location("backend_client", CLIENT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Handler(BaseHTTPRequestHandler):
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
        if self.path == "/v1/slow":
            time.sleep(0.2)
            self._send_json({"ok": True})
            return
        if self.path == "/v1/nvidia/status":
            self._send_json({"ok": True, "nvidia": {"enabled": True, "profile": "omniverse"}})
            return
        if self.path == "/v1/nvidia/services":
            self._send_json(
                {
                    "ok": True,
                    "services": {
                        "cosmos": {"configured": True, "probe": {"reachable": True}},
                        "triton": {"configured": True, "probe": {"reachable": True}},
                    },
                    "readiness": {"level": "ready"},
                }
            )
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/v1/nvidia/services/") and parsed.path.endswith("/status"):
            service_id = unquote(parsed.path.split("/")[4])
            self._send_json(
                {
                    "ok": True,
                    "service_id": service_id,
                    "service": {"configured": True, "probe": {"reachable": True}},
                }
            )
            return
        if self.path == "/v1/projects":
            self._send_json(
                {
                    "projects": [
                        {"id": "project one", "name": "Project One"},
                        {"id": "project-two", "name": "Project Two"},
                    ]
                }
            )
            return
        if parsed.path.startswith("/v1/projects/") and parsed.path.count("/") == 3:
            project_id = unquote(parsed.path.split("/")[3])
            self._send_json({"project": {"id": project_id, "name": "Loaded Project", "meta": {}}})
            return
        if parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/nvidia/scene-plan"):
            project_id = unquote(parsed.path.split("/")[3])
            self._send_json(
                {
                    "ok": True,
                    "project_id": project_id,
                    "scene_plan": {
                        "project_id": project_id,
                        "title": "Persisted",
                        "duration_s": 10,
                        "scenes": [{"id": "persisted", "start_s": 0, "end_s": 10, "prompt": "persisted"}],
                    },
                    "planner": {"provider": "openai_compat", "model": "test-model", "variant_index": 0},
                    "saved_at": "2026-05-31 12:00:00",
                    "history": [{"scene_count": 1}],
                    "usd_stage": {
                        "format": "usda",
                        "text": '#usda 1.0\ncustom string edmg:projectId = "persisted"\n',
                    },
                }
            )
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        is_project_generate = parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/nvidia/scene-plan")
        is_project_export = parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/nvidia/scene-plan/export")
        is_project_create = parsed.path == "/v1/projects"
        is_audio_upload = parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/assets/audio")
        is_audio_analysis = parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/analyze_audio")
        is_riva_transcribe = parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/nvidia/riva/transcribe")
        is_render_job = parsed.path.startswith("/v1/projects/") and parsed.path.endswith("/nvidia/render-job")
        typed_service = parsed.path in {
            "/v1/nvidia/audio2face/generate",
            "/v1/nvidia/ace/generate",
            "/v1/nvidia/cosmos/infer",
            "/v1/nvidia/triton/infer",
        }
        if (
            self.path not in {"/v1/usd/scene-plan", "/v1/nvidia/scene-plan"}
            and not is_project_create
            and not is_audio_upload
            and not is_audio_analysis
            and not is_riva_transcribe
            and not is_project_generate
            and not is_project_export
            and not is_render_job
            and not typed_service
        ):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or "0")
        raw_body = self.rfile.read(length)
        payload = {} if is_audio_upload else json.loads(raw_body.decode("utf-8") or "{}")
        if is_project_create:
            self._send_json({"project": {"id": "created-project", "name": payload.get("name"), "meta": {}}})
            return
        if is_audio_upload:
            project_id = unquote(parsed.path.split("/")[3])
            content_type = self.headers.get("Content-Type") or ""
            self._send_json(
                {
                    "ok": "multipart/form-data" in content_type and b'filename="' in raw_body,
                    "project_id": project_id,
                    "path": f"C:/tmp/{project_id}/assets/audio/uploaded.wav",
                }
            )
            return
        if is_audio_analysis:
            project_id = unquote(parsed.path.split("/")[3])
            self._send_json(
                {
                    "ok": True,
                    "project_id": project_id,
                    "analysis": {
                        "duration_s": 12.5,
                        "features": {"tempo_bpm": 128},
                        "transcript": {"text": "hush now"},
                    },
                }
            )
            return
        if is_riva_transcribe:
            project_id = unquote(parsed.path.split("/")[3])
            self._send_json(
                {
                    "ok": True,
                    "project_id": project_id,
                    "transcript": {"text": "hush now", "segments": [{"text": "hush", "start": 0, "end": 1}]},
                }
            )
            return
        if is_render_job:
            project_id = unquote(parsed.path.split("/")[3])
            self._send_json(
                {
                    "ok": True,
                    "project_id": project_id,
                    "job": {
                        "renderer": {
                            "fps": payload.get("fps"),
                            "resolution": {"width": payload.get("width"), "height": payload.get("height")},
                        }
                    },
                    "export": {"path": "outputs/nvidia/render_jobs/job.json"},
                }
            )
            return
        if typed_service:
            service_id = {
                "/v1/nvidia/audio2face/generate": "audio2face",
                "/v1/nvidia/ace/generate": "ace",
                "/v1/nvidia/cosmos/infer": "cosmos",
                "/v1/nvidia/triton/infer": "triton",
            }[parsed.path]
            self._send_json(
                {
                    "ok": True,
                    service_id: {
                        "ok": True,
                        "status_code": 200,
                        "model": payload.get("model") or f"{service_id}-test",
                        "response": {"job_id": f"{service_id}-1", "echo": payload},
                    },
                }
            )
            return
        if is_project_export:
            project_id = unquote(parsed.path.split("/")[3])
            self._send_json(
                {
                    "ok": True,
                    "project_id": project_id,
                    "export": {
                        "path": "outputs/nvidia/persisted.usda",
                        "metadata_path": "outputs/nvidia/persisted.json",
                        "scene_count": 1,
                    },
                }
            )
            return
        if self.path == "/v1/nvidia/scene-plan" or is_project_generate:
            project_id = unquote(parsed.path.split("/")[3]) if is_project_generate else payload.get("project_id") or "generated"
            payload = {
                "project_id": project_id,
                "title": payload.get("title") or "Generated",
                "duration_s": payload.get("duration_s") or 30,
                "bpm": payload.get("bpm"),
                "provider": "nvidia-profile:openai_compat:test-model",
                "scenes": [
                    {
                        "id": "generated",
                        "start_s": 0,
                        "end_s": payload.get("duration_s") or 30,
                        "prompt": "generated NVIDIA scene",
                    }
                ],
            }
        self._send_json(
            {
                "ok": True,
                "scene_plan": payload,
                "planner": {"provider": "openai_compat", "model": "test-model", "variant_index": 0},
                "project": {"persisted": bool(is_project_generate), "project_id": payload.get("project_id")},
                "usd_stage": {
                    "format": "usda",
                    "text": '#usda 1.0\ncustom string edmg:projectId = "sample"\n',
                },
            }
        )


def test_kit_backend_client_reads_status_and_scene_plan():
    module = _load_client_module()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        client = module.EdmgBackendClient(base_url=base_url)

        status = client.nvidia_status()
        projects = client.projects()
        usda = client.scene_plan_usda({"project_id": "sample", "scenes": []})

        assert status["nvidia"]["enabled"] is True
        assert projects["projects"][0]["name"] == "Project One"
        assert usda.startswith("#usda 1.0")
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_kit_backend_client_invokes_typed_nvidia_services():
    module = _load_client_module()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        client = module.EdmgBackendClient(base_url=base_url)

        services = client.nvidia_services()
        cosmos_status = client.nvidia_service_status("cosmos")
        cosmos = client.cosmos_infer({"prompt": "stage world"})
        triton = client.invoke_typed_nvidia_service("triton", {"model": "edmg_ensemble", "inputs": []})

        assert services["readiness"]["level"] == "ready"
        assert cosmos_status["service_id"] == "cosmos"
        assert cosmos["cosmos"]["response"]["job_id"] == "cosmos-1"
        assert cosmos["cosmos"]["response"]["echo"]["prompt"] == "stage world"
        assert triton["triton"]["model"] == "edmg_ensemble"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_kit_backend_client_generates_scene_plan_usda():
    module = _load_client_module()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        client = module.EdmgBackendClient(base_url=base_url)

        response = client.generate_scene_plan({"title": "Generated", "duration_s": 12})
        usda = client.generated_scene_plan_usda({"title": "Generated", "duration_s": 12})

        assert response["planner"]["provider"] == "openai_compat"
        assert response["scene_plan"]["provider"] == "nvidia-profile:openai_compat:test-model"
        assert usda.startswith("#usda 1.0")
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_kit_backend_client_defaults_to_nim_generation_timeout():
    module = _load_client_module()
    client = module.EdmgBackendClient()

    assert client.timeout_s >= 120


def test_kit_backend_client_reports_timeout_cleanly():
    module = _load_client_module()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        client = module.EdmgBackendClient(base_url=base_url, timeout_s=0.01)

        try:
            client._json_request("GET", "/v1/slow")
        except module.BackendClientError as exc:
            assert "timed out after" in str(exc)
        else:
            raise AssertionError("Expected BackendClientError for timeout")
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_kit_backend_client_reads_and_generates_project_scene_plan():
    module = _load_client_module()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        client = module.EdmgBackendClient(base_url=base_url)

        response = client.generate_project_scene_plan("project one", {"title": "Generated", "duration_s": 12})
        persisted = client.project_scene_plan("project one")
        usda = client.project_scene_plan_usda("project one")
        exported = client.export_project_scene_plan("project one")

        assert response["project"]["persisted"] is True
        assert response["scene_plan"]["project_id"] == "project one"
        assert persisted["project_id"] == "project one"
        assert persisted["history"][0]["scene_count"] == 1
        assert usda.startswith("#usda 1.0")
        assert exported["export"]["path"] == "outputs/nvidia/persisted.usda"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_kit_backend_client_supports_guided_studio_workflow(tmp_path, monkeypatch):
    module = _load_client_module()
    audio_path = tmp_path / "uploaded.wav"
    audio_path.write_bytes(b"RIFFfake")
    monkeypatch.setenv("EDMG_KIT_AUDIO_SEARCH_DIRS", str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        client = module.EdmgBackendClient(base_url=base_url)

        created = client.create_project("Created Project")
        loaded = client.project("created-project")
        uploaded = client.upload_project_audio("created-project", audio_path.name)
        analysis = client.analyze_project_audio("created-project")
        transcript = client.transcribe_project_audio_riva("created-project")
        render_job = client.create_project_render_job("created-project", {"fps": 24, "width": 1920, "height": 1080})

        assert created["project"]["id"] == "created-project"
        assert loaded["project"]["name"] == "Loaded Project"
        assert uploaded["ok"] is True
        assert uploaded["path"].endswith("uploaded.wav")
        assert analysis["analysis"]["features"]["tempo_bpm"] == 128
        assert transcript["transcript"]["segments"][0]["text"] == "hush"
        assert render_job["job"]["renderer"]["resolution"]["width"] == 1920
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_kit_backend_client_resolves_filename_only_audio_path(tmp_path, monkeypatch):
    module = _load_client_module()
    audio_path = tmp_path / "song.wav"
    audio_path.write_bytes(b"RIFFfake")
    monkeypatch.setenv("EDMG_KIT_AUDIO_SEARCH_DIRS", str(tmp_path))

    assert module.resolve_audio_upload_path("song.wav") == audio_path


def test_kit_backend_smoke_script_writes_optional_usda(tmp_path):
    out_path = tmp_path / "from_backend.usda"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(SMOKE_SCRIPT),
                "--backend-url",
                f"http://127.0.0.1:{server.server_port}",
                "--scene-plan",
                str(SAMPLE_PLAN),
                "--output-usda",
                str(out_path),
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert "Smoke complete" in result.stdout
        assert out_path.read_text(encoding="utf-8").startswith("#usda 1.0")
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_kit_backend_smoke_script_can_generate_usda(tmp_path):
    out_path = tmp_path / "generated_from_backend.usda"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(SMOKE_SCRIPT),
                "--backend-url",
                f"http://127.0.0.1:{server.server_port}",
                "--generate",
                "--title",
                "Generated Smoke",
                "--duration-s",
                "12",
                "--output-usda",
                str(out_path),
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert "Planner: openai_compat test-model" in result.stdout
        assert "Smoke complete" in result.stdout
        assert out_path.read_text(encoding="utf-8").startswith("#usda 1.0")
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_kit_backend_smoke_script_can_generate_and_fetch_project_usda(tmp_path):
    out_path = tmp_path / "project_generated.usda"
    persisted_out_path = tmp_path / "project_persisted.usda"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_args = [
            sys.executable,
            str(SMOKE_SCRIPT),
            "--backend-url",
            f"http://127.0.0.1:{server.server_port}",
            "--project-id",
            "project one",
        ]
        generated = subprocess.run(
            [
                *base_args,
                "--generate",
                "--title",
                "Generated Smoke",
                "--duration-s",
                "12",
                "--output-usda",
                str(out_path),
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        fetched = subprocess.run(
            [
                *base_args,
                "--fetch-persisted",
                "--output-usda",
                str(persisted_out_path),
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        assert generated.returncode == 0, generated.stderr + generated.stdout
        assert "Persisted: True project one" in generated.stdout
        assert fetched.returncode == 0, fetched.stderr + fetched.stdout
        assert "Persisted project: project one" in fetched.stdout
        assert out_path.read_text(encoding="utf-8").startswith("#usda 1.0")
        assert persisted_out_path.read_text(encoding="utf-8").startswith("#usda 1.0")
    finally:
        server.shutdown()
        thread.join(timeout=5)

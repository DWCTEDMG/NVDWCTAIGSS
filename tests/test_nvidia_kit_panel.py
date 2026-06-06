from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AI_DIRECTOR_ROOT = REPO_ROOT / "studio" / "nvidia-kit" / "extensions" / "edmg.ai_director"
AI_DIRECTOR_MODULE_ROOT = AI_DIRECTOR_ROOT / "edmg" / "ai_director"
TEST_PACKAGE = "edmg_kit_ai_director_test"


def _import_ai_director_module(name: str):
    package = sys.modules.get(TEST_PACKAGE)
    if package is None:
        package = types.ModuleType(TEST_PACKAGE)
        package.__path__ = [str(AI_DIRECTOR_MODULE_ROOT)]  # type: ignore[attr-defined]
        sys.modules[TEST_PACKAGE] = package
    module_name = f"{TEST_PACKAGE}.{name}"
    if module_name in sys.modules:
        return importlib.import_module(module_name)
    spec = importlib.util.spec_from_file_location(module_name, AI_DIRECTOR_MODULE_ROOT / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_kit_panel_builds_project_generation_payload():
    panel = _import_ai_director_module("panel")

    payload = panel.build_generation_payload(
        title="  Projected Stage  ",
        duration_s="90.5",
        bpm="128.25",
        max_scenes="999",
        notes="lean into RTX reflections",
    )

    assert payload == {
        "title": "Projected Stage",
        "duration_s": 90.5,
        "bpm": 128.25,
        "max_scenes": 64,
        "num_variants": 1,
        "variant_index": 0,
        "user_notes": "lean into RTX reflections",
    }


def test_kit_panel_builds_workflow_summaries():
    panel = _import_ai_director_module("panel")

    project_summary = panel.summarize_project_response(
        {"project": {"id": "project-one", "name": "Project One", "meta": {}}}
    )
    analysis_summary = panel.summarize_analysis_response(
        {
            "analysis": {
                "duration_s": 91.25,
                "features": {"tempo_bpm": 123.5},
                "transcript": {"text": "Hush now"},
            }
        }
    )
    render_payload = panel.build_render_job_payload(fps="999", width="3840", height="2160")
    render_summary = panel.summarize_render_job_response(
        {
            "job": {
                "renderer": {"fps": 24, "resolution": {"width": 1920, "height": 1080}},
            },
            "export": {"path": "outputs/nvidia/render_jobs/job.json"},
        }
    )

    assert project_summary.project_id == "project-one"
    assert "Project One" in project_summary.status_text
    assert "91.25s" in analysis_summary
    assert "123.5 BPM" in analysis_summary
    assert "transcript text available" in analysis_summary
    assert render_payload == {"fps": 240, "width": 3840, "height": 2160}
    assert "outputs/nvidia/render_jobs/job.json" in render_summary


def test_kit_panel_summarizes_and_writes_usda_preview(tmp_path):
    panel = _import_ai_director_module("panel")
    response = {
        "project_id": "project/one",
        "saved_at": "2026-05-31 12:00:00",
        "planner": {"provider": "openai_compat", "model": "test-model"},
        "scene_plan": {
            "title": "Persisted NVIDIA Stage",
            "project_id": "project/one",
            "duration_s": 12,
            "provider": "nvidia-profile:openai_compat:test-model",
            "scenes": [
                {"id": "a", "start_s": 0, "end_s": 6, "prompt": "a"},
                {"id": "b", "start_s": 6, "end_s": 12, "prompt": "b"},
            ],
        },
        "usd_stage": {"format": "usda", "text": "#usda 1.0\n"},
    }

    summary = panel.summarize_scene_plan_response(response)
    written = panel.write_usda_preview(response, root=tmp_path)

    assert summary.title == "Persisted NVIDIA Stage"
    assert summary.scene_count == 2
    assert summary.project_id == "project/one"
    assert summary.usda_bytes > 0
    assert "Persisted NVIDIA Stage" in summary.status_text
    assert written.name == "project_one.usda"
    assert written.read_text(encoding="utf-8").startswith("#usda 1.0")


def test_kit_panel_resolves_project_name_to_id():
    panel = _import_ai_director_module("panel")
    projects = {
        "projects": [
            {"id": "4af898d1ebb24a0d8ed2d59a337dd965", "name": "the end"},
            {"id": "project-two", "name": "Project Two"},
        ]
    }

    assert panel.resolve_project_reference("the end", projects) == "4af898d1ebb24a0d8ed2d59a337dd965"
    assert panel.resolve_project_reference("THE END", projects) == "4af898d1ebb24a0d8ed2d59a337dd965"
    assert panel.resolve_project_reference("project-two", projects) == "project-two"
    assert panel.resolve_project_reference("unknown", projects) == "unknown"


def test_kit_panel_builds_and_summarizes_optional_service_payloads():
    panel = _import_ai_director_module("panel")

    default_payload = panel.build_optional_service_payload("cosmos", "")
    custom_payload = panel.build_optional_service_payload("audio_2_face", '{"path":"/v1/a2f/jobs","audio_path":"voice.wav"}')
    services_summary = panel.summarize_nvidia_services_response(
        {
            "services": {
                "nim": {"configured": True, "probe": {"reachable": True}},
                "audio2face": {"configured": True, "probe": {"reachable": False}},
                "cosmos": {"configured": False, "probe": {"reachable": False}},
            },
            "readiness": {"level": "partial"},
        }
    )
    invoke_summary = panel.summarize_optional_service_response(
        "cosmos",
        {"ok": True, "cosmos": {"ok": True, "status_code": 200, "model": "cosmos-test", "response": {"job_id": "1"}}},
    )

    assert default_payload["prompt"] == "audio-reactive RTX stage world"
    assert custom_payload["audio_path"] == "voice.wav"
    assert services_summary.configured == ("audio2face", "nim")
    assert services_summary.reachable == ("nim",)
    assert "readiness partial" in services_summary.status_text
    assert "cosmos ok, HTTP 200, model cosmos-test" in invoke_summary
    assert "response keys: job_id" in invoke_summary


def test_kit_panel_rejects_invalid_optional_service_json():
    panel = _import_ai_director_module("panel")

    try:
        panel.build_optional_service_payload("cosmos", "[1, 2, 3]")
    except ValueError as exc:
        assert "root must be an object" in str(exc)
    else:
        raise AssertionError("Expected invalid service payload JSON to be rejected")


def test_kit_ai_director_resolves_project_model_name_to_id():
    extension = _import_ai_director_module("extension")

    class FakeModel:
        def __init__(self, value: str) -> None:
            self.value = value

        def get_value_as_string(self) -> str:
            return self.value

        def set_value(self, value: str) -> None:
            self.value = value

    class FakeBackend:
        def projects(self) -> dict:
            return {"projects": [{"id": "project-id-1", "name": "The End"}]}

    ext = extension.EdmgAiDirectorExtension()
    ext._project_model = FakeModel("the end")
    ext._status_label = None
    ext._backend = FakeBackend()

    assert ext._resolve_project_id(required=True, action="fetch a persisted scene plan") == "project-id-1"
    assert ext._project_model.value == "project-id-1"


def test_kit_ai_director_invokes_typed_service_from_json():
    extension = _import_ai_director_module("extension")

    class FakeModel:
        def __init__(self, value: str) -> None:
            self.value = value

        def get_value_as_string(self) -> str:
            return self.value

        def set_value(self, value: str) -> None:
            self.value = value

    class FakeBackend:
        def __init__(self) -> None:
            self.calls = []

        def invoke_typed_nvidia_service(self, service_id: str, payload: dict) -> dict:
            self.calls.append((service_id, payload))
            return {"ok": True, service_id: {"ok": True, "status_code": 200, "response": {"job_id": "svc-1"}}}

    backend = FakeBackend()
    ext = extension.EdmgAiDirectorExtension()
    ext._service_model = FakeModel("cosmos")
    ext._service_payload_model = FakeModel('{"prompt":"stage world"}')
    ext._service_label = FakeModel("")
    ext._backend = backend

    ext._invoke_optional_service()

    assert backend.calls == [("cosmos", {"prompt": "stage world"})]
    assert "cosmos ok, HTTP 200" in ext._service_label.text


def test_kit_ai_director_runs_guided_project_audio_render_flow():
    extension = _import_ai_director_module("extension")

    class FakeModel:
        def __init__(self, value: str = "") -> None:
            self.value = value
            self.text = value

        def get_value_as_string(self) -> str:
            return self.value

        def set_value(self, value: str) -> None:
            self.value = value

    class FakeBackend:
        def __init__(self) -> None:
            self.calls = []

        def create_project(self, name: str) -> dict:
            self.calls.append(("create_project", name))
            return {"project": {"id": "project-one", "name": name, "meta": {}}}

        def projects(self) -> dict:
            return {"projects": [{"id": "project-one", "name": "Project One"}]}

        def project(self, project_id: str) -> dict:
            self.calls.append(("project", project_id))
            return {
                "project": {
                    "id": project_id,
                    "name": "Project One",
                    "meta": {"audio": {"filename": "song.wav"}, "analysis": {"duration_s": 12, "features": {"bpm": 128}}},
                }
            }

        def upload_project_audio(self, project_id: str, audio_path: str) -> dict:
            self.calls.append(("upload_audio", project_id, audio_path))
            return {"ok": True, "path": audio_path}

        def analyze_project_audio(self, project_id: str) -> dict:
            self.calls.append(("analyze_audio", project_id))
            return {"ok": True, "analysis": {"duration_s": 14, "features": {"tempo_bpm": 130}, "transcript": {"text": "hi"}}}

        def transcribe_project_audio_riva(self, project_id: str, payload: dict) -> dict:
            self.calls.append(("riva", project_id, payload))
            return {"ok": True, "transcript": {"text": "hi", "segments": [{"text": "hi"}]}}

        def export_project_scene_plan(self, project_id: str) -> dict:
            self.calls.append(("export", project_id))
            return {"ok": True, "project_id": project_id, "export": {"path": "outputs/nvidia/project.usda", "scene_count": 2}}

        def create_project_render_job(self, project_id: str, payload: dict) -> dict:
            self.calls.append(("render_job", project_id, payload))
            return {
                "ok": True,
                "job": {"renderer": {"fps": 24, "resolution": {"width": 1920, "height": 1080}}},
                "export": {"path": "outputs/nvidia/render_jobs/job.json"},
            }

    backend = FakeBackend()
    ext = extension.EdmgAiDirectorExtension()
    ext._backend = backend
    ext._project_name_model = FakeModel("Project One")
    ext._project_model = FakeModel("")
    ext._audio_path_model = FakeModel(r"D:\song.wav")
    ext._title_model = FakeModel("Project One")
    ext._duration_model = FakeModel("60")
    ext._bpm_model = FakeModel("")
    ext._render_fps_model = FakeModel("24")
    ext._render_width_model = FakeModel("1920")
    ext._render_height_model = FakeModel("1080")
    ext._status_label = FakeModel()
    ext._project_label = FakeModel()
    ext._audio_label = FakeModel()
    ext._analysis_label = FakeModel()
    ext._summary_label = FakeModel()
    ext._render_label = FakeModel()

    ext._create_project()
    ext._upload_audio()
    ext._analyze_audio()
    ext._transcribe_audio_with_riva()
    ext._queue_render_job()

    assert ext._project_model.value == "project-one"
    assert "Audio uploaded" in ext._audio_label.text
    assert ext._duration_model.value == "14"
    assert ext._bpm_model.value == "130"
    assert "Riva transcript ready" in ext._analysis_label.text
    assert "outputs/nvidia/render_jobs/job.json" in ext._render_label.text
    assert ("render_job", "project-one", {"fps": 24, "width": 1920, "height": 1080}) in backend.calls


def test_kit_ai_director_extension_starts_headless_with_fake_backend(monkeypatch):
    extension = _import_ai_director_module("extension")

    class FakeBackendClient:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url

        def nvidia_status(self) -> dict:
            return {"nvidia": {"enabled": True, "profile": "omniverse"}}

    monkeypatch.setattr(extension, "EdmgBackendClient", FakeBackendClient)

    ext = extension.EdmgAiDirectorExtension()
    ext.on_startup("edmg.ai_director-test")
    ext.on_shutdown()

    assert ext._backend.base_url == "http://127.0.0.1:8000"

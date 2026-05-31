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

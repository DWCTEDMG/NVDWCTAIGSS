from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = REPO_ROOT / "studio" / "nvidia-kit" / "tools" / "validate_scene_plan.py"
EXPORTER_PATH = REPO_ROOT / "studio" / "nvidia-kit" / "tools" / "export_scene_plan_usda.py"
SAMPLE_PLAN = (
    REPO_ROOT
    / "studio"
    / "nvidia-kit"
    / "sample_projects"
    / "audio_reactive_stage"
    / "scene_plan.json"
)


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_scene_plan", VALIDATOR_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_nvidia_scene_plan_sample_validates():
    validator = _load_validator()
    payload = validator.json.loads(SAMPLE_PLAN.read_text(encoding="utf-8"))

    assert validator.validate_scene_plan(payload) == []


def test_nvidia_scene_plan_validator_rejects_overlaps():
    validator = _load_validator()
    payload = {
        "project_id": "bad-plan",
        "title": "Bad Plan",
        "duration_s": 10,
        "scenes": [
            {"id": "a", "start_s": 0, "end_s": 6, "prompt": "one"},
            {"id": "b", "start_s": 5, "end_s": 9, "prompt": "two"},
        ],
    }

    errors = validator.validate_scene_plan(payload)

    assert "scenes[1].start_s overlaps the previous scene" in errors


def test_nvidia_scene_plan_exporter_writes_usda(tmp_path):
    out_path = tmp_path / "stage.usda"

    result = subprocess.run(
        [sys.executable, str(EXPORTER_PATH), str(SAMPLE_PLAN), str(out_path)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    text = out_path.read_text(encoding="utf-8")
    assert text.startswith("#usda 1.0")
    assert 'custom string edmg:projectId = "sample-audio-reactive-stage"' in text
    assert 'def Scope "AudioTimeline"' in text
    assert "double3 xformOp:translate.timeSamples" in text
    assert 'def Xform "drop"' in text

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from edmg_studio_backend import app as backend_app
from edmg_studio_backend.services import nvidia_optional_services


class _FakeResponse:
    def __init__(self, payload: dict, *, status: int = 200):
        self._payload = payload
        self.status = status
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int = -1) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_nvidia_service_invoke_route_calls_audio2face_without_leaking_secret(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_AUDIO2FACE_URL", "http://audio2face.local:8011")
    monkeypatch.setenv("EDMG_NVIDIA_AUDIO2FACE_API_KEY", "a2f-secret-token")
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["auth"] = req.get_header("Authorization")
        seen["timeout"] = timeout
        return _FakeResponse({"ready": True})

    monkeypatch.setattr(nvidia_optional_services.request, "urlopen", fake_urlopen)

    assert any(getattr(route, "path", None) == "/v1/nvidia/services/{service_id}/invoke" for route in backend_app.app.routes)
    payload = backend_app.nvidia_service_invoke_route(
        "a2f",
        {"path": "/v1/health?token=raw-secret", "method": "GET", "timeout_s": 2},
    )

    assert payload["ok"] is True
    assert payload["service_id"] == "audio2face"
    assert payload["result"]["response"] == {"ready": True}
    assert seen["url"] == "http://audio2face.local:8011/v1/health?token=raw-secret"
    assert seen["method"] == "GET"
    assert seen["auth"] == "Bearer a2f-secret-token"
    assert "a2f-secret-token" not in repr(payload)
    assert "raw-secret" not in repr(payload)


def test_nvidia_service_invoke_route_rejects_absolute_downstream_path(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_COSMOS_URL", "http://cosmos.local:9000")

    with pytest.raises(HTTPException) as exc:
        backend_app.nvidia_service_invoke_route("cosmos", {"path": "http://example.com/steal", "method": "GET"})

    assert exc.value.status_code == 400
    assert "relative" in str(exc.value.detail)


def test_nvidia_audio2face_generate_route_uses_configured_gateway_path(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_AUDIO2FACE_URL", "http://audio2face.local:8011")
    monkeypatch.setenv("EDMG_NVIDIA_AUDIO2FACE_GENERATE_PATH", "/v1/a2f/jobs")
    monkeypatch.setenv("EDMG_NVIDIA_AUDIO2FACE_API_KEY", "a2f-secret-token")
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["auth"] = req.get_header("Authorization")
        seen["timeout"] = timeout
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"job_id": "a2f-job-1"})

    monkeypatch.setattr(nvidia_optional_services.request, "urlopen", fake_urlopen)

    payload = backend_app.nvidia_audio2face_generate_route(
        {
            "audio_path": "outputs/dialogue.wav",
            "character": "metahuman",
            "emotion": "focused",
            "timeout_s": 4,
        }
    )

    assert payload["ok"] is True
    assert seen["url"] == "http://audio2face.local:8011/v1/a2f/jobs"
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer a2f-secret-token"
    assert seen["timeout"] == 4
    assert seen["body"]["audio_path"] == "outputs/dialogue.wav"
    assert "a2f-secret-token" not in repr(payload)


def test_nvidia_audio2face_generate_route_requires_gateway_path(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_AUDIO2FACE_URL", "http://audio2face.local:8011")
    monkeypatch.delenv("EDMG_NVIDIA_AUDIO2FACE_GENERATE_PATH", raising=False)

    with pytest.raises(HTTPException) as exc:
        backend_app.nvidia_audio2face_generate_route({"audio_path": "voice.wav"})

    assert exc.value.status_code == 400
    assert "gateway path" in str(exc.value.detail)


def test_nvidia_ace_generate_route_accepts_request_path(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_ACE_URL", "http://ace.local:8020")
    monkeypatch.setenv("EDMG_NVIDIA_ACE_API_KEY", "ace-secret-token")
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"session_id": "ace-session-1"})

    monkeypatch.setattr(nvidia_optional_services.request, "urlopen", fake_urlopen)

    payload = backend_app.nvidia_ace_generate_route(
        {
            "path": "/v1/avatar/animation",
            "avatar_id": "performer",
            "text": "Welcome to the RTX stage.",
        }
    )

    assert payload["ok"] is True
    assert seen["url"] == "http://ace.local:8020/v1/avatar/animation"
    assert seen["auth"] == "Bearer ace-secret-token"
    assert seen["body"]["avatar_id"] == "performer"
    assert "ace-secret-token" not in repr(payload)


def test_nvidia_cosmos_infer_route_posts_official_infer_payload(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_COSMOS_URL", "http://cosmos.local:8000")
    monkeypatch.setenv("EDMG_NVIDIA_COSMOS_API_KEY", "cosmos-secret-token")
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["auth"] = req.get_header("Authorization")
        seen["timeout"] = timeout
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"b64_video": "encoded", "seed": 4})

    monkeypatch.setattr(nvidia_optional_services.request, "urlopen", fake_urlopen)

    payload = backend_app.nvidia_cosmos_infer_route(
        {
            "prompt": "first person camera move through an RTX concert stage",
            "negative_prompt": "blurry, low quality",
            "seed": 4,
            "guidance_scale": 7.5,
            "steps": 50,
            "video_params": {"height": 704, "width": 1280, "frames_count": 121, "frames_per_sec": 24},
            "timeout_s": 5,
        }
    )

    assert payload["ok"] is True
    assert seen["url"] == "http://cosmos.local:8000/v1/infer"
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer cosmos-secret-token"
    assert seen["timeout"] == 5
    assert seen["body"]["video_params"]["frames_per_sec"] == 24
    assert payload["cosmos"]["response"]["seed"] == 4
    assert "cosmos-secret-token" not in repr(payload)


def test_nvidia_cosmos_infer_route_requires_prompt(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_COSMOS_URL", "http://cosmos.local:8000")

    with pytest.raises(HTTPException) as exc:
        backend_app.nvidia_cosmos_infer_route({"video_params": {"frames_count": 8}})

    assert exc.value.status_code == 400
    assert "prompt" in str(exc.value.detail)


def test_nvidia_triton_infer_route_posts_v2_infer_payload(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_TRITON_URL", "http://triton.local:8000")
    monkeypatch.setenv("EDMG_NVIDIA_TRITON_MODEL", "edmg_ensemble")
    monkeypatch.setenv("EDMG_NVIDIA_TRITON_API_KEY", "triton-secret-token")
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["auth"] = req.get_header("Authorization")
        seen["timeout"] = timeout
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"model_name": "edmg_ensemble", "outputs": [{"name": "scores"}]})

    monkeypatch.setattr(nvidia_optional_services.request, "urlopen", fake_urlopen)

    assert any(getattr(route, "path", None) == "/v1/nvidia/triton/infer" for route in backend_app.app.routes)
    payload = backend_app.nvidia_triton_infer_route(
        {
            "inputs": [{"name": "prompt", "shape": [1], "datatype": "BYTES", "data": ["hello"]}],
            "outputs": [{"name": "scores"}],
            "parameters": {"temperature": 0.2},
            "timeout_s": 3,
        }
    )

    assert payload["ok"] is True
    assert seen["url"] == "http://triton.local:8000/v2/models/edmg_ensemble/infer"
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer triton-secret-token"
    assert seen["body"]["inputs"][0]["name"] == "prompt"
    assert seen["body"]["outputs"][0]["name"] == "scores"
    assert payload["triton"]["response"]["model_name"] == "edmg_ensemble"
    assert "triton-secret-token" not in repr(payload)


def test_nvidia_triton_infer_route_requires_inputs(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_TRITON_URL", "http://triton.local:8000")
    monkeypatch.setenv("EDMG_NVIDIA_TRITON_MODEL", "edmg_ensemble")

    with pytest.raises(HTTPException) as exc:
        backend_app.nvidia_triton_infer_route({"inputs": {"name": "not-a-list"}})

    assert exc.value.status_code == 400
    assert "inputs" in str(exc.value.detail)


def test_nvidia_triton_shot_features_route_posts_scene_plan_contract(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_TRITON_URL", "http://triton.local:8000")
    monkeypatch.setenv("EDMG_NVIDIA_TRITON_SHOT_MODEL", "edmg_shot_features")
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["timeout"] = timeout
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse(
            {
                "model_name": "edmg_shot_features",
                "outputs": [
                    {
                        "name": "features_json",
                        "datatype": "BYTES",
                        "shape": [1],
                        "data": ['{"schema_version":"edmg.triton.shot_features.v1","scene_count":2}'],
                    },
                    {"name": "energy_score", "datatype": "FP32", "shape": [1], "data": [0.82]},
                    {"name": "scene_count", "datatype": "INT32", "shape": [1], "data": [2]},
                ],
            }
        )

    monkeypatch.setattr(nvidia_optional_services.request, "urlopen", fake_urlopen)

    payload = backend_app.nvidia_triton_shot_features_route(
        {
            "scene_plan": {
                "title": "RTX stage plan",
                "bpm": 128,
                "scenes": [
                    {"title": "Intro pulse", "duration_s": 8},
                    {"title": "Laser drop", "duration_s": 16},
                ],
            },
            "timeout_s": 4,
        }
    )

    assert any(getattr(route, "path", None) == "/v1/nvidia/triton/shot-features" for route in backend_app.app.routes)
    assert payload["ok"] is True
    assert seen["url"] == "http://triton.local:8000/v2/models/edmg_shot_features/infer"
    assert seen["method"] == "POST"
    assert seen["timeout"] == 4
    assert seen["body"]["inputs"][0]["name"] == "scene_plan"
    assert seen["body"]["inputs"][0]["datatype"] == "BYTES"
    assert json.loads(seen["body"]["inputs"][0]["data"][0])["scenes"][1]["title"] == "Laser drop"
    assert seen["body"]["outputs"][0]["name"] == "features_json"
    assert payload["triton"]["response"]["model_name"] == "edmg_shot_features"

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from edmg_studio_backend import app as backend_app
from edmg_studio_backend.services import nvidia_diagnostics as diagnostics_module
from edmg_studio_backend.services.nvidia_profile import nvidia_profile_status
from edmg_studio_backend.services.nvidia_scene_plan import scene_plan_usda_text
from edmg_studio_backend.store.projects import ProjectStore


def test_nvidia_profile_status_masks_ngc_key(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_MODE", "1")
    monkeypatch.setenv("EDMG_NVIDIA_PROFILE", "omniverse")
    monkeypatch.setenv("NGC_API_KEY", "secret-ngc-token")
    monkeypatch.setenv("EDMG_NVIDIA_NIM_URL", "http://nim.local:8000")
    monkeypatch.setenv("EDMG_AI_OPENAI_COMPAT_MODEL", "nvidia/model")
    monkeypatch.setenv("EDMG_RIVA_URL", "http://riva.local:50051")
    monkeypatch.setenv("EDMG_NVIDIA_RIVA_ASR", "1")
    monkeypatch.setenv("EDMG_NVIDIA_RIVA_LANGUAGE_CODE", "en-US")
    monkeypatch.setenv("EDMG_NVIDIA_TRITON_URL", "http://triton.local:8000")
    monkeypatch.setenv("EDMG_NVIDIA_TRITON_MODEL", "edmg_plan_ensemble")

    status = nvidia_profile_status()

    assert status["enabled"] is True
    assert status["profile"] == "omniverse"
    assert status["credentials"]["ngc_api_key_configured"] is True
    assert status["services"]["nim"]["base_url"] == "http://nim.local:8000"
    assert status["services"]["nim"]["model"] == "nvidia/model"
    assert status["services"]["riva"]["configured"] is True
    assert status["services"]["riva"]["asr_enabled"] is True
    assert status["services"]["riva"]["language_code"] == "en-US"
    assert status["services"]["triton"]["configured"] is True
    assert status["services"]["triton"]["model"] == "edmg_plan_ensemble"
    assert "secret-ngc-token" not in repr(status)


def test_nvidia_profile_status_uses_openai_compat_url_as_nim_fallback(monkeypatch):
    monkeypatch.delenv("EDMG_NVIDIA_NIM_URL", raising=False)
    monkeypatch.setenv("EDMG_NVIDIA_MODE", "0")
    monkeypatch.setenv("EDMG_AI_OPENAI_COMPAT_BASE_URL", "http://compat.local/v1")
    monkeypatch.delenv("NGC_API_KEY", raising=False)

    status = nvidia_profile_status()

    assert status["enabled"] is False
    assert status["credentials"]["ngc_api_key_configured"] is False
    assert status["services"]["nim"]["base_url"] == "http://compat.local/v1"
    assert status["services"]["nim"]["configured"] is True


def test_nvidia_status_route_does_not_return_secret(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_MODE", "1")
    monkeypatch.setenv("NGC_API_KEY", "route-secret-token")
    monkeypatch.setenv("EDMG_NVIDIA_NIM_URL", "http://nim.local:8000")

    assert any(getattr(route, "path", None) == "/v1/nvidia/status" for route in backend_app.app.routes)
    payload = backend_app.nvidia_status()

    assert payload["ok"] is True
    assert payload["nvidia"]["enabled"] is True
    assert payload["nvidia"]["credentials"]["ngc_api_key_configured"] is True
    assert "route-secret-token" not in repr(payload)


def test_nvidia_diagnostics_route_masks_secret_and_reports_reachability(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_MODE", "1")
    monkeypatch.setenv("NGC_API_KEY", "diag-secret-token")
    monkeypatch.setenv("EDMG_NVIDIA_NIM_URL", "http://nim.local:8000")
    monkeypatch.setenv("EDMG_AI_OPENAI_COMPAT_MODEL", "nvidia/model")

    def fake_gpu_status():
        return {"available": True, "ok": True, "gpus": [{"name": "Test RTX"}], "error": ""}

    def fake_docker_status():
        return {"available": True, "ok": True, "version": "Docker test", "nvidia_runtime": True, "error": ""}

    def fake_http_probe(url: str, **_kwargs):
        return {"configured": True, "reachable": True, "status_code": 200, "models_url_seen": url}

    def fake_tcp_probe(endpoint: str, **_kwargs):
        return {"configured": True, "reachable": True, "endpoint_seen": endpoint}

    monkeypatch.setattr(diagnostics_module, "_gpu_status", fake_gpu_status)
    monkeypatch.setattr(diagnostics_module, "_docker_status", fake_docker_status)
    monkeypatch.setattr(diagnostics_module, "_http_probe", fake_http_probe)
    monkeypatch.setattr(diagnostics_module, "_tcp_probe", fake_tcp_probe)

    assert any(getattr(route, "path", None) == "/v1/nvidia/diagnostics" for route in backend_app.app.routes)
    payload = backend_app.nvidia_diagnostics_route()

    assert payload["ok"] is True
    assert payload["nvidia"]["host"]["gpu"]["gpus"][0]["name"] == "Test RTX"
    assert payload["nvidia"]["host"]["docker"]["nvidia_runtime"] is True
    assert payload["nvidia"]["services"]["nim"]["probe"]["reachable"] is True
    assert payload["nvidia"]["services"]["nim"]["probe"]["models_url_seen"] == "http://nim.local:8000/v1/models"
    assert payload["nvidia"]["services"]["riva"]["probe"]["protocol"] == "grpc"
    assert payload["nvidia"]["readiness"]["level"] == "ready"
    assert payload["nvidia"]["readiness"]["planner_ready"] is True
    assert payload["nvidia"]["readiness"]["official_local_ready"] is True
    assert "diag-secret-token" not in repr(payload)


def test_nvidia_service_status_routes_report_optional_services(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_MODE", "1")
    monkeypatch.setenv("NGC_API_KEY", "service-secret-token")
    monkeypatch.setenv("EDMG_NVIDIA_NIM_URL", "http://nim.local:8000")
    monkeypatch.setenv("EDMG_AI_OPENAI_COMPAT_MODEL", "nvidia/model")
    monkeypatch.setenv("EDMG_NVIDIA_AUDIO2FACE_URL", "http://audio2face.local:8011")
    monkeypatch.setenv("EDMG_NVIDIA_TRITON_URL", "http://triton.local:8000")
    monkeypatch.setenv("EDMG_NVIDIA_TRITON_MODEL", "edmg_ensemble")

    monkeypatch.setattr(
        diagnostics_module,
        "_gpu_status",
        lambda: {"available": True, "ok": True, "gpus": [{"name": "Test RTX"}], "error": ""},
    )
    monkeypatch.setattr(
        diagnostics_module,
        "_docker_status",
        lambda: {"available": True, "ok": True, "version": "Docker test", "nvidia_runtime": True, "error": ""},
    )
    monkeypatch.setattr(
        diagnostics_module,
        "_http_probe",
        lambda url, **_kwargs: {"configured": True, "reachable": True, "url_seen": url},
    )
    monkeypatch.setattr(
        diagnostics_module,
        "_tcp_probe",
        lambda endpoint, **_kwargs: {"configured": True, "reachable": True, "endpoint_seen": endpoint},
    )

    services_payload = backend_app.nvidia_services_route()
    audio2face_payload = backend_app.nvidia_service_status_route("a2f")
    triton_payload = backend_app.nvidia_service_status_route("triton")

    assert any(getattr(route, "path", None) == "/v1/nvidia/services" for route in backend_app.app.routes)
    assert services_payload["ok"] is True
    assert services_payload["services"]["audio2face"]["probe"]["url_seen"] == "http://audio2face.local:8011"
    assert audio2face_payload["service_id"] == "audio2face"
    assert audio2face_payload["service"]["configured"] is True
    assert triton_payload["service"]["model"] == "edmg_ensemble"
    assert "service-secret-token" not in repr(services_payload)
    assert "service-secret-token" not in repr(audio2face_payload)


def test_nvidia_diagnostics_readiness_flags_placeholder_nim(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_MODE", "1")
    monkeypatch.delenv("NGC_API_KEY", raising=False)
    monkeypatch.setenv("EDMG_NVIDIA_NIM_URL", "https://your-nim-endpoint/v1")
    monkeypatch.setenv("EDMG_AI_OPENAI_COMPAT_MODEL", "your-nim-model")

    monkeypatch.setattr(
        diagnostics_module,
        "_gpu_status",
        lambda: {"available": True, "ok": True, "gpus": [{"name": "Test RTX"}], "error": ""},
    )
    monkeypatch.setattr(
        diagnostics_module,
        "_docker_status",
        lambda: {"available": True, "ok": True, "version": "Docker test", "nvidia_runtime": False, "error": ""},
    )
    monkeypatch.setattr(
        diagnostics_module,
        "_http_probe",
        lambda *_args, **_kwargs: {"configured": True, "reachable": False, "error": "placeholder"},
    )

    readiness = diagnostics_module.nvidia_diagnostics()["readiness"]

    assert readiness["level"] == "blocked"
    assert readiness["planner_ready"] is False
    assert readiness["official_local_ready"] is False
    failed_ids = {action["id"] for action in readiness["next_actions"]}
    assert "nim_endpoint_configured" in failed_ids
    assert "docker_nvidia_runtime" in failed_ids


def test_config_includes_nvidia_profile_without_secret(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_MODE", "1")
    monkeypatch.setenv("EDMG_NVIDIA_PROFILE", "omniverse")
    monkeypatch.setenv("NGC_API_KEY", "config-secret-token")

    payload = backend_app.get_config()

    assert payload["nvidia_mode"] is True
    assert payload["nvidia_profile_name"] == "omniverse"
    assert payload["nvidia_profile"]["credentials"]["ngc_api_key_configured"] is True
    assert "config-secret-token" not in repr(payload)


def test_setup_ai_config_labels_nvidia_openai_compat(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_MODE", "1")
    monkeypatch.setenv("EDMG_AI_PROVIDER", "openai_compat")
    monkeypatch.setenv("EDMG_AI_OPENAI_COMPAT_MODEL", "nvidia/model")

    payload = backend_app._setup_ai_config()

    assert payload["label"] == "NVIDIA NIM / OpenAI-compatible provider"
    assert payload["model"] == "nvidia/model"
    assert payload["nvidia_profile"]["enabled"] is True


def test_nvidia_usd_scene_plan_route_returns_normalized_metadata(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_MODE", "1")
    payload = {
        "project_id": " sample ",
        "title": " Sample Stage ",
        "duration_s": "60",
        "bpm": "128",
        "provider": "nvidia-nim",
        "scenes": [
            {
                "id": "intro",
                "start_s": 0,
                "end_s": 16,
                "prompt": "neon stage opens",
                "camera": "push",
                "look": "cyan lasers",
                "motion": "beat pulse",
                "usd_variant": "intro_neon",
            }
        ],
    }

    response = backend_app.nvidia_usd_scene_plan(payload)

    assert response["ok"] is True
    assert response["scene_plan"]["project_id"] == "sample"
    assert response["scene_plan"]["duration_s"] == 60.0
    assert response["scene_plan"]["bpm"] == 128.0
    assert response["usd_metadata"]["edmg:projectId"] == "sample"
    assert response["usd_metadata"]["edmg:sceneCount"] == 1
    assert response["usd_stage"]["format"] == "usda"
    assert 'def Xform "intro"' in response["usd_stage"]["text"]
    assert 'custom string edmg:variant = "intro_neon"' in response["usd_stage"]["text"]
    assert response["nvidia"]["enabled"] is True


def test_nvidia_usd_scene_plan_route_rejects_overlaps():
    payload = {
        "project_id": "sample",
        "title": "Sample",
        "duration_s": 30,
        "scenes": [
            {"id": "a", "start_s": 0, "end_s": 20, "prompt": "a"},
            {"id": "b", "start_s": 10, "end_s": 30, "prompt": "b"},
        ],
    }

    with pytest.raises(HTTPException) as exc_info:
        backend_app.nvidia_usd_scene_plan(payload)

    assert exc_info.value.status_code == 400
    assert "overlaps the previous scene" in repr(exc_info.value.detail)


def test_nvidia_generate_scene_plan_route_calls_planner_and_returns_usda(monkeypatch):
    monkeypatch.setenv("EDMG_NVIDIA_MODE", "1")
    monkeypatch.setenv("NGC_API_KEY", "planner-secret-token")

    class FakeAi:
        def plan(self, payload):
            assert payload["title"] == "Generated Stage"
            assert payload["duration_s"] == 24
            return {
                "provider": "openai_compat",
                "model": "nvidia/test-nim",
                "variants": [
                    {
                        "name": "RTX Pass",
                        "mood": "high contrast",
                        "color_palette": ["cyan", "black"],
                        "scenes": [
                            {
                                "start_s": 0,
                                "end_s": 12,
                                "prompt": "wide neon opening",
                                "camera": "push in",
                                "motion": "beat pulse",
                            },
                            {
                                "start_s": 12,
                                "end_s": 24,
                                "prompt": "drop section",
                                "camera": "orbit",
                                "motion": "strobe hits",
                            },
                        ],
                    }
                ],
            }

    monkeypatch.setattr(backend_app, "ai", FakeAi())

    payload = backend_app.nvidia_generate_scene_plan(
        {
            "project_id": "generated-stage",
            "title": "Generated Stage",
            "duration_s": 24,
            "bpm": 140,
            "style_prefs": "RTX stage",
            "max_scenes": 2,
        }
    )

    assert payload["ok"] is True
    assert payload["planner"]["provider"] == "openai_compat"
    assert payload["planner"]["model"] == "nvidia/test-nim"
    assert payload["scene_plan"]["project_id"] == "generated-stage"
    assert payload["scene_plan"]["provider"] == "nvidia-profile:openai_compat:nvidia/test-nim"
    assert payload["usd_metadata"]["edmg:sceneCount"] == 2
    assert 'def Xform "scene_1"' in payload["usd_stage"]["text"]
    assert "planner-secret-token" not in repr(payload)


def test_nvidia_generate_scene_plan_persists_existing_project(monkeypatch, tmp_path):
    monkeypatch.setenv("EDMG_NVIDIA_MODE", "1")
    monkeypatch.setenv("NGC_API_KEY", "persist-secret-token")
    project_store = ProjectStore(tmp_path)
    project = project_store.create("NVIDIA Persist Test")
    monkeypatch.setattr(backend_app, "store", project_store)

    class FakeAi:
        def plan(self, payload):
            return {
                "provider": "openai_compat",
                "model": "nvidia/test-nim",
                "variants": [
                    {
                        "name": "Persisted RTX Pass",
                        "scenes": [
                            {
                                "start_s": 0,
                                "end_s": payload["duration_s"],
                                "prompt": "saved neon stage",
                                "camera": "wide",
                                "motion": "beat pulse",
                            },
                        ],
                    }
                ],
            }

    monkeypatch.setattr(backend_app, "ai", FakeAi())

    response = backend_app.nvidia_generate_scene_plan(
        {
            "project_id": project.id,
            "title": "Persisted NVIDIA Stage",
            "duration_s": 18,
            "bpm": 126,
        }
    )

    saved = project_store.get(project.id)
    assert response["project"]["persisted"] is True
    assert saved is not None
    persisted = saved.meta["last_nvidia_scene_plan"]
    assert persisted["scene_plan"]["project_id"] == project.id
    assert persisted["usd_metadata"]["edmg:sceneCount"] == 1
    assert persisted["usd_stage"]["format"] == "usda"
    assert "saved neon stage" in persisted["usd_stage"]["text"]
    assert saved.meta["nvidia_scene_plan_history"][-1]["scene_count"] == 1
    assert "persist-secret-token" not in repr(response)
    assert "persist-secret-token" not in repr(saved.meta)


def test_project_nvidia_scene_plan_routes_generate_and_read(monkeypatch, tmp_path):
    monkeypatch.setenv("EDMG_NVIDIA_MODE", "1")
    project_store = ProjectStore(tmp_path)
    project = project_store.create("Project Scoped NVIDIA")
    monkeypatch.setattr(backend_app, "store", project_store)

    class FakeAi:
        def plan(self, payload):
            assert payload["title"] == "Project Scoped NVIDIA"
            return {
                "provider": "openai_compat",
                "model": "nvidia/test-nim",
                "variants": [
                    {
                        "name": "Project RTX Pass",
                        "scenes": [
                            {
                                "start_s": 0,
                                "end_s": 12,
                                "prompt": "project route scene",
                            }
                        ],
                    }
                ],
            }

    monkeypatch.setattr(backend_app, "ai", FakeAi())

    generated = backend_app.generate_project_nvidia_scene_plan(project.id, {"duration_s": 12})
    saved_with_stale_usda = project_store.get(project.id)
    assert saved_with_stale_usda is not None
    saved_with_stale_usda.meta["last_nvidia_scene_plan"]["usd_stage"]["text"] = "#usda 1.0\n# stale generated stage\n"
    project_store.save(saved_with_stale_usda)
    fetched = backend_app.get_project_nvidia_scene_plan(project.id)
    exported = backend_app.export_project_nvidia_scene_plan(project.id, {})
    render_job = backend_app.create_project_nvidia_render_job(project.id, {"fps": 30, "width": 1280, "height": 720})

    assert generated["project"]["persisted"] is True
    assert fetched["ok"] is True
    assert fetched["project_id"] == project.id
    assert fetched["scene_plan"]["title"] == "Project Scoped NVIDIA"
    assert fetched["scene_plan"]["scenes"][0]["prompt"] == "project route scene"
    assert fetched["history"][-1]["scene_count"] == 1
    assert exported["ok"] is True
    assert exported["export"]["path"].startswith("outputs/nvidia/")
    assert exported["export"]["path"].endswith(".usda")
    assert (project_store.project_dir(project.id) / exported["export"]["path"]).exists()
    assert exported["export"]["absolute_path"] == str((project_store.project_dir(project.id) / exported["export"]["path"]).resolve())
    assert exported["export"]["metadata_absolute_path"] == str((project_store.project_dir(project.id) / exported["export"]["metadata_path"]).resolve())
    exported_text = (project_store.project_dir(project.id) / exported["export"]["path"]).read_text(encoding="utf-8")
    assert 'def Scope "AudioTimeline"' in exported_text
    assert "double3 xformOp:translate.timeSamples" in exported_text
    assert render_job["ok"] is True
    assert render_job["job"]["kind"] == "nvidia_rtx_render_job"
    assert render_job["job"]["renderer"]["fps"] == 30
    assert render_job["job"]["renderer"]["resolution"] == {"width": 1280, "height": 720}
    assert render_job["job"]["outputs"]["movie"].startswith("outputs/nvidia/renders/project_scoped_nvidia_")
    assert render_job["job"]["outputs"]["movie"].endswith(".mp4")
    assert render_job["job"]["outputs_absolute"]["movie"] == str((project_store.project_dir(project.id) / render_job["job"]["outputs"]["movie"]).resolve())
    assert render_job["job"]["timeline"][0]["prompt"] == "project route scene"
    assert " " not in render_job["job"]["timeline"][0]["output"]["sequence_dir"]
    assert render_job["export"]["path"].startswith("outputs/nvidia/render_jobs/")
    assert render_job["export"]["absolute_path"] == str((project_store.project_dir(project.id) / render_job["export"]["path"]).resolve())
    assert (project_store.project_dir(project.id) / render_job["export"]["path"]).exists()
    with pytest.raises(HTTPException) as sidecar_exc:
        backend_app.report_project_nvidia_render_job_result(
            project.id,
            {
                "job_path": f"{render_job['export']['path']}.json",
                "status": "completed",
            },
        )
    assert sidecar_exc.value.status_code == 400
    result = backend_app.report_project_nvidia_render_job_result(
        project.id,
        {
            "job_path": render_job["export"]["path"],
            "status": "completed",
            "message": "Kit RTX worker rendered preview frames.",
            "outputs": {"still": "outputs/nvidia/renders/001_project/frame_000000.png"},
            "worker": {"name": "edmg.render_queue", "runtime": "omniverse-kit"},
        },
    )
    assert result["ok"] is True
    assert result["result"]["status"] == "completed"
    assert result["result"]["outputs"]["still"].endswith("frame_000000.png")
    saved_job = json.loads((project_store.project_dir(project.id) / render_job["export"]["path"]).read_text(encoding="utf-8"))
    assert saved_job["status"] == "completed"
    assert saved_job["result"]["worker"]["name"] == "edmg.render_queue"
    saved = project_store.get(project.id)
    assert saved is not None
    assert 'def Scope "AudioTimeline"' in saved.meta["last_nvidia_scene_plan"]["usd_stage"]["text"]
    assert saved.meta["exports"]["nvidia_scene_plans"][-1]["path"] == exported["export"]["path"]
    assert saved.meta["exports"]["nvidia_render_jobs"][-1]["path"] == render_job["export"]["path"]
    assert saved.meta["last_nvidia_render_result"]["status"] == "completed"
    outputs = backend_app.list_outputs(project.id)
    nvidia_export_entry = outputs["nvidia_exports"][0]
    nvidia_job_entry = outputs["nvidia_render_jobs"][0]
    export_abs_path = project_store.project_dir(project.id) / exported["export"]["path"]
    job_abs_path = project_store.project_dir(project.id) / render_job["export"]["path"]
    expected_export_path = str(export_abs_path.resolve())
    expected_job_path = str(job_abs_path.resolve())
    expected_result_path = str(job_abs_path.with_name(f"{job_abs_path.stem}.result.json").resolve())
    expected_still_path = str((project_store.project_dir(project.id) / "outputs/nvidia/renders/001_project/frame_000000.png").resolve())
    assert nvidia_export_entry["absolute_path"] == expected_export_path
    assert nvidia_job_entry["absolute_path"] == expected_job_path
    assert nvidia_job_entry["result_absolute_path"] == expected_result_path
    assert nvidia_job_entry["result"]["outputs_absolute"]["still"] == expected_still_path


def test_project_nvidia_scene_plan_read_404_when_missing(tmp_path, monkeypatch):
    project_store = ProjectStore(tmp_path)
    project = project_store.create("No NVIDIA Plan")
    monkeypatch.setattr(backend_app, "store", project_store)

    with pytest.raises(HTTPException) as exc_info:
        backend_app.get_project_nvidia_scene_plan(project.id)

    assert exc_info.value.status_code == 404
    assert "NVIDIA scene plan not found" in str(exc_info.value.detail)


def test_scene_plan_usda_text_escapes_strings_and_sanitizes_prim_names():
    payload = {
        "project_id": "quoted-project",
        "title": 'A "quoted" title',
        "duration_s": 4,
        "bpm": 120,
        "scenes": [
            {
                "id": "1 drop.scene",
                "start_s": 0,
                "end_s": 4,
                "prompt": 'laser "wall"\nwith haze',
            }
        ],
    }

    text = scene_plan_usda_text(payload)

    assert 'custom string edmg:title = "A \\"quoted\\" title"' in text
    assert 'upAxis = "Y"' in text
    assert 'def Scope "AudioTimeline"' in text
    assert 'def Scope "BeatGrid"' in text
    assert "custom double edmg:secondsPerBeat = 0.5" in text
    assert 'def Camera "MainCamera"' in text
    assert "double3 xformOp:translate.timeSamples" in text
    assert "double3 xformOp:scale.timeSamples" in text
    assert "token visibility.timeSamples" in text
    assert 'def Cube "Preview__1_drop_scene"' in text
    assert 'def Xform "_1_drop_scene"' in text
    assert "custom int edmg:startFrame = 0" in text
    assert "custom int edmg:endFrame = 96" in text
    assert 'custom string edmg:prompt = "laser \\"wall\\"\\nwith haze"' in text

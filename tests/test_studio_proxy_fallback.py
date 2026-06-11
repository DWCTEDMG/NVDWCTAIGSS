from __future__ import annotations

from pathlib import Path
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from edmg_studio_backend import app as studio_app
from edmg_studio_backend.errors import UserFacingError
from edmg_studio_backend.services import internal_video as internal_video_service
from edmg_studio_backend.store.projects import ProjectStore
from edmg_studio_backend.store.jobs import JobStore


def _make_project(tmp_path: Path):
    store = ProjectStore(tmp_path / "data")
    jobs = JobStore(store.projects_dir)
    proj = store.create("Proxy Fallback Test")
    proj.meta = {
        "analysis": {"duration_s": 6.0},
        "last_plan": {
            "variants": [
                {
                    "name": "v1",
                    "duration_s": 6.0,
                    "scenes": [
                        {"start_s": 0.0, "end_s": 3.0, "prompt": "neon city skyline"},
                        {"start_s": 3.0, "end_s": 6.0, "prompt": "stormy abstract ocean"},
                    ],
                }
            ]
        },
        "timeline": {"layers": [], "camera": {"keyframes": []}},
    }
    store.save(proj)
    return store, jobs, proj


def test_internal_preflight_rejects_proxy_fallback(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)
    monkeypatch.setattr(studio_app.models, "installed_path", lambda _mid: None)

    with pytest.raises(UserFacingError):
        studio_app._internal_render_preflight_data(
            proj.id,
            {"variant_index": 0, "fps_render": 2, "fps_output": 24, "model_id": "auto", "allow_proxy_fallback": False},
        )


def test_internal_preflight_rejects_proxy_for_incomplete_internal_model(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)
    monkeypatch.setattr(studio_app.models, "installed_path", lambda _mid: None)
    monkeypatch.setattr(
        studio_app.models,
        "internal_asset_issue",
        lambda model_id: "incomplete" if model_id == "hf_sd35_medium_internal" else None,
    )

    with pytest.raises(UserFacingError):
        studio_app._internal_render_preflight_data(
            proj.id,
            {
                "variant_index": 0,
                "fps_render": 2,
                "fps_output": 24,
                "model_id": "hf_sd35_medium_internal",
                "allow_proxy_fallback": False,
            },
        )


def test_run_pipeline_auto_rejects_proxy_when_comfy_and_models_missing(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)
    monkeypatch.setattr(studio_app.models, "installed_path", lambda _mid: None)
    monkeypatch.setattr(studio_app.comfy_pool, "diagnose", lambda _req: {"compatible": [], "busy_compatible": []})

    with pytest.raises(UserFacingError):
        studio_app.run_pipeline(proj.id, variant_index=0, preset="balanced", mode="auto", engine="auto")


def test_proxy_renderer_creates_video_and_metadata_without_ffmpeg(tmp_path, monkeypatch):
    project_dir = tmp_path / "project"
    (project_dir / "outputs" / "videos").mkdir(parents=True, exist_ok=True)

    def fake_assemble_image_sequence(*, frames_dir, out_mp4, fps, glob_pattern, audio_path=None, ffmpeg_path=None):
        frames = sorted(frames_dir.glob(glob_pattern))
        assert frames, "expected rendered frames"
        out_mp4.write_bytes(b"raw-mp4")

    def fake_interpolate_video_fps(*, in_mp4, out_mp4, fps_out, engine, ffmpeg_path=None):
        out_mp4.write_bytes(in_mp4.read_bytes() + f"-fps{fps_out}".encode("utf-8"))

    monkeypatch.setattr(internal_video_service, "assemble_image_sequence", fake_assemble_image_sequence)
    monkeypatch.setattr(internal_video_service, "interpolate_video_fps", fake_interpolate_video_fps)

    settings = internal_video_service.InternalVideoSettings(
        fps_render=2,
        fps_output=4,
        width=320,
        height=180,
        interpolation_engine="fps",
        model_id="proxy_draft",
        resume_existing_frames=False,
    )
    variant = {"index": 0, "duration_s": 2.0}
    scenes = [{"start_s": 0.0, "end_s": 2.0, "prompt": "glowing tunnel"}]

    out = internal_video_service.render_internal_proxy_video_variant(
        ffmpeg_path="ffmpeg",
        project_dir=project_dir,
        variant=variant,
        scenes=scenes,
        audio_path=None,
        settings=settings,
        timeline={"layers": []},
    )

    assert out.exists()
    assert out.read_bytes().startswith(b"raw-mp4")
    meta_path = out.with_suffix(".render.json")
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["render_mode"] == "proxy"
    assert meta["frames"]["expected"] == 4
    checkpoint_path = out.with_suffix(".checkpoint.json")
    assert checkpoint_path.exists()
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["status"] == "complete"
    assert checkpoint["completed_frames"] == 4
    assert checkpoint["outputs"]["final_exists"] is True


def test_resume_and_restart_routes_clone_internal_job_with_checkpoint(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)
    fake_model_dir = tmp_path / "models" / "hf_sd15_internal"
    fake_model_dir.mkdir(parents=True)
    monkeypatch.setattr(studio_app.models, "installed_path", lambda _mid: fake_model_dir)
    monkeypatch.setattr(
        studio_app,
        "_hardware_profile",
        lambda: {
            "backend": "cuda",
            "backend_family": "discrete_gpu",
            "vram_gb": 12.0,
            "ram_gb": 32.0,
            "cpu_threads": 16,
            "preferred_internal_model": "hf_sd15_internal",
        },
    )

    source = jobs.create(
        proj.id,
        "internal_video",
        {
            "variant_index": 0,
            "fps_render": 2,
            "fps_output": 24,
            "render_mode": "auto",
            "model_id": "auto",
            "allow_proxy_fallback": False,
            "resume_existing_frames": True,
        },
    )
    source.status = "canceled"
    source.progress = {
        "stage": "canceled",
        "current": 6,
        "total": 12,
        "percent": 50.0,
        "runtime_checkpoint": {
            "status": "frames",
            "resume_percent": 50.0,
            "completed_chunks": 1,
            "estimated_chunks": 2,
            "next_frame_index": 6,
            "total_frames": 12,
            "can_resume": True,
        },
    }
    jobs.save(source)

    with ExitStack() as stack:
        stack.enter_context(patch.object(studio_app.worker, "start", lambda: None))
        stack.enter_context(patch.object(studio_app.worker, "stop", lambda: None))
        with TestClient(studio_app.app) as client:
            resumed = client.post(f"/v1/projects/{proj.id}/jobs/{source.id}/resume_from_checkpoint")
            resumed.raise_for_status()
            resumed_payload = resumed.json()
            assert resumed_payload["job"]["id"] != source.id
            assert resumed_payload["job"]["payload"]["resume_existing_frames"] is True
            assert resumed_payload["job"]["progress"]["runtime_checkpoint"]["resume_percent"] == 50.0
            assert resumed_payload["job"]["progress"]["queue_action"] == "resume_from_checkpoint"

            restarted = client.post(f"/v1/projects/{proj.id}/jobs/{source.id}/restart_clean")
            restarted.raise_for_status()
            restarted_payload = restarted.json()
            assert restarted_payload["job"]["id"] != source.id
            assert restarted_payload["job"]["payload"]["resume_existing_frames"] is False
            assert restarted_payload["job"]["progress"]["queue_action"] == "restart_clean"


def test_resume_route_rejects_running_internal_job(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)

    source = jobs.create(
        proj.id,
        "internal_video",
        {
            "variant_index": 0,
            "fps_render": 2,
            "fps_output": 24,
            "render_mode": "proxy",
            "allow_proxy_fallback": False,
        },
    )
    source.status = "running"
    jobs.save(source)

    with ExitStack() as stack:
        stack.enter_context(patch.object(studio_app.worker, "start", lambda: None))
        stack.enter_context(patch.object(studio_app.worker, "stop", lambda: None))
        with TestClient(studio_app.app) as client:
            resp = client.post(f"/v1/projects/{proj.id}/jobs/{source.id}/resume_from_checkpoint")
            assert resp.status_code == 409


def test_job_detail_endpoint_returns_checkpoint_and_log_metadata(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)

    outputs_dir = store.project_dir(proj.id) / "outputs" / "videos"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    video_rel = "outputs/videos/test_render.mp4"
    video_path = store.project_dir(proj.id) / video_rel
    video_path.write_bytes(b"video")
    checkpoint_path = video_path.with_suffix(".checkpoint.json")
    checkpoint_path.write_text(json.dumps({
        "status": "running",
        "resume_percent": 37.5,
        "completed_chunks": 1,
        "estimated_chunks": 3,
        "next_frame_index": 9,
        "total_frames": 24,
        "can_resume": True,
        "outputs": {
            "checkpoint_json": str(checkpoint_path.relative_to(store.project_dir(proj.id)))
        },
    }), encoding="utf-8")
    render_meta_path = video_path.with_suffix('.render.json')
    render_meta_path.write_text(json.dumps({
        "outputs": {
            "raw_mp4": "raw.mp4",
            "interp_mp4": "interp.mp4",
            "final_mp4": str(video_path),
            "checkpoint_json": str(checkpoint_path),
        },
        "frames": {"dir": "frames_dir"},
    }), encoding="utf-8")

    job = jobs.create(proj.id, "internal_video", {"variant_index": 0, "render_mode": "proxy"})
    job.status = "failed"
    job.result = {"video": video_rel}
    job.progress = {
        "stage": "failed",
        "current": 9,
        "total": 24,
        "percent": 37.5,
        "runtime_checkpoint": json.loads(checkpoint_path.read_text(encoding="utf-8")),
    }
    jobs.save(job)
    jobs.append_log(proj.id, job.id, "hello")
    jobs.append_log(proj.id, job.id, "world")

    with ExitStack() as stack:
        stack.enter_context(patch.object(studio_app.worker, "start", lambda: None))
        stack.enter_context(patch.object(studio_app.worker, "stop", lambda: None))
        with TestClient(studio_app.app) as client:
            resp = client.get(f"/v1/projects/{proj.id}/jobs/{job.id}?tail_lines=1")
            resp.raise_for_status()
            payload = resp.json()
            assert payload["job"]["id"] == job.id
            assert payload["runtime_checkpoint"]["resume_percent"] == 37.5
            assert payload["resume_ready"] is True
            assert payload["log_line_count"] >= 2
            assert payload["log_tail"].strip().endswith("world")
            assert payload["outputs"]["checkpoint_exists"] is True
            assert payload["outputs"]["render_meta_exists"] is True
            assert payload["outputs"]["cache_paths"]["frames_dir"] == "frames_dir"


def test_audio_upload_endpoint_persists_large_audio_payload(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)

    payload = (b"riff" * 4096) + b"tail"

    with TestClient(studio_app.app) as client:
        response = client.post(
            f"/v1/projects/{proj.id}/assets/audio",
            files={"file": ("long.wav", payload, "audio/wav")},
        )

    assert response.status_code == 200
    audio_path = store.project_dir(proj.id) / "assets" / "audio" / "long.wav"
    assert audio_path.read_bytes() == payload

    saved = store.get(proj.id)
    assert saved is not None
    assert saved.meta["audio"]["filename"] == "long.wav"
    assert saved.meta["audio"]["size_bytes"] == len(payload)

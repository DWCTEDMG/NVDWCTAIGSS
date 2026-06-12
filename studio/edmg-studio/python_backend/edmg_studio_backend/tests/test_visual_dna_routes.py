from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from edmg_studio_backend import app as backend_app
from edmg_studio_backend.schemas import StoryboardMovieRenderRequest
from edmg_studio_backend.store.jobs import JobStore
from edmg_studio_backend.store.projects import ProjectStore


def _make_project(tmp_path: Path):
    store = ProjectStore(tmp_path / "data")
    jobs = JobStore(store.projects_dir)
    proj = store.create("DNA Route Test")
    proj.meta = {"timeline": {"layers": [], "camera": {"keyframes": []}}}
    store.save(proj)
    return store, jobs, proj


def test_planner_import_persists_visual_dna_and_project_reads_expose_it(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(backend_app, "store", store)
    monkeypatch.setattr(backend_app, "jobs", jobs)

    payload = {
        "analysis": {
            "basicInfo": {"durationSeconds": 12, "tempo": 124},
            "themes": [{"theme": "future nostalgia"}],
            "visualImagery": [{"element": "neon skyline"}],
        },
        "plan": {
            "scenes": [
                {
                    "id": 1,
                    "approved": True,
                    "status": "approved",
                    "text": "neon skyline with reflective lead silhouette",
                    "negativePrompt": "muddy lighting",
                    "shotType": "tracking side profile",
                    "transitionCue": "rise into chorus",
                }
            ],
            "scenePlan": [{"id": 1, "startTime": "00:00", "endTime": "00:12"}],
        },
        "settings": {"promptStyle": "cinematic"},
        "apply_timeline": False,
        "overwrite_timeline": True,
    }

    with TestClient(backend_app.app) as client:
        imported = client.post(f"/v1/projects/{proj.id}/planner_lab/import", json=payload)
        imported.raise_for_status()
        imported_payload = imported.json()
        assert imported_payload["visual_dna"]["identity"]["core_themes"] == ["future nostalgia"]
        assert "neon skyline" in imported_payload["visual_dna"]["identity"]["motifs"]

        project = client.get(f"/v1/projects/{proj.id}")
        project.raise_for_status()
        project_payload = project.json()
        assert project_payload["visual_dna"]["project_id"] == proj.id
        assert "tracking side profile" in project_payload["visual_dna"]["identity"]["camera_language"]
        assert project_payload["visual_dna_hints"]["style_bias"]["cinematic"] >= 0.8

    assert (store.project_dir(proj.id) / "analysis" / "visual_dna.json").exists()


def test_reactive_apply_and_conductor_plan_routes_use_visual_dna(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    proj.meta["last_plan"] = {
        "variants": [
            {
                "duration_s": 8.0,
                "scenes": [
                    {
                        "id": "scene-1",
                        "start_s": 0.0,
                        "end_s": 4.0,
                        "prompt": "keep the same lead on a neon rooftop",
                        "continuity_note": "preserve the same lead silhouette",
                        "approved": True,
                    },
                    {
                        "id": "scene-2",
                        "start_s": 4.0,
                        "end_s": 8.0,
                        "prompt": "hero burst transition with glitch impact",
                    },
                ],
            }
        ]
    }
    store.save(proj)

    monkeypatch.setattr(backend_app, "store", store)
    monkeypatch.setattr(backend_app, "jobs", jobs)
    monkeypatch.setattr(
        backend_app,
        "_build_render_conductor_environment",
        lambda: {
            "engines": {
                "internal": {"available": True, "quality_score": 0.9, "speed_score": 0.55},
                "comfyui_still": {"available": True, "quality_score": 0.85, "speed_score": 0.45},
                "comfyui_motion": {"available": True, "quality_score": 0.82, "speed_score": 0.6},
                "deforum_export": {"available": False, "quality_score": 0.7, "speed_score": 0.45},
            },
            "diagnostics": ["stubbed-environment"],
        },
    )

    reactive_payload = {
        "metadata": {"renderMode": "performance-led"},
        "keyframes": [],
        "beat_markers": [],
        "cue_events": [],
        "sections": [{"label": "chorus", "approved": True}],
        "repair_suggestions": [{"issue": "face drift", "action": "reuse anchor seed"}],
        "schedules": {"zoom": "0:(1.0), 48:(1.2)", "rotation_y": "0:(0),48:(6)"},
        "handoff_manifest": {"renderMode": "performance-led"},
        "overwrite_motion_track": True,
        "overwrite_camera": True,
    }

    with TestClient(backend_app.app) as client:
        applied = client.post(f"/v1/projects/{proj.id}/reactive_lab/apply", json=reactive_payload)
        applied.raise_for_status()
        applied_payload = applied.json()
        assert applied_payload["visual_dna"]["learning_state"]["sources"]["reactive_imports"] == 1
        assert "push-in dynamics" in applied_payload["visual_dna"]["identity"]["camera_language"]

        planned = client.post(
            f"/v1/projects/{proj.id}/render/conductor/plan",
            json={"variant_index": 0, "preset": "balanced"},
        )
        planned.raise_for_status()
        planned_payload = planned.json()
        assert planned_payload["plan"]["advisory_only"] is True
        assert len(planned_payload["plan"]["sections"]) == 2
        assert planned_payload["environment"]["diagnostics"] == ["stubbed-environment"]
        assert "continuity-heavy" not in planned_payload["plan"]["summary"]  # summary stays generic
        assert planned_payload["visual_dna_hints"]["confidence"] > 0.0

        unreal_preview = client.get(f"/v1/projects/{proj.id}/unreal/preview")
        unreal_preview.raise_for_status()
        unreal_payload = unreal_preview.json()
        assert unreal_payload["ok"] is True
        assert unreal_payload["preview"]["project_id"] == proj.id
        assert unreal_payload["preview"]["render_handoff"]["render_mode"] == "performance-led"
        assert len(unreal_payload["preview"]["render_handoff"]["sections"]) == 2
        assert unreal_payload["preview"]["live_control_bridge"]["section_events"][0]["label"] == "chorus"

        exported = client.post(
            f"/v1/projects/{proj.id}/export/unreal",
            json={"variant_index": 0, "bundle_name": "route-demo", "include_zip": True},
        )
        exported.raise_for_status()
        export_payload = exported.json()
        assert export_payload["ok"] is True
        assert export_payload["bundle"]["bundle_dir"].startswith("outputs/unreal/route_demo")
        assert export_payload["bundle"]["manifest_path"].endswith("bundle_manifest.json")
        assert export_payload["bundle"]["zip_path"].endswith(".zip")

        import_plan = client.post(
            f"/v1/projects/{proj.id}/unreal/import-plan",
            json={
                "bundle_dir": export_payload["bundle"]["bundle_dir"],
                "content_path": "/Game/Cinematics/EDMG",
                "asset_name": "DemoSequence",
            },
        )
        import_plan.raise_for_status()
        import_plan_payload = import_plan.json()
        assert import_plan_payload["ok"] is True
        assert import_plan_payload["plan_path"].endswith("unreal_import_plan.json")
        assert import_plan_payload["plan"]["asset_path"] == "/Game/Cinematics/EDMG/DemoSequence"

        returned_dir = store.project_dir(proj.id) / export_payload["bundle"]["bundle_dir"] / "returned"
        returned_dir.mkdir(parents=True, exist_ok=True)
        (returned_dir / "shot_render.mp4").write_bytes(b"fake-mp4")
        (returned_dir / "hero_frame.png").write_bytes(b"fake-png")

        imported = client.post(
            f"/v1/projects/{proj.id}/import/unreal",
            json={"bundle_dir": export_payload["bundle"]["bundle_dir"]},
        )
        imported.raise_for_status()
        imported_payload = imported.json()
        assert imported_payload["ok"] is True
        assert imported_payload["imported"]["source_dir"].endswith("/returned")
        assert len(imported_payload["imported"]["media"]) == 2

        outputs = client.get(f"/v1/projects/{proj.id}/outputs")
        outputs.raise_for_status()
        outputs_payload = outputs.json()
        assert len(outputs_payload["unreal_exports"]) == 1
        assert outputs_payload["unreal_exports"][0]["sequence_name"].endswith("_MainSequence")
        assert outputs_payload["unreal_exports"][0]["manifest"]["export_family"] == "unreal_bridge_bundle"
        assert outputs_payload["unreal_exports"][0]["import_plan"]["asset_path"] == "/Game/Cinematics/EDMG/DemoSequence"
        assert len(outputs_payload["unreal_returns"]) == 1
        assert outputs_payload["unreal_returns"][0]["source_dir"].endswith("/returned")
        assert len(outputs_payload["unreal_returns"][0]["media"]) == 2
        assert any(item["kind"] == "unreal_bridge_return" for item in outputs_payload["videos"])


def test_storyboard_movie_route_applies_reactive_timeline_and_queues_job(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "data")
    jobs = JobStore(store.projects_dir)
    proj = store.create("Storyboard Movie")
    audio_path = store.project_dir(proj.id) / "assets" / "audio" / "track.wav"
    audio_path.write_bytes(b"fake-wav")
    proj.meta = {
        "audio": {"filename": "track.wav", "size_bytes": audio_path.stat().st_size},
        "analysis": {
            "features": {
                "duration_s": 8.0,
                "bpm": 120,
                "energy_curve": [0.2, 0.45, 0.9, 0.55, 0.3, 0.72, 0.84, 0.4],
                "beat_times": [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
            },
            "tags": ["neon", "stage"],
        },
        "last_plan": {
            "duration_s": 8.0,
            "variants": [
                {
                    "name": "NIM storyboard",
                    "duration_s": 8.0,
                    "scenes": [
                        {"id": "intro", "start_s": 0.0, "end_s": 4.0, "prompt": "neon stage opens"},
                        {"id": "drop", "start_s": 4.0, "end_s": 8.0, "prompt": "drop hits with light columns"},
                    ],
                }
            ],
        },
        "timeline": {"layers": [], "camera": {"keyframes": []}},
    }
    store.save(proj)

    monkeypatch.setattr(backend_app, "store", store)
    monkeypatch.setattr(backend_app, "jobs", jobs)
    monkeypatch.setattr(
        backend_app,
        "_hardware_profile",
        lambda: {
            "backend": "cuda",
            "backend_family": "discrete_gpu",
            "cuda_runtime_ready": True,
            "available_backends": ["cuda"],
            "vram_gb": 16.0,
            "ram_gb": 16.0,
            "cpu_threads": 8,
            "device_name": "test cuda",
            "preferred_internal_model": "hf_sd15_internal",
        },
    )
    model_dir = tmp_path / "models" / "hf_sd15_internal"
    model_dir.mkdir(parents=True)
    monkeypatch.setattr(backend_app.models, "installed_path", lambda mid: model_dir if mid == "hf_sd15_internal" else None)

    response = backend_app.render_storyboard_movie(
        proj.id,
        StoryboardMovieRenderRequest(
            variant_index=0,
            preset="fast",
            render_mode="auto",
            fps_output=24,
            fps_render=1,
        ),
    )

    assert response["ok"] is True
    assert response["render_mode"] == "diffusion"
    assert response["job"]["type"] == "internal_video"
    assert response["job"]["payload"]["storyboard_movie"]["audio_reactive"] is True
    assert response["preflight"]["mode"] == "diffusion"

    saved = store.get(proj.id)
    assert saved is not None
    assert saved.meta["last_storyboard_movie"]["job_id"] == response["job"]["id"]
    tracks = saved.meta["timeline"]["tracks"]
    prompt_track = next(track for track in tracks if track["type"] == "prompt")
    motion_track = next(track for track in tracks if track["type"] == "motion")
    assert prompt_track["clips"][0]["data"]["prompt"] == "neon stage opens"
    motion_data = motion_track["clips"][0]["data"]
    assert motion_data["reactive_source"] == "storyboard_movie"
    assert motion_data["beat_count"] == 7
    assert "zoom" in motion_data["motion_schedules"]
    assert saved.meta["timeline"]["storyboard_movie"]["audio_reactive"] is True
    assert len(saved.meta["timeline"]["camera"]["keyframes"]) >= 2

from __future__ import annotations

import json
from pathlib import Path

from edmg_studio_backend import app as studio_app
from edmg_studio_backend.store.jobs import JobStore
from edmg_studio_backend.store.projects import ProjectStore


def _make_project(tmp_path: Path):
    store = ProjectStore(tmp_path / "data")
    jobs = JobStore(store.projects_dir)
    proj = store.create("Longform Analysis Test")
    return store, jobs, proj


def test_analyze_audio_builds_rich_longform_payload(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)

    audio_path = store.project_dir(proj.id) / "assets" / "audio" / "track.wav"
    audio_path.write_bytes(b"fake-wav")
    store.set_audio(proj.id, "track.wav", audio_path.stat().st_size)

    monkeypatch.setattr(
        studio_app,
        "_collect_audio_analysis_features",
        lambda _path: {
            "duration_s": 612.0,
            "bpm": 122.0,
            "tempo_bpm": 122.0,
            "beats": [0.5, 1.0, 1.5],
            "energy": [0.2, 0.7, 0.4, 0.8, 0.3],
            "onset_strength": [0.1, 0.5, 0.9],
        },
    )
    monkeypatch.setattr(
        studio_app.ai,
        "transcribe",
        lambda _audio_path, model_size="small": {
            "text": (
                "Neon streets open into the skyline. "
                "The chorus lifts the crowd through electric rain. "
                "A final dawn lands over mirrored glass."
            ),
            "language": "en",
            "duration_s": 612.0,
            "duration_after_vad_s": 598.0,
            "segment_count": 3,
            "word_count": 21,
            "model_size": model_size,
            "source": "nvidia_riva",
            "segments": [
                {"start": 0.0, "end": 18.0, "text": "Neon streets open into the skyline."},
                {"start": 296.0, "end": 332.0, "text": "The chorus lifts the crowd through electric rain."},
                {"start": 586.0, "end": 612.0, "text": "A final dawn lands over mirrored glass."},
            ],
        },
    )

    result = studio_app.analyze_audio(proj.id)

    analysis = result["analysis"]
    assert analysis["duration_s"] == 612.0
    assert analysis["transcript"]["language"] == "en"
    assert analysis["transcript"]["segment_count"] == 3
    assert analysis["summary"].startswith("Neon streets open into the skyline.")
    assert "neon" in analysis["tags"]
    assert analysis["themes"]
    assert len(analysis["sections"]) >= 3
    assert analysis["analysis_path"] == "analysis/audio_analysis.json"

    snapshot_path = store.project_dir(proj.id) / "analysis" / "audio_analysis.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["transcript"]["segments"][1]["text"] == "The chorus lifts the crowd through electric rain."
    assert snapshot["summary"] == analysis["summary"]

    saved_proj = store.get(proj.id)
    assert saved_proj is not None
    payload = studio_app._build_creative_direction_payload(saved_proj, 0, "cinematic", 1.0)
    assert payload["sections"]
    assert payload["transcript_summary"] == analysis["summary"]
    assert payload["narrative_analysis"]["segment_count"] == 3


def test_analyze_audio_surfaces_no_speech_after_vad_status(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)

    audio_path = store.project_dir(proj.id) / "assets" / "audio" / "instrumental.wav"
    audio_path.write_bytes(b"fake-wav")
    store.set_audio(proj.id, "instrumental.wav", audio_path.stat().st_size)

    monkeypatch.setattr(
        studio_app,
        "_collect_audio_analysis_features",
        lambda _path: {
            "duration_s": 374.8,
            "bpm": 60.0,
            "tempo_bpm": 60.0,
            "beats": [1.0, 2.0, 3.0],
            "energy": [0.3, 0.5, 0.4, 0.6],
            "onset_strength": [0.2, 0.4, 0.7],
        },
    )
    monkeypatch.setattr(
        studio_app.ai,
        "transcribe",
        lambda _audio_path, model_size="small": {
            "text": "",
            "language": "en",
            "duration_s": 374.8,
            "duration_after_vad_s": 0.0,
            "segment_count": 0,
            "word_count": 0,
            "model_size": "medium",
            "source": "nvidia_riva",
            "segments": [],
            "note": "No speech detected after VAD.",
        },
    )

    result = studio_app.analyze_audio(proj.id)

    analysis = result["analysis"]
    assert analysis["summary"].startswith("No speech detected after VAD.")
    assert analysis["transcript"]["note"] == "No speech detected after VAD."
    assert analysis["sections"]

    saved_proj = store.get(proj.id)
    assert saved_proj is not None
    payload = studio_app._build_creative_direction_payload(saved_proj, 0, "cinematic", 1.0)
    assert payload["transcript_summary"].startswith("No speech detected after VAD.")

from __future__ import annotations

import types

import pytest
from fastapi import HTTPException

from edmg_studio_backend import app as backend_app
from edmg_studio_backend.services.ai_client import LocalAiDirectorClient
from edmg_studio_backend.services import ai_client as ai_client_module
from edmg_studio_backend.services import nvidia_riva
from edmg_studio_backend.store.projects import ProjectStore


class _FakeAlternative:
    transcript = "Riva heard the neon chorus."


class _FakeResult:
    alternatives = [_FakeAlternative()]


class _FakeAsrResponse:
    results = [_FakeResult()]


class _FakeRivaClient:
    class Auth:
        def __init__(self, *, uri: str, use_ssl: bool = False) -> None:
            self.uri = uri
            self.use_ssl = use_ssl

    class RecognitionConfig:
        def __init__(self) -> None:
            self.language_code = ""
            self.max_alternatives = 0
            self.enable_automatic_punctuation = False
            self.audio_channel_count = 0
            self.model = ""

    class ASRService:
        def __init__(self, auth) -> None:
            self.auth = auth

        def offline_recognize(self, content: bytes, config):
            assert content == b"fake-wav"
            assert self.auth.uri == "riva.local:50051"
            assert config.language_code == "en-US"
            assert config.max_alternatives == 1
            return _FakeAsrResponse()


def test_nvidia_riva_status_and_transcribe(monkeypatch, tmp_path):
    monkeypatch.setenv("EDMG_NVIDIA_RIVA_URL", "grpc://riva.local:50051")
    monkeypatch.setenv("EDMG_NVIDIA_RIVA_ASR", "1")
    monkeypatch.setenv("EDMG_NVIDIA_RIVA_MODEL", "parakeet")
    monkeypatch.setattr(nvidia_riva, "_load_riva_client", lambda: _FakeRivaClient)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-wav")

    status = nvidia_riva.riva_asr_status()
    transcript = nvidia_riva.transcribe_with_riva(audio_path)

    assert status["enabled"] is True
    assert status["uri"] == "riva.local:50051"
    assert status["model"] == "parakeet"
    assert transcript["source"] == "nvidia_riva"
    assert transcript["text"] == "Riva heard the neon chorus."
    assert transcript["segment_count"] == 1


def test_local_ai_director_prefers_enabled_riva(monkeypatch, tmp_path):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-wav")
    monkeypatch.setattr(ai_client_module, "riva_asr_enabled", lambda: True)
    monkeypatch.setattr(ai_client_module, "transcribe_with_riva", lambda _path: {"text": "from riva", "source": "nvidia_riva"})

    result = LocalAiDirectorClient().transcribe(str(audio_path))

    assert result["text"] == "from riva"
    assert result["source"] == "nvidia_riva"


def test_local_ai_director_falls_back_when_enabled_riva_fails(monkeypatch, tmp_path):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake-wav")
    monkeypatch.setattr(ai_client_module, "riva_asr_enabled", lambda: True)
    monkeypatch.setattr(ai_client_module, "riva_fallback_enabled", lambda: True)

    def fail_riva(_path):
        raise ai_client_module.RivaTranscriptionError("riva offline")

    monkeypatch.setattr(ai_client_module, "transcribe_with_riva", fail_riva)
    fake_asr = types.SimpleNamespace(
        transcribe_detailed=lambda _audio_path, model_size="turbo": {
            "text": "fallback whisper",
            "source": "faster_whisper",
            "model_size": model_size,
        }
    )
    monkeypatch.setitem(__import__("sys").modules, "edmg_ai_service.asr", fake_asr)

    result = LocalAiDirectorClient().transcribe(str(audio_path), model_size="small")

    assert result["text"] == "fallback whisper"
    assert result["source"] == "faster_whisper"
    assert result["nvidia_riva"]["error"] == "riva offline"


def test_project_riva_transcribe_route_persists_transcript(monkeypatch, tmp_path):
    project_store = ProjectStore(tmp_path)
    project = project_store.create("Riva Project")
    monkeypatch.setattr(backend_app, "store", project_store)
    audio_path = project_store.project_dir(project.id) / "assets" / "audio" / "track.wav"
    audio_path.write_bytes(b"fake-wav")
    project_store.set_audio(project.id, "track.wav", audio_path.stat().st_size)
    monkeypatch.setattr(
        backend_app,
        "transcribe_with_riva",
        lambda _path, language_code=None, require_enabled=False: {
            "text": "project riva transcript",
            "source": "nvidia_riva",
            "language": language_code or "en-US",
            "segments": [],
            "segment_count": 0,
        },
    )

    response = backend_app.transcribe_project_audio_with_riva(project.id, {"language_code": "en-US"})

    assert response["ok"] is True
    assert response["transcript"]["text"] == "project riva transcript"
    saved = project_store.get(project.id)
    assert saved is not None
    assert saved.meta["last_nvidia_riva_transcript"]["transcript"]["source"] == "nvidia_riva"


def test_project_riva_transcribe_route_requires_audio(tmp_path, monkeypatch):
    project_store = ProjectStore(tmp_path)
    project = project_store.create("No Audio")
    monkeypatch.setattr(backend_app, "store", project_store)

    with pytest.raises(HTTPException) as exc_info:
        backend_app.transcribe_project_audio_with_riva(project.id, {})

    assert exc_info.value.status_code == 400

from __future__ import annotations

from edmg_ai_service.config import Settings


def test_settings_reads_openai_compat_env_at_instantiation(monkeypatch):
    monkeypatch.setenv("EDMG_AI_PROVIDER", "openai_compat")
    monkeypatch.setenv("EDMG_AI_OPENAI_COMPAT_BASE_URL", "https://integrate.api.nvidia.com/v1")
    monkeypatch.setenv("EDMG_AI_OPENAI_COMPAT_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1.5")
    monkeypatch.setenv("EDMG_NVIDIA_AUDIO2FACE_API_KEY", "alias-secret-token")
    monkeypatch.delenv("EDMG_AI_OPENAI_COMPAT_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("EDMG_NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("EDMG_NVIDIA_NIM_API_KEY", raising=False)

    settings = Settings()

    assert settings.provider == "openai_compat"
    assert settings.openai_compat_base_url == "https://integrate.api.nvidia.com/v1"
    assert settings.openai_compat_model == "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    assert settings.openai_compat_api_key == "alias-secret-token"


def test_settings_prefers_explicit_openai_compat_key(monkeypatch):
    monkeypatch.setenv("EDMG_AI_OPENAI_COMPAT_API_KEY", "planner-secret-token")
    monkeypatch.setenv("EDMG_NVIDIA_AUDIO2FACE_API_KEY", "alias-secret-token")

    assert Settings().openai_compat_api_key == "planner-secret-token"


def test_settings_does_not_alias_nvidia_key_for_non_nvidia_endpoint(monkeypatch):
    monkeypatch.delenv("EDMG_AI_OPENAI_COMPAT_API_KEY", raising=False)
    monkeypatch.setenv("EDMG_AI_OPENAI_COMPAT_BASE_URL", "https://planner.example.test/v1")
    monkeypatch.setenv("EDMG_NVIDIA_AUDIO2FACE_API_KEY", "alias-secret-token")

    assert Settings().openai_compat_api_key is None

from __future__ import annotations

from pathlib import Path

import pytest

from edmg_studio_backend.services.model_manager import ModelManager, ModelTask


class FakeS3OnlyCache:
    label = "AWS S3 model cache"

    def __init__(self, *, hit: bool = False):
        self.hit = hit
        self.uploaded: list[tuple[str, Path]] = []

    def model_exists(self, entry: dict, path: Path) -> str | None:
        if not self.hit:
            return None
        return f"models/checkpoints/{entry['id']}/{path.name}"

    def upload_model(self, entry: dict, path: Path) -> str:
        self.uploaded.append((str(entry["id"]), path))
        return f"models/checkpoints/{entry['id']}/{path.name}"


def _manager(tmp_path: Path) -> ModelManager:
    return ModelManager(
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "models",
        external_dir=tmp_path / "external",
        comfyui_url="http://127.0.0.1:8188",
        ollama_url="http://127.0.0.1:11434",
    )


def _entry() -> dict:
    return {
        "id": "hf_sd35_large_turbo_ckpt",
        "name": "Stable Diffusion 3.5 Large Turbo",
        "kind": "checkpoint",
        "source": "hf",
        "hf_url": "https://example.invalid/sd3.5_large_turbo.safetensors",
        "filename": "sd3.5_large_turbo.safetensors",
        "target": {"engine": "comfyui", "folder": "checkpoints"},
    }


def test_s3_only_file_install_uploads_and_removes_transfer_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDMG_MODEL_STORAGE_MODE", "cloud_only")
    manager = _manager(tmp_path)
    cache = FakeS3OnlyCache(hit=False)
    manager.model_cache = cache

    def write_source_download(_task: ModelTask, _url: str, dest: Path, headers=None) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"from source")

    monkeypatch.setattr(manager, "_download_stream", write_source_download)

    task = ModelTask(id="task", name="Install")
    manager._install_file_model(task, _entry())

    local_dest = tmp_path / "models" / "checkpoints" / "sd3.5_large_turbo.safetensors"
    assert not local_dest.exists()
    assert cache.uploaded
    assert not cache.uploaded[0][1].exists()
    cloud = manager.catalog()["cloud"]["hf_sd35_large_turbo_ckpt"]
    assert cloud["provider"] == "AWS S3 model cache"
    assert cloud["mode"] == "cloud_only"
    assert cloud["object"] == "models/checkpoints/hf_sd35_large_turbo_ckpt/sd3.5_large_turbo.safetensors"


def test_s3_only_file_install_marks_existing_remote_without_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDMG_MODEL_STORAGE_MODE", "cloud_only")
    manager = _manager(tmp_path)
    manager.model_cache = FakeS3OnlyCache(hit=True)

    def fail_source_download(*_args, **_kwargs) -> None:
        raise AssertionError("source download should not run when S3 object already exists")

    monkeypatch.setattr(manager, "_download_stream", fail_source_download)

    task = ModelTask(id="task", name="Install")
    manager._install_file_model(task, _entry())

    assert task.progress == 1.0
    assert "Already stored in AWS S3 model cache" in task.last_log
    assert manager.catalog()["installed"]["hf_sd35_large_turbo_ckpt"] is False
    assert manager.catalog()["cloud"]["hf_sd35_large_turbo_ckpt"]["mode"] == "cloud_only"

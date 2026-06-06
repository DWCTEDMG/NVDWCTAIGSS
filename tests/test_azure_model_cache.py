from __future__ import annotations

from pathlib import Path

from edmg_studio_backend.services.model_manager import ModelManager, ModelTask


class FakeModelCache:
    label = "Azure model cache"

    def __init__(self, *, hit: bool):
        self.hit = hit
        self.downloaded: list[tuple[str, Path]] = []
        self.uploaded: list[tuple[str, Path]] = []

    def download_model(self, entry: dict, dest: Path) -> bool:
        self.downloaded.append((str(entry["id"]), dest))
        if not self.hit:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"from azure cache")
        return True

    def upload_model(self, entry: dict, path: Path) -> str:
        self.uploaded.append((str(entry["id"]), path))
        return f"models/{entry['id']}/{path.name}"


def _manager(tmp_path: Path) -> ModelManager:
    return ModelManager(
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "models",
        external_dir=tmp_path / "external",
        comfyui_url="http://127.0.0.1:8188",
        ollama_url="http://127.0.0.1:11434",
    )


def _sd35_entry() -> dict:
    return {
        "id": "hf_sd35_large_turbo_ckpt",
        "name": "Stable Diffusion 3.5 Large Turbo",
        "kind": "checkpoint",
        "source": "hf",
        "hf_url": "https://example.invalid/sd3.5_large_turbo.safetensors",
        "filename": "sd3.5_large_turbo.safetensors",
        "target": {"engine": "comfyui", "folder": "checkpoints"},
    }


def test_file_model_install_restores_from_azure_cache_before_source_download(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = _manager(tmp_path)
    cache = FakeModelCache(hit=True)
    manager.model_cache = cache

    def fail_source_download(*_args, **_kwargs) -> None:
        raise AssertionError("source download should not run on an Azure cache hit")

    monkeypatch.setattr(manager, "_download_stream", fail_source_download)

    task = ModelTask(id="task", name="Install")
    manager._install_file_model(task, _sd35_entry())

    dest = tmp_path / "models" / "checkpoints" / "sd3.5_large_turbo.safetensors"
    assert dest.read_bytes() == b"from azure cache"
    assert cache.downloaded == [("hf_sd35_large_turbo_ckpt", dest)]
    assert cache.uploaded == []
    assert task.progress == 1.0
    assert "Azure model cache" in task.last_log


def test_file_model_install_uploads_source_download_to_azure_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = _manager(tmp_path)
    cache = FakeModelCache(hit=False)
    manager.model_cache = cache

    def write_source_download(_task: ModelTask, _url: str, dest: Path, headers=None) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"from source")

    monkeypatch.setattr(manager, "_download_stream", write_source_download)

    task = ModelTask(id="task", name="Install")
    manager._install_file_model(task, _sd35_entry())

    dest = tmp_path / "models" / "checkpoints" / "sd3.5_large_turbo.safetensors"
    assert dest.read_bytes() == b"from source"
    assert cache.downloaded == [("hf_sd35_large_turbo_ckpt", dest)]
    assert cache.uploaded == [("hf_sd35_large_turbo_ckpt", dest)]

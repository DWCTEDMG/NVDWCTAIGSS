from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from edmg_studio_backend.integrations.aws import S3ModelCache, S3ModelCacheSettings
from edmg_studio_backend.services import model_manager as model_manager_module
from edmg_studio_backend.services.model_manager import ModelManager, ModelTask


class FakeS3ModelCache:
    label = "AWS S3 model cache"

    def __init__(self, objects: dict[str, bytes]):
        self.objects = dict(objects)
        self.settings = SimpleNamespace(
            bucket="test-bucket",
            prefix="models",
            region="us-east-1",
            endpoint_url="",
        )
        self.downloads: list[str] = []

    def _key_for(self, entry: dict, path: Path) -> str:
        uri = str(entry.get("s3_uri") or entry.get("aws_s3_uri") or "").strip()
        if uri:
            parsed = urlparse(uri)
            return str(parsed.path).lstrip("/")
        key = str(entry.get("s3_key") or entry.get("aws_s3_key") or entry.get("object_key") or "").strip()
        if key:
            return key.strip("/")
        target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
        folder = str(target.get("folder") or "models").strip("/")
        model_id = str(entry.get("id") or path.stem)
        return f"models/{folder}/{model_id}/{path.name}"

    def model_exists(self, entry: dict, path: Path) -> str | None:
        key = self._key_for(entry, path)
        return key if key in self.objects else None

    def download_model(self, entry: dict, dest: Path) -> bool:
        key = self._key_for(entry, dest)
        payload = self.objects.get(key)
        if payload is None:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        self.downloads.append(key)
        return True

    def upload_model(self, entry: dict, path: Path) -> str:
        key = self._key_for(entry, path)
        self.objects[key] = path.read_bytes()
        return key


def _offline_manager(tmp_path, monkeypatch, objects: dict[str, bytes]) -> ModelManager:
    def _offline_get(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(model_manager_module.requests, "get", _offline_get)
    monkeypatch.setenv("EDMG_MODEL_STORAGE_MODE", "local_cache")
    manager = ModelManager(
        tmp_path / "data",
        tmp_path / "models",
        tmp_path / "external",
        "http://127.0.0.1:8188",
        "http://127.0.0.1:11434",
    )
    manager.model_cache = FakeS3ModelCache(objects)
    return manager


def _checkpoint_entry(model_id: str, filename: str, **extra) -> dict:
    entry = {
        "id": model_id,
        "name": "Hosted checkpoint",
        "kind": "checkpoint",
        "source": "hf",
        "filename": filename,
        "target": {"engine": "comfyui", "folder": "checkpoints"},
        "license_id": "test",
        "license_url": "",
    }
    entry.update(extra)
    return entry


def test_s3_object_location_accepts_explicit_uri_and_key() -> None:
    cache = S3ModelCache.__new__(S3ModelCache)
    cache.settings = S3ModelCacheSettings(bucket="default-bucket", prefix="models")

    uri_entry = {"s3_uri": "s3://hosted-bucket/path/to/model.safetensors"}
    assert cache.object_location_for(uri_entry, Path("model.safetensors")) == (
        "hosted-bucket",
        "path/to/model.safetensors",
    )

    key_entry = {"s3_key": "custom/path/model.safetensors"}
    assert cache.object_location_for(key_entry, Path("model.safetensors")) == (
        "default-bucket",
        "custom/path/model.safetensors",
    )


def test_resolve_comfy_asset_materializes_cloud_record(tmp_path, monkeypatch) -> None:
    manager = _offline_manager(tmp_path, monkeypatch, {"hosted/demo.safetensors": b"weights"})
    entry = _checkpoint_entry("hosted_demo", "demo.safetensors")
    manager.add_user_model(entry)
    manager._record_cloud_model(entry, "hosted/demo.safetensors", mode="cloud_only")

    assert manager.installed_path("hosted_demo") is None

    asset = manager.resolve_comfy_asset("hosted_demo", folder="checkpoints", allowed_kinds={"checkpoint"})

    resolved = Path(str(asset["path"]))
    assert resolved == tmp_path / "models" / "checkpoints" / "demo.safetensors"
    assert resolved.read_bytes() == b"weights"
    assert manager.model_cache.downloads == ["hosted/demo.safetensors"]
    assert manager.installed_path("hosted_demo") == resolved


def test_s3_source_installs_to_local_model_path(tmp_path, monkeypatch) -> None:
    manager = _offline_manager(tmp_path, monkeypatch, {"hosted/source.safetensors": b"s3-weights"})
    entry = _checkpoint_entry(
        "s3_source",
        "source.safetensors",
        source="s3",
        s3_uri="s3://test-bucket/hosted/source.safetensors",
    )
    manager.add_user_model(entry)

    task = ModelTask(id="test", name="install")
    manager._install_file_model(task, entry)

    resolved = tmp_path / "models" / "checkpoints" / "source.safetensors"
    assert resolved.read_bytes() == b"s3-weights"
    assert manager._cloud_model_record("s3_source")["object"] == "hosted/source.safetensors"
    assert task.progress == 1.0

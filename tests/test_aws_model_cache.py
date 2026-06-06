from __future__ import annotations

from pathlib import Path

from edmg_studio_backend.integrations import aws


class FakeClientError(Exception):
    def __init__(self, code: str):
        self.response = {"Error": {"Code": code}}
        super().__init__(code)


class FakeS3Client:
    def __init__(self, *, hit: bool = True):
        self.hit = hit
        self.head_calls: list[tuple[str, str]] = []
        self.download_calls: list[tuple[str, str, str]] = []
        self.upload_calls: list[tuple[str, str, str, dict]] = []

    def head_object(self, *, Bucket: str, Key: str) -> None:
        self.head_calls.append((Bucket, Key))
        if not self.hit:
            raise FakeClientError("404")

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self.download_calls.append((bucket, key, filename))
        Path(filename).write_bytes(b"from s3 cache")

    def upload_file(self, filename: str, bucket: str, key: str, ExtraArgs: dict) -> None:
        self.upload_calls.append((filename, bucket, key, ExtraArgs))


def _entry() -> dict:
    return {
        "id": "hf_sd35_large_turbo_ckpt",
        "kind": "checkpoint",
        "source": "hf",
        "target": {"engine": "comfyui", "folder": "checkpoints"},
    }


def test_s3_model_cache_restores_existing_object(tmp_path: Path, monkeypatch) -> None:
    fake_s3 = FakeS3Client(hit=True)
    monkeypatch.setattr(aws, "_s3_client", lambda _settings: fake_s3)
    monkeypatch.setattr(aws, "_require_client_error", lambda: FakeClientError)

    cache = aws.S3ModelCache(
        aws.S3ModelCacheSettings(bucket="edmg-model-cache", prefix="models", region="us-east-1"),
    )
    dest = tmp_path / "checkpoints" / "sd3.5_large_turbo.safetensors"

    assert cache.download_model(_entry(), dest) is True
    assert dest.read_bytes() == b"from s3 cache"
    assert fake_s3.head_calls == [
        (
            "edmg-model-cache",
            "models/checkpoints/hf_sd35_large_turbo_ckpt/sd3.5_large_turbo.safetensors",
        ),
    ]


def test_s3_model_cache_miss_returns_false(tmp_path: Path, monkeypatch) -> None:
    fake_s3 = FakeS3Client(hit=False)
    monkeypatch.setattr(aws, "_s3_client", lambda _settings: fake_s3)
    monkeypatch.setattr(aws, "_require_client_error", lambda: FakeClientError)

    cache = aws.S3ModelCache(
        aws.S3ModelCacheSettings(bucket="edmg-model-cache", prefix="models", region="us-east-1"),
    )

    assert cache.download_model(_entry(), tmp_path / "missing.safetensors") is False


def test_s3_model_cache_uploads_with_metadata_and_storage_class(tmp_path: Path, monkeypatch) -> None:
    fake_s3 = FakeS3Client(hit=True)
    monkeypatch.setattr(aws, "_s3_client", lambda _settings: fake_s3)

    cache = aws.S3ModelCache(
        aws.S3ModelCacheSettings(
            bucket="edmg-model-cache",
            prefix="models",
            region="us-east-1",
            storage_class="STANDARD_IA",
        ),
    )
    source = tmp_path / "sd3.5_large_turbo.safetensors"
    source.write_bytes(b"model")

    key = cache.upload_model(_entry(), source)

    assert key == "models/checkpoints/hf_sd35_large_turbo_ckpt/sd3.5_large_turbo.safetensors"
    assert fake_s3.upload_calls == [
        (
            str(source),
            "edmg-model-cache",
            key,
            {
                "Metadata": {
                    "model_id": "hf_sd35_large_turbo_ckpt",
                    "source": "hf",
                    "kind": "checkpoint",
                },
                "StorageClass": "STANDARD_IA",
            },
        ),
    ]

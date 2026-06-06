from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any


def _require_boto3():
    try:
        import boto3  # type: ignore
        return boto3
    except Exception as e:
        raise RuntimeError("AWS integration requires optional deps: pip install -e '.[aws]'") from e


def _require_client_error():
    try:
        from botocore.exceptions import ClientError  # type: ignore
        return ClientError
    except Exception as e:
        raise RuntimeError("AWS integration requires optional deps: pip install -e '.[aws]'") from e


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _clean_part(value: str, fallback: str) -> str:
    raw = str(value or "").strip().strip("/\\") or fallback
    return re.sub(r"[^A-Za-z0-9._=-]+", "-", raw).strip(".-") or fallback


@dataclass
class AwsTestResult:
    ok: bool
    account: str | None = None
    region: str | None = None


@dataclass(frozen=True)
class S3ModelCacheSettings:
    bucket: str
    prefix: str = "models"
    region: str = "us-east-1"
    endpoint_url: str = ""
    storage_class: str = "STANDARD"


def _aws_region() -> str:
    return (
        os.getenv("AWS_REGION", "").strip()
        or os.getenv("AWS_DEFAULT_REGION", "").strip()
        or "us-east-1"
    )


def settings_from_env(*, bucket: str | None = None, prefix: str | None = None) -> S3ModelCacheSettings:
    resolved_bucket = (
        str(bucket or "").strip()
        or os.getenv("EDMG_AWS_MODEL_CACHE_BUCKET", "").strip()
        or os.getenv("EDMG_AWS_MODEL_BUCKET", "").strip()
        or os.getenv("EDMG_S3_MODEL_CACHE_BUCKET", "").strip()
    )
    if not resolved_bucket:
        raise RuntimeError("Set EDMG_AWS_MODEL_CACHE_BUCKET to the S3 bucket for model cache files.")

    return S3ModelCacheSettings(
        bucket=resolved_bucket,
        prefix=(
            str(prefix or "").strip()
            or os.getenv("EDMG_AWS_MODEL_CACHE_PREFIX", "").strip()
            or os.getenv("EDMG_S3_MODEL_CACHE_PREFIX", "").strip()
            or "models"
        ).strip("/"),
        region=_aws_region(),
        endpoint_url=(
            os.getenv("EDMG_S3_ENDPOINT_URL", "").strip()
            or os.getenv("AWS_ENDPOINT_URL", "").strip()
        ),
        storage_class=(
            os.getenv("EDMG_AWS_MODEL_CACHE_STORAGE_CLASS", "").strip()
            or os.getenv("EDMG_S3_MODEL_CACHE_STORAGE_CLASS", "").strip()
            or "STANDARD"
        ).strip().upper(),
    )


def _s3_client(settings: S3ModelCacheSettings | None = None):
    boto3 = _require_boto3()
    settings = settings or S3ModelCacheSettings(bucket="")
    kwargs: dict[str, Any] = {"region_name": settings.region or _aws_region()}
    if settings.endpoint_url:
        kwargs["endpoint_url"] = settings.endpoint_url
    return boto3.client("s3", **kwargs)


def test_credentials(bucket: Optional[str] = None, prefix: Optional[str] = None) -> AwsTestResult:
    boto3 = _require_boto3()
    sts = boto3.client("sts")
    ident = sts.get_caller_identity()
    account = ident.get("Account")
    region = boto3.session.Session().region_name
    if bucket:
        settings = S3ModelCacheSettings(
            bucket=bucket,
            prefix=(str(prefix or "").strip() or "models").strip("/"),
            region=region or _aws_region(),
            endpoint_url=os.getenv("EDMG_S3_ENDPOINT_URL", "").strip() or os.getenv("AWS_ENDPOINT_URL", "").strip(),
        )
        s3 = _s3_client(settings)
        s3.head_bucket(Bucket=bucket)
    return AwsTestResult(ok=True, account=account, region=region)


def upload_file_s3(bucket: str, key: str, path: str) -> dict[str, Any]:
    boto3 = _require_boto3()
    s3 = boto3.client("s3")
    s3.upload_file(path, bucket, key)
    return {"bucket": bucket, "key": key}


class S3ModelCache:
    label = "AWS S3 model cache"

    def __init__(self, settings: S3ModelCacheSettings):
        self.settings = settings
        self._client = _s3_client(settings)

    @classmethod
    def from_env(cls) -> "S3ModelCache | None":
        if not _truthy(os.getenv("EDMG_AWS_MODEL_CACHE")) and not _truthy(os.getenv("EDMG_S3_MODEL_CACHE")):
            return None
        return cls(settings_from_env())

    def object_key_for(self, entry: dict[str, Any], path: Path) -> str:
        target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
        folder = _clean_part(str(target.get("folder") or "models"), "models")
        model_id = _clean_part(str(entry.get("id") or path.stem), path.stem)
        filename = _clean_part(path.name, "model")
        prefix = self.settings.prefix.strip("/")
        return "/".join(part for part in (prefix, folder, model_id, filename) if part)

    def _is_missing_object(self, exc: Exception) -> bool:
        response = getattr(exc, "response", {})
        error = response.get("Error", {}) if isinstance(response, dict) else {}
        code = str(error.get("Code") or "")
        return code in {"404", "NoSuchKey", "NotFound"}

    def download_model(self, entry: dict[str, Any], dest: Path) -> bool:
        key = self.object_key_for(entry, dest)
        ClientError = _require_client_error()
        try:
            self._client.head_object(Bucket=self.settings.bucket, Key=key)
        except ClientError as exc:
            if self._is_missing_object(exc):
                return False
            raise
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".s3.tmp")
        self._client.download_file(self.settings.bucket, key, str(tmp))
        tmp.replace(dest)
        return True

    def upload_model(self, entry: dict[str, Any], path: Path) -> str:
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"Model cache upload source is not a file: {path}")
        key = self.object_key_for(entry, path)
        metadata = {
            "model_id": _clean_part(str(entry.get("id") or path.stem), path.stem)[:128],
            "source": _clean_part(str(entry.get("source") or "unknown"), "unknown")[:128],
            "kind": _clean_part(str(entry.get("kind") or "model"), "model")[:128],
        }
        extra_args: dict[str, Any] = {"Metadata": metadata}
        if self.settings.storage_class:
            extra_args["StorageClass"] = self.settings.storage_class
        self._client.upload_file(str(path), self.settings.bucket, key, ExtraArgs=extra_args)
        return key

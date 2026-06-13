from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any
from urllib.parse import urlparse


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


def _first_string(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _parse_s3_uri(value: str) -> tuple[str, str] | None:
    raw = str(value or "").strip()
    if not raw.lower().startswith("s3://"):
        return None
    parsed = urlparse(raw)
    bucket = str(parsed.netloc or "").strip()
    key = str(parsed.path or "").lstrip("/")
    if not bucket or not key:
        return None
    return bucket, key


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
    supports_directories = True

    def __init__(self, settings: S3ModelCacheSettings):
        self.settings = settings
        self._client = _s3_client(settings)

    @classmethod
    def from_env(cls) -> "S3ModelCache | None":
        if not _truthy(os.getenv("EDMG_AWS_MODEL_CACHE")) and not _truthy(os.getenv("EDMG_S3_MODEL_CACHE")):
            return None
        return cls(settings_from_env())

    def _explicit_object_location(self, entry: dict[str, Any]) -> tuple[str, str] | None:
        target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
        uri = _first_string(
            entry.get("s3_uri"),
            entry.get("aws_s3_uri"),
            entry.get("s3_url"),
            target.get("s3_uri"),
            target.get("aws_s3_uri"),
            target.get("s3_url"),
        )
        if uri:
            parsed = _parse_s3_uri(uri)
            if parsed is not None:
                return parsed

        key = _first_string(
            entry.get("s3_key"),
            entry.get("aws_s3_key"),
            entry.get("object_key"),
            entry.get("key"),
            target.get("s3_key"),
            target.get("aws_s3_key"),
            target.get("object_key"),
            target.get("key"),
        )
        if not key:
            return None

        parsed = _parse_s3_uri(key)
        if parsed is not None:
            return parsed

        bucket = _first_string(
            entry.get("s3_bucket"),
            entry.get("aws_s3_bucket"),
            target.get("s3_bucket"),
            target.get("aws_s3_bucket"),
            self.settings.bucket,
        )
        return bucket, key.strip("/")

    def _default_object_key_for(self, entry: dict[str, Any], path: Path) -> str:
        target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
        folder = _clean_part(str(target.get("folder") or "models"), "models")
        model_id = _clean_part(str(entry.get("id") or path.stem), path.stem)
        filename = _clean_part(path.name, "model")
        prefix = self.settings.prefix.strip("/")
        return "/".join(part for part in (prefix, folder, model_id, filename) if part)

    def _default_object_prefix_for(self, entry: dict[str, Any], path: Path) -> str:
        target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
        folder = _clean_part(str(target.get("folder") or "models"), "models")
        model_id = _clean_part(str(entry.get("id") or path.name or path.stem), path.name or path.stem)
        prefix = self.settings.prefix.strip("/")
        return "/".join(part for part in (prefix, folder, model_id) if part).strip("/")

    def _is_directory_asset(self, entry: dict[str, Any], path: Path) -> bool:
        target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
        engine = str(target.get("engine") or entry.get("engine") or "").strip().lower()
        kind = str(entry.get("kind") or "").strip().lower()
        return path.is_dir() or engine in {"internal", "runtime_bundle"} or kind in {"diffusers"}

    def object_location_for(self, entry: dict[str, Any], path: Path) -> tuple[str, str]:
        explicit = self._explicit_object_location(entry)
        if explicit is not None:
            return explicit
        return self.settings.bucket, self._default_object_key_for(entry, path)

    def object_key_for(self, entry: dict[str, Any], path: Path) -> str:
        return self.object_location_for(entry, path)[1]

    def object_prefix_for(self, entry: dict[str, Any], path: Path) -> tuple[str, str]:
        explicit = self._explicit_object_location(entry)
        if explicit is not None:
            bucket, key = explicit
            return bucket, key.strip("/")
        return self.settings.bucket, self._default_object_prefix_for(entry, path)

    def _is_missing_object(self, exc: Exception) -> bool:
        response = getattr(exc, "response", {})
        error = response.get("Error", {}) if isinstance(response, dict) else {}
        code = str(error.get("Code") or "")
        return code in {"404", "NoSuchKey", "NotFound"}

    def download_model(self, entry: dict[str, Any], dest: Path) -> bool:
        if self._is_directory_asset(entry, dest):
            return self._download_directory(entry, dest)

        bucket, key = self.object_location_for(entry, dest)
        ClientError = _require_client_error()
        try:
            self._client.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if self._is_missing_object(exc):
                return False
            raise
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".s3.tmp")
        self._client.download_file(bucket, key, str(tmp))
        tmp.replace(dest)
        return True

    def model_exists(self, entry: dict[str, Any], path: Path) -> str | None:
        if self._is_directory_asset(entry, path):
            bucket, prefix = self.object_prefix_for(entry, path)
            response = self._client.list_objects_v2(Bucket=bucket, Prefix=f"{prefix.rstrip('/')}/", MaxKeys=1)
            if int(response.get("KeyCount") or 0) <= 0:
                return None
            return prefix

        bucket, key = self.object_location_for(entry, path)
        ClientError = _require_client_error()
        try:
            self._client.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if self._is_missing_object(exc):
                return None
            raise
        return key

    def upload_model(self, entry: dict[str, Any], path: Path) -> str:
        if path.exists() and path.is_dir():
            return self._upload_directory(entry, path)
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"Model cache upload source is not a file: {path}")
        bucket, key = self.object_location_for(entry, path)
        metadata = {
            "model_id": _clean_part(str(entry.get("id") or path.stem), path.stem)[:128],
            "source": _clean_part(str(entry.get("source") or "unknown"), "unknown")[:128],
            "kind": _clean_part(str(entry.get("kind") or "model"), "model")[:128],
        }
        extra_args: dict[str, Any] = {"Metadata": metadata}
        if self.settings.storage_class:
            extra_args["StorageClass"] = self.settings.storage_class
        self._client.upload_file(str(path), bucket, key, ExtraArgs=extra_args)
        return key

    def _upload_directory(self, entry: dict[str, Any], path: Path) -> str:
        bucket, prefix = self.object_prefix_for(entry, path)
        if not path.exists() or not path.is_dir():
            raise RuntimeError(f"Model cache upload source is not a directory: {path}")

        metadata = {
            "model_id": _clean_part(str(entry.get("id") or path.name), path.name)[:128],
            "source": _clean_part(str(entry.get("source") or "unknown"), "unknown")[:128],
            "kind": _clean_part(str(entry.get("kind") or "model"), "model")[:128],
        }
        extra_args: dict[str, Any] = {"Metadata": metadata}
        if self.settings.storage_class:
            extra_args["StorageClass"] = self.settings.storage_class

        uploaded = 0
        for item in path.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(path).as_posix()
            key = f"{prefix.rstrip('/')}/{rel}"
            self._client.upload_file(str(item), bucket, key, ExtraArgs=extra_args)
            uploaded += 1
        if uploaded <= 0:
            raise RuntimeError(f"Model cache upload directory is empty: {path}")
        return prefix

    def _download_directory(self, entry: dict[str, Any], dest: Path) -> bool:
        bucket, prefix = self.object_prefix_for(entry, dest)
        prefix = prefix.rstrip("/")
        paginator = self._client.get_paginator("list_objects_v2")
        objects: list[str] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/"):
            for item in page.get("Contents") or []:
                key = str(item.get("Key") or "")
                if key and not key.endswith("/"):
                    objects.append(key)
        if not objects:
            return False

        tmp = dest.parent / f".{dest.name}.s3.tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True, exist_ok=True)
        for key in objects:
            rel = key[len(prefix) :].lstrip("/")
            if not rel:
                continue
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            self._client.download_file(bucket, key, str(target))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        tmp.replace(dest)
        return True

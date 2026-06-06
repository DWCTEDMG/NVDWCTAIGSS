from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _require_blob_service_client():
    try:
        from azure.storage.blob import BlobServiceClient  # type: ignore
        return BlobServiceClient
    except Exception as exc:
        raise RuntimeError("Azure integration requires optional deps: pip install -e '.[azure]'") from exc


def _require_default_credential():
    try:
        from azure.identity import DefaultAzureCredential  # type: ignore
        return DefaultAzureCredential
    except Exception as exc:
        raise RuntimeError("Azure CLI/AAD auth requires optional deps: pip install -e '.[azure]'") from exc


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _clean_part(value: str, fallback: str) -> str:
    raw = str(value or "").strip().strip("/\\") or fallback
    return re.sub(r"[^A-Za-z0-9._=-]+", "-", raw).strip(".-") or fallback


@dataclass(frozen=True)
class AzureBlobSettings:
    container: str
    prefix: str = "models"
    connection_string: str = ""
    account_url: str = ""
    account_name: str = ""


def settings_from_env(*, container: str | None = None, prefix: str | None = None) -> AzureBlobSettings:
    account_name = os.getenv("EDMG_AZURE_STORAGE_ACCOUNT", "").strip() or os.getenv("AZURE_STORAGE_ACCOUNT", "").strip()
    account_url = os.getenv("EDMG_AZURE_STORAGE_ACCOUNT_URL", "").strip() or os.getenv("AZURE_STORAGE_ACCOUNT_URL", "").strip()
    if not account_url and account_name:
        account_url = f"https://{account_name}.blob.core.windows.net"

    resolved_container = (
        str(container or "").strip()
        or os.getenv("EDMG_AZURE_MODEL_CONTAINER", "").strip()
        or os.getenv("EDMG_AZURE_STORAGE_CONTAINER", "").strip()
        or os.getenv("AZURE_STORAGE_CONTAINER", "").strip()
    )
    if not resolved_container:
        raise RuntimeError("Set EDMG_AZURE_MODEL_CONTAINER to the Azure Blob container for model cache files.")

    return AzureBlobSettings(
        container=resolved_container,
        prefix=(str(prefix or "").strip() or os.getenv("EDMG_AZURE_MODEL_CACHE_PREFIX", "").strip() or "models").strip("/"),
        connection_string=os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip(),
        account_url=account_url,
        account_name=account_name,
    )


def _blob_service_client(settings: AzureBlobSettings):
    BlobServiceClient = _require_blob_service_client()
    if settings.connection_string:
        return BlobServiceClient.from_connection_string(settings.connection_string)
    if not settings.account_url:
        raise RuntimeError(
            "Set AZURE_STORAGE_CONNECTION_STRING or EDMG_AZURE_STORAGE_ACCOUNT/EDMG_AZURE_STORAGE_ACCOUNT_URL."
        )
    DefaultAzureCredential = _require_default_credential()
    return BlobServiceClient(account_url=settings.account_url, credential=DefaultAzureCredential())


class AzureModelCache:
    label = "Azure model cache"

    def __init__(self, settings: AzureBlobSettings):
        self.settings = settings
        self._client = _blob_service_client(settings)

    @classmethod
    def from_env(cls) -> "AzureModelCache | None":
        if not _truthy(os.getenv("EDMG_AZURE_MODEL_CACHE")):
            return None
        return cls(settings_from_env())

    def blob_name_for(self, entry: dict[str, Any], path: Path) -> str:
        target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
        folder = _clean_part(str(target.get("folder") or "models"), "models")
        model_id = _clean_part(str(entry.get("id") or path.stem), path.stem)
        filename = _clean_part(path.name, "model")
        prefix = self.settings.prefix.strip("/")
        return "/".join(part for part in (prefix, folder, model_id, filename) if part)

    def _blob_client(self, entry: dict[str, Any], path: Path):
        return self._client.get_blob_client(
            container=self.settings.container,
            blob=self.blob_name_for(entry, path),
        )

    def download_model(self, entry: dict[str, Any], dest: Path) -> bool:
        blob = self._blob_client(entry, dest)
        if not blob.exists():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".azure.tmp")
        with open(tmp, "wb") as handle:
            stream = blob.download_blob()
            stream.readinto(handle)
        tmp.replace(dest)
        return True

    def upload_model(self, entry: dict[str, Any], path: Path) -> str:
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"Model cache upload source is not a file: {path}")
        blob = self._blob_client(entry, path)
        metadata = {
            "model_id": _clean_part(str(entry.get("id") or path.stem), path.stem)[:128],
            "source": _clean_part(str(entry.get("source") or "unknown"), "unknown")[:128],
            "kind": _clean_part(str(entry.get("kind") or "model"), "model")[:128],
        }
        with open(path, "rb") as handle:
            blob.upload_blob(handle, overwrite=True, metadata=metadata)
        return str(blob.blob_name)


def test_credentials(*, container: str | None = None, prefix: str | None = None) -> dict[str, Any]:
    settings = settings_from_env(container=container, prefix=prefix)
    client = _blob_service_client(settings)
    account = client.get_account_information()
    container_client = client.get_container_client(settings.container)
    container_client.get_container_properties()
    return {
        "ok": True,
        "provider": "azure",
        "container": settings.container,
        "prefix": settings.prefix,
        "account_kind": account.get("account_kind") or account.get("sku_name"),
    }

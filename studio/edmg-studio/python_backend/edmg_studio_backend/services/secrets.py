from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


SERVICE_NAME = "dwct-edmg-studio"


def _restrict_permissions(path: Path, mode: int) -> None:
    try:
        if os.name != "nt":
            os.chmod(path, mode)
    except Exception:
        pass


def _config_dir(data_dir: Path) -> Path:
    p = (data_dir / "config").resolve()
    p.mkdir(parents=True, exist_ok=True)
    _restrict_permissions(p, 0o700)
    return p


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _restrict_permissions(path.parent, 0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    _restrict_permissions(tmp, 0o600)
    tmp.replace(path)
    _restrict_permissions(path, 0o600)


def _b64e(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def _b64d(s: str) -> str:
    return base64.b64decode(s.encode("ascii")).decode("utf-8")


@dataclass
class SecretsStatus:
    store: str
    available: bool
    has_hf_token: bool
    has_civitai_api_key: bool
    has_openai_compat_api_key: bool
    note: str | None = None


class SecretStore:
    """Persist small secrets (tokens) for the Studio.

    Preferred backend: OS keychain via `keyring`.
    Fallback: local file under <data_dir>/config/secrets.json (base64-encoded; not strong encryption).

    We never return secrets over the API.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._file_path = _config_dir(data_dir) / "secrets.json"

        forced = os.getenv("EDMG_SECRETS_STORE", "auto").strip().lower()
        self._forced = forced

        self._keyring = None
        self._keyring_ok = False
        self._note: str | None = None

        if forced in ("file", "plaintext"):
            self._note = "Using file-based secret storage (set EDMG_SECRETS_STORE=auto to try OS keychain)."
            return

        try:
            import keyring  # type: ignore

            self._keyring = keyring
            # We don't write a test secret here; just mark as available.
            self._keyring_ok = True
        except Exception as e:
            self._keyring = None
            self._keyring_ok = False
            self._note = f"OS keychain unavailable; falling back to file storage. ({e})"

    # ---- public API ----
    def status(self) -> SecretsStatus:
        hf = bool(self.get("hf_token"))
        cv = bool(self.get("civitai_api_key"))
        oa = bool(self.get("openai_compat_api_key"))
        store = "keyring" if self._keyring_ok else "file"
        if self._forced in ("file", "plaintext"):
            store = "file"
        return SecretsStatus(
            store=store,
            available=True,
            has_hf_token=hf,
            has_civitai_api_key=cv,
            has_openai_compat_api_key=oa,
            note=self._note,
        )

    def get(self, name: str) -> str | None:
        name = (name or "").strip().lower()
        if not name:
            return None

        # Prefer keyring.
        if self._keyring_ok and self._forced not in ("file", "plaintext") and self._keyring is not None:
            try:
                v = self._keyring.get_password(SERVICE_NAME, name)
                if v:
                    return str(v)
            except Exception:
                # fall back
                pass

        # File fallback.
        data = _read_json(self._file_path, default={})
        if isinstance(data, dict) and name in data and isinstance(data[name], dict):
            vb = data[name].get("value_b64")
            if isinstance(vb, str) and vb:
                try:
                    return _b64d(vb)
                except Exception:
                    return None
        return None

    def set(self, name: str, value: str) -> None:
        name = (name or "").strip().lower()
        if not name:
            raise ValueError("Missing secret name")
        value = value or ""

        if self._keyring_ok and self._forced not in ("file", "plaintext") and self._keyring is not None:
            try:
                self._keyring.set_password(SERVICE_NAME, name, value)
                return
            except Exception as e:
                self._note = f"Keychain write failed; using file storage instead. ({e})"

        data = _read_json(self._file_path, default={})
        if not isinstance(data, dict):
            data = {}
        data[name] = {"value_b64": _b64e(value), "set_at": time.time()}
        _write_json(self._file_path, data)

    def delete(self, name: str) -> None:
        name = (name or "").strip().lower()
        if not name:
            return
        if self._keyring_ok and self._forced not in ("file", "plaintext") and self._keyring is not None:
            try:
                self._keyring.delete_password(SERVICE_NAME, name)
            except Exception:
                pass

        data = _read_json(self._file_path, default={})
        if isinstance(data, dict) and name in data:
            try:
                del data[name]
                _write_json(self._file_path, data)
            except Exception:
                pass

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_RENDER_PROVIDER_SETTINGS: dict[str, Any] = {
    "nvidia": {
        "enabled": True,
        "non_nvidia_providers_enabled": False,
    },
}


def _config_dir(data_dir: Path) -> Path:
    p = (data_dir / "config").resolve()
    p.mkdir(parents=True, exist_ok=True)
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
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _clone_defaults() -> dict[str, Any]:
    return json.loads(json.dumps(DEFAULT_RENDER_PROVIDER_SETTINGS))


class RenderSettingsStore:
    def __init__(self, data_dir: Path):
        self._path = _config_dir(data_dir) / "render_providers.json"

    def get(self) -> dict[str, Any]:
        current = _read_json(self._path, default={})
        return self._sanitize(current if isinstance(current, dict) else {})

    def update(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        cleaned = self._sanitize(payload if isinstance(payload, dict) else {})
        _write_json(self._path, cleaned)
        return cleaned

    def _sanitize(self, payload: dict[str, Any]) -> dict[str, Any]:
        out = _clone_defaults()
        nvidia = payload.get("nvidia") if isinstance(payload.get("nvidia"), dict) else {}
        out["nvidia"] = {
            "enabled": True,
            "non_nvidia_providers_enabled": False,
            "preferred_service": str(nvidia.get("preferred_service") or "auto").strip().lower() or "auto",
        }
        return out

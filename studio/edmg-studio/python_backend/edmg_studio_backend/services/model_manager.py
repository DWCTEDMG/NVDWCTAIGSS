from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests

try:
    from huggingface_hub import snapshot_download  # type: ignore
except Exception:  # pragma: no cover
    snapshot_download = None  # type: ignore

from ..errors import UserFacingError
from .model_catalog import built_in_catalog, built_in_packs
from ..services.setup_wizard import comfy_portable_installed, comfy_portable_root
from .secrets import SecretStore

try:
    from ..integrations.azure import AzureModelCache
except Exception:  # pragma: no cover - optional integration
    AzureModelCache = None  # type: ignore

try:
    from ..integrations.aws import S3ModelCache
except Exception:  # pragma: no cover - optional integration
    S3ModelCache = None  # type: ignore


# ------------------------------ persistence ------------------------------

def _config_dir(data_dir: Path) -> Path:
    return _ensure_managed_dir(data_dir / "config", label="config")

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


def _normalize_path(path: Path | str) -> str:
    raw = os.fspath(path)
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
    return os.path.normcase(os.path.normpath(os.path.abspath(raw)))


def _same_path(left: Path | str, right: Path | str) -> bool:
    return _normalize_path(left) == _normalize_path(right)


def _read_reparse_target(path: Path) -> Path | None:
    try:
        raw = os.readlink(path)
    except OSError:
        return None
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
    if not os.path.isabs(raw):
        raw = os.path.join(os.fspath(path.parent), raw)
    return Path(os.path.abspath(raw))


def _repair_mutual_junction(path: Path) -> bool:
    if os.name != "nt":
        return False
    target = _read_reparse_target(path)
    if target is None:
        return False
    reverse = _read_reparse_target(target)
    if reverse is None or not _same_path(reverse, path):
        return False
    try:
        os.rmdir(path)
        path.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


def _repair_mutual_junction_chain(path: Path) -> bool:
    current = path
    while True:
        if _repair_mutual_junction(current):
            return True
        if current.parent == current:
            return False
        current = current.parent


def _ensure_managed_dir(path: Path, *, label: str) -> Path:
    candidate = Path(os.path.abspath(os.fspath(path.expanduser())))
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    except (OSError, RuntimeError) as exc:
        if _repair_mutual_junction_chain(candidate):
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        raise UserFacingError(
            f"Studio {label} path is invalid: {candidate}",
            hint="Restart EDMG Studio so it can repair the storage junctions, then retry.",
            code="INVALID_STORAGE_PATH",
        ) from exc


def _entry_render(entry: dict[str, Any]) -> dict[str, Any]:
    render = entry.get("render")
    return dict(render) if isinstance(render, dict) else {}


def _entry_target(entry: dict[str, Any]) -> dict[str, Any]:
    target = entry.get("target")
    return dict(target) if isinstance(target, dict) else {}


def _entry_engine(entry: dict[str, Any]) -> str:
    render = _entry_render(entry)
    target = _entry_target(entry)
    engine = str(render.get("engine") or target.get("engine") or "").strip().lower()
    if engine:
        return engine
    kind = str(entry.get("kind") or "").strip().lower()
    if kind == "diffusers":
        return "internal"
    return "comfyui"


def _entry_family(entry: dict[str, Any]) -> str | None:
    family = str(entry.get("family") or _entry_render(entry).get("family") or "").strip().lower()
    return family or None


def _entry_support_flags(entry: dict[str, Any]) -> dict[str, bool]:
    kind = str(entry.get("kind") or "").strip().lower()
    engine = _entry_engine(entry)
    family = _entry_family(entry)
    if kind in {"checkpoint", "diffusers"}:
        return {
            "supports_txt2img": True,
            "supports_img2img": True,
            "supports_inpaint": True,
            "supports_outpaint": True,
            "supports_controlnet": not (engine == "internal" and family == "sd35"),
        }
    return {
        "supports_txt2img": False,
        "supports_img2img": False,
        "supports_inpaint": False,
        "supports_outpaint": False,
        "supports_controlnet": False,
    }


def _normalize_catalog_entry(entry: dict[str, Any]) -> dict[str, Any]:
    item = dict(entry)
    render = _entry_render(item)
    engine = _entry_engine(item)
    family = _entry_family(item)
    kind = str(item.get("kind") or "").strip().lower()
    render_modes = [str(mode).strip() for mode in (render.get("render_modes") or []) if str(mode).strip()]
    if kind in {"checkpoint", "diffusers"} and "stills" not in render_modes:
        render_modes.append("stills")
    if kind == "diffusers" and "internal_video" not in render_modes:
        render_modes.append("internal_video")
    render["engine"] = engine
    render["family"] = family
    render["render_modes"] = render_modes
    item["render"] = render
    item["engine"] = engine
    item["family"] = family
    item.update(_entry_support_flags(item))
    return item


# ------------------------------ tasks ------------------------------

@dataclass
class ModelTask:
    id: str
    name: str
    status: str = "queued"  # queued|running|done|failed
    progress: Optional[float] = None
    last_log: str = ""
    error: Optional[str] = None
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    model_id: Optional[str] = None


class ModelTaskManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._tasks: dict[str, ModelTask] = {}

    def list(self) -> list[ModelTask]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda t: (t.started_at or 0), reverse=True)

    def start(self, name: str, fn, *args, **kwargs) -> ModelTask:
        task = ModelTask(id=str(uuid.uuid4())[:8], name=name, status="queued")
        with self._lock:
            self._tasks[task.id] = task

        def runner():
            task.status = "running"
            task.started_at = time.time()
            try:
                fn(task, *args, **kwargs)
                task.status = "done"
            except Exception as e:
                task.status = "failed"
                task.error = str(e)
                task.last_log = (task.last_log + "\n" if task.last_log else "") + f"ERROR: {e}"
            finally:
                task.ended_at = time.time()

        threading.Thread(target=runner, daemon=True).start()
        return task

    @staticmethod
    def log(task: ModelTask, msg: str) -> None:
        task.last_log = msg

    @staticmethod
    def set_progress(task: ModelTask, v: Optional[float]) -> None:
        task.progress = v


# ------------------------------ manager ------------------------------

class ModelManager:
    def __init__(
        self,
        data_dir: Path,
        models_dir: Path,
        external_dir: Path,
        comfyui_url: str,
        _legacy_planner_url: str,
        secrets: SecretStore | None = None,
    ):
        self.data_dir = data_dir
        self.models_dir = models_dir
        self.external_dir = external_dir
        self.comfyui_url = comfyui_url.rstrip("/")
        self.secrets = secrets
        self.tasks = ModelTaskManager()
        self.model_cache = self._build_model_cache()

        cfg = _config_dir(self.data_dir)
        self._user_models_path = cfg / "models_user.json"
        self._accept_path = cfg / "licenses_accepted.json"
        self._cloud_models_path = cfg / "models_cloud.json"

        self._lock = threading.Lock()

    def _build_model_cache(self):
        for cache_type in (S3ModelCache, AzureModelCache):
            if cache_type is None:
                continue
            try:
                cache = cache_type.from_env()
            except Exception:
                continue
            if cache is not None:
                return cache
        return None

    def _model_cache_label(self) -> str:
        cache = getattr(self, "model_cache", None)
        label = getattr(cache, "label", "")
        return str(label or "model cache")

    def _model_storage_mode(self) -> str:
        raw = (
            os.getenv("EDMG_MODEL_STORAGE_MODE", "").strip().lower()
            or os.getenv("EDMG_AWS_MODEL_CACHE_MODE", "").strip().lower()
            or os.getenv("EDMG_MODEL_CACHE_MODE", "").strip().lower()
        )
        if raw in {"cloud_only", "s3_only", "remote_only"}:
            return "cloud_only"
        return "local_cache"

    def _cloud_models(self) -> dict[str, Any]:
        data = _read_json(self._cloud_models_path, default={})
        return data if isinstance(data, dict) else {}

    def _write_cloud_models(self, data: dict[str, Any]) -> None:
        _write_json(self._cloud_models_path, data)

    def _record_cloud_model(self, entry: dict[str, Any], object_name: str, *, mode: str) -> None:
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            return
        cache = getattr(self, "model_cache", None)
        settings = getattr(cache, "settings", None)
        record: dict[str, Any] = {
            "provider": self._model_cache_label(),
            "object": object_name,
            "mode": mode,
            "stored_at": time.time(),
        }
        for attr in ("bucket", "container", "prefix", "region", "endpoint_url"):
            value = getattr(settings, attr, None)
            if value:
                record[attr] = value
        data = self._cloud_models()
        data[model_id] = record
        self._write_cloud_models(data)

    def _cloud_model_record(self, model_id: str) -> dict[str, Any] | None:
        record = self._cloud_models().get(str(model_id or ""))
        return record if isinstance(record, dict) else None

    def _cache_model_exists(self, entry: dict[str, Any], dest: Path) -> str | None:
        cache = getattr(self, "model_cache", None)
        exists = getattr(cache, "model_exists", None)
        if cache is None or not callable(exists):
            return None
        return exists(entry, dest)

    def _cache_entry_from_cloud_record(self, entry: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(record, dict):
            return entry
        object_name = str(record.get("object") or record.get("key") or "").strip()
        if not object_name:
            return entry

        cache_entry = dict(entry)
        # S3ModelCache understands these override fields. Other cache backends
        # keep using their deterministic cache key.
        cache_entry["s3_key"] = object_name
        bucket = str(record.get("bucket") or "").strip()
        if bucket:
            cache_entry["s3_bucket"] = bucket
        return cache_entry

    def _cloud_temp_path(self, dest: Path) -> Path:
        root = _ensure_managed_dir(self.data_dir / "cache" / "model_transfers", label="model transfer cache")
        return root / uuid.uuid4().hex / dest.name

    def _all_entries(self) -> list[dict[str, Any]]:
        cat = self.catalog()
        return list(cat.get("catalog") or []) + list(cat.get("user") or [])

    def _find_entry(self, model_id: str) -> dict[str, Any] | None:
        return next(
            (e for e in self._all_entries() if isinstance(e, dict) and e.get("id") == model_id),
            None,
        )

    # ---- catalog ----
    def catalog(self) -> dict[str, Any]:
        built = [_normalize_catalog_entry(entry) for entry in built_in_catalog()]
        user = _read_json(self._user_models_path, default=[])
        if not isinstance(user, list):
            user = []
        user = [_normalize_catalog_entry(entry) for entry in user if isinstance(entry, dict)]
        accepted = _read_json(self._accept_path, default={})
        if not isinstance(accepted, dict):
            accepted = {}

        installed = self._installed_map(built + user)
        cloud = self._cloud_models()

        return {
            "catalog": built,
            "user": user,
            "packs": built_in_packs(),
            "accepted": accepted,
            "installed": installed,
            "cloud": cloud,
            "storage_mode": self._model_storage_mode(),
            "model_cache": self._model_cache_label() if self.model_cache is not None else None,
        }

    # ---- acceptance ----
    def accept_license(self, model_id: str, license_id: str) -> None:
        if not model_id or not license_id:
            raise UserFacingError("Missing model_id or license_id", hint="Select a model and accept its license terms.")
        data = _read_json(self._accept_path, default={})
        if not isinstance(data, dict):
            data = {}
        data[model_id] = {
            "license_id": license_id,
            "accepted_at": time.time(),
        }
        _write_json(self._accept_path, data)

    def _is_accepted(self, model_id: str) -> bool:
        data = _read_json(self._accept_path, default={})
        return isinstance(data, dict) and model_id in data

    # ---- add/remove user models ----
    def add_user_model(self, entry: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(entry, dict) or not entry.get("id"):
            raise UserFacingError("Invalid model entry", hint="Provide a valid model entry.")
        with self._lock:
            user = _read_json(self._user_models_path, default=[])
            if not isinstance(user, list):
                user = []
            # replace if exists
            user = [u for u in user if isinstance(u, dict) and u.get("id") != entry["id"]]
            user.append(entry)
            _write_json(self._user_models_path, user)
        return entry

    def remove_user_model(self, model_id: str) -> None:
        with self._lock:
            user = _read_json(self._user_models_path, default=[])
            if not isinstance(user, list):
                return
            user2 = [u for u in user if isinstance(u, dict) and u.get("id") != model_id]
            _write_json(self._user_models_path, user2)

    # ---- install ----
    def install(self, model_id: str) -> ModelTask:
        entry = self._find_entry(model_id)
        if not entry:
            raise UserFacingError(f"Unknown model id: {model_id}", hint="Refresh the model catalog and try again.")
        if entry.get("installable", True) is False:
            raise UserFacingError(
                "This model is discovery-only in Studio right now",
                hint="Open the model card to review the external runtime bundle, or install a Studio-supported checkpoint/diffusers model instead.",
                code="MODEL_BROWSER_ONLY",
            )

        # Enforce license acceptance for any external weights/download.
        if entry.get("kind") != "llm" and not self._is_accepted(model_id):
            raise UserFacingError(
                "License not accepted",
                hint="Open Model Manager, click the model, review license, then click Accept & Install."
            )

        source = (entry.get("source") or "").lower()
        if source in {"ollama", "legacy_ollama"}:
            raise UserFacingError(
                "Legacy local planner installs are disabled in the NVIDIA-only profile.",
                hint="Use the configured NVIDIA NIM planner service instead.",
                code="NVIDIA_ONLY_PROFILE",
                status_code=400,
            )
        if source in ("hf", "civitai", "local", "s3"):
            name = f"Install: {entry.get('name')}"
            return self.tasks.start(name, self._install_file_model, entry)

        raise UserFacingError("Unsupported model source", hint=f"Source '{source}' is not supported yet.")

    def install_pack(self, pack_id: str) -> list[ModelTask]:
        packs = built_in_packs()
        pack = next((p for p in packs if p.get("id") == pack_id), None)
        if not pack:
            raise UserFacingError("Unknown pack", hint="Choose a valid pack.")
        tasks: list[ModelTask] = []
        for mid in (pack.get("models") or []):
            tasks.append(self.install(mid))
        return tasks

    def restore_local(self, model_id: str) -> ModelTask:
        entry = self._find_entry(model_id)
        if not entry:
            raise UserFacingError(f"Unknown model id: {model_id}", hint="Refresh the model catalog and try again.")
        name = f"Restore local: {entry.get('name')}"
        return self.tasks.start(name, self._restore_cloud_model, entry)


    # ---- resolution ----
    def _internal_models_dir(self, folder: str) -> Path:
        return _ensure_managed_dir(self.models_dir / "internal" / folder, label="internal models")

    def _models_dest(self, entry: dict[str, Any]) -> tuple[str, Path]:
        """Return (mode, dest_path).

        mode:
          - "file": download/copy a single file into dest_path
          - "snapshot": download a HF repo snapshot into dest_path (directory)
        """
        target = entry.get("target") or {}
        engine = (target.get("engine") if isinstance(target, dict) else "") or "comfyui"
        folder = (target.get("folder") if isinstance(target, dict) else None) or "checkpoints"
        fname = str(entry.get("filename") or "")

        if engine == "internal":
            # Diffusers expects a directory repo snapshot.
            model_dir = self._internal_models_dir(folder) / str(entry.get("id") or "model")
            return "snapshot", model_dir

        # default: comfyui file model
        if not fname:
            fname = "model.safetensors"
        return "file", self._comfy_models_dir(folder) / fname

    # ---- resolution ----
    def _comfy_models_dir(self, folder: str) -> Path:
        return _ensure_managed_dir(self.models_dir / folder, label="models")

    def _legacy_comfy_models_dir(self, folder: str) -> Path | None:
        if comfy_portable_installed(self.external_dir, self.data_dir):
            root = Path(os.path.abspath(os.fspath(comfy_portable_root(self.external_dir, self.data_dir) / "ComfyUI" / "models" / folder)))
            try:
                if root.exists():
                    return root
            except (OSError, RuntimeError):
                if _repair_mutual_junction_chain(root) and root.exists():
                    return root
        return None

    def _installed_map(self, entries: list[dict[str, Any]]) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for e in entries:
            mid = str(e.get("id") or "")
            if not mid:
                continue
            src = (e.get("source") or "").lower()
            if src in {"ollama", "legacy_ollama"}:
                out[mid] = False
                continue

            target = e.get("target") or {}
            engine = (target.get("engine") if isinstance(target, dict) else "") or "comfyui"
            folder = (target.get("folder") if isinstance(target, dict) else None) or "checkpoints"
            fname = str(e.get("filename") or "")

            if engine == "internal":
                out[mid] = self._internal_asset_installed(e, self._internal_models_dir(folder) / mid)
                continue

            if fname:
                primary = self._comfy_models_dir(folder) / fname
                legacy_root = self._legacy_comfy_models_dir(folder)
                out[mid] = primary.exists() or bool(legacy_root and (legacy_root / fname).exists())
            else:
                out[mid] = False
        return out

    def _iter_comfy_model_dirs(self, folder: str) -> list[Path]:
        dirs = [self._comfy_models_dir(folder)]
        legacy_root = self._legacy_comfy_models_dir(folder)
        if legacy_root is not None:
            dirs.append(legacy_root)
        return dirs

    def _internal_component_has_weights(self, component_dir: Path) -> bool:
        if not component_dir.exists() or not component_dir.is_dir():
            return False
        patterns = (
            "diffusion_pytorch_model*.safetensors",
            "diffusion_pytorch_model*.bin",
            "pytorch_model*.safetensors",
            "pytorch_model*.bin",
            "model*.safetensors",
            "model*.bin",
            "model.onnx",
            "model.onnx_data",
            "openvino_model.bin",
            "flax_model.msgpack",
        )
        return any(
            candidate.exists()
            for pattern in patterns
            for candidate in component_dir.glob(pattern)
        )

    def _diffusers_snapshot_complete(self, path: Path) -> bool:
        model_index = path / "model_index.json"
        if not model_index.exists():
            return False
        try:
            data = json.loads(model_index.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(data, dict):
            return False

        weightless_markers = (
            "Tokenizer",
            "TokenizerFast",
            "Scheduler",
            "ImageProcessor",
            "FeatureExtractor",
        )
        required_components: list[str] = []
        for name, spec in data.items():
            if not isinstance(name, str) or not isinstance(spec, list) or len(spec) < 2:
                continue
            class_name = str(spec[1] or "")
            if any(marker in class_name for marker in weightless_markers):
                continue
            required_components.append(name)

        if not required_components:
            return False

        return all(self._internal_component_has_weights(path / component) for component in required_components)

    def _internal_asset_installed(self, entry: dict[str, Any], path: Path) -> bool:
        if not path.exists():
            return False

        kind = str(entry.get("kind") or "").strip().lower()
        if kind == "diffusers":
            return self._diffusers_snapshot_complete(path)
        if kind == "controlnet":
            if not (path / "config.json").exists():
                return False
            return any(
                candidate.exists()
                for pattern in ("diffusion_pytorch_model*.safetensors", "diffusion_pytorch_model*.bin")
                for candidate in path.glob(pattern)
            )
        return True

    def _local_installed_path(self, entry: dict[str, Any]) -> Path | None:
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            return None

        target = entry.get("target") or {}
        engine = (target.get("engine") if isinstance(target, dict) else "") or "comfyui"
        folder = (target.get("folder") if isinstance(target, dict) else None) or "checkpoints"
        if engine == "internal":
            path = self._internal_models_dir(folder) / model_id
            return path if self._internal_asset_installed(entry, path) else None
        if engine == "runtime_bundle":
            path = self._internal_models_dir(folder) / model_id
            return path if path.exists() else None

        filename = str(entry.get("filename") or "")
        if not filename:
            return None

        primary = self._comfy_models_dir(folder) / filename
        if primary.exists():
            return primary
        legacy_root = self._legacy_comfy_models_dir(folder)
        if legacy_root is not None:
            legacy = legacy_root / filename
            if legacy.exists():
                return legacy
        return None

    def _materialize_file_from_model_cache(self, entry: dict[str, Any], dest: Path) -> Path | None:
        cache = getattr(self, "model_cache", None)
        if cache is None:
            return None

        model_id = str(entry.get("id") or "").strip()
        record = self._cloud_model_record(model_id)
        candidates: list[dict[str, Any]] = []
        if record is not None:
            candidates.append(self._cache_entry_from_cloud_record(entry, record))
        candidates.append(entry)

        seen_objects: set[str] = set()
        for candidate in candidates:
            try:
                object_name = self._cache_model_exists(candidate, dest)
            except Exception as exc:
                if record is not None:
                    raise UserFacingError(
                        "Cloud model cache is unavailable",
                        hint=f"Check the {self._model_cache_label()} credentials and bucket/prefix settings, then retry.",
                        code="MODEL_CACHE_UNAVAILABLE",
                    ) from exc
                continue

            if not object_name or object_name in seen_objects:
                continue
            seen_objects.add(str(object_name))

            try:
                if not cache.download_model(candidate, dest):
                    continue
            except Exception as exc:
                raise UserFacingError(
                    "Could not restore model from cloud cache",
                    hint=f"Check that the model object exists in {self._model_cache_label()} and that Studio has read access.",
                    code="MODEL_CACHE_RESTORE_FAILED",
                ) from exc

            mode = str(record.get("mode") or "remote_cache") if record is not None else "remote_cache"
            self._record_cloud_model(entry, str(object_name), mode=mode)
            return dest if dest.exists() else None

        return None

    def internal_asset_issue(self, model_id: str) -> str | None:
        entry = self._find_entry(model_id)
        if not entry:
            return None
        target = entry.get("target") or {}
        engine = (target.get("engine") if isinstance(target, dict) else "") or "comfyui"
        if engine != "internal":
            return None
        folder = (target.get("folder") if isinstance(target, dict) else None) or "checkpoints"
        path = self._internal_models_dir(folder) / model_id
        if not path.exists():
            return "missing"
        if self._internal_asset_installed(entry, path):
            return None
        return "incomplete"

    def _find_existing_comfy_file(self, folder: str, ref: str) -> Path | None:
        raw = str(ref or "").strip()
        if not raw:
            return None

        candidates = {raw, Path(raw).name}
        stem = Path(raw).stem
        for model_dir in self._iter_comfy_model_dirs(folder):
            for candidate in candidates:
                match = model_dir / candidate
                if match.exists() and match.is_file():
                    return match
            if stem:
                for match in model_dir.glob("*"):
                    if match.is_file() and match.stem == stem:
                        return match
        return None

    def resolve_comfy_asset(
        self,
        ref: str,
        *,
        folder: str,
        allowed_kinds: set[str] | None = None,
    ) -> dict[str, Any]:
        raw = str(ref or "").strip()
        if not raw:
            raise UserFacingError("Missing model selection", hint=f"Pick a Studio {folder.rstrip('s')} first.")

        entry = self._find_entry(raw)
        if entry is None:
            normalized_folder = str(folder or "checkpoints").strip().lower()
            for candidate in self._all_entries():
                if not isinstance(candidate, dict):
                    continue
                target = candidate.get("target") if isinstance(candidate.get("target"), dict) else {}
                candidate_folder = str(target.get("folder") or "checkpoints").strip().lower()
                filename = str(candidate.get("filename") or "").strip()
                if candidate_folder != normalized_folder:
                    continue
                if filename == raw or Path(filename).stem == Path(raw).stem:
                    entry = candidate
                    break

        if entry is not None:
            kind = str(entry.get("kind") or "").strip().lower()
            target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
            engine = str(target.get("engine") or entry.get("engine") or "comfyui").strip().lower()
            if engine != "comfyui":
                raise UserFacingError(
                    f"{entry.get('name') or raw} is not a valid {folder.rstrip('s')} selection",
                    hint="Pick a Studio ComfyUI asset for this workflow.",
                )
            if allowed_kinds and kind not in allowed_kinds:
                expected = ", ".join(sorted(allowed_kinds))
                raise UserFacingError(
                    f"{entry.get('name') or raw} is not a valid {folder.rstrip('s')} selection",
                    hint=f"Pick a Studio asset of type: {expected}.",
                )

            filename = str(
                entry.get("filename")
                or Path(str(entry.get("source_path") or "")).name
                or raw
            ).strip()
            installed = self.resolve_installed_path(str(entry.get("id") or ""), materialize_remote=True)
            resolved_path = installed or self._find_existing_comfy_file(folder, filename)
            if resolved_path is None:
                cloud_record = self._cloud_model_record(str(entry.get("id") or ""))
                hint = "Install the asset in Model Manager, or import it as a local Studio model first."
                if cloud_record is not None:
                    hint = (
                        "This asset is stored in the cloud cache only. Local ComfyUI needs a filesystem model path; "
                        "restore it locally or use a remote worker that mounts/downloads the S3 cache."
                    )
                raise UserFacingError(
                    f"{entry.get('name') or filename} is not installed",
                    hint=hint,
                )

            return {
                "id": entry.get("id"),
                "name": entry.get("name") or Path(filename).stem,
                "kind": entry.get("kind") or folder.rstrip("s"),
                "filename": resolved_path.name,
                "path": str(resolved_path),
                "source": entry.get("source") or "local",
                "folder": folder,
            }

        resolved_path = self._find_existing_comfy_file(folder, raw)
        if resolved_path is None:
            raise UserFacingError(
                f"Unknown Studio asset: {raw}",
                hint=f"Import the file into Models as a {folder.rstrip('s')} first, then retry.",
            )

        return {
            "id": None,
            "name": resolved_path.stem,
            "kind": folder.rstrip("s"),
            "filename": resolved_path.name,
            "path": str(resolved_path),
            "source": "local",
            "folder": folder,
        }

    def resolve_internal_asset(
        self,
        ref: str,
        *,
        folder: str,
        allowed_kinds: set[str] | None = None,
    ) -> dict[str, Any]:
        raw = str(ref or "").strip()
        if not raw:
            raise UserFacingError("Missing model selection", hint=f"Pick a Studio {folder.rstrip('s')} first.")

        entry = self._find_entry(raw)
        if entry is None:
            for candidate in self._all_entries():
                if not isinstance(candidate, dict):
                    continue
                target = candidate.get("target") if isinstance(candidate.get("target"), dict) else {}
                candidate_folder = str(target.get("folder") or "").strip().lower()
                if str(target.get("engine") or "").strip().lower() != "internal":
                    continue
                if candidate_folder != str(folder or "").strip().lower():
                    continue
                if str(candidate.get("id") or "").strip() == raw:
                    entry = candidate
                    break

        if entry is None:
            raise UserFacingError(
                f"Unknown internal Studio asset: {raw}",
                hint=f"Install an internal {folder.rstrip('s')} asset in Models first, then retry.",
            )

        kind = str(entry.get("kind") or "").strip().lower()
        if allowed_kinds and kind not in allowed_kinds:
            expected = ", ".join(sorted(allowed_kinds))
            raise UserFacingError(
                f"{entry.get('name') or raw} is not a valid {folder.rstrip('s')} selection",
                hint=f"Pick a Studio internal asset of type: {expected}.",
            )

        target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
        engine = str(target.get("engine") or entry.get("engine") or "").strip().lower()
        if engine != "internal":
            raise UserFacingError(
                f"{entry.get('name') or raw} is not an internal Studio asset",
                hint="Pick an internal Studio asset for the internal diffusers path.",
            )

        resolved_path = self.resolve_installed_path(str(entry.get("id") or ""), materialize_remote=True)
        if resolved_path is None:
            issue = self.internal_asset_issue(str(entry.get("id") or ""))
            hint = "Install the asset in Model Manager, then retry."
            if issue == "incomplete":
                hint = "Reinstall the asset in Model Manager. The current local snapshot is missing required weight files."
            elif self._cloud_model_record(str(entry.get("id") or "")) is not None:
                hint = (
                    "This asset is stored in the cloud cache only. Internal diffusers rendering needs a local snapshot "
                    "or a remote worker that materializes the model near the GPU."
                )
            raise UserFacingError(
                f"{entry.get('name') or raw} is not installed",
                hint=hint,
            )

        return {
            "id": entry.get("id"),
            "name": entry.get("name") or raw,
            "kind": entry.get("kind") or folder.rstrip("s"),
            "path": str(resolved_path),
            "source": entry.get("source") or "local",
            "folder": folder,
            "engine": "internal",
            "family": entry.get("family"),
        }

    def resolve_loras(self, requested: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        for item in requested or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            asset = self.resolve_comfy_asset(name, folder="loras", allowed_kinds={"lora"})
            weight = float(item.get("weight", 1.0))
            clip_weight = item.get("clip_weight")
            resolved.append(
                {
                    "id": asset.get("id"),
                    "name": asset.get("name") or Path(asset["filename"]).stem,
                    "filename": asset["filename"],
                    "path": asset["path"],
                    "weight": weight,
                    "clip_weight": float(clip_weight) if clip_weight is not None else weight,
                }
            )
        return resolved

    # ---- installers ----
    def _download_stream(self, task: ModelTask, url: str, dest: Path, headers: Optional[dict[str, str]] = None) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        headers = headers or {}
        ModelTaskManager.log(task, f"Downloading…\n{url}")
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        with requests.get(url, stream=True, timeout=60 * 60, headers=headers) as r:
            if r.status_code in (401, 403):
                raise UserFacingError(
                    "Download unauthorized",
                    hint="Set an API token in Settings → Tokens (Hugging Face token for HF downloads, Civitai API key for Civitai downloads), then retry."
                )
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            got = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        ModelTaskManager.set_progress(task, max(0.0, min(0.99, got / total)))
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, dest)
        ModelTaskManager.set_progress(task, 1.0)
        ModelTaskManager.log(task, f"Saved: {dest.name}")

    def _append_task_log(self, task: ModelTask, msg: str) -> None:
        current = str(task.last_log or "").strip()
        ModelTaskManager.log(task, f"{current}\n{msg}" if current else msg)

    def _restore_from_model_cache(self, task: ModelTask, entry: dict[str, Any], dest: Path) -> bool:
        cache = getattr(self, "model_cache", None)
        if cache is None:
            return False
        cache_entry = self._cache_entry_from_cloud_record(
            entry,
            self._cloud_model_record(str(entry.get("id") or "").strip()),
        )
        try:
            if not cache.download_model(cache_entry, dest):
                return False
        except Exception as exc:
            self._append_task_log(task, f"{self._model_cache_label()} restore skipped: {exc}")
            return False
        ModelTaskManager.set_progress(task, 1.0)
        ModelTaskManager.log(task, f"Restored from {self._model_cache_label()}: {dest.name}")
        return True

    def _upload_to_model_cache(self, task: ModelTask, entry: dict[str, Any], path: Path, *, mode: str = "local_cache") -> str | None:
        cache = getattr(self, "model_cache", None)
        if cache is None:
            return None
        try:
            object_name = cache.upload_model(entry, path)
        except Exception as exc:
            self._append_task_log(task, f"{self._model_cache_label()} upload skipped: {exc}")
            return None
        self._record_cloud_model(entry, object_name, mode=mode)
        self._append_task_log(task, f"{self._model_cache_label()}: {object_name}")
        return str(object_name)

    def _restore_cloud_model(self, task: ModelTask, entry: dict[str, Any]) -> None:
        mode, dest = self._models_dest(entry)
        if mode != "file":
            raise UserFacingError(
                "Only single-file model assets can be restored from the model cache here",
                hint="Diffusers snapshots need a local install or a remote worker materialization path.",
                code="CACHE_RESTORE_UNSUPPORTED_MODEL",
            )
        if self.model_cache is None:
            raise UserFacingError(
                "No model cache is enabled",
                hint="Set EDMG_AWS_MODEL_CACHE=1 and EDMG_AWS_MODEL_CACHE_BUCKET, then restart Studio.",
                code="MODEL_CACHE_REQUIRED",
            )
        if not self._restore_from_model_cache(task, entry, dest):
            raise UserFacingError(
                f"{entry.get('name') or entry.get('id') or 'Model'} is not present in the model cache",
                hint="Install it in S3-only mode first, or install it locally from the original source.",
                code="MODEL_CACHE_MISS",
            )

    def _install_file_model(self, task: ModelTask, entry: dict[str, Any]) -> None:
        src = (entry.get("source") or "").lower()
        kind = (entry.get("kind") or "").lower()
        target = entry.get("target") or {}
        folder = (target.get("folder") if isinstance(target, dict) else None) or "checkpoints"
        fname = str(entry.get("filename") or "")
        if not fname:
            # for civitai user entries we may set filename later
            fname = "model.safetensors"

        mode, dest = self._models_dest(entry)
        storage_mode = self._model_storage_mode()
        cloud_only = storage_mode == "cloud_only"
        if cloud_only and mode != "file":
            raise UserFacingError(
                "Cloud-only install is only available for single-file model assets",
                hint="Diffusers snapshots and runtime bundles need a local filesystem path, or a remote worker that materializes them near the GPU.",
                code="CLOUD_ONLY_UNSUPPORTED_MODEL",
            )
        if mode == "file":
            if cloud_only:
                if self.model_cache is None:
                    raise UserFacingError(
                        "Cloud-only model storage requires an enabled model cache",
                        hint="Set EDMG_AWS_MODEL_CACHE=1 and EDMG_AWS_MODEL_CACHE_BUCKET, then restart Studio.",
                        code="MODEL_CACHE_REQUIRED",
                    )
                object_name = self._cache_model_exists(entry, dest)
                if object_name:
                    self._record_cloud_model(entry, object_name, mode="cloud_only")
                    ModelTaskManager.set_progress(task, 1.0)
                    ModelTaskManager.log(task, f"Already stored in {self._model_cache_label()}: {object_name}")
                    return
            elif src != "s3" and self._restore_from_model_cache(task, entry, dest):
                return

        headers: dict[str, str] = {}
        # optional HF token support (prefer SecretStore; fall back to env vars)
        hf_token = ""
        if self.secrets is not None:
            hf_token = self.secrets.get("hf_token") or ""
        if not hf_token:
            hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN") or ""
        if hf_token:
            headers["Authorization"] = f"Bearer {hf_token}"

        civitai_key = ""
        if self.secrets is not None:
            civitai_key = self.secrets.get("civitai_api_key") or ""
        if not civitai_key:
            civitai_key = os.getenv("CIVITAI_API_KEY") or ""

        if src == "hf":
            repo_id = str(entry.get("hf_repo_id") or entry.get("hf_repo") or "")
            url = str(entry.get("hf_url") or "")
            if mode == "snapshot":
                if not repo_id:
                    raise RuntimeError("Missing hf_repo_id for snapshot install")
                if snapshot_download is None:
                    raise RuntimeError("huggingface_hub is not installed (required for snapshot downloads)")
                dest.mkdir(parents=True, exist_ok=True)
                ModelTaskManager.log(task, f"Downloading HF snapshot: {repo_id}")
                snapshot_download(
                    repo_id=repo_id,
                    local_dir=str(dest),
                    local_dir_use_symlinks=False,
                    revision=str(entry.get("hf_revision") or "") or None,
                    token=(hf_token or None),
                    resume_download=True,
                )
                ModelTaskManager.set_progress(task, 1.0)
                return
            # file mode
            if not url:
                raise RuntimeError("Missing hf_url")
            target_path = self._cloud_temp_path(dest) if cloud_only else dest
            try:
                self._download_stream(task, url, target_path, headers=headers)
                if cloud_only:
                    object_name = self._upload_to_model_cache(task, entry, target_path, mode="cloud_only")
                    if not object_name:
                        raise RuntimeError("Cloud-only upload failed")
                    ModelTaskManager.set_progress(task, 1.0)
                    self._append_task_log(task, f"Cloud-only install complete; no local model file kept: {object_name}")
                else:
                    self._upload_to_model_cache(task, entry, dest)
            finally:
                if cloud_only:
                    try:
                        target_path.unlink(missing_ok=True)
                        target_path.parent.rmdir()
                    except Exception:
                        pass
            return

        if src == "s3":
            if self.model_cache is None:
                raise UserFacingError(
                    "S3 model source requires an enabled model cache",
                    hint="Set EDMG_AWS_MODEL_CACHE=1 and EDMG_AWS_MODEL_CACHE_BUCKET, then restart Studio.",
                    code="MODEL_CACHE_REQUIRED",
                )
            if mode != "file":
                raise UserFacingError(
                    "S3 model source is only available for single-file model assets",
                    hint="Use S3-hosted checkpoint, LoRA, ControlNet, VAE, or similar single-file assets.",
                    code="S3_SOURCE_UNSUPPORTED_MODEL",
                )
            object_name = self._cache_model_exists(entry, dest)
            if not object_name:
                raise UserFacingError(
                    f"{entry.get('name') or entry.get('id') or 'Model'} was not found in S3",
                    hint="Check the model entry's s3_uri/s3_key, bucket, prefix, and Studio AWS credentials.",
                    code="MODEL_CACHE_MISS",
                )
            self._record_cloud_model(entry, object_name, mode="cloud_only" if cloud_only else "remote_cache")
            if cloud_only:
                ModelTaskManager.set_progress(task, 1.0)
                ModelTaskManager.log(task, f"Stored in {self._model_cache_label()}: {object_name}")
                return
            if not self._restore_from_model_cache(task, entry, dest):
                raise UserFacingError(
                    "Could not download S3 model source",
                    hint="Check that Studio has read access to the configured S3 object.",
                    code="MODEL_CACHE_RESTORE_FAILED",
                )
            return

        if src == "civitai":
            dl = str(entry.get("civitai_download_url") or "")
            if not dl:
                raise RuntimeError("Missing civitai_download_url")
            if civitai_key:
                headers["Authorization"] = f"Bearer {civitai_key}"
            target_path = self._cloud_temp_path(dest) if cloud_only else dest
            try:
                self._download_stream(task, dl, target_path, headers=headers)
                if cloud_only:
                    object_name = self._upload_to_model_cache(task, entry, target_path, mode="cloud_only")
                    if not object_name:
                        raise RuntimeError("Cloud-only upload failed")
                    ModelTaskManager.set_progress(task, 1.0)
                    self._append_task_log(task, f"Cloud-only install complete; no local model file kept: {object_name}")
                else:
                    self._upload_to_model_cache(task, entry, dest)
            finally:
                if cloud_only:
                    try:
                        target_path.unlink(missing_ok=True)
                        target_path.parent.rmdir()
                    except Exception:
                        pass
            return

        if src == "local":
            # local models are assumed already placed. Copy if source_path provided.
            sp = str(entry.get("source_path") or "")
            if not sp:
                raise RuntimeError("Missing source_path")
            srcp = Path(sp).expanduser()
            if not srcp.exists():
                raise RuntimeError(f"File not found: {srcp}")
            if cloud_only:
                object_name = self._upload_to_model_cache(task, entry, srcp, mode="cloud_only")
                if not object_name:
                    raise RuntimeError("Cloud-only upload failed")
                ModelTaskManager.set_progress(task, 1.0)
                ModelTaskManager.log(task, f"Stored in {self._model_cache_label()} only: {object_name}")
                return
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(srcp.read_bytes())
            ModelTaskManager.log(task, f"Copied: {srcp.name}")
            ModelTaskManager.set_progress(task, 1.0)
            self._upload_to_model_cache(task, entry, dest)
            return

        raise RuntimeError(f"Unsupported source: {src}")

    def installed_path(self, model_id: str) -> Path | None:
        """Return local path for an installed model (file or directory), else None."""
        entry = self._find_entry(model_id)
        if not entry:
            return None
        return self._local_installed_path(entry)

    def resolve_installed_path(self, model_id: str, *, materialize_remote: bool = True) -> Path | None:
        """Return a local runtime path, restoring a cached remote file when requested."""
        entry = self._find_entry(model_id)
        if not entry:
            return None

        local = self._local_installed_path(entry)
        if local is not None or not materialize_remote:
            return local

        mode, dest = self._models_dest(entry)
        if mode != "file":
            return None
        return self._materialize_file_from_model_cache(entry, dest)


    def import_local(self, file_path: str, name: str | None = None, folder: str = "checkpoints") -> dict[str, Any]:
        """Register a local model file and copy it into the configured ComfyUI models folder.

        This is the BYO path for checkpoints/loras/etc.
        """
        srcp = Path(file_path).expanduser()
        if not srcp.exists() or not srcp.is_file():
            raise UserFacingError("File not found", hint="Pick a valid local model file.")
        folder = (folder or "checkpoints").strip().lower()
        safe_folder = folder if folder in ("checkpoints","loras","embeddings","vae","controlnet","upscale_models") else "checkpoints"
        cloud_only = self._model_storage_mode() == "cloud_only"
        if not cloud_only:
            dest_dir = self._comfy_models_dir(safe_folder)
            dest = dest_dir / srcp.name
            dest.write_bytes(srcp.read_bytes())

        entry = {
            "id": f"local_{uuid.uuid4().hex[:8]}",
            "name": name or srcp.stem,
            "kind": safe_folder.rstrip("s") if safe_folder.endswith("s") else safe_folder,
            "source": "local",
            "source_path": str(srcp if cloud_only else dest),
            "filename": srcp.name,
            "target": {"engine": "comfyui", "folder": safe_folder},
            "license_id": "user-provided",
            "license_url": "",
            "redistributable_in_installer": False,
            "recommended": "advanced",
            "notes": "User-provided local file. Ensure you have rights to use/distribute outputs as applicable.",
        }
        if cloud_only:
            if self.model_cache is None:
                raise UserFacingError(
                    "Cloud-only model storage requires an enabled model cache",
                    hint="Set EDMG_AWS_MODEL_CACHE=1 and EDMG_AWS_MODEL_CACHE_BUCKET, then restart Studio.",
                    code="MODEL_CACHE_REQUIRED",
                )
            object_name = self._upload_to_model_cache(ModelTask(id="import", name="Import local"), entry, srcp, mode="cloud_only")
            if not object_name:
                raise RuntimeError("Cloud-only upload failed")
        self.add_user_model(entry)
        return entry

    # ---- civitai helper ----
    def civitai_import(self, url_or_id: str) -> dict[str, Any]:
        """Import a model from Civitai by URL or numeric modelId.

        We add an entry to the user model registry but DO NOT download until user clicks Install.
        """
        model_id, version_id = _parse_civitai_url(url_or_id)
        if not model_id:
            raise UserFacingError("Couldn't parse Civitai model URL/ID", hint="Paste a Civitai model URL like https://civitai.com/models/12345 or a numeric ID.")
        api_key = ""
        if self.secrets is not None:
            api_key = self.secrets.get("civitai_api_key") or ""
        if not api_key:
            api_key = os.getenv("CIVITAI_API_KEY") or ""
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Fetch model metadata
        r = requests.get(f"https://civitai.com/api/v1/models/{model_id}", headers=headers, timeout=30)
        if r.status_code in (401, 403):
            raise UserFacingError(
                "Civitai API unauthorized",
                hint="Set CIVITAI_API_KEY in Settings → Tokens (some downloads require auth), then retry."
            )
        r.raise_for_status()
        m = r.json() or {}
        name = m.get("name") or f"Civitai Model {model_id}"
        mtype = (m.get("type") or "").lower()  # Checkpoint, LORA, TextualInversion, etc.

        # Pick a version (latest by createdAt)
        versions = m.get("modelVersions") or []
        if version_id:
            v = next((vv for vv in versions if str(vv.get("id")) == str(version_id)), None)
        else:
            v = None
            if versions and isinstance(versions, list):
                versions_sorted = sorted(
                    [vv for vv in versions if isinstance(vv, dict)],
                    key=lambda vv: vv.get("createdAt") or "",
                    reverse=True,
                )
                v = versions_sorted[0] if versions_sorted else None
        if not v:
            raise UserFacingError("No model version found", hint="Try a different model or specify a version.")

        # Determine download URL + filename from primary file
        files = v.get("files") or []
        primary = None
        for f in files:
            if isinstance(f, dict) and f.get("primary"):
                primary = f
                break
        if not primary and files:
            primary = files[0]
        if not primary:
            # Some versions include top-level downloadUrl
            dl = v.get("downloadUrl")
            if not dl:
                raise UserFacingError("No downloadable file found", hint="This model may require login/API key to download.")
            fname = f"civitai_{model_id}_{v.get('id')}.safetensors"
        else:
            dl = primary.get("downloadUrl") or v.get("downloadUrl")
            # Safety: avoid pickle tensors by default.
            meta = primary.get("metadata") or {}
            fmt = str(meta.get("format") or "").lower()
            if fmt and "safetensor" not in fmt:
                raise UserFacingError(
                    "Unsafe model format blocked",
                    hint="This Civitai file is not a SafeTensor. Choose a SafeTensor variant or export/download manually."
                )

            fname = primary.get("name") or f"civitai_{model_id}_{v.get('id')}.safetensors"

        # Map to comfy folder
        folder = "checkpoints"
        if "lora" in mtype:
            folder = "loras"
        elif "textualinversion" in mtype or "embedding" in mtype:
            folder = "embeddings"
        elif "vae" in mtype:
            folder = "vae"
        elif "controlnet" in mtype:
            folder = "controlnet"

        entry = {
            "id": f"civitai_{model_id}_{v.get('id')}",
            "name": f"{name} (Civitai)",
            "kind": "checkpoint" if folder == "checkpoints" else folder.rstrip("s"),
            "source": "civitai",
            "civitai_model_id": model_id,
            "civitai_version_id": v.get("id"),
            "civitai_page_url": f"https://civitai.com/models/{model_id}",
            "civitai_download_url": dl,
            "filename": fname,
            "target": {"engine": "comfyui", "folder": folder},
            # Civitai license varies per model; we surface the page and mark as unknown unless the API returns license data.
            "license_id": str(m.get("license") or m.get("licenseId") or "unknown"),
            "license_url": f"https://civitai.com/models/{model_id}",
            "redistributable_in_installer": False,
            "recommended": "advanced",
            "notes": "Community model from Civitai. Review license/terms on the model page before using commercially.",
        }
        self.add_user_model(entry)
        return entry


def _parse_civitai_url(s: str) -> tuple[str | None, str | None]:
    s = (s or "").strip()
    if not s:
        return None, None
    if s.isdigit():
        return s, None

    # URLs like:
    #  - https://civitai.com/models/12345
    #  - https://civitai.com/models/12345/name?modelVersionId=67890
    m = re.search(r"civitai\.com/(?:en/)?models/(\d+)", s)
    model_id = m.group(1) if m else None
    mv = re.search(r"modelVersionId=(\d+)", s)
    version_id = mv.group(1) if mv else None
    return model_id, version_id

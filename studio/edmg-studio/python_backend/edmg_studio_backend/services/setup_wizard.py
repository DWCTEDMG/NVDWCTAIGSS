from __future__ import annotations

import importlib.util
import re
import os
import platform
import subprocess
import shutil
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import requests

try:
    import py7zr  # type: ignore
except Exception:  # pragma: no cover
    py7zr = None

BACKEND_SETUPTOOLS_CONSTRAINT = "setuptools==70.0.0"


@dataclass
class SetupTask:
    id: str
    name: str
    status: str = "queued"  # queued|running|done|failed|canceled
    progress: Optional[float] = None
    last_log: str = ""
    error: Optional[str] = None
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    cancel_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "progress": self.progress,
            "last_log": self.last_log,
            "error": self.error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "cancel_requested": self.cancel_requested,
        }


class SetupTaskCanceled(Exception):
    """Raised when a setup task is canceled and should stop promptly."""


class SetupTaskManager:
    """Very small in-memory task runner for installer operations."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tasks: dict[str, SetupTask] = {}

    def list(self) -> list[SetupTask]:
        with self._lock:
            # newest first
            return sorted(self._tasks.values(), key=lambda t: (t.started_at or 0), reverse=True)

    def start(self, name: str, fn, *args, **kwargs) -> SetupTask:
        task = SetupTask(id=str(uuid.uuid4())[:8], name=name, status="queued")
        with self._lock:
            self._tasks[task.id] = task

        def runner():
            if task.cancel_requested:
                task.status = "canceled"
                task.started_at = time.time()
                task.ended_at = task.started_at
                if not task.last_log:
                    task.last_log = "Canceled before start."
                return
            task.status = "running"
            task.started_at = time.time()
            try:
                self.check_canceled(task)
                fn(task, *args, **kwargs)
                self.check_canceled(task)
                task.status = "done"
            except SetupTaskCanceled as e:
                task.status = "canceled"
                if str(e):
                    task.last_log = str(e)
            except Exception as e:
                if task.cancel_requested:
                    task.status = "canceled"
                    task.last_log = str(e) or "Canceled."
                else:
                    task.status = "failed"
                    task.error = str(e)
                    task.last_log = (task.last_log + "\n" if task.last_log else "") + f"ERROR: {e}"
            finally:
                task.ended_at = time.time()

        threading.Thread(target=runner, daemon=True).start()
        return task

    def cancel(self, task_id: str) -> SetupTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if task.status in ("done", "failed", "canceled"):
                return task
            task.cancel_requested = True
            if task.status == "queued":
                task.status = "canceled"
                task.last_log = task.last_log or "Canceled before start."
                now = time.time()
                task.started_at = task.started_at or now
                task.ended_at = now
            elif task.status == "running" and "Cancel requested" not in task.last_log:
                task.last_log = "Cancel requested — stopping after current step."
            return task

    @staticmethod
    def log(task: SetupTask, msg: str) -> None:
        task.last_log = msg

    @staticmethod
    def set_progress(task: SetupTask, v: Optional[float]) -> None:
        task.progress = v

    @staticmethod
    def check_canceled(task: SetupTask, message: str = "Setup task canceled.") -> None:
        if getattr(task, "cancel_requested", False):
            raise SetupTaskCanceled(message)


def _run_subprocess(
    task: SetupTask,
    args: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    creationflags: int = 0,
) -> None:
    proc = subprocess.Popen(args, cwd=cwd, env=env, creationflags=creationflags)
    try:
        while True:
            SetupTaskManager.check_canceled(task)
            code = proc.poll()
            if code is not None:
                if code != 0:
                    raise subprocess.CalledProcessError(code, args)
                return
            time.sleep(0.25)
    except SetupTaskCanceled:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        raise


def _extract_zip_with_cancel(task: SetupTask, archive_path: Path, dest_dir: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        total = max(1, len(members))
        for index, member in enumerate(members, start=1):
            SetupTaskManager.check_canceled(task)
            archive.extract(member, dest_dir)
            SetupTaskManager.set_progress(task, max(task.progress or 0.0, min(0.98, 0.8 + (index / total) * 0.18)))


# ------------------------------ Runtime helpers ------------------------------

def _env_truthy(name: str) -> bool:
    value = str(os.environ.get(name, "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


# ------------------------------ ComfyUI Portable ------------------------------

COMFY_REPO = "comfyanonymous/ComfyUI"


def _github_latest_assets(repo: str) -> list[dict[str, Any]]:
    r = requests.get(f"https://api.github.com/repos/{repo}/releases/latest", timeout=20)
    r.raise_for_status()
    data = r.json()
    return data.get("assets") or []


def _pick_portable_asset(assets: list[dict[str, Any]], flavor: str) -> dict[str, Any]:
    flavor = (flavor or "nvidia").lower()
    if flavor == "cuda":
        flavor = "nvidia"
    if flavor != "nvidia":
        raise RuntimeError("Only the NVIDIA ComfyUI portable flavor is supported by this profile.")
    # Heuristics across historical naming.
    candidates: list[dict[str, Any]] = []
    for a in assets:
        name = (a.get("name") or "").lower()
        if "portable" not in name:
            continue
        if not name.endswith((".7z", ".zip")):
            continue
        candidates.append(a)

    def score(a: dict[str, Any]) -> int:
        name = (a.get("name") or "").lower()
        s = 0
        if "nvidia" in name or "cu" in name:
            s += 5
        if "cpu" in name:
            s -= 4
        # prefer smaller artifacts when tie
        try:
            size = int(a.get("size") or 0)
            s += max(0, 3 - int(size / (1024 * 1024 * 1024)))
        except Exception:
            pass
        return s

    if not candidates:
        raise RuntimeError("No portable assets found in latest ComfyUI release.")

    candidates.sort(key=score, reverse=True)
    return candidates[0]


def _legacy_external_root(data_dir: Path | None) -> Path | None:
    if data_dir is None:
        return None
    return (data_dir / "third_party").resolve()


def comfy_portable_root(external_dir: Path, data_dir: Path | None = None) -> Path:
    preferred = (external_dir / "ComfyUI_windows_portable").resolve()
    if (preferred / "ComfyUI").exists() and (preferred / "python_embeded").exists():
        return preferred

    legacy_root = _legacy_external_root(data_dir)
    if legacy_root is not None:
        legacy = (legacy_root / "ComfyUI_windows_portable").resolve()
        if (legacy / "ComfyUI").exists() and (legacy / "python_embeded").exists():
            return legacy

    return preferred


def comfy_portable_installed(external_dir: Path, data_dir: Path | None = None) -> bool:
    root = comfy_portable_root(external_dir, data_dir)
    return (root / "ComfyUI").exists() and (root / "python_embeded").exists()



def _find_7z_exe(external_dir: Path, data_dir: Path | None = None) -> str:
    """Locate a 7-Zip CLI that supports BCJ2 (required for some .7z archives).

    Resolution order:
    1) EDMG_7Z_PATH env var
    2) bundled inside the Studio external-tools root (external/bin/7z.exe)
    3) legacy bundled path alongside the Studio data dir (data/third_party/bin/7z.exe)
    4) common system install paths
    5) PATH
    """
    env = os.environ.get("EDMG_7Z_PATH")
    if env and Path(env).exists():
        return env

    ignore_system = _env_truthy("EDMG_SETUP_IGNORE_SYSTEM_7Z")
    bundled_names = ("7z.exe", "7za.exe", "7zr.exe", "7zz.exe") if platform.system() == "Windows" else ("7zz", "7za", "7zr")
    for bundled_name in bundled_names:
        bundled = (external_dir / "bin" / bundled_name).resolve()
        if bundled.exists():
            return str(bundled)

    legacy_root = _legacy_external_root(data_dir)
    if legacy_root is not None:
        for bundled_name in bundled_names:
            legacy_bundled = (legacy_root / "bin" / bundled_name).resolve()
            if legacy_bundled.exists():
                return str(legacy_bundled)

    candidates = []
    if platform.system() == "Windows" and not ignore_system:
        candidates += [
            Path(r"C:\Program Files\7-Zip\7z.exe"),
            Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
        ]
    for c in candidates:
        if c.exists():
            return str(c)

    if not ignore_system:
        which = shutil.which("7z") or shutil.which("7z.exe") or shutil.which("7zz") or shutil.which("7zz.exe")
        if which:
            return which

    raise RuntimeError("7-Zip CLI not found. Install 7-Zip or bundle 7z.exe and/or set EDMG_7Z_PATH.")


def _extract_7z_cli(task: SetupTask, external_dir: Path, archive: Path, out_parent: Path, data_dir: Path | None = None) -> None:
    seven = _find_7z_exe(external_dir, data_dir)
    SetupTaskManager.log(task, f"Using 7-Zip: {seven}")
    out_parent.mkdir(parents=True, exist_ok=True)
    # `x` preserves folders; `-y` assumes Yes on all queries.
    cmd = [seven, "x", str(archive), f"-o{str(out_parent)}", "-y"]
    SetupTaskManager.log(task, "Extract command: " + " ".join(cmd))
    _run_subprocess(task, cmd)


def _yaml_quote(value: str) -> str:
    return "'" + str(value).replace("\\", "/").replace("'", "''") + "'"


def ensure_comfyui_model_paths(external_dir: Path, models_dir: Path, data_dir: Path | None = None) -> Path | None:
    root = comfy_portable_root(external_dir, data_dir)
    if not (root / "ComfyUI").exists():
        return None

    yaml_path = root / "ComfyUI" / "extra_model_paths.yaml"
    base_path = _yaml_quote(str(models_dir.resolve()))
    content = (
        "edmg_studio:\n"
        f"  base_path: {base_path}\n"
        "  checkpoints: checkpoints\n"
        "  loras: loras\n"
        "  embeddings: embeddings\n"
        "  vae: vae\n"
        "  controlnet: controlnet\n"
        "  upscale_models: upscale_models\n"
        "  clip: clip\n"
        "  clip_vision: clip_vision\n"
    )
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(content, encoding="utf-8")
    return yaml_path


def download_and_extract_portable(
    task: SetupTask,
    external_dir: Path,
    flavor: str,
    data_dir: Path | None = None,
    models_dir: Path | None = None,
) -> Path:

    assets = _github_latest_assets(COMFY_REPO)
    asset = _pick_portable_asset(assets, flavor)

    url = asset.get("browser_download_url")
    name = asset.get("name")
    if not url or not name:
        raise RuntimeError("Portable download URL not found.")

    dest_root = (external_dir / "ComfyUI_windows_portable").resolve()
    tmp_dir = (external_dir / "_downloads").resolve()
    tmp_dir.mkdir(parents=True, exist_ok=True)
    archive = tmp_dir / name

    SetupTaskManager.log(task, f"Downloading ComfyUI Portable ({flavor})…")
    backup: Path | None = None
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            got = 0
            with open(archive, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    SetupTaskManager.check_canceled(task, "ComfyUI Portable install canceled.")
                    if not chunk:
                        continue
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        SetupTaskManager.set_progress(task, min(0.7, (got / total) * 0.7))
                        SetupTaskManager.log(task, f"Downloading… {int((got/total)*100)}%")

        SetupTaskManager.check_canceled(task, "ComfyUI Portable install canceled.")
        SetupTaskManager.log(task, "Extracting ComfyUI Portable…")
        SetupTaskManager.set_progress(task, 0.75)

        # Clear existing
        if dest_root.exists():
            # keep user models if they already have
            # (best-effort; don't delete if there's a chance the user put models there)
            backup = dest_root.parent / f"ComfyUI_windows_portable_backup_{int(time.time())}"
            dest_root.rename(backup)

        dest_root.parent.mkdir(parents=True, exist_ok=True)

        if str(archive).lower().endswith(".7z"):
            _extract_7z_cli(task, external_dir, archive, dest_root.parent, data_dir)
        else:
            _extract_zip_with_cancel(task, archive, dest_root.parent)
    except SetupTaskCanceled:
        try:
            archive.unlink(missing_ok=True)
        except Exception:
            pass
        if backup is not None and backup.exists() and not dest_root.exists():
            try:
                backup.rename(dest_root)
            except Exception:
                pass
        raise

    # Some archives include a top-level folder; normalize to expected name.
    # Find a folder that contains python_embeded + ComfyUI.
    parent = dest_root.parent
    found = None
    for p in parent.iterdir():
        if not p.is_dir():
            continue
        if (p / "python_embeded").exists() and (p / "ComfyUI").exists():
            found = p
            break
    if found and found.name != dest_root.name:
        # Move/rename into place
        if dest_root.exists():
            # should not, but guard
            pass
        found.rename(dest_root)

    if not comfy_portable_installed(external_dir, data_dir):
        raise RuntimeError("ComfyUI Portable extraction completed, but expected folders were not found.")

    if models_dir is not None:
        yaml_path = ensure_comfyui_model_paths(external_dir, models_dir, data_dir)
        if yaml_path is not None:
            SetupTaskManager.log(task, f"Configured external model paths: {yaml_path}")

    SetupTaskManager.set_progress(task, 1.0)
    SetupTaskManager.log(task, "ComfyUI Portable installed.")
    return dest_root


class ComfyPortableProcess:
    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self.root: Optional[Path] = None

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(
        self,
        task: SetupTask,
        external_dir: Path,
        flavor: str,
        host: str = "127.0.0.1",
        port: int = 8188,
        data_dir: Path | None = None,
        models_dir: Path | None = None,
    ) -> None:
        if platform.system().lower() != "windows":
            raise RuntimeError("ComfyUI Portable auto-start is currently implemented for Windows.")

        if not comfy_portable_installed(external_dir, data_dir):
            raise RuntimeError("ComfyUI Portable is not installed yet. Click Install first.")

        root = comfy_portable_root(external_dir, data_dir)
        py = root / "python_embeded" / "python.exe"
        main = root / "ComfyUI" / "main.py"
        if not py.exists() or not main.exists():
            raise RuntimeError("ComfyUI Portable install looks incomplete.")

        if models_dir is not None:
            ensure_comfyui_model_paths(external_dir, models_dir, data_dir)

        if self.running():
            SetupTaskManager.log(task, "ComfyUI is already running.")
            return

        args = [
            str(py),
            "-s",
            str(main),
            "--listen",
            host,
            "--port",
            str(port),
            "--windows-standalone-build",
        ]
        flavor_l = (flavor or "nvidia").lower()
        if flavor_l == "cuda":
            flavor_l = "nvidia"
        if flavor_l != "nvidia":
            raise RuntimeError("Only NVIDIA ComfyUI startup is supported by this profile.")

        # Hide console window.
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        SetupTaskManager.log(task, f"Starting ComfyUI Portable ({flavor})…")
        self.proc = subprocess.Popen(args, cwd=str(root), creationflags=creationflags)
        self.root = root

        # Wait a bit for port to open
        try:
            for _ in range(80):
                SetupTaskManager.check_canceled(task, "ComfyUI startup canceled.")
                try:
                    r = requests.get(f"http://{host}:{port}/system_stats", timeout=1.0)
                    if r.status_code == 200:
                        SetupTaskManager.log(task, "ComfyUI is running.")
                        return
                except Exception:
                    pass
                time.sleep(0.25)
        except SetupTaskCanceled:
            self.stop()
            raise

        SetupTaskManager.log(task, "ComfyUI started (still warming up). If it doesn't come online, try again or install GPU-compatible build.")

    def stop(self) -> None:
        if self.proc and self.running():
            try:
                self.proc.terminate()
            except Exception:
                pass


def check_ffmpeg(ffmpeg_path: str) -> dict[str, Any]:
    hint = "Packaged EDMG NVIDIA Workshop should include bundled FFmpeg. If this is a dev checkout, install FFmpeg and add it to PATH, or set EDMG_FFMPEG_PATH to the ffmpeg executable."
    try:
        r = subprocess.run([ffmpeg_path, "-version"], capture_output=True, text=True, timeout=3)
        ok = r.returncode == 0
        return {
            "ok": ok,
            "path": ffmpeg_path,
            "version": (r.stdout.splitlines()[0] if r.stdout else None),
            "hint": None if ok else hint,
        }
    except Exception as e:
        return {
            "ok": False,
            "path": ffmpeg_path,
            "error": str(e),
            "hint": hint,
        }


BACKEND_BUNDLE_MODULES: dict[str, dict[str, str]] = {
    "audio": {
        "librosa": "librosa",
        "soundfile": "soundfile",
    },
    "internal": {
        "diffusers": "diffusers",
        "transformers": "transformers",
        "accelerate": "accelerate",
        "safetensors": "safetensors",
        "torch": "torch",
    },
}

BACKEND_BUNDLE_ALIASES: dict[str, tuple[str, ...]] = {
    "full": ("audio", "internal"),
    "studio_bundle": ("audio", "internal"),
}


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _bundle_module_map(bundle: str) -> dict[str, str]:
    keys = BACKEND_BUNDLE_ALIASES.get(bundle, (bundle,))
    modules: dict[str, str] = {}
    for key in keys:
        modules.update(BACKEND_BUNDLE_MODULES.get(key, {}))
    return modules


def check_backend_bundle(bundle: str = "studio_bundle") -> dict[str, Any]:
    modules = _bundle_module_map(bundle)
    if getattr(sys, "frozen", False):
        return {
            "ok": True,
            "bundle": bundle,
            "python": sys.executable,
            "backend_root": str(_backend_root()),
            "missing": [],
            "checked_modules": sorted(modules),
            "frozen": True,
            "hint": None,
        }
    missing = sorted(
        package_name
        for package_name, module_name in modules.items()
        if importlib.util.find_spec(module_name) is None
    )
    return {
        "ok": not missing,
        "bundle": bundle,
        "python": sys.executable,
        "backend_root": str(_backend_root()),
        "missing": missing,
        "hint": None if not missing else (
            f"Install backend runtime deps with `pip install -e .[{bundle}]` from python_backend, "
            "or run Setup -> Full Setup."
        ),
    }


def install_backend_bundle(task: SetupTask, bundle: str = "studio_bundle") -> None:
    root = _backend_root()
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        raise RuntimeError(f"Backend pyproject.toml not found at {pyproject}")

    SetupTaskManager.log(task, f"Installing backend runtime bundle `{bundle}`...")
    SetupTaskManager.set_progress(task, 0.1)

    _run_subprocess(
        task,
        [sys.executable, "-m", "pip", "install", "--upgrade", "pip", BACKEND_SETUPTOOLS_CONSTRAINT, "wheel"],
        cwd=str(root),
    )

    SetupTaskManager.set_progress(task, 0.35)
    SetupTaskManager.log(task, f"Running pip install -e .[{bundle}]")

    _run_subprocess(
        task,
        [sys.executable, "-m", "pip", "install", "-e", f".[{bundle}]"],
        cwd=str(root),
    )

    SetupTaskManager.set_progress(task, 0.9)
    status = check_backend_bundle(bundle)
    if not status["ok"]:
        missing = ", ".join(status["missing"]) or "unknown modules"
        raise RuntimeError(
            f"Backend runtime bundle `{bundle}` installed, but imports are still missing: {missing}"
        )

    SetupTaskManager.set_progress(task, 1.0)
    SetupTaskManager.log(task, f"Backend runtime bundle `{bundle}` is ready.")


def _resolve_7zip_cli_download(page_url: str, html: str) -> tuple[str, str]:
    """Return the portable 7-Zip CLI URL and filename from the upstream download page."""

    patterns = (
        r'href="([^"]*7zr\.exe[^"]*)"',
        r"href='([^']*7zr\.exe[^']*)'",
    )
    match = None
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            break
    if not match:
        raise RuntimeError("Could not locate portable 7-Zip CLI link on 7-zip.org download page.")

    href = match.group(1).strip()
    url = urljoin(page_url, href)
    fname = Path(urlparse(url).path).name or "7zr.exe"
    return url, fname


def _7zip_cli_download_candidates(page_url: str, html: str | None = None) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []

    explicit = str(os.environ.get("EDMG_7ZIP_URL") or "").strip()
    if explicit:
        candidates.append((explicit, Path(urlparse(explicit).path).name or "7zr.exe"))

    candidates.append(("https://github.com/ip7z/7zip/releases/latest/download/7zr.exe", "7zr.exe"))

    if html:
        try:
            candidates.append(_resolve_7zip_cli_download(page_url, html))
        except Exception:
            pass

    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url, fname in candidates:
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append((url, fname))
    return deduped


def download_and_install_7zip(task: SetupTask, external_dir: Path, data_dir: Path | None = None) -> None:
    """Download a portable 7-Zip CLI into the Studio external-tools root."""
    if platform.system() != "Windows":
        SetupTaskManager.log(task, "7-Zip install is Windows-only; skipping.")
        return

    try:
        existing = _find_7z_exe(external_dir, data_dir)
        SetupTaskManager.log(task, f"7-Zip already available at: {existing}")
        return
    except Exception:
        pass

    dest_dir = (external_dir / "bin").resolve()
    download_dir = (external_dir / "_downloads").resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    download_dir.mkdir(parents=True, exist_ok=True)

    page_url = "https://7-zip.org/download.html"
    html: str | None = None
    try:
        SetupTaskManager.check_canceled(task, "7-Zip download canceled.")
        SetupTaskManager.log(task, f"Fetching 7-Zip download page: {page_url}")
        r = requests.get(page_url, timeout=30)
        r.raise_for_status()
        html = r.text
    except Exception as exc:
        SetupTaskManager.log(task, f"7-Zip page fetch unavailable; falling back to direct release URL. ({exc})")

    last_error: Exception | None = None
    portable_exe = dest_dir / "7zr.exe"
    for url, fname in _7zip_cli_download_candidates(page_url, html):
        archive = download_dir / fname
        portable_exe = dest_dir / fname
        try:
            SetupTaskManager.log(task, f"Downloading portable 7-Zip CLI: {url}")
            with requests.get(url, stream=True, timeout=60) as rr:
                rr.raise_for_status()
                total = int(rr.headers.get("content-length") or "0")
                got = 0
                with open(archive, "wb") as f:
                    for chunk in rr.iter_content(chunk_size=1024 * 1024):
                        SetupTaskManager.check_canceled(task, "7-Zip download canceled.")
                        if not chunk:
                            continue
                        f.write(chunk)
                        got += len(chunk)
                        if total > 0:
                            task.progress = min(0.95, got / total)

            SetupTaskManager.check_canceled(task, "7-Zip download canceled.")
            shutil.copy2(archive, portable_exe)
            break
        except SetupTaskCanceled:
            try:
                archive.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        except Exception as exc:
            last_error = exc
            SetupTaskManager.log(task, f"Portable 7-Zip download failed from {url}: {exc}")
    else:
        if last_error is not None:
            raise RuntimeError(str(last_error))
        raise RuntimeError("Could not resolve a portable 7-Zip CLI download URL.")

    task.progress = 0.97
    SetupTaskManager.log(task, f"Validating portable 7-Zip CLI: {portable_exe}")
    probe = subprocess.run([str(portable_exe), "i"], capture_output=True, text=True, timeout=10)
    if probe.returncode != 0:
        raise RuntimeError(f"Portable 7-Zip validation failed: {probe.stderr or probe.stdout or 'unknown error'}")

    seven = _find_7z_exe(external_dir, data_dir)
    task.progress = 1.0
    SetupTaskManager.log(task, f"Portable 7-Zip CLI is ready: {seven}")

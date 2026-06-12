import os
import sys
import json
import re
import threading
import subprocess
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import urllib.request
import time

STUDIO_DIR = Path(__file__).resolve().parents[1]
ROOT = STUDIO_DIR.parents[1]
BACKEND_DIR = STUDIO_DIR / "python_backend"
BACKEND_VENV = BACKEND_DIR / "venv"
BUNDLED_FFMPEG = STUDIO_DIR / "electron-resources" / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
PACKAGE_JSON_PATH = STUDIO_DIR / "package.json"
DEFAULT_PACKAGE_MANAGER = "pnpm"
DEFAULT_BACKEND_PORT = 7863
DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_UI_PORT = 5173
DEFAULT_NVIDIA_NIM_URL = "http://127.0.0.1:8001"
DEFAULT_NVIDIA_NIM_OPENAI_BASE_URL = f"{DEFAULT_NVIDIA_NIM_URL}/v1"
DEFAULT_NVIDIA_NIM_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
DEFAULT_NVIDIA_RIVA_URL = "127.0.0.1:50051"
DEFAULT_NVIDIA_TRITON_URL = "http://127.0.0.1:8002"
DEFAULT_NVIDIA_OMNIVERSE_URL = "http://127.0.0.1:8011"
NVIDIA_RUNTIME_SCRIPT = ROOT / "deployment" / "nvidia" / "start_nvidia_runtime_local.ps1"
LAUNCHER_ENV_PATH = STUDIO_DIR / "launcher_env.json"
BOOTSTRAP_CONFIG_BASENAME = "bootstrap.json"
SUPPORTED_PYTHON_MIN = (3, 10)
SUPPORTED_PYTHON_MAX_EXCLUSIVE = (3, 14)


def _resolve_ffmpeg_path() -> str:
    explicit = os.environ.get("EDMG_FFMPEG_PATH", "").strip()
    if explicit:
        if not os.path.isabs(explicit) or Path(explicit).exists():
            return explicit

    if BUNDLED_FFMPEG.exists():
        return str(BUNDLED_FFMPEG)

    if explicit:
        return explicit

    return "ffmpeg"


def _format_python_requirement() -> str:
    return (
        f"Python >= {SUPPORTED_PYTHON_MIN[0]}.{SUPPORTED_PYTHON_MIN[1]} "
        f"and < {SUPPORTED_PYTHON_MAX_EXCLUSIVE[0]}.{SUPPORTED_PYTHON_MAX_EXCLUSIVE[1]}"
    )


def _python_version_for_command(cmd: list[str]) -> tuple[int, int, int] | None:
    try:
        proc = subprocess.run(
            [*cmd, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    raw = str(proc.stdout or "").strip()
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", raw)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _is_supported_python_version(version: tuple[int, int, int] | None) -> bool:
    if version is None:
        return False
    return SUPPORTED_PYTHON_MIN <= version[:2] < SUPPORTED_PYTHON_MAX_EXCLUSIVE


def _format_python_version(version: tuple[int, int, int] | None) -> str:
    if version is None:
        return "unknown"
    return ".".join(str(part) for part in version)


def _describe_python_command(cmd: list[str]) -> str:
    return " ".join(cmd)


def _supported_python_candidates() -> list[list[str]]:
    candidates: list[list[str]] = []
    explicit = os.environ.get("EDMG_STUDIO_PYTHON", "").strip()
    if explicit:
        candidates.append([explicit])
    candidates.append([sys.executable])
    candidates.append(["python"])
    if sys.platform.startswith("win"):
        for minor in range(SUPPORTED_PYTHON_MAX_EXCLUSIVE[1] - 1, SUPPORTED_PYTHON_MIN[1] - 1, -1):
            candidates.append(["py", f"-3.{minor}"])
        candidates.append(["py", "-3"])

    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for cmd in candidates:
        key = tuple(cmd)
        if key in seen:
            continue
        seen.add(key)
        unique.append(cmd)
    return unique


def _resolve_supported_python_command() -> tuple[list[str], tuple[int, int, int]]:
    unsupported: list[str] = []
    for cmd in _supported_python_candidates():
        version = _python_version_for_command(cmd)
        if version is None:
            continue
        if _is_supported_python_version(version):
            return cmd, version
        unsupported.append(f"{_describe_python_command(cmd)} ({_format_python_version(version)})")
    detail = ""
    if unsupported:
        detail = " Unsupported candidates: " + ", ".join(unsupported)
    raise RuntimeError(f"Could not find a supported Python interpreter. Need {_format_python_requirement()}.{detail}")


def _backend_venv_status() -> tuple[bool, str]:
    py = _venv_python(BACKEND_VENV)
    if not py.exists():
        return False, "missing python executable"
    version = _python_version_for_command([str(py)])
    if version is None:
        return False, "python executable is not runnable"
    if not _is_supported_python_version(version):
        return False, f"unsupported Python {_format_python_version(version)}"
    return True, _format_python_version(version)


def _reset_backend_venv(log_cb) -> None:
    if BACKEND_VENV.exists():
        shutil.rmtree(BACKEND_VENV, ignore_errors=True)
    py = _venv_python(BACKEND_VENV)
    if py.exists():
        try:
            py.unlink()
        except Exception:
            pass

def _read_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default

def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _studio_package_manager_name() -> str:
    package_json = _read_json(PACKAGE_JSON_PATH, default={})
    if isinstance(package_json, dict):
        spec = str(package_json.get("packageManager", "")).strip()
        if spec:
            return spec.partition("@")[0] or DEFAULT_PACKAGE_MANAGER
    return DEFAULT_PACKAGE_MANAGER


def _resolve_package_manager_command(name: str) -> tuple[list[str], str] | None:
    direct = shutil.which(name)
    if direct:
        return [direct], direct
    if name == "pnpm":
        corepack = shutil.which("corepack")
        if corepack:
            return [corepack, "pnpm"], f"{corepack} pnpm"
    return None

def _user_appdata_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))

def _bootstrap_config_path() -> Path:
    return _user_appdata_dir() / "EDMG NVIDIA Workshop" / BOOTSTRAP_CONFIG_BASENAME

def _saved_path_if_usable(raw_value: str | None) -> Path | None:
    value = str(raw_value or "").strip()
    if not value:
        return None
    candidate = Path(value).expanduser()
    try:
        resolved = candidate.resolve()
    except Exception:
        try:
            resolved = candidate.absolute()
        except Exception:
            return None

    if os.name == "nt":
        anchor = resolved.anchor
        if anchor:
            try:
                if not Path(anchor).exists():
                    return None
            except Exception:
                return None

    return resolved

def _derive_studio_home(data_dir: Path) -> Path:
    return data_dir.expanduser().resolve().parent

def _default_storage_env(studio_home: Path, data_dir: Path | None = None) -> dict[str, str]:
    home = studio_home.expanduser().resolve()
    data = data_dir.expanduser().resolve() if data_dir is not None else (home / "data").resolve()
    models = (home / "models").resolve()
    return {
        "EDMG_STUDIO_HOME": str(home),
        "EDMG_STUDIO_DATA_DIR": str(data),
        "EDMG_STUDIO_MODELS_DIR": str(models),
        "EDMG_STUDIO_CACHE_DIR": str((home / "cache").resolve()),
        "EDMG_STUDIO_LOGS_DIR": str((home / "logs").resolve()),
        "EDMG_STUDIO_EXTERNAL_DIR": str((home / "external").resolve()),
    }

def _default_nvidia_env() -> dict[str, str]:
    return {
        "EDMG_NVIDIA_MODE": "1",
        "EDMG_NVIDIA_PROFILE": "omniverse",
        "EDMG_NVIDIA_NIM_URL": DEFAULT_NVIDIA_NIM_URL,
        "EDMG_AI_MODE": "local",
        "EDMG_AI_PROVIDER": "openai_compat",
        "EDMG_AI_OPENAI_COMPAT_BASE_URL": DEFAULT_NVIDIA_NIM_OPENAI_BASE_URL,
        "EDMG_AI_OPENAI_COMPAT_MODEL": DEFAULT_NVIDIA_NIM_MODEL,
        "EDMG_NVIDIA_RIVA_URL": DEFAULT_NVIDIA_RIVA_URL,
        "EDMG_NVIDIA_RIVA_ASR": "1",
        "EDMG_NVIDIA_RIVA_LANGUAGE_CODE": "en-US",
        "EDMG_NVIDIA_TRITON_URL": DEFAULT_NVIDIA_TRITON_URL,
        "EDMG_NVIDIA_OMNIVERSE_URL": DEFAULT_NVIDIA_OMNIVERSE_URL,
    }

def _persist_studio_location(*, studio_home: Path | None = None, data_dir: Path | None = None) -> tuple[Path, Path]:
    if studio_home is None and data_dir is None:
        raise ValueError("studio_home or data_dir is required")

    if studio_home is not None:
        studio_home = studio_home.expanduser().resolve()
    if data_dir is not None:
        data_dir = data_dir.expanduser().resolve()

    if studio_home is None:
        assert data_dir is not None
        studio_home = _derive_studio_home(data_dir)
    if data_dir is None:
        data_dir = (studio_home / "data").resolve()

    storage_env = _default_storage_env(studio_home, data_dir)
    for key, value in storage_env.items():
        os.environ[key] = value

    cfg = _read_json(LAUNCHER_ENV_PATH, default={})
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.update(storage_env)
    _write_json(LAUNCHER_ENV_PATH, cfg)

    bootstrap = _read_json(_bootstrap_config_path(), default={})
    if not isinstance(bootstrap, dict):
        bootstrap = {}
    bootstrap["studioHome"] = str(studio_home)
    bootstrap["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_json(_bootstrap_config_path(), bootstrap)

    return studio_home, data_dir

def _studio_path_set(studio_home: Path, data_dir: Path | None = None) -> dict[str, str]:
    home = studio_home.expanduser().resolve()
    data = data_dir.expanduser().resolve() if data_dir is not None else (home / "data").resolve()
    electron = (home / "electron").resolve()
    return {
        "studioHome": str(home),
        "dataDir": str(data),
        "modelsDir": str((home / "models").resolve()),
        "cacheRoot": str((home / "cache").resolve()),
        "externalDir": str((home / "external").resolve()),
        "electronUserData": str(electron),
        "sessionData": str((electron / "session").resolve()),
        "logsDir": str((home / "logs").resolve()),
    }

def _queue_studio_migration(source_home: Path, source_data_dir: Path, target_home: Path) -> bool:
    source = _studio_path_set(source_home, source_data_dir)
    target = _studio_path_set(target_home)
    if source["studioHome"] == target["studioHome"] and source["dataDir"] == target["dataDir"]:
        bootstrap = _read_json(_bootstrap_config_path(), default={})
        if isinstance(bootstrap, dict) and "pendingMigration" in bootstrap:
            del bootstrap["pendingMigration"]
            _write_json(_bootstrap_config_path(), bootstrap)
        return False

    bootstrap = _read_json(_bootstrap_config_path(), default={})
    if not isinstance(bootstrap, dict):
        bootstrap = {}
    bootstrap["pendingMigration"] = {
        "requestedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source,
        "target": target,
    }
    _write_json(_bootstrap_config_path(), bootstrap)
    return True

def _default_data_dir() -> Path:
    # Keep runtime data OUTSIDE python_backend/ to avoid packaging issues.
    env_home = os.environ.get("EDMG_STUDIO_HOME", "").strip()
    if env_home:
        return (Path(env_home).expanduser().resolve() / "data")

    cur = os.environ.get("EDMG_STUDIO_DATA_DIR", "").strip()
    if cur:
        return Path(cur).expanduser().resolve()

    bootstrap = _read_json(_bootstrap_config_path(), default={})
    if isinstance(bootstrap, dict):
        saved_home = _saved_path_if_usable(bootstrap.get("studioHome"))
        if saved_home is not None:
            return (saved_home / "data")

    cfg = _read_json(LAUNCHER_ENV_PATH, default={})
    if isinstance(cfg, dict):
        saved_home = _saved_path_if_usable(cfg.get("EDMG_STUDIO_HOME"))
        if saved_home is not None:
            return (saved_home / "data")
        saved_data = _saved_path_if_usable(cfg.get("EDMG_STUDIO_DATA_DIR"))
        if saved_data is not None:
            return saved_data

    return (STUDIO_DIR / "data").resolve()

def _ensure_data_dir_env() -> Path:
    # Priority: explicit env -> Studio bootstrap -> launcher config -> default.
    env_home = os.environ.get("EDMG_STUDIO_HOME", "").strip()
    if env_home:
        _, p = _persist_studio_location(studio_home=Path(env_home))
        return p

    cur = os.environ.get("EDMG_STUDIO_DATA_DIR", "").strip()
    if cur:
        _, p = _persist_studio_location(data_dir=Path(cur))
        return p

    bootstrap = _read_json(_bootstrap_config_path(), default={})
    if isinstance(bootstrap, dict):
        saved_home = _saved_path_if_usable(bootstrap.get("studioHome"))
        if saved_home is not None:
            _, p = _persist_studio_location(studio_home=saved_home)
            return p

    cfg = _read_json(LAUNCHER_ENV_PATH, default={})
    if isinstance(cfg, dict):
        saved_home = _saved_path_if_usable(cfg.get("EDMG_STUDIO_HOME"))
        if saved_home is not None:
            _, p = _persist_studio_location(studio_home=saved_home)
            return p
        saved = _saved_path_if_usable(cfg.get("EDMG_STUDIO_DATA_DIR"))
        if saved is not None:
            _, p = _persist_studio_location(data_dir=saved)
            return p

    _, p = _persist_studio_location(data_dir=_default_data_dir())
    return p

def _safe_merge_copy(src: Path, dst: Path) -> tuple[int, int]:
    """Copy src -> dst, merging directories.

    Returns (files_copied, files_renamed) where renamed indicates name-collision renames.
    """
    files = 0
    renamed = 0
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            f, r = _safe_merge_copy(child, dst / child.name)
            files += f
            renamed += r
        return files, renamed

    dst.parent.mkdir(parents=True, exist_ok=True)
    target = dst
    if target.exists():
        # Avoid overwriting; add suffix.
        stem = target.stem
        suf = target.suffix
        i = 1
        while True:
            cand = target.with_name(f"{stem}_dup{i}{suf}")
            if not cand.exists():
                target = cand
                renamed += 1
                break
            i += 1
    shutil.copy2(src, target)
    files += 1
    return files, renamed


def _migrate_legacy_data_dir(new_data_dir: Path) -> str | None:
    """Migrate legacy runtime data into new_data_dir.

    Legacy locations we support:
      - studio/edmg-studio/python_backend/data   (the one that breaks pip install -e)

    We never delete user data:
      - We merge-copy into new_data_dir
      - Then we move the legacy folder into studio/edmg-studio/_legacy_migrations/...
    """
    legacy = BACKEND_DIR / "data"
    try:
        legacy = legacy.resolve()
        new_data_dir = new_data_dir.resolve()
    except Exception:
        pass

    if not legacy.exists() or not legacy.is_dir():
        return None
    if legacy == new_data_dir:
        return None

    new_data_dir.mkdir(parents=True, exist_ok=True)
    files, renamed = _safe_merge_copy(legacy, new_data_dir)

    backup_root = STUDIO_DIR / "_legacy_migrations"
    backup_root.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    backup = backup_root / f"python_backend_data_{ts}"
    try:
        shutil.move(str(legacy), str(backup))
    except Exception:
        # If move fails, leave it in place but warn.
        return f"Copied {files} files to {new_data_dir} (renamed {renamed} on collisions). WARNING: could not move legacy folder {legacy}."

    return f"Migrated legacy data: copied {files} files to {new_data_dir} (renamed {renamed}), moved old folder to {backup}."



def _try_create_junction(link_path: Path, target: Path) -> bool:
    """Best-effort create a directory junction (Windows) for backwards-compat paths."""
    if not sys.platform.startswith("win"):
        return False
    try:
        link_path.parent.mkdir(parents=True, exist_ok=True)
        # mklink is a cmd builtin
        cmd = ["cmd", "/c", "mklink", "/J", str(link_path), str(target)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return proc.returncode == 0
    except Exception:
        return False


def _migrate_repo_root_data_dir(new_data_dir: Path) -> str | None:
    """Migrate legacy repo-root ./data into new_data_dir.

    Why:
    - Older builds often wrote runtime artifacts into repo-root ./data.
    - Leaving it there can cause confusion; we centralize under Studio data dir.

    Safety:
    - Merge-copy into new_data_dir
    - Move legacy folder into studio/edmg-studio/_legacy_migrations/...
    - Recreate a junction at the original path to new_data_dir when possible.
    """
    legacy = ROOT / "data"
    try:
        legacy = legacy.resolve()
        new_data_dir = new_data_dir.resolve()
    except Exception:
        pass

    if not legacy.exists() or not legacy.is_dir():
        return None
    if legacy == new_data_dir:
        return None

    # If legacy is already a junction/symlink pointing at new_data_dir, do nothing.
    try:
        if legacy.samefile(new_data_dir):
            return None
    except Exception:
        pass

    new_data_dir.mkdir(parents=True, exist_ok=True)
    files, renamed = _safe_merge_copy(legacy, new_data_dir)

    backup_root = STUDIO_DIR / "_legacy_migrations"
    backup_root.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    backup = backup_root / f"repo_root_data_{ts}"
    try:
        shutil.move(str(legacy), str(backup))
    except Exception:
        return f"Copied {files} files from repo-root data/ to {new_data_dir} (renamed {renamed}). WARNING: could not move legacy folder {legacy}."

    # Recreate compatibility path (junction preferred).
    try:
        if _try_create_junction(ROOT / "data", new_data_dir):
            return f"Migrated repo-root data/: copied {files} files to {new_data_dir} (renamed {renamed}), moved old folder to {backup}, created junction data/ -> {new_data_dir}."
    except Exception:
        pass

    # Fallback: create stub folder with note.
    try:
        stub = ROOT / "data"
        stub.mkdir(parents=True, exist_ok=True)
        (stub / "MOVED_TO.txt").write_text(f"This folder was migrated to:\n{new_data_dir}\n", encoding="utf-8")
    except Exception:
        pass
    return f"Migrated repo-root data/: copied {files} files to {new_data_dir} (renamed {renamed}), moved old folder to {backup}."


def _migrate_work_dir(src: Path, dst: Path, *, label: str, create_junction: bool) -> str | None:
    """Migrate a legacy work/output directory into the canonical Studio data dir."""
    try:
        src = src.resolve()
        dst = dst.resolve()
    except Exception:
        pass

    if not src.exists() or not src.is_dir():
        return None
    if src == dst:
        return None
    try:
        if src.samefile(dst):
            return None
    except Exception:
        pass

    dst.mkdir(parents=True, exist_ok=True)
    files, renamed = _safe_merge_copy(src, dst)

    backup_root = STUDIO_DIR / "_legacy_migrations"
    backup_root.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    backup = backup_root / f"{label}_{ts}"
    try:
        shutil.move(str(src), str(backup))
    except Exception:
        return f"Copied {files} files from {label} to {dst} (renamed {renamed}). WARNING: could not move legacy folder {src}."

    if create_junction:
        try:
            if _try_create_junction(src, dst):
                return f"Migrated {label}: copied {files} files to {dst} (renamed {renamed}), moved old folder to {backup}, created junction {src} -> {dst}."
        except Exception:
            pass

    # Fallback: create stub note.
    try:
        src.mkdir(parents=True, exist_ok=True)
        (src / "MOVED_TO.txt").write_text(f"This folder was migrated to:\n{dst}\n", encoding="utf-8")
    except Exception:
        pass
    return f"Migrated {label}: copied {files} files to {dst} (renamed {renamed}), moved old folder to {backup}."


def _migrate_legacy_work_dirs(new_data_dir: Path) -> str | None:
    """Migrate common legacy work dirs like python_backend/.edmg_work into new_data_dir."""
    msgs = []
    # Legacy: inside python_backend (do NOT junction to avoid packaging/installation issues)
    m1 = _migrate_work_dir(BACKEND_DIR / ".edmg_work", new_data_dir / "work" / "edmg_work", label="python_backend_edmg_work", create_junction=False)
    if m1:
        msgs.append(m1)

    # Legacy: repo root .edmg_work (junction is OK for compatibility)
    m2 = _migrate_work_dir(ROOT / ".edmg_work", new_data_dir / "work" / "edmg_work", label="repo_root_edmg_work", create_junction=True)
    if m2:
        msgs.append(m2)

    return "\n".join(msgs) if msgs else None

def _migrate_legacy_data_dirs(new_data_dir: Path) -> str | None:
    msgs = []
    m1 = _migrate_legacy_data_dir(new_data_dir)
    if m1:
        msgs.append(m1)
    m2 = _migrate_repo_root_data_dir(new_data_dir)
    if m2:
        msgs.append(m2)
    m3 = _migrate_legacy_work_dirs(new_data_dir)
    if m3:
        msgs.append(m3)
    return "\n".join(msgs) if msgs else None


def _ensure_backend_env() -> tuple[str, int]:
    """Ensure backend host/port env vars are set and persisted.

    Priority: explicit env -> launcher_env.json -> defaults.
    """
    cfg = _read_json(LAUNCHER_ENV_PATH, default={})
    if not isinstance(cfg, dict):
        cfg = {}

    host = (os.environ.get("EDMG_STUDIO_BACKEND_HOST") or str(cfg.get("EDMG_STUDIO_BACKEND_HOST") or "")).strip()
    if not host:
        host = DEFAULT_BACKEND_HOST

    port_raw = (os.environ.get("EDMG_STUDIO_BACKEND_PORT") or str(cfg.get("EDMG_STUDIO_BACKEND_PORT") or "")).strip()
    try:
        port = int(port_raw) if port_raw else DEFAULT_BACKEND_PORT
    except Exception:
        port = DEFAULT_BACKEND_PORT
    if port == DEFAULT_UI_PORT:
        port = DEFAULT_BACKEND_PORT

    os.environ["EDMG_STUDIO_BACKEND_HOST"] = host
    os.environ["EDMG_STUDIO_BACKEND_PORT"] = str(port)

    cfg["EDMG_STUDIO_BACKEND_HOST"] = host
    cfg["EDMG_STUDIO_BACKEND_PORT"] = port
    if os.environ.get("EDMG_STUDIO_DATA_DIR"):
        cfg["EDMG_STUDIO_DATA_DIR"] = os.environ["EDMG_STUDIO_DATA_DIR"]
    if os.environ.get("EDMG_STUDIO_HOME"):
        cfg["EDMG_STUDIO_HOME"] = os.environ["EDMG_STUDIO_HOME"]
    _write_json(LAUNCHER_ENV_PATH, cfg)
    return host, port


def _is_port_bindable(host: str, port: int) -> bool:
    import socket
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            if s:
                s.close()
        except Exception:
            pass


def _listening_pid(port: int) -> int | None:
    """Best-effort PID of process listening on TCP port (Windows-first)."""
    try:
        if sys.platform.startswith("win"):
            proc = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True)
            if proc.returncode != 0:
                return None
            pat = re.compile(rf":{port}\s+LISTENING\s+(\d+)\s*$", re.IGNORECASE)
            pat6 = re.compile(rf"\]:{port}\s+LISTENING\s+(\d+)\s*$", re.IGNORECASE)
            for line in proc.stdout.splitlines():
                line = line.strip()
                m = pat.search(line) or pat6.search(line)
                if m:
                    return int(m.group(1))
            return None

        if shutil.which("lsof"):
            proc = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True)
            if proc.returncode != 0:
                return None
            for line in proc.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1])
    except Exception:
        return None
    return None


def _pid_name(pid: int) -> str | None:
    try:
        if sys.platform.startswith("win"):
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                return None
            line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
            if not line:
                return None
            if line.startswith('"'):
                return line.split('","')[0].strip('"')
            return line.split()[0]
        proc = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True)
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None
    except Exception:
        return None


def _port_doctor_line(name: str, host: str, port: int, *, health_url: str | None = None) -> tuple[bool, str]:
    """Return (ok, line). ok means service reachable (if health_url provided)."""
    url = f"http://{host}:{port}"
    pid = _listening_pid(port)
    proc_name = _pid_name(pid) if pid else None

    if health_url:
        try:
            _http_get(health_url, timeout=1.2)
            return True, f"{name}: OK ({url})"
        except Exception:
            if pid:
                return False, f"{name}: not reachable, port in use by PID {pid}{' ('+proc_name+')' if proc_name else ''} ({url})"
            return False, f"{name}: not reachable ({url})"

    if pid:
        return True, f"{name}: port open (PID {pid}{' ('+proc_name+')' if proc_name else ''}) ({url})"
    return False, f"{name}: not detected ({url})"


def _parse_service_host_port(raw_url: str, *, default_host: str, default_port: int) -> tuple[str, int]:
    value = str(raw_url or "").strip()
    if not value:
        return default_host, default_port
    try:
        from urllib.parse import urlparse
        parsed = urlparse(value if "://" in value else f"//{value}", scheme="http")
        host = parsed.hostname or default_host
        port = parsed.port or default_port
        return host, int(port)
    except Exception:
        return default_host, default_port


def _nvidia_service_specs() -> list[tuple[str, str, int, str | None]]:
    nim_url = os.environ.get("EDMG_NVIDIA_NIM_URL") or os.environ.get("EDMG_AI_OPENAI_COMPAT_BASE_URL") or DEFAULT_NVIDIA_NIM_URL
    nim_host, nim_port = _parse_service_host_port(nim_url, default_host="127.0.0.1", default_port=8001)
    riva_host, riva_port = _parse_service_host_port(
        os.environ.get("EDMG_NVIDIA_RIVA_URL") or DEFAULT_NVIDIA_RIVA_URL,
        default_host="127.0.0.1",
        default_port=50051,
    )
    triton_url = os.environ.get("EDMG_NVIDIA_TRITON_URL") or DEFAULT_NVIDIA_TRITON_URL
    triton_host, triton_port = _parse_service_host_port(triton_url, default_host="127.0.0.1", default_port=8002)
    omniverse_url = os.environ.get("EDMG_NVIDIA_OMNIVERSE_URL") or DEFAULT_NVIDIA_OMNIVERSE_URL
    omni_host, omni_port = _parse_service_host_port(omniverse_url, default_host="127.0.0.1", default_port=8011)

    return [
        ("NVIDIA NIM", nim_host, nim_port, f"http://{nim_host}:{nim_port}/v1/models"),
        ("NVIDIA Riva", riva_host, riva_port, None),
        ("NVIDIA Triton", triton_host, triton_port, f"http://{triton_host}:{triton_port}/v2/health/ready"),
        ("Omniverse / Audio2Face", omni_host, omni_port, None),
    ]


def _find_free_port(host: str, start_port: int, *, max_tries: int = 50) -> int:
    for p in range(start_port, start_port + max_tries):
        if _is_port_bindable(host, p):
            return p
    raise RuntimeError(f"No free port found in range {start_port}..{start_port+max_tries-1} for host {host}")


LOG_MAX_CHARS = 200_000

def _venv_python(venv: Path) -> Path:
    if sys.platform.startswith("win"):
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"

def _run_cmd(cmd, cwd=None, env=None, log_cb=None):
    p = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )
    for line in p.stdout:
        if log_cb:
            log_cb(line.rstrip("\n"))
    return p.wait()

def _http_get(url: str, timeout=3.0):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent":"EDMG-Studio-Launcher"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")

def _tail_file(path: Path, max_bytes: int = 200_000) -> str:
    """Return last max_bytes of a text file."""
    try:
        if not path.exists():
            return ""
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read()
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _parse_backend_url_from_logs(text: str) -> tuple[str, int] | None:
    """Parse EDMG_BACKEND_URL marker from desktop dev logs."""
    for pattern in (
        r"EDMG_BACKEND_URL=(https?://[^\s]+)",
        r"\[backend\][^\n]*?(https?://(?:127\.0\.0\.1|localhost):\d{4,5})",
    ):
        m = re.search(pattern, text)
        if not m:
            continue
        u = m.group(1).strip().rstrip("/")
        m2 = re.search(r"^https?://([^:/]+):(\d+)", u)
        if m2:
            return m2.group(1), int(m2.group(2))
    return None


class Launcher(tk.Tk):
    """Installer-style launcher for EDMG NVIDIA Workshop.

    Features:
    - Migrates legacy .edmg_work and data/ into the canonical Workshop data dir.
    - Port doctor + attach/switch/optional safe terminate.
    - Silent fix mode (attach/switch only; never kills processes).
    - Launches the merged NVIDIA Workshop runtime.
    """

    def __init__(self):
        super().__init__()
        self.title("EDMG NVIDIA Workshop Launcher")
        self.geometry("920x620")
        self.minsize(920, 620)

        self.backend_proc: subprocess.Popen | None = None
        self.studio_proc: subprocess.Popen | None = None
        self._studio_log_fp = None

        self._refresh_in_progress = False

        self.data_dir = _ensure_data_dir_env()
        self.studio_home = Path(os.environ.get("EDMG_STUDIO_HOME", str(_derive_studio_home(self.data_dir)))).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.studio_log_path = (self.data_dir / "logs" / "studio_dev.log").resolve()
        self.studio_log_path.parent.mkdir(parents=True, exist_ok=True)

        self._studio_log_pos = 0
        self._studio_log_poll_ms = 400
        self.var_follow_studio_log = tk.BooleanVar(value=True)

        self.backend_host, self.backend_port = _ensure_backend_env()

        self._startup_migration_msg = _migrate_legacy_data_dirs(self.data_dir)

        self._build_ui()
        self._refresh_status()

        self.after(500, self._poll_studio_log)

        self.after(250, self._auto_attach_backend_if_found)
        if self._startup_migration_msg:
            self.after(400, lambda: self._log(self._startup_migration_msg))

    # ---------------- UI / logging ----------------

    def _append_text(self, widget: tk.Text, msg: str, *, max_lines: int, follow: bool = True) -> None:
        msg = str(msg).rstrip("\n")
        if not msg:
            return
        if len(msg) > 200_000:
            msg = msg[-200_000:]

        try:
            widget.configure(state="normal")
            widget.insert("end", msg + "\n")
            if follow:
                widget.see("end")

            try:
                lines = int(widget.index("end-1c").split(".")[0])
                if lines > max_lines:
                    widget.delete("1.0", f"{lines - max_lines}.0")
            except Exception:
                pass

            widget.configure(state="disabled")
        except Exception:
            print(msg)

    def _log(self, msg: str) -> None:
        if not hasattr(self, "txt"):
            print(msg)
            return
        self._append_text(self.txt, str(msg), max_lines=1500, follow=True)

    def _log_studio(self, msg: str) -> None:
        if not hasattr(self, "txt_studio"):
            return
        follow = True
        try:
            follow = bool(self.var_follow_studio_log.get())
        except Exception:
            pass
        self._append_text(self.txt_studio, str(msg), max_lines=2500, follow=follow)

    def _clear_studio_log_view(self) -> None:
        try:
            self.txt_studio.configure(state="normal")
            self.txt_studio.delete("1.0", "end")
            self.txt_studio.configure(state="disabled")
        except Exception:
            pass

    def _open_studio_log_file(self) -> None:
        try:
            p = str(self.studio_log_path)
            if sys.platform.startswith("win"):
                os.startfile(p)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            self._log(f"Could not open log file: {e}")
    def _run_bg(self, title: str, fn) -> None:
        def runner():
            try:
                self._log(f"== {title} ==")
                fn()
                self._log(f"== {title}: done ==")
            except Exception as e:
                self._log(f"!! {title} failed: {e}")
                try:
                    messagebox.showerror("Error", f"{title} failed:\n{e}")
                except Exception:
                    pass
            finally:
                try:
                    self._refresh_status()
                except Exception:
                    pass

        threading.Thread(target=runner, daemon=True).start()

    def _which(self, exe: str) -> str | None:
        return shutil.which(exe)

    def _apply_studio_home(self, studio_home: Path, *, reason: str) -> None:
        migration_queued = _queue_studio_migration(self.studio_home, self.data_dir, studio_home)
        studio_home, data_dir = _persist_studio_location(studio_home=studio_home)
        self.studio_home = studio_home
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.studio_log_path = (self.data_dir / "logs" / "studio_dev.log").resolve()
        self.studio_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._studio_log_pos = 0

        if hasattr(self, "var_studio_home"):
            self.var_studio_home.set(str(self.studio_home))
        if hasattr(self, "var_data_dir"):
            self.var_data_dir.set(str(self.data_dir))

        if hasattr(self, "txt"):
            self._log(f"Workshop home set ({reason}): {self.studio_home}")
            if migration_queued:
                self._log("Existing Workshop data will migrate into the new home on the next Workshop launch.")
            if (self.backend_proc and self.backend_proc.poll() is None) or (self.studio_proc and self.studio_proc.poll() is None):
                self._log("Restart the running backend/Workshop to apply the new storage location.")

    def _build_ui(self) -> None:
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        top = ttk.Frame(frm)
        top.pack(fill="x")
        ttk.Label(top, text="EDMG NVIDIA Workshop", font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Label(top, text="NVIDIA services launcher (no CLI typing)", foreground="#555").pack(side="left", padx=12)

        # Paths
        path_row = ttk.LabelFrame(frm, text="Paths", padding=10)
        path_row.pack(fill="x", pady=(12, 8))

        self.var_root = tk.StringVar(value=str(ROOT))
        self.var_studio_home = tk.StringVar(value=str(self.studio_home))
        self.var_data_dir = tk.StringVar(value=str(self.data_dir))
        self.var_studio = tk.StringVar(value=str(STUDIO_DIR))
        self.var_backend = tk.StringVar(value=str(BACKEND_DIR))
        self.var_backend_host = tk.StringVar(value=str(self.backend_host))
        self.var_backend_port = tk.StringVar(value=str(self.backend_port))

        for label, var in [("Repo root", self.var_root), ("Studio dir", self.var_studio), ("Backend dir", self.var_backend)]:
            row = ttk.Frame(path_row)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, width=12).pack(side="left")
            ent = ttk.Entry(row, textvariable=var)
            ent.pack(side="left", fill="x", expand=True)

        # Backend host/port
        row = ttk.Frame(path_row)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Backend host", width=12).pack(side="left")
        ttk.Entry(row, textvariable=self.var_backend_host, width=22).pack(side="left", padx=(0, 8))
        ttk.Label(row, text="Port").pack(side="left")
        ttk.Entry(row, textvariable=self.var_backend_port, width=10).pack(side="left", padx=(6, 0))

        def _apply_backend_host_port():
            host = self.var_backend_host.get().strip() or DEFAULT_BACKEND_HOST
            try:
                port = int(self.var_backend_port.get().strip() or str(DEFAULT_BACKEND_PORT))
            except Exception:
                port = DEFAULT_BACKEND_PORT
            self._set_backend_host_port(host, port, reason="manual")
        ttk.Button(row, text="Apply", command=_apply_backend_host_port).pack(side="left", padx=8)

        # Workshop home
        row = ttk.Frame(path_row)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Workshop home", width=12).pack(side="left")
        ttk.Entry(row, textvariable=self.var_studio_home).pack(side="left", fill="x", expand=True)

        def _apply_studio_home():
            raw = self.var_studio_home.get().strip()
            if not raw:
                messagebox.showerror("Workshop home", "Pick a valid Workshop home folder first.")
                return
            self._apply_studio_home(Path(raw), reason="manual")
            self._refresh_status()

        def _browse_studio_home():
            d = filedialog.askdirectory(title="Select EDMG NVIDIA Workshop home folder", initialdir=str(self.studio_home))
            if not d:
                return
            self._apply_studio_home(Path(d), reason="browse")
            self._refresh_status()

        ttk.Button(row, text="Apply", command=_apply_studio_home).pack(side="left", padx=6)
        ttk.Button(row, text="Browse…", command=_browse_studio_home).pack(side="left", padx=6)

        # Data dir
        row = ttk.Frame(path_row)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Data dir", width=12).pack(side="left")
        ttk.Entry(row, textvariable=self.var_data_dir, state="readonly").pack(side="left", fill="x", expand=True)

        # Status
        stat = ttk.LabelFrame(frm, text="Status", padding=10)
        stat.pack(fill="x", pady=(0, 8))

        self.lbl_python = ttk.Label(stat, text="Python: …")
        self.lbl_node = ttk.Label(stat, text="Node: …")
        self.lbl_nim = ttk.Label(stat, text="NVIDIA NIM: …")
        self.lbl_riva = ttk.Label(stat, text="NVIDIA Riva: …")
        self.lbl_triton = ttk.Label(stat, text="NVIDIA Triton: …")
        self.lbl_omniverse = ttk.Label(stat, text="Omniverse / Audio2Face: …")
        self.lbl_backend = ttk.Label(stat, text="Backend: …")

        for w in [self.lbl_python, self.lbl_node, self.lbl_nim, self.lbl_riva, self.lbl_triton, self.lbl_omniverse, self.lbl_backend]:
            w.pack(anchor="w")

        btn_stat = ttk.Frame(stat)
        btn_stat.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_stat, text="Fix ports…", command=self.open_fix_ports_dialog).pack(side="left")
        ttk.Button(btn_stat, text="Fix ports (silent)", command=self.fix_ports_silent).pack(side="left", padx=8)
        ttk.Button(btn_stat, text="Rescan backend ports", command=self._auto_attach_backend_if_found).pack(side="left", padx=8)

        # Actions
        actions = ttk.LabelFrame(frm, text="Actions", padding=10)
        actions.pack(fill="x", pady=(0, 8))

        btn_row = ttk.Frame(actions)
        btn_row.pack(fill="x")

        ttk.Button(btn_row, text="Start NVIDIA Workshop Runtime", command=self.start_nvidia_runtime).pack(side="left")
        ttk.Button(btn_row, text="Install/Update Backend (venv + deps)", command=self.install_backend).pack(side="left", padx=8)
        ttk.Button(btn_row, text="Stop Backend", command=self.stop_backend).pack(side="left")
        ttk.Button(btn_row, text="Run Health Test", command=self.health_test).pack(side="left", padx=8)

        # Packaging (Windows)
        pkg_row = ttk.Frame(actions)
        pkg_row.pack(fill="x", pady=(8, 0))
        ttk.Button(pkg_row, text="Get FFmpeg (for NVIDIA assembly)", command=self.get_ffmpeg).pack(side="left")
        ttk.Button(pkg_row, text="Stage NVIDIA Workshop Bundle", command=self.stage_nvidia_kit_bundle).pack(side="left", padx=8)
        ttk.Button(pkg_row, text="Build Workshop Installer", command=self.build_installer).pack(side="left", padx=8)
        ttk.Button(pkg_row, text="Build Workshop Inno Installer", command=self.build_inno_installer).pack(side="left")
        ttk.Button(pkg_row, text="Open Release Folder", command=self.open_release_folder).pack(side="left")
        ttk.Button(pkg_row, text="Open Inno Folder", command=self.open_inno_folder).pack(side="left", padx=8)

        # Log
        logbox = ttk.LabelFrame(frm, text="Logs", padding=10)
        logbox.pack(fill="both", expand=True)

        nb = ttk.Notebook(logbox)
        nb.pack(fill="both", expand=True)

        tab_launcher = ttk.Frame(nb)
        tab_studio = ttk.Frame(nb)
        nb.add(tab_launcher, text="Launcher Log")
        nb.add(tab_studio, text="Workshop Dev Log (live)")

        # Launcher log
        self.txt = tk.Text(tab_launcher, height=18, wrap="word")
        sb1 = ttk.Scrollbar(tab_launcher, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb1.set)
        self.txt.pack(side="left", fill="both", expand=True)
        sb1.pack(side="right", fill="y")
        self.txt.configure(state="disabled")

        # Studio log toolbar
        studio_toolbar = ttk.Frame(tab_studio)
        studio_toolbar.pack(fill="x", pady=(0, 6))
        ttk.Checkbutton(studio_toolbar, text="Follow", variable=self.var_follow_studio_log).pack(side="left")
        ttk.Button(studio_toolbar, text="Open log file…", command=self._open_studio_log_file).pack(side="left", padx=8)
        ttk.Button(studio_toolbar, text="Clear view", command=self._clear_studio_log_view).pack(side="left")

        studio_body = ttk.Frame(tab_studio)
        studio_body.pack(fill="both", expand=True)

        self.txt_studio = tk.Text(studio_body, height=18, wrap="none")
        sb2 = ttk.Scrollbar(studio_body, command=self.txt_studio.yview)
        self.txt_studio.configure(yscrollcommand=sb2.set)
        self.txt_studio.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")
        self.txt_studio.configure(state="disabled")

        ttk.Button(frm, text="Refresh status", command=self._refresh_status).pack(anchor="e", pady=(8, 0))

    # ---------------- backend host/port persistence ----------------

    def _set_backend_host_port(self, host: str, port: int, *, reason: str) -> None:
        host = host.strip() or DEFAULT_BACKEND_HOST
        port = int(port)
        self.backend_host = host
        self.backend_port = port
        self.var_backend_host.set(host)
        self.var_backend_port.set(str(port))

        os.environ["EDMG_STUDIO_BACKEND_HOST"] = host
        os.environ["EDMG_STUDIO_BACKEND_PORT"] = str(port)

        cfg = _read_json(LAUNCHER_ENV_PATH, default={})
        if not isinstance(cfg, dict):
            cfg = {}
        cfg["EDMG_STUDIO_BACKEND_HOST"] = host
        cfg["EDMG_STUDIO_BACKEND_PORT"] = port
        cfg.update(_default_storage_env(self.studio_home, self.data_dir))
        _write_json(LAUNCHER_ENV_PATH, cfg)

        self._log(f"Backend host/port set ({reason}): {host}:{port}")

    # ---------------- health / scan / sync ----------------

    def _backend_health_ok(self, host: str, port: int) -> bool:
        try:
            body = _http_get(f"http://{host}:{port}/health", timeout=0.9)
            data = json.loads(body)
            return isinstance(data, dict) and data.get("ok") is True
        except Exception:
            return False

    def _scan_for_running_backend(self, host: str, start_port: int, end_port: int) -> int | None:
        for p in range(start_port, end_port + 1):
            if self._backend_health_ok(host, p):
                return p
        return None

    def _sync_backend_from_studio_logs(self) -> bool:
        """If desktop dev logs show a backend URL, sync launcher to it."""
        parsed = _parse_backend_url_from_logs(_tail_file(self.studio_log_path))
        if not parsed:
            return False
        host, port = parsed
        try:
            port = int(port)
        except Exception:
            port = DEFAULT_BACKEND_PORT

        if (host != self.backend_host) or (port != int(self.backend_port)):
            self._set_backend_host_port(host, port, reason="studio-log-scan")
            if self._backend_health_ok(host, port):
                self._log(f"Synced backend from Studio logs: {host}:{port}.")
            else:
                self._log(f"Studio logs indicate backend at {host}:{port} (not healthy yet).")
        return True


    def _poll_studio_log(self) -> None:


        parsed = None  # hotfix: avoid UnboundLocalError if log parse fails

        """Tail Studio dev log into the GUI (live)."""
        parsed = None  # may remain None if no marker found yet
        try:
            p = self.studio_log_path
            if p.exists() and p.is_file():
                size = p.stat().st_size
                if size < self._studio_log_pos:
                    self._studio_log_pos = 0
                if size > self._studio_log_pos:
                    with p.open("rb") as f:
                        f.seek(self._studio_log_pos)
                        data = f.read()
                    self._studio_log_pos = size
                    chunk = data.decode("utf-8", errors="ignore")
                    if chunk:
                        self._log_studio(chunk.rstrip("\n"))
                        parsed = _parse_backend_url_from_logs(chunk)
                        if parsed:
                            host, port = parsed
                            try:
                                port = int(port)
                            except Exception:
                                port = 7863
                            if (host != self.backend_host) or (port != int(self.backend_port)):
                                self._set_backend_host_port(host, port, reason="studio-log-live")
                                if self._backend_health_ok(host, port):
                                    self._log(f"Synced backend from Studio logs: {host}:{port}.")
                                else:
                                    self._log(f"Studio logs indicate backend at {host}:{port} (not healthy yet).")
        except Exception:
            pass
        finally:
            try:
                self.after(int(self._studio_log_poll_ms), self._poll_studio_log)
            except Exception:
                pass


    def _ensure_backend_port_available(self) -> None:
        """Auto-pick a free backend port if current port is taken by something else."""
        host = self.backend_host
        port = int(self.backend_port)
        if port == DEFAULT_UI_PORT:
            self._log(f"Backend port {port} conflicts with the Studio UI port. Resetting to a backend-safe port…")
            new_port = _find_free_port(host, DEFAULT_BACKEND_PORT, max_tries=50)
            self._set_backend_host_port(host, new_port, reason="ui-port-conflict")
            return

        # If it responds as our backend, keep it.
        if self._backend_health_ok(host, port):
            return

        # If bindable, it's free → keep.
        if _is_port_bindable(host, port):
            return

        pid = _listening_pid(port)
        pname = _pid_name(pid) if pid else None
        self._log(f"Backend port {port} is in use by PID {pid}{' ('+pname+')' if pname else ''}. Picking a free port…")

        start_from = port if port >= DEFAULT_BACKEND_PORT else DEFAULT_BACKEND_PORT
        new_port = _find_free_port(host, start_from, max_tries=50)
        self._set_backend_host_port(host, new_port, reason="auto-port-pick")

    def _auto_attach_backend_if_found(self) -> None:
        """Attach to an existing backend (scan + studio logs)."""
        if self._refresh_in_progress:
            return

        # Prefer desktop UI logs when that dev client picked a different port.
        if self._sync_backend_from_studio_logs():
            self._refresh_status()
            return

        host = self.backend_host
        port = int(self.backend_port)

        if self._backend_health_ok(host, port):
            self._refresh_status()
            return

        if self.backend_proc and self.backend_proc.poll() is None:
            self._refresh_status()
            return

        found = self._scan_for_running_backend(host, DEFAULT_BACKEND_PORT, DEFAULT_BACKEND_PORT + 10)
        if found and found != port:
            self._log(f"Detected already-running backend at {host}:{found}; attaching.")
            self._set_backend_host_port(host, found, reason="auto-detect scan")

        self._refresh_status()

    # ---------------- port doctor / fixes ----------------

    def _is_safe_kill(self, proc_name: str | None) -> bool:
        if not proc_name:
            return False
        name = proc_name.lower()
        deny = ["system", "svchost", "services", "wininit", "csrss", "lsass", "explorer", "dwm"]
        if any(d in name for d in deny):
            return False
        allow = [
            "python", "python.exe", "pythonw.exe", "uvicorn",
            "node", "node.exe",
            "comfy", "comfyui", "edmg", "electron",
            "kit", "kit.exe", "tritonserver", "riva", "nim"
        ]
        return any(a in name for a in allow)

    def _terminate_pid(self, pid: int) -> None:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True)
        else:
            subprocess.run(["kill", "-9", str(pid)], capture_output=True, text=True)

    def fix_ports_silent(self) -> None:
        """Attach/switch only; never kills processes."""
        def work():
            if self._sync_backend_from_studio_logs():
                return
            host = self.backend_host
            port = int(self.backend_port)

            found = self._scan_for_running_backend(host, DEFAULT_BACKEND_PORT, DEFAULT_BACKEND_PORT + 10)
            if found and found != port:
                self._log(f"[silent-fix] Found backend at {host}:{found}; attaching.")
                self._set_backend_host_port(host, found, reason="silent-fix attach")
                return

            if self._backend_health_ok(host, port):
                self._log("[silent-fix] Backend healthy; nothing to do.")
                return

            new_port = _find_free_port(host, max(DEFAULT_BACKEND_PORT, port), max_tries=50)
            if new_port != port:
                self._log(f"[silent-fix] Switching backend port {port} -> {new_port}")
                self._set_backend_host_port(host, new_port, reason="silent-fix switch")

        self._run_bg("Fix ports (silent)", work)

    def open_fix_ports_dialog(self) -> None:
        """Port fixer UI: attach/switch, optionally terminate known-safe processes."""
        win = tk.Toplevel(self)
        win.title("Fix ports")
        win.geometry("820x460")
        win.transient(self)
        win.grab_set()

        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Port Doctor", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(
            frm,
            text="Shows which processes occupy required ports. You can attach to an existing backend, auto-pick a new port, or terminate only known-safe processes.",
            wraplength=780,
            foreground="#444",
        ).pack(anchor="w", pady=(2, 10))

        host = self.backend_host
        cur_port = int(self.backend_port)

        services = [
            ("Studio backend", host, cur_port, f"http://{host}:{cur_port}/health"),
            *_nvidia_service_specs(),
        ]

        rows = []
        kill_vars: dict[int, tk.BooleanVar] = {}

        table = ttk.Frame(frm)
        table.pack(fill="x", pady=(0, 8))

        hdr = ttk.Frame(table)
        hdr.pack(fill="x")
        for col, w in [("Service", 18), ("Host", 14), ("Port", 6), ("PID/Process", 38), ("Action", 12)]:
            ttk.Label(hdr, text=col, width=w, font=("Segoe UI", 9, "bold")).pack(side="left")

        body = ttk.Frame(table)
        body.pack(fill="x")

        def _render_rows():
            for child in body.winfo_children():
                child.destroy()
            rows.clear()
            kill_vars.clear()

            for name, h, p, health in services:
                pid = _listening_pid(p)
                pname = _pid_name(pid) if pid else None
                ok = False
                try:
                    _http_get(health, timeout=0.9)
                    ok = True
                except Exception:
                    ok = False

                row = ttk.Frame(body)
                row.pack(fill="x", pady=2)
                ttk.Label(row, text=name, width=18).pack(side="left")
                ttk.Label(row, text=h, width=14).pack(side="left")
                ttk.Label(row, text=str(p), width=6).pack(side="left")
                proc_txt = "free"
                if pid:
                    proc_txt = f"{pid}{' ('+pname+')' if pname else ''}" + ("  [OK]" if ok else "  [CONFLICT]")
                ttk.Label(row, text=proc_txt, width=38).pack(side="left")

                can_kill = bool(pid and self._is_safe_kill(pname) and not ok)
                if can_kill:
                    v = tk.BooleanVar(value=False)
                    kill_vars[p] = v
                    ttk.Checkbutton(row, text="Kill", variable=v).pack(side="left")
                else:
                    ttk.Label(row, text="—", width=12, foreground="#777").pack(side="left")

                rows.append((name, h, p, pid, pname, ok))

        def _attach_scan():
            found = self._scan_for_running_backend(host, DEFAULT_BACKEND_PORT, DEFAULT_BACKEND_PORT + 10)
            if found and found != int(self.backend_port):
                self._set_backend_host_port(host, found, reason="fix-ports attach-scan")
                services[0] = ("Studio backend", host, int(self.backend_port), f"http://{host}:{int(self.backend_port)}/health")
            _render_rows()

        def _attach_logs():
            if self._sync_backend_from_studio_logs():
                services[0] = ("Studio backend", self.backend_host, int(self.backend_port), f"http://{self.backend_host}:{int(self.backend_port)}/health")
            _render_rows()

        def _auto_switch_backend():
            if self._backend_health_ok(host, int(self.backend_port)):
                _render_rows()
                return
            new_port = _find_free_port(host, max(DEFAULT_BACKEND_PORT, int(self.backend_port)), max_tries=50)
            self._set_backend_host_port(host, new_port, reason="fix-ports auto-switch")
            services[0] = ("Studio backend", host, int(self.backend_port), f"http://{host}:{int(self.backend_port)}/health")
            _render_rows()

        def _terminate_selected():
            to_kill = []
            for _name, _h, p, pid, pname, ok in rows:
                if pid and not ok and kill_vars.get(p, tk.BooleanVar(value=False)).get():
                    to_kill.append((p, pid, pname))
            if not to_kill:
                messagebox.showinfo("Fix ports", "No processes selected for termination.")
                return
            msg = "Terminate these processes?\n\n" + "\n".join([f"port {p}: PID {pid} ({pname or 'unknown'})" for p, pid, pname in to_kill])
            if not messagebox.askyesno("Confirm terminate", msg):
                return
            for _, pid, _ in to_kill:
                self._terminate_pid(pid)
            time.sleep(0.2)
            _render_rows()

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Rescan", command=_render_rows).pack(side="left")
        ttk.Button(btns, text="Attach (scan 7863–7873)", command=_attach_scan).pack(side="left", padx=8)
        ttk.Button(btns, text="Attach (Studio logs)", command=_attach_logs).pack(side="left")
        ttk.Button(btns, text="Auto-switch backend port", command=_auto_switch_backend).pack(side="left", padx=8)
        ttk.Button(btns, text="Silent fix", command=lambda: (self.fix_ports_silent(), _render_rows())).pack(side="left")
        ttk.Button(btns, text="Terminate selected", command=_terminate_selected).pack(side="right")
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right", padx=8)

        _render_rows()

    # ---------------- actions ----------------

    def _refresh_status(self) -> None:
        if self._refresh_in_progress:
            return
        self._refresh_in_progress = True
        try:
            py = sys.executable
            try:
                bootstrap_cmd, bootstrap_version = _resolve_supported_python_command()
                bootstrap_note = f" | bootstrap: {_describe_python_command(bootstrap_cmd)} ({_format_python_version(bootstrap_version)})"
            except Exception as e:
                bootstrap_note = f" | bootstrap: NOT FOUND ({e})"
            node = self._which("node")
            package_manager_name = _studio_package_manager_name()
            package_manager = _resolve_package_manager_command(package_manager_name)

            self.lbl_python.config(text=f"Python: {py}{bootstrap_note}")
            self.lbl_node.config(
                text=f"Node: {node or 'NOT FOUND'} ({package_manager_name}: {(package_manager[1] if package_manager else 'NOT FOUND')})"
            )

            nvidia_lines = {}
            for service_name, service_host, service_port, service_health in _nvidia_service_specs():
                _, line = _port_doctor_line(service_name, service_host, service_port, health_url=service_health)
                nvidia_lines[service_name] = line
            self.lbl_nim.config(text=nvidia_lines.get("NVIDIA NIM", "NVIDIA NIM: not configured"))
            self.lbl_riva.config(text=nvidia_lines.get("NVIDIA Riva", "NVIDIA Riva: not configured"))
            self.lbl_triton.config(text=nvidia_lines.get("NVIDIA Triton", "NVIDIA Triton: not configured"))
            self.lbl_omniverse.config(text=nvidia_lines.get("Omniverse / Audio2Face", "Omniverse / Audio2Face: not configured"))

            host = self.backend_host
            port = int(self.backend_port)
            ok_backend, line_backend = _port_doctor_line("Studio backend", host, port, health_url=f"http://{host}:{port}/health")
            self.lbl_backend.config(text=line_backend)

            # If not OK and we didn't start it, try syncing via logs or scan.
            if (not ok_backend) and (not self.backend_proc or self.backend_proc.poll() is not None):
                if self._sync_backend_from_studio_logs():
                    return
                found = self._scan_for_running_backend(host, DEFAULT_BACKEND_PORT, DEFAULT_BACKEND_PORT + 10)
                if found and found != port:
                    self._log(f"Detected backend on {host}:{found}; switching launcher to it.")
                    self._set_backend_host_port(host, found, reason="auto-detect refresh")
        finally:
            self._refresh_in_progress = False

    def install_backend(self) -> None:
        def work():
            BACKEND_DIR.mkdir(parents=True, exist_ok=True)
            bootstrap_cmd, bootstrap_version = _resolve_supported_python_command()
            self._log(
                f"Using bootstrap Python: {_describe_python_command(bootstrap_cmd)} "
                f"({_format_python_version(bootstrap_version)})"
            )

            venv_ok, venv_detail = _backend_venv_status()
            if BACKEND_VENV.exists() and not venv_ok:
                self._log(f"Backend venv is incompatible ({venv_detail}). Recreating it.")
                _reset_backend_venv(self._log)

            if not BACKEND_VENV.exists():
                self._log(f"Creating venv: {BACKEND_VENV}")
                rc = _run_cmd([*bootstrap_cmd, "-m", "venv", str(BACKEND_VENV)], cwd=BACKEND_DIR, log_cb=self._log)
                if rc != 0:
                    raise RuntimeError("venv creation failed")

            py = str(_venv_python(BACKEND_VENV))
            venv_ok, venv_detail = _backend_venv_status()
            if not venv_ok:
                raise RuntimeError(f"Backend venv is not usable after creation: {venv_detail}")

            self._log("Upgrading pip…")
            rc = _run_cmd([py, "-m", "pip", "install", "-U", "pip"], cwd=BACKEND_DIR, log_cb=self._log)
            if rc != 0:
                raise RuntimeError("pip upgrade failed")

            self._log("Installing backend + bundled EDMG Core (editable)…")
            rc = _run_cmd([py, "-m", "pip", "install", "-e", ".[studio_bundle]"], cwd=BACKEND_DIR, log_cb=self._log)
            if rc != 0:
                raise RuntimeError("backend install failed")

        self._run_bg("Install backend", work)

    def install_ui(self) -> None:
        def work():
            package_manager_name = _studio_package_manager_name()
            package_manager = _resolve_package_manager_command(package_manager_name)
            if not package_manager:
                raise RuntimeError(f"{package_manager_name} not found. Install Node.js LTS, enable Corepack if needed, then retry.")
            if not PACKAGE_JSON_PATH.exists():
                raise RuntimeError(f"package.json not found at {STUDIO_DIR}")
            rc = _run_cmd([*package_manager[0], "install"], cwd=STUDIO_DIR, log_cb=self._log)
            if rc != 0:
                raise RuntimeError(f"{package_manager_name} install failed")

        self._run_bg("Install Studio UI deps", work)

    def start_backend(self) -> None:
        def work():
            # Attach to an already-running backend on nearby ports (avoid duplicates).
            found = self._scan_for_running_backend(self.backend_host, DEFAULT_BACKEND_PORT, DEFAULT_BACKEND_PORT + 10)
            if found:
                if found != int(self.backend_port):
                    self._log(f"Found existing backend at {self.backend_host}:{found}; attaching (not starting new).")
                    self._set_backend_host_port(self.backend_host, found, reason="attach start-backend")
                else:
                    self._log("Backend reachable on configured port; not starting a new one.")
                return

            if self.backend_proc and self.backend_proc.poll() is None:
                self._log("Backend already running.")
                return

            venv_ok, venv_detail = _backend_venv_status()
            if not BACKEND_VENV.exists() or not venv_ok:
                self._log(f"Backend venv missing or incompatible ({venv_detail}). Running backend install first.")
                self.install_backend()
                raise RuntimeError("Backend not installed yet. Re-run Start Backend after install completes.")

            self._ensure_backend_port_available()

            py = str(_venv_python(BACKEND_VENV))
            env = os.environ.copy()
            for key, value in _default_storage_env(self.studio_home, self.data_dir).items():
                env.setdefault(key, value)
            for key, value in _default_nvidia_env().items():
                env.setdefault(key, value)
            ffmpeg_path = _resolve_ffmpeg_path()
            env["EDMG_FFMPEG_PATH"] = ffmpeg_path
            self._log(f"Using FFmpeg: {ffmpeg_path}")
            cmd = [py, "-m", "edmg_studio_backend", "serve", "--host", self.backend_host, "--port", str(self.backend_port)]
            self._log("Starting backend: " + " ".join(cmd))
            self.backend_proc = subprocess.Popen(cmd, cwd=str(BACKEND_DIR), env=env)
            time.sleep(0.25)

        self._run_bg("Start backend", work)

    def stop_backend(self) -> None:
        def work():
            if not self.backend_proc or self.backend_proc.poll() is not None:
                self._log("Backend not running.")
                return
            self._log("Stopping backend…")
            try:
                self.backend_proc.terminate()
            except Exception:
                pass
            time.sleep(0.3)
            if self.backend_proc and self.backend_proc.poll() is None:
                try:
                    self.backend_proc.kill()
                except Exception:
                    pass
            self.backend_proc = None

        self._run_bg("Stop backend", work)

    def health_test(self) -> None:
        def work():
            url = f"http://{self.backend_host}:{self.backend_port}/health"
            try:
                h = _http_get(url, timeout=1.2)
                self._log("Health OK: " + h)
            except Exception:
                self._log("Backend not running; starting it for test.")
                self.start_backend()
                for _ in range(40):
                    try:
                        h = _http_get(url, timeout=1.2)
                        self._log("Health OK: " + h)
                        break
                    except Exception:
                        time.sleep(0.25)
                else:
                    raise RuntimeError("Backend did not become healthy in time.")
            try:
                s = _http_get(f"http://{self.backend_host}:{self.backend_port}/v1/setup/status", timeout=2.0)
                self._log("Setup status: " + s)
            except Exception as e:
                self._log(f"Setup status endpoint not available (ok): {e}")

        self._run_bg("Health test", work)

    def _run_powershell(self, ps1: Path, extra_args: list[str] | None = None, *, cwd: Path | None = None) -> None:
        if not sys.platform.startswith("win"):
            raise RuntimeError("Windows-only action.")
        if not ps1.exists():
            raise RuntimeError(f"Script not found: {ps1}")
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)]
        if extra_args:
            cmd.extend(extra_args)
        rc = _run_cmd(cmd, cwd=cwd or STUDIO_DIR, log_cb=self._log)
        if rc != 0:
            raise RuntimeError(f"PowerShell script failed (exit {rc}): {ps1.name}")

    def start_nvidia_runtime(self) -> None:
        def work():
            backend_url = f"http://{self.backend_host}:{int(self.backend_port)}"
            ffmpeg_path = _resolve_ffmpeg_path()
            args = [
                "-BackendUrl", backend_url,
                "-HostName", self.backend_host,
                "-Port", str(int(self.backend_port)),
                "-ReuseExistingBackend",
                "-FfmpegPath", ffmpeg_path,
            ]
            self._log(f"Launching NVIDIA Kit runtime against backend {backend_url}")
            self._log(f"Using FFmpeg: {ffmpeg_path}")
            self._run_powershell(NVIDIA_RUNTIME_SCRIPT, args, cwd=ROOT)

        self._run_bg("Start NVIDIA Runtime", work)

    def get_ffmpeg(self) -> None:
        def work():
            ps1 = STUDIO_DIR / "packaging" / "windows" / "get_ffmpeg.ps1"
            self._log("This will download FFmpeg for NVIDIA Kit render assembly.")
            self._run_powershell(ps1)

        self._run_bg("Get FFmpeg", work)

    def stage_nvidia_kit_bundle(self) -> None:
        def work():
            ps1 = ROOT / "deployment" / "nvidia" / "stage_nvidia_kit_app.ps1"
            self._log("Staging the NVIDIA Workshop app bundle under dist/nvidia-workshop-app.")
            self._run_powershell(ps1, ["-PackageMode", "thin"], cwd=ROOT)
            self._log("NVIDIA Workshop staging finished. Use the staged Start-EDMG-NVIDIA-Workshop.ps1 launcher.")

        self._run_bg("Stage NVIDIA Kit Bundle", work)

    def build_installer(self) -> None:
        def work():
            ps1 = STUDIO_DIR / "packaging" / "windows" / "build_all.ps1"
            self._log("Building Workshop installer (backend EXE + Electron shell)...")
            self._run_powershell(ps1)
            self._log("Workshop build finished. Use 'Open Release Folder' to find the installer.")

        self._run_bg("Build installer", work)

    def build_inno_installer(self) -> None:
        def work():
            ps1 = STUDIO_DIR / "packaging" / "windows" / "build_inno_external.ps1"
            self._log("Building Workshop Inno external-payload installer (setup EXE + payload archive)...")
            self._run_powershell(ps1)
            self._log("Workshop Inno build finished. Use 'Open Inno Folder' and ship the setup EXE with the payload folder.")

        self._run_bg("Build Inno installer", work)

    def _open_folder(self, folder: Path, missing_title: str, missing_message: str) -> None:
        if not folder.exists():
            messagebox.showinfo(missing_title, missing_message)
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(folder))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as e:
            messagebox.showerror("Open folder failed", str(e))

    def open_release_folder(self) -> None:
        rel = STUDIO_DIR / "release"
        if not rel.exists():
            rel = STUDIO_DIR / "dist"
        self._open_folder(
            rel,
            "Release folder",
            f"Release output folder not found yet.\nExpected at:\n{STUDIO_DIR / 'release'}",
        )

    def open_inno_folder(self) -> None:
        folder = STUDIO_DIR / "dist-inno"
        self._open_folder(
            folder,
            "Inno release folder",
            f"Inno release folder not found yet.\nExpected at:\n{folder}",
        )

    def _start_studio_impl(self) -> None:
        package_manager_name = _studio_package_manager_name()
        package_manager = _resolve_package_manager_command(package_manager_name)
        if not package_manager:
            raise RuntimeError(f"{package_manager_name} not found. Install Node.js LTS, enable Corepack if needed, then retry.")
        self._log(f"Starting Workshop desktop dev UI ({package_manager_name} run dev)...")

        # Align backend first.
        self._auto_attach_backend_if_found()
        self._ensure_backend_port_available()

        env = os.environ.copy()
        env.setdefault("EDMG_STUDIO_SPAWN_BACKEND", "1")
        env.setdefault("EDMG_STUDIO_BACKEND_HOST", self.backend_host)
        env.setdefault("EDMG_STUDIO_BACKEND_PORT", str(self.backend_port))
        for key, value in _default_storage_env(self.studio_home, self.data_dir).items():
            env.setdefault(key, value)
        for key, value in _default_nvidia_env().items():
            env.setdefault(key, value)

        self.studio_log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self._studio_log_fp and not self._studio_log_fp.closed:
                self._studio_log_fp.close()
        except Exception:
            pass

        self._studio_log_fp = open(self.studio_log_path, "a", encoding="utf-8", errors="ignore")
        self._studio_log_fp.write(f"\n=== launcher start_workshop_desktop_ui {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        self._studio_log_fp.flush()

        self.studio_proc = subprocess.Popen(
            [*package_manager[0], "run", "dev"],
            cwd=str(STUDIO_DIR),
            env=env,
            stdout=self._studio_log_fp,
            stderr=subprocess.STDOUT,
        )
        self._log(f"Studio logs: {self.studio_log_path}")

        # Prime live view with tail.
        try:
            self._studio_log_pos = max(0, self.studio_log_path.stat().st_size - 200_000)
            self._clear_studio_log_view()
            tail = _tail_file(self.studio_log_path, max_bytes=200_000)
            if tail:
                self._log_studio(tail.rstrip("\n"))
        except Exception:
            pass

    def _stop_studio_impl(self) -> None:
        if not self.studio_proc or self.studio_proc.poll() is not None:
            self._log("Workshop desktop UI not running.")
            return
        self._log("Stopping Workshop desktop UI...")
        try:
            self.studio_proc.terminate()
        except Exception:
            pass
        time.sleep(0.7)
        if self.studio_proc and self.studio_proc.poll() is None:
            try:
                self.studio_proc.kill()
            except Exception:
                pass
        self.studio_proc = None
        try:
            if self._studio_log_fp and not self._studio_log_fp.closed:
                self._studio_log_fp.flush()
        except Exception:
            pass

    def start_studio(self) -> None:
        self._run_bg("Start Workshop Desktop UI", self._start_studio_impl)

    def restart_studio(self) -> None:
        def work():
            self._log("Restarting Workshop desktop UI...")
            try:
                self._stop_studio_impl()
            except Exception:
                pass
            self._start_studio_impl()
        self._run_bg("Restart Workshop Desktop UI", work)

    def stop_studio(self) -> None:
        self._run_bg("Stop Workshop Desktop UI", self._stop_studio_impl)


if __name__ == "__main__":
    try:
        import tkinter  # noqa: F401
    except Exception as e:
        print("Tkinter not available:", e)
        sys.exit(1)
    app = Launcher()
    app.mainloop()

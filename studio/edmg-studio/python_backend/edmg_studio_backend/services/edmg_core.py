from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

INSTALLER_NAME = "edmg_core_installer.py"


def _is_studio_root(candidate: Path) -> bool:
    return (candidate / "python_backend").exists() and (candidate / "scripts" / INSTALLER_NAME).exists()


def _find_studio_root() -> Path | None:
    env_root = os.getenv("EDMG_STUDIO_REPO_ROOT", "").strip()
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        if _is_studio_root(candidate):
            return candidate
        nested = candidate / "studio" / "edmg-studio"
        if _is_studio_root(nested):
            return nested

    cur = Path(__file__).resolve()
    for parent in cur.parents:
        if _is_studio_root(parent):
            return parent
    return None


def _repo_root() -> Path:
    studio_root = _find_studio_root()
    if studio_root is None:
        raise RuntimeError("Could not locate the Studio support installer for EDMG Core.")
    if studio_root.parent.name == "studio":
        return studio_root.parent.parent
    return studio_root.parent


def _installer_path() -> Path | None:
    studio_root = _find_studio_root()
    if studio_root is None:
        return None
    return studio_root / "scripts" / INSTALLER_NAME


def _core_cache_root(data_dir: Path) -> Path:
    return (data_dir / "cache" / "edmg_core").resolve()


def _try_import_template() -> tuple[bool, Any | None, str | None]:
    try:
        from enhanced_deforum_music_generator.deforum_defaults import make_deforum_settings_template  # type: ignore
        return True, make_deforum_settings_template(), None
    except Exception as e:
        return False, None, str(e)

def core_status() -> dict[str, Any]:
    installer = _installer_path()
    repo_root = _repo_root() if installer is not None else None
    installable = bool(installer and installer.exists())
    try:
        import enhanced_deforum_music_generator  # type: ignore
        ver = getattr(enhanced_deforum_music_generator, "__version__", None)
        return {
            "available": True,
            "version": ver or "unknown",
            "bundled": True,
            "installable": installable,
            "installer_path": str(installer) if installer is not None else None,
            "repo_root": str(repo_root) if repo_root is not None else None,
        }
    except Exception as e:
        hint = (
            "Studio backend installs should bundle EDMG Core by default. Use Studio Setup to repair or reinstall it if this environment is missing Core."
            if installable
            else "This packaged Studio build cannot self-repair EDMG Core because the Studio support installer is not bundled. Reinstall or rebuild Studio if Core is missing."
        )
        return {
            "available": False,
            "error": str(e),
            "bundled": False,
            "installable": installable,
            "installer_path": str(installer) if installer is not None else None,
            "repo_root": str(repo_root) if repo_root is not None else None,
            "hint": hint,
        }

def selfcheck() -> dict[str, Any]:
    # Run as module so it uses the installed package environment
    cmd = [sys.executable, "-m", "enhanced_deforum_music_generator", "selfcheck"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = proc.stdout.strip() or "{}"
    try:
        payload = json.loads(out)
    except Exception:
        payload = {"ok": False, "raw": out, "stderr": proc.stderr[:2000], "returncode": proc.returncode}
    payload["returncode"] = proc.returncode
    return payload

def deforum_template() -> dict[str, Any]:
    ok, templ, err = _try_import_template()
    if ok and isinstance(templ, dict):
        return templ

    # Fallback to subprocess emit JSON
    code = "import json; from enhanced_deforum_music_generator.deforum_defaults import make_deforum_settings_template; print(json.dumps(make_deforum_settings_template()))"
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[:2000] or (err or "EDMG Core not available"))
    return json.loads(proc.stdout)


def install_core(task: Any, data_dir: Path, *, mode: str = "standard", backend: str = "cu124") -> None:
    installer = _installer_path()
    if installer is None or not installer.exists():
        raise RuntimeError("EDMG Core repair installer is not available in this Studio build.")

    cache_root = _core_cache_root(data_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("EDMG_STUDIO_DATA_DIR", str(data_dir))

    cmd = [
        sys.executable,
        str(installer),
        "install",
        "--mode",
        mode,
        "--backend",
        backend,
        "--venv",
        "",
        "--cache-root",
        str(cache_root),
        "--skip-corpora",
        "--skip-models",
        "--skip-whisper",
    ]

    task.progress = 0.05
    task.last_log = "Installing or repairing EDMG Core inside the Studio backend environment…"
    proc = subprocess.Popen(
        cmd,
        cwd=str(installer.parent.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        task.last_log = line.rstrip("\n") or task.last_log
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"EDMG Core install failed (exit={rc})")
    task.progress = 1.0
    task.last_log = "EDMG Core is installed in the Studio backend environment."

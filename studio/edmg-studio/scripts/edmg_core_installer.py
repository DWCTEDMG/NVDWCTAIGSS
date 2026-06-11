#!/usr/bin/env python3
"""Studio-local support installer for the vendored EDMG Core packages.

This script is intentionally internal to EDMG Studio. It is used by Studio's
setup/repair flow to install or repair the vendored EDMG engine packages inside
the backend environment without relying on the retired root-level legacy
installers.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence


STUDIO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = STUDIO_ROOT / "python_backend"
BACKEND_SETUPTOOLS_CONSTRAINT = "setuptools==70.0.0"


def _is_windows() -> bool:
    return os.name == "nt"


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if _is_windows() else "bin/python")


def _resolve_path(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = STUDIO_ROOT / path
    return path.resolve()


def _managed_env(cache_root: Optional[Path]) -> Optional[dict[str, str]]:
    if cache_root is None:
        return None

    cache_root = _resolve_path(cache_root)
    paths = {
        "tmp": cache_root / "tmp",
        "pip": cache_root / "pip",
        "xdg": cache_root / "xdg",
        "hf": cache_root / "huggingface",
        "transformers": cache_root / "transformers",
        "torch": cache_root / "torch",
        "nltk": cache_root / "nltk_data",
        "whisper": cache_root / "whisper",
        "matplotlib": cache_root / "matplotlib",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "EDMG_CACHE_ROOT": str(cache_root),
            "PIP_CACHE_DIR": str(paths["pip"]),
            "XDG_CACHE_HOME": str(paths["xdg"]),
            "HF_HOME": str(paths["hf"]),
            "HUGGINGFACE_HUB_CACHE": str(paths["hf"] / "hub"),
            "TRANSFORMERS_CACHE": str(paths["transformers"]),
            "TORCH_HOME": str(paths["torch"]),
            "NLTK_DATA": str(paths["nltk"]),
            "WHISPER_CACHE_DIR": str(paths["whisper"]),
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": str(paths["matplotlib"]),
            "TMP": str(paths["tmp"]),
            "TEMP": str(paths["tmp"]),
        }
    )
    return env


def _run(cmd: Sequence[str], *, cwd: Optional[Path] = None, env: Optional[dict[str, str]] = None) -> int:
    proc = subprocess.run(list(cmd), cwd=str(cwd) if cwd else None, env=env)
    return int(proc.returncode)


def _backend_env(env: Optional[dict[str, str]] = None) -> dict[str, str]:
    merged = dict(env or os.environ.copy())
    current = merged.get("PYTHONPATH", "").strip()
    backend_str = str(BACKEND_ROOT)
    merged["PYTHONPATH"] = backend_str if not current else os.pathsep.join([backend_str, current])
    return merged


def _pip(py: Path, args: Sequence[str], *, env: Optional[dict[str, str]] = None) -> int:
    return _run([str(py), "-m", "pip", *args], cwd=STUDIO_ROOT, env=env)


def _ensure_venv(venv_dir: Path, *, env: Optional[dict[str, str]] = None) -> Path:
    py = _venv_python(venv_dir)
    if py.exists():
        return py
    print(f"[edmg-core-installer] Creating venv: {venv_dir}")
    if _run([sys.executable, "-m", "venv", str(venv_dir)], cwd=STUDIO_ROOT, env=env) != 0:
        raise RuntimeError("Failed to create venv")
    return _venv_python(venv_dir)


def _torch_index_url(backend: str) -> str:
    backend = backend.strip().lower()
    if backend in {"cu118", "cu121", "cu124"}:
        return f"https://download.pytorch.org/whl/{backend}"
    raise ValueError(f"Unsupported backend: {backend} (use cu118, cu121, cu124)")


def _install_torch(py: Path, backend: str, *, env: Optional[dict[str, str]] = None) -> int:
    url = _torch_index_url(backend)
    print(f"[edmg-core-installer] Installing PyTorch ({backend}) from {url}")
    return _pip(
        py,
        [
            "install",
            "-U",
            "torch",
            "torchvision",
            "torchaudio",
            "--index-url",
            url,
        ],
        env=env,
    )


def _install_whisper_no_deps(py: Path, *, env: Optional[dict[str, str]] = None) -> int:
    return _pip(py, ["install", "--no-deps", "-U", "openai-whisper>=20230314"], env=env)


def _editable_target(mode: str) -> str:
    extras = ["core"]
    if mode == "dev":
        extras.append("test")
    return f"{BACKEND_ROOT}[{','.join(extras)}]"


def _post_install(
    py: Path,
    *,
    skip_corpora: bool,
    skip_models: bool,
    skip_whisper: bool,
    env: Optional[dict[str, str]] = None,
) -> None:
    if not skip_corpora:
        _run(
            [str(py), "-c", "import nltk; nltk.download('punkt', quiet=True); nltk.download('stopwords', quiet=True)"],
            cwd=STUDIO_ROOT,
            env=_backend_env(env),
        )
        _run(
            [
                str(py),
                "-c",
                "import nltk; nltk.download('punkt_tab', quiet=True); nltk.download('vader_lexicon', quiet=True)",
            ],
            cwd=STUDIO_ROOT,
            env=_backend_env(env),
        )
        _run([str(py), "-c", "import spacy; print('spacy ok')"], cwd=STUDIO_ROOT, env=_backend_env(env))

    if not skip_models and not skip_whisper:
        _run(
            [
                str(py),
                "-c",
                (
                    "import importlib.util as u; "
                    "spec=u.find_spec('whisper'); "
                    "print('whisper_installed', bool(spec)); "
                    "import sys; "
                    "sys.exit(0) if not spec else None"
                ),
            ],
            cwd=STUDIO_ROOT,
            env=_backend_env(env),
        )


def install(
    *,
    mode: str,
    backend: str,
    venv: Optional[str],
    cache_root: Optional[str],
    skip_torch: bool,
    skip_corpora: bool,
    skip_models: bool,
    skip_whisper: bool,
) -> int:
    managed_env = _managed_env(_resolve_path(cache_root) if cache_root else None)
    py = Path(sys.executable)
    resolved_venv: Optional[Path] = None
    if venv:
        resolved_venv = _resolve_path(venv)
        py = _ensure_venv(resolved_venv, env=managed_env)

    if _pip(py, ["install", "-U", "pip", BACKEND_SETUPTOOLS_CONSTRAINT, "wheel"], env=managed_env) != 0:
        return 1

    if not skip_torch:
        if _install_torch(py, backend, env=managed_env) != 0:
            return 1

    target = _editable_target(mode)
    print(f"[edmg-core-installer] Installing backend package from: {target}")
    if _pip(py, ["install", "-e", target], env=managed_env) != 0:
        return 1

    if mode in ("full", "dev") and not skip_whisper:
        print("[edmg-core-installer] Installing Whisper (no-deps)")
        if _install_whisper_no_deps(py, env=managed_env) != 0:
            print("[edmg-core-installer] WARNING: Whisper install failed. Continuing.")

    _post_install(
        py,
        skip_corpora=skip_corpora,
        skip_models=skip_models,
        skip_whisper=skip_whisper,
        env=managed_env,
    )

    print("\n[edmg-core-installer] OK")
    if resolved_venv:
        if _is_windows():
            print(f"  Activate: {resolved_venv / 'Scripts' / 'activate'}")
        else:
            print(f"  Activate: source {resolved_venv / 'bin' / 'activate'}")
    if cache_root:
        print(f"  Cache:    {_resolve_path(cache_root)}")
    print("  Verify:   python studio/edmg-studio/scripts/edmg_core_installer.py verify")
    return 0


def verify() -> int:
    code = _run(
        [
            sys.executable,
            "-c",
            "import enhanced_deforum_music_generator as e, deforum_music as d; "
            "print('enhanced_deforum_music_generator:', e.__file__); "
            "print('deforum_music:', d.__file__)",
        ],
        cwd=STUDIO_ROOT,
        env=_backend_env(),
    )
    if code != 0:
        return code

    code = _run(
        [
            sys.executable,
            "-c",
            "from enhanced_deforum_music_generator.deforum_defaults import make_deforum_settings_template; "
            "d=make_deforum_settings_template(); "
            "print('deforum_template_keys', len(d)); "
            "assert 'W' in d and 'H' in d and 'prompts' in d",
        ],
        cwd=STUDIO_ROOT,
        env=_backend_env(),
    )
    return int(code)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="edmg-core-installer")
    sub = parser.add_subparsers(dest="cmd", required=True)

    install_parser = sub.add_parser("install", help="Install or repair the vendored EDMG Core packages")
    install_parser.add_argument("--mode", default="standard", choices=["minimal", "standard", "full", "dev"])
    install_parser.add_argument("--venv", default="", help="Optional venv dir (empty uses current Python)")
    install_parser.add_argument("--cache-root", default="", help="Shared cache root for pip/HF/Torch/Whisper/temp files")
    install_parser.add_argument("--skip-torch", action="store_true", default=False)
    install_parser.add_argument("--backend", default="cu124", choices=["cu118", "cu121", "cu124"])
    install_parser.add_argument("--cuda", action="store_true", default=False, help="Deprecated alias for --backend cu121")
    install_parser.add_argument("--cuda-version", default="", choices=["", "118", "121", "124"], help="Deprecated convenience alias")
    install_parser.add_argument("--skip-corpora", action="store_true", default=False)
    install_parser.add_argument("--skip-models", action="store_true", default=False)
    install_parser.add_argument("--skip-whisper", action="store_true", default=False)

    sub.add_parser("verify", help="Verify key EDMG Core imports and template generation")

    args = parser.parse_args(argv)

    if args.cmd == "install":
        venv = args.venv.strip() if isinstance(args.venv, str) else ""
        venv = venv or None

        backend = str(args.backend)
        if args.cuda_version:
            backend = f"cu{args.cuda_version}"
        if bool(args.cuda) and not args.cuda_version:
            backend = "cu121"

        return install(
            mode=str(args.mode),
            backend=backend,
            venv=venv,
            cache_root=str(args.cache_root).strip() or None,
            skip_torch=bool(args.skip_torch),
            skip_corpora=bool(args.skip_corpora),
            skip_models=bool(args.skip_models),
            skip_whisper=bool(args.skip_whisper),
        )

    if args.cmd == "verify":
        return verify()

    return 2


if __name__ == "__main__":
    raise SystemExit(main())

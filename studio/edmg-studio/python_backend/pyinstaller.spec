# PyInstaller spec for the EDMG Studio backend.
# Build from: studio/edmg-studio/python_backend

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

from pyinstaller_support import ensure_installed_pycparser_compat_modules, ensure_nltk_resources

here = Path(os.getcwd()).resolve()
build_assets_dir = here / "build" / "pyinstaller-support"
nltk_data_dir = build_assets_dir / "nltk_data"
hooks_dir = here / "pyinstaller_hooks"


def safe_collect(collector, *args, **kwargs):
    try:
        return collector(*args, **kwargs)
    except Exception:
        return []


def collect_windows_support_binaries():
    if os.name != "nt":
        return []
    bin_dir = Path(sys.prefix) / "Library" / "bin"
    binaries = []
    for pattern in ("tbb*.dll", "tcmlib*.dll"):
        for candidate in sorted(bin_dir.glob(pattern)):
            binaries.append((str(candidate), "."))
    return binaries


ensure_installed_pycparser_compat_modules()
ensure_nltk_resources(nltk_data_dir)

hidden = []
hidden += collect_submodules("uvicorn")
hidden += collect_submodules("fastapi")
hidden += collect_submodules("pydantic")
hidden += collect_submodules("keyring")
hidden += collect_submodules("yaml")
hidden += collect_submodules("nltk")
hidden += collect_submodules("textblob")
hidden += collect_submodules("spacy")
hidden += safe_collect(collect_submodules, "boto3")
hidden += safe_collect(collect_submodules, "botocore")
hidden += safe_collect(collect_submodules, "s3transfer")
hidden += safe_collect(collect_submodules, "jmespath")
hidden += [
    "matplotlib",
    "matplotlib.pyplot",
    "matplotlib.animation",
    "matplotlib.backends.backend_agg",
    "tensorboard",
    "tzdata",
    "pycparser.lextab",
    "pycparser.yacctab",
]

# Explicit first-party packages used by the backend.
hidden += collect_submodules("edmg_studio_backend")
hidden += collect_submodules("edmg_ai_service")
hidden += collect_submodules("enhanced_deforum_music_generator")
hidden += collect_submodules("deforum_music")
hidden += collect_submodules("edmg")
hidden += collect_submodules("core")
hidden += collect_submodules("config")
hidden += collect_submodules("integrations")

datas = []
datas += safe_collect(collect_data_files, "matplotlib")
datas += safe_collect(collect_data_files, "nltk")
datas += safe_collect(collect_data_files, "tensorboard")
datas += safe_collect(collect_data_files, "tzdata")
datas += safe_collect(copy_metadata, "matplotlib")
datas += safe_collect(copy_metadata, "nltk")
datas += safe_collect(copy_metadata, "tensorboard")
datas += safe_collect(copy_metadata, "tzdata")
datas += safe_collect(copy_metadata, "pycparser")
datas += safe_collect(copy_metadata, "boto3")
datas += safe_collect(copy_metadata, "botocore")
datas += safe_collect(copy_metadata, "s3transfer")
datas += safe_collect(copy_metadata, "jmespath")
if nltk_data_dir.exists():
    datas.append((str(nltk_data_dir), "nltk_data"))

binaries = []
if os.name == "nt":
    binaries += collect_windows_support_binaries()

a = Analysis(
    [str(here / "backend_entry.py")],
    pathex=[str(here)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[str(hooks_dir)],
    hooksconfig={},
    runtime_hooks=[str(here / "pyinstaller_runtime_hook.py")],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="edmg-studio-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

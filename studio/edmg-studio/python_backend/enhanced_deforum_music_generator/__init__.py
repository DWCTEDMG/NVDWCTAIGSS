"""Enhanced Deforum Music Generator (EDMG) — embedded engine.

This package is vendored into EDMG NVIDIA Workshop and used as a backend library.
Standalone UI entrypoints are intentionally not shipped.
"""

from __future__ import annotations

from importlib import import_module

__all__ = ["DeforumMusicGenerator", "AudioAnalysis"]

_public = import_module(".public_api", __name__)

DeforumMusicGenerator = _public.DeforumMusicGenerator
AudioAnalysis = _public.AudioAnalysis

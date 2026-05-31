from __future__ import annotations

import os
from pathlib import Path

from .panel import cue_lines, load_scene_plan_summary

try:
    import carb.settings  # type: ignore
except Exception:
    carb = None  # type: ignore

try:
    import omni.ui as ui  # type: ignore
except Exception:
    ui = None  # type: ignore

try:
    import omni.ext  # type: ignore
except Exception:
    class _ExtBase:
        def on_startup(self, ext_id: str) -> None:
            return None

        def on_shutdown(self) -> None:
            return None

    class _OmniExt:
        IExt = _ExtBase

    class omni:  # type: ignore
        ext = _OmniExt()


def _setting(path: str, default: str = "") -> str:
    try:
        settings = carb.settings.get_settings()  # type: ignore[name-defined]
        value = settings.get(path)
        return str(value or default)
    except Exception:
        return default


def _sample_scene_plan_path() -> str:
    configured = _setting("/edmg/nvidia/sample_scene_plan")
    if configured:
        expanded = os.path.expandvars(configured)
        if "${app}" not in expanded:
            return expanded
    kit_root = Path(__file__).resolve().parents[4]
    return str(kit_root / "sample_projects" / "audio_reactive_stage" / "scene_plan.json")


class EdmgTimelineExtension(omni.ext.IExt):  # type: ignore[name-defined]
    def on_startup(self, ext_id: str) -> None:
        self._ext_id = ext_id
        self._window = None
        self._summary_label = None
        self._cue_label = None
        print("[edmg.timeline] startup")
        self._build_window()
        self._load_sample_timeline()

    def on_shutdown(self) -> None:
        self._window = None
        print("[edmg.timeline] shutdown")

    def _build_window(self) -> None:
        if ui is None:
            return
        try:
            self._window = ui.Window("EDMG Timeline", width=420, height=420)
            with self._window.frame:
                with ui.VStack(spacing=8):
                    ui.Label("NVIDIA scene timeline")
                    self._summary_label = ui.Label("No timeline loaded.")
                    self._cue_label = ui.Label("")
                    ui.Button("Reload Sample Scene Plan", clicked_fn=self._load_sample_timeline)
        except Exception as exc:
            print(f"[edmg.timeline] window unavailable: {exc}")
            self._window = None

    def _set_label(self, attr: str, text: str) -> None:
        label = getattr(self, attr, None)
        if label is not None:
            try:
                label.text = text
            except Exception:
                pass

    def _load_sample_timeline(self) -> None:
        path = _sample_scene_plan_path()
        if not path:
            print("[edmg.timeline] sample_scene_plan setting is empty")
            return
        try:
            summary = load_scene_plan_summary(path)
            cue_text = "\n".join(cue_lines(summary))
            print(f"[edmg.timeline] {summary.status_text}")
            self._set_label("_summary_label", summary.status_text)
            self._set_label("_cue_label", cue_text)
        except Exception as exc:
            message = f"timeline unavailable: {exc}"
            print(f"[edmg.timeline] {message}")
            self._set_label("_summary_label", message)


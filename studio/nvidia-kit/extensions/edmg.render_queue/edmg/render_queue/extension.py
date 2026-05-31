from __future__ import annotations

from .panel import RenderQueueBackendClient, RenderQueueBackendError, summarize_render_queue

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


def _setting(path: str, default: str) -> str:
    try:
        settings = carb.settings.get_settings()  # type: ignore[name-defined]
        value = settings.get(path)
        return str(value or default)
    except Exception:
        return default


class EdmgRenderQueueExtension(omni.ext.IExt):  # type: ignore[name-defined]
    def on_startup(self, ext_id: str) -> None:
        self._ext_id = ext_id
        backend_url = _setting("/edmg/nvidia/backend_url", "http://127.0.0.1:8000")
        self._backend = RenderQueueBackendClient(base_url=backend_url)
        self._window = None
        self._status_label = None
        self._worker_label = None
        self._readiness_label = None
        print(f"[edmg.render_queue] startup backend={backend_url}")
        self._build_window()
        self._refresh()

    def on_shutdown(self) -> None:
        self._window = None
        print("[edmg.render_queue] shutdown")

    def _build_window(self) -> None:
        if ui is None:
            return
        try:
            self._window = ui.Window("EDMG Render Queue", width=420, height=320)
            with self._window.frame:
                with ui.VStack(spacing=8):
                    ui.Label("NVIDIA render queue readiness")
                    self._status_label = ui.Label("Checking backend...")
                    self._worker_label = ui.Label("")
                    self._readiness_label = ui.Label("")
                    ui.Button("Refresh Queue Status", clicked_fn=self._refresh)
        except Exception as exc:
            print(f"[edmg.render_queue] window unavailable: {exc}")
            self._window = None

    def _set_label(self, attr: str, text: str) -> None:
        label = getattr(self, attr, None)
        if label is not None:
            try:
                label.text = text
            except Exception:
                pass

    def _refresh(self) -> None:
        try:
            summary = summarize_render_queue(
                self._backend.worker_status(),
                self._backend.nvidia_diagnostics(),
            )
            print(f"[edmg.render_queue] {summary.status_text}")
            self._set_label("_status_label", summary.status_text)
            self._set_label("_worker_label", summary.worker_text)
            self._set_label("_readiness_label", summary.readiness_text)
        except RenderQueueBackendError as exc:
            message = f"backend unavailable: {exc}"
            print(f"[edmg.render_queue] {message}")
            self._set_label("_status_label", message)


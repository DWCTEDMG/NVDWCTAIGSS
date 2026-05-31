from __future__ import annotations

from .backend_client import BackendClientError, EdmgBackendClient
from .panel import build_generation_payload, summarize_scene_plan_response, write_usda_preview

try:
    import carb.settings  # type: ignore
except Exception:
    carb = None  # type: ignore

try:
    import omni.ui as ui  # type: ignore
except Exception:
    ui = None  # type: ignore

try:
    import omni.usd as omni_usd  # type: ignore
except Exception:
    omni_usd = None  # type: ignore

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


def _model_string(model: object | None, default: str = "") -> str:
    if model is None:
        return default
    for attr in ("get_value_as_string", "as_string"):
        try:
            value = getattr(model, attr)
            return str(value() if callable(value) else value)
        except Exception:
            continue
    return default


class EdmgAiDirectorExtension(omni.ext.IExt):  # type: ignore[name-defined]
    def on_startup(self, ext_id: str) -> None:
        self._ext_id = ext_id
        backend_url = _setting("/edmg/nvidia/backend_url", "http://127.0.0.1:8000")
        self._backend = EdmgBackendClient(base_url=backend_url)
        self._window = None
        self._status_label = None
        self._summary_label = None
        self._project_model = None
        self._title_model = None
        self._duration_model = None
        self._bpm_model = None
        self._max_scenes_model = None
        self._notes_model = None
        self._last_response: dict | None = None
        self._last_preview_path = None
        print(f"[edmg.ai_director] startup backend={backend_url}")
        self._build_window()
        try:
            status = self._backend.nvidia_status()
            nvidia = status.get("nvidia") if isinstance(status.get("nvidia"), dict) else {}
            self._set_status(
                "[edmg.ai_director] NVIDIA "
                f"enabled={nvidia.get('enabled')} profile={nvidia.get('profile') or 'omniverse'}"
            )
        except BackendClientError as exc:
            self._set_status(f"[edmg.ai_director] backend status unavailable: {exc}")

    def on_shutdown(self) -> None:
        self._window = None
        self._last_response = None
        self._last_preview_path = None
        print("[edmg.ai_director] shutdown")

    def _build_window(self) -> None:
        if ui is None:
            return
        try:
            self._project_model = ui.SimpleStringModel(_setting("/edmg/nvidia/project_id", ""))
            self._title_model = ui.SimpleStringModel("EDMG NVIDIA Scene Plan")
            self._duration_model = ui.SimpleStringModel("60")
            self._bpm_model = ui.SimpleStringModel("")
            self._max_scenes_model = ui.SimpleStringModel("6")
            self._notes_model = ui.SimpleStringModel("")
            self._window = ui.Window("EDMG AI Director", width=460, height=560)
            with self._window.frame:
                with ui.VStack(spacing=8):
                    ui.Label("NVIDIA / OpenUSD handoff")
                    self._status_label = ui.Label("Checking backend...")
                    self._summary_label = ui.Label("No scene plan loaded.")
                    ui.Button("Refresh NVIDIA Status", clicked_fn=self._refresh_status)
                    ui.Label("Project ID")
                    ui.StringField(model=self._project_model)
                    ui.Label("Title")
                    ui.StringField(model=self._title_model)
                    with ui.HStack(spacing=6):
                        with ui.VStack(spacing=4):
                            ui.Label("Duration seconds")
                            ui.StringField(model=self._duration_model)
                        with ui.VStack(spacing=4):
                            ui.Label("BPM")
                            ui.StringField(model=self._bpm_model)
                        with ui.VStack(spacing=4):
                            ui.Label("Max scenes")
                            ui.StringField(model=self._max_scenes_model)
                    ui.Label("Director notes")
                    ui.StringField(model=self._notes_model)
                    with ui.HStack(spacing=6):
                        ui.Button("Fetch Project Plan", clicked_fn=self._fetch_project_plan)
                        ui.Button("Generate Plan", clicked_fn=self._generate_scene_plan)
                    with ui.HStack(spacing=6):
                        ui.Button("Export USDA", clicked_fn=self._export_project_scene_plan)
                        ui.Button("Open Preview", clicked_fn=self._open_current_preview)
        except Exception as exc:
            print(f"[edmg.ai_director] window unavailable: {exc}")
            self._window = None

    def _set_status(self, text: str) -> None:
        print(text)
        if self._status_label is not None:
            try:
                self._status_label.text = text
            except Exception:
                pass

    def _set_summary(self, text: str) -> None:
        print(f"[edmg.ai_director] {text}")
        if self._summary_label is not None:
            try:
                self._summary_label.text = text
            except Exception:
                pass

    def _project_id(self) -> str:
        return _model_string(self._project_model).strip()

    def _store_response(self, response: dict) -> None:
        self._last_response = response
        summary = summarize_scene_plan_response(response)
        self._set_summary(summary.status_text)
        try:
            self._last_preview_path = write_usda_preview(response)
            self._set_status(f"USDA preview ready: {self._last_preview_path}")
        except Exception as exc:
            self._set_status(f"USDA preview unavailable: {exc}")

    def _refresh_status(self) -> None:
        try:
            status = self._backend.nvidia_status()
            nvidia = status.get("nvidia") if isinstance(status.get("nvidia"), dict) else {}
            self._set_status(
                "NVIDIA "
                f"enabled={nvidia.get('enabled')} profile={nvidia.get('profile') or 'omniverse'}"
            )
        except BackendClientError as exc:
            self._set_status(f"backend status unavailable: {exc}")

    def _fetch_project_plan(self) -> None:
        project_id = self._project_id()
        if not project_id:
            self._set_status("Project ID is required to fetch a persisted scene plan.")
            return
        try:
            self._store_response(self._backend.project_scene_plan(project_id))
        except BackendClientError as exc:
            self._set_status(f"project scene plan unavailable: {exc}")

    def _generate_scene_plan(self) -> None:
        payload = build_generation_payload(
            title=_model_string(self._title_model, "EDMG NVIDIA Scene Plan"),
            duration_s=_model_string(self._duration_model, "60"),
            bpm=_model_string(self._bpm_model),
            max_scenes=_model_string(self._max_scenes_model, "6"),
            notes=_model_string(self._notes_model),
        )
        project_id = self._project_id()
        try:
            response = (
                self._backend.generate_project_scene_plan(project_id, payload)
                if project_id
                else self._backend.generate_scene_plan(payload)
            )
            self._store_response(response)
        except BackendClientError as exc:
            self._set_status(f"NVIDIA planner unavailable: {exc}")

    def _export_project_scene_plan(self) -> None:
        project_id = self._project_id()
        if not project_id:
            self._set_status("Project ID is required to export a project USDA.")
            return
        try:
            response = self._backend.export_project_scene_plan(project_id)
            summary = summarize_scene_plan_response(response)
            self._set_summary(summary.status_text)
            self._set_status(f"Exported project USDA: {summary.export_path or 'backend output'}")
        except BackendClientError as exc:
            self._set_status(f"project USDA export failed: {exc}")

    def _open_current_preview(self) -> None:
        if self._last_response is None:
            self._fetch_project_plan()
        if self._last_response is None:
            return
        try:
            self._last_preview_path = write_usda_preview(self._last_response)
        except Exception as exc:
            self._set_status(f"USDA preview unavailable: {exc}")
            return
        if omni_usd is None:
            self._set_status(f"OpenUSD preview written: {self._last_preview_path}")
            return
        try:
            context = omni_usd.get_context()
            context.open_stage(str(self._last_preview_path))
            self._set_status(f"Opened OpenUSD preview: {self._last_preview_path}")
        except Exception as exc:
            self._set_status(f"could not open OpenUSD preview: {exc}")

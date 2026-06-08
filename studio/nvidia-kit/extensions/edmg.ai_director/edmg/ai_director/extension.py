from __future__ import annotations

from .backend_client import BackendClientError, EdmgBackendClient
from .panel import (
    OPTIONAL_SERVICE_IDS,
    build_generation_payload,
    build_optional_service_payload,
    build_render_job_payload,
    build_storyboard_movie_payload,
    default_optional_service_payload_text,
    normalize_optional_service_id,
    project_id_from_response,
    resolve_project_reference,
    summarize_analysis_response,
    summarize_audio_upload_response,
    summarize_nvidia_diagnostics_response,
    summarize_nvidia_services_response,
    summarize_optional_service_response,
    summarize_project_response,
    summarize_render_job_response,
    summarize_riva_transcript_response,
    summarize_scene_plan_response,
    summarize_storyboard_movie_response,
    write_usda_preview,
)

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
        self._setup_label = None
        self._project_label = None
        self._audio_label = None
        self._analysis_label = None
        self._render_label = None
        self._project_model = None
        self._project_name_model = None
        self._audio_path_model = None
        self._title_model = None
        self._duration_model = None
        self._bpm_model = None
        self._max_scenes_model = None
        self._notes_model = None
        self._render_fps_model = None
        self._render_width_model = None
        self._render_height_model = None
        self._service_model = None
        self._service_payload_model = None
        self._service_label = None
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
            self._project_name_model = ui.SimpleStringModel("Hush Now NVIDIA Studio")
            self._audio_path_model = ui.SimpleStringModel(_setting("/edmg/nvidia/audio_path", ""))
            self._title_model = ui.SimpleStringModel("EDMG NVIDIA Scene Plan")
            self._duration_model = ui.SimpleStringModel("60")
            self._bpm_model = ui.SimpleStringModel("")
            self._max_scenes_model = ui.SimpleStringModel("6")
            self._notes_model = ui.SimpleStringModel("")
            self._render_fps_model = ui.SimpleStringModel("24")
            self._render_width_model = ui.SimpleStringModel("1920")
            self._render_height_model = ui.SimpleStringModel("1080")
            self._service_model = ui.SimpleStringModel("cosmos")
            self._service_payload_model = ui.SimpleStringModel(default_optional_service_payload_text("cosmos"))
            self._window = ui.Window("EDMG NVIDIA Studio", width=640, height=860)
            with self._window.frame:
                with ui.VStack(spacing=8):
                    ui.Label("EDMG NVIDIA Studio")
                    self._status_label = ui.Label("Checking backend...")
                    self._setup_label = ui.Label("Setup check not run.")
                    with ui.HStack(spacing=6):
                        ui.Button("Refresh Setup", clicked_fn=self._refresh_setup)
                        ui.Button("Refresh NVIDIA Status", clicked_fn=self._refresh_status)

                    ui.Label("Project")
                    self._project_label = ui.Label("No project loaded.")
                    ui.Label("Project name")
                    ui.StringField(model=self._project_name_model)
                    ui.Label("Project ID or name")
                    ui.StringField(model=self._project_model)
                    with ui.HStack(spacing=6):
                        ui.Button("Create Project", clicked_fn=self._create_project)
                        ui.Button("Load Project", clicked_fn=self._load_project)

                    ui.Label("Audio")
                    self._audio_label = ui.Label("No audio uploaded.")
                    ui.Label("Audio file path")
                    ui.StringField(model=self._audio_path_model)
                    with ui.HStack(spacing=6):
                        ui.Button("Upload Audio", clicked_fn=self._upload_audio)
                        ui.Button("Analyze Audio", clicked_fn=self._analyze_audio)
                        ui.Button("Riva Transcript", clicked_fn=self._transcribe_audio_with_riva)

                    ui.Label("Storyboard and USD")
                    self._analysis_label = ui.Label("No audio analysis or transcript loaded.")
                    self._summary_label = ui.Label("No scene plan loaded.")
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
                        ui.Button("Generate Storyboard", clicked_fn=self._generate_scene_plan)
                    with ui.HStack(spacing=6):
                        ui.Button("Export USDA", clicked_fn=self._export_project_scene_plan)
                        ui.Button("Open Preview", clicked_fn=self._open_current_preview)

                    ui.Label("Render and Movie Output")
                    self._render_label = ui.Label("No NVIDIA render or storyboard movie queued.")
                    with ui.HStack(spacing=6):
                        with ui.VStack(spacing=4):
                            ui.Label("FPS")
                            ui.StringField(model=self._render_fps_model)
                        with ui.VStack(spacing=4):
                            ui.Label("Width")
                            ui.StringField(model=self._render_width_model)
                        with ui.VStack(spacing=4):
                            ui.Label("Height")
                            ui.StringField(model=self._render_height_model)
                    with ui.HStack(spacing=6):
                        ui.Button("Queue AI Storyboard Movie", clicked_fn=self._queue_storyboard_movie)
                        ui.Button("Queue RTX Render Job", clicked_fn=self._queue_render_job)

                    ui.Label("Advanced NVIDIA service bridge")
                    self._service_label = ui.Label("No service call yet.")
                    ui.Label("Service ID")
                    ui.StringField(model=self._service_model)
                    ui.Label("Service JSON payload")
                    ui.StringField(model=self._service_payload_model)
                    with ui.HStack(spacing=6):
                        ui.Button("Refresh Services", clicked_fn=self._refresh_services)
                        ui.Button("Load Service Default", clicked_fn=self._load_service_default)
                        ui.Button("Invoke Typed Service", clicked_fn=self._invoke_optional_service)
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

    def _set_setup_summary(self, text: str) -> None:
        print(f"[edmg.ai_director] {text}")
        if self._setup_label is not None:
            try:
                self._setup_label.text = text
            except Exception:
                pass

    def _set_project_summary(self, text: str) -> None:
        print(f"[edmg.ai_director] {text}")
        if self._project_label is not None:
            try:
                self._project_label.text = text
            except Exception:
                pass

    def _set_audio_summary(self, text: str) -> None:
        print(f"[edmg.ai_director] {text}")
        if self._audio_label is not None:
            try:
                self._audio_label.text = text
            except Exception:
                pass

    def _set_analysis_summary(self, text: str) -> None:
        print(f"[edmg.ai_director] {text}")
        if self._analysis_label is not None:
            try:
                self._analysis_label.text = text
            except Exception:
                pass

    def _set_render_summary(self, text: str) -> None:
        print(f"[edmg.ai_director] {text}")
        if self._render_label is not None:
            try:
                self._render_label.text = text
            except Exception:
                pass

    def _set_service_summary(self, text: str) -> None:
        print(f"[edmg.ai_director] {text}")
        if self._service_label is not None:
            try:
                self._service_label.text = text
            except Exception:
                pass

    def _project_id(self) -> str:
        return _model_string(self._project_model).strip()

    def _project_name(self) -> str:
        return _model_string(self._project_name_model).strip()

    def _audio_path(self) -> str:
        return _model_string(self._audio_path_model).strip()

    def _set_project_id(self, project_id: str) -> None:
        try:
            self._project_model.set_value(project_id)
        except Exception:
            pass

    def _set_title_if_blank(self, title: str) -> None:
        if not title.strip() or _model_string(self._title_model).strip():
            return
        try:
            self._title_model.set_value(title.strip())
        except Exception:
            pass

    def _service_id(self) -> str:
        return normalize_optional_service_id(_model_string(self._service_model, "cosmos"))

    def _set_service_id(self, service_id: str) -> None:
        try:
            self._service_model.set_value(service_id)
        except Exception:
            pass

    def _set_service_payload(self, payload_text: str) -> None:
        try:
            self._service_payload_model.set_value(payload_text)
        except Exception:
            pass

    def _resolve_project_id(self, *, required: bool, action: str) -> str:
        reference = self._project_id()
        if not reference:
            if required:
                self._set_status(f"Project ID or name is required to {action}.")
            return ""
        try:
            resolved = resolve_project_reference(reference, self._backend.projects())
        except BackendClientError as exc:
            self._set_status(f"project list unavailable; using `{reference}` directly: {exc}")
            return reference
        if resolved and resolved != reference:
            self._set_project_id(resolved)
            self._set_status(f"Resolved project `{reference}` to id `{resolved}`.")
        return resolved or reference

    def _refresh_setup(self) -> None:
        try:
            diagnostics = self._backend.nvidia_diagnostics()
            self._set_setup_summary(summarize_nvidia_diagnostics_response(diagnostics))
        except Exception as exc:
            self._set_setup_summary(f"NVIDIA diagnostics unavailable: {exc}")
        try:
            summary = summarize_nvidia_services_response(self._backend.nvidia_services())
            self._set_service_summary(summary.status_text)
        except Exception as exc:
            self._set_service_summary(f"NVIDIA services unavailable: {exc}")

    def _create_project(self) -> None:
        name = self._project_name() or _model_string(self._title_model).strip() or "Untitled NVIDIA Studio Project"
        try:
            response = self._backend.create_project(name)
            summary = summarize_project_response(response)
            if summary.project_id:
                self._set_project_id(summary.project_id)
            self._set_project_summary(summary.status_text)
            self._set_status(f"Created project: {summary.project_id or summary.name or name}")
        except BackendClientError as exc:
            self._set_project_summary(f"project create failed: {exc}")

    def _load_project(self) -> None:
        project_id = self._resolve_project_id(required=True, action="load a project")
        if not project_id:
            return
        try:
            response = self._backend.project(project_id)
        except BackendClientError as exc:
            self._set_project_summary(f"project load failed: {exc}")
            return
        summary = summarize_project_response(response)
        if summary.project_id:
            self._set_project_id(summary.project_id)
        self._set_project_summary(summary.status_text)
        project = response.get("project") if isinstance(response.get("project"), dict) else {}
        meta = project.get("meta") if isinstance(project.get("meta"), dict) else {}
        audio = meta.get("audio") if isinstance(meta.get("audio"), dict) else {}
        if audio.get("filename"):
            self._set_audio_summary(f"Audio loaded: {audio.get('filename')}")
        analysis = meta.get("analysis") if isinstance(meta.get("analysis"), dict) else {}
        if analysis:
            self._set_analysis_summary(summarize_analysis_response({"analysis": analysis}))
            self._apply_analysis_defaults({"analysis": analysis})

    def _upload_audio(self) -> None:
        project_id = self._resolve_project_id(required=True, action="upload audio")
        if not project_id:
            return
        audio_path = self._audio_path()
        if not audio_path:
            self._set_audio_summary("Audio file path is required.")
            return
        try:
            response = self._backend.upload_project_audio(project_id, audio_path)
            self._set_audio_summary(summarize_audio_upload_response(response))
        except BackendClientError as exc:
            self._set_audio_summary(f"audio upload failed: {exc}")

    def _apply_analysis_defaults(self, response: dict) -> None:
        analysis = response.get("analysis") if isinstance(response.get("analysis"), dict) else {}
        features = analysis.get("features") if isinstance(analysis.get("features"), dict) else {}
        duration = features.get("duration_s") or features.get("duration") or analysis.get("duration_s")
        bpm = features.get("bpm") or features.get("tempo_bpm") or analysis.get("bpm")
        try:
            duration_value = float(duration)
            if duration_value > 0 and self._duration_model is not None:
                self._duration_model.set_value(f"{duration_value:g}")
        except Exception:
            pass
        try:
            bpm_value = float(bpm)
            if bpm_value > 0 and self._bpm_model is not None:
                self._bpm_model.set_value(f"{bpm_value:g}")
        except Exception:
            pass

    def _analyze_audio(self) -> None:
        project_id = self._resolve_project_id(required=True, action="analyze audio")
        if not project_id:
            return
        try:
            response = self._backend.analyze_project_audio(project_id)
        except BackendClientError as exc:
            self._set_analysis_summary(f"audio analysis failed: {exc}")
            return
        self._apply_analysis_defaults(response)
        self._set_analysis_summary(summarize_analysis_response(response))

    def _transcribe_audio_with_riva(self) -> None:
        project_id = self._resolve_project_id(required=True, action="transcribe project audio")
        if not project_id:
            return
        try:
            response = self._backend.transcribe_project_audio_riva(project_id, {})
            self._set_analysis_summary(summarize_riva_transcript_response(response))
        except BackendClientError as exc:
            self._set_analysis_summary(f"Riva transcription failed: {exc}")

    def _store_response(self, response: dict) -> None:
        self._last_response = response
        response_project_id = project_id_from_response(response)
        if response_project_id:
            self._set_project_id(response_project_id)
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

    def _refresh_services(self) -> None:
        try:
            summary = summarize_nvidia_services_response(self._backend.nvidia_services())
            self._set_service_summary(summary.status_text)
        except BackendClientError as exc:
            self._set_service_summary(f"NVIDIA services unavailable: {exc}")

    def _load_service_default(self) -> None:
        service_id = self._service_id()
        if not service_id:
            self._set_service_summary(f"Unsupported NVIDIA service id. Use one of: {', '.join(OPTIONAL_SERVICE_IDS)}")
            return
        self._set_service_id(service_id)
        try:
            self._set_service_payload(default_optional_service_payload_text(service_id))
        except ValueError as exc:
            self._set_service_summary(str(exc))
            return
        self._set_service_summary(f"Loaded {service_id} typed payload default.")

    def _invoke_optional_service(self) -> None:
        service_id = self._service_id()
        if not service_id:
            self._set_service_summary(f"Unsupported NVIDIA service id. Use one of: {', '.join(OPTIONAL_SERVICE_IDS)}")
            return
        self._set_service_id(service_id)
        try:
            payload = build_optional_service_payload(service_id, _model_string(self._service_payload_model))
        except ValueError as exc:
            self._set_service_summary(str(exc))
            return
        try:
            response = self._backend.invoke_typed_nvidia_service(service_id, payload)
            self._set_service_summary(summarize_optional_service_response(service_id, response))
        except BackendClientError as exc:
            self._set_service_summary(f"{service_id} invocation failed: {exc}")

    def _fetch_project_plan(self) -> None:
        project_id = self._resolve_project_id(required=True, action="fetch a persisted scene plan")
        if not project_id:
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
        project_id = self._resolve_project_id(required=False, action="generate a project scene plan")
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
        project_id = self._resolve_project_id(required=True, action="export a project USDA")
        if not project_id:
            return
        try:
            response = self._backend.export_project_scene_plan(project_id)
            summary = summarize_scene_plan_response(response)
            self._set_summary(summary.status_text)
            self._set_status(f"Exported project USDA: {summary.export_path or 'backend output'}")
        except BackendClientError as exc:
            self._set_status(f"project USDA export failed: {exc}")

    def _queue_render_job(self) -> None:
        project_id = self._resolve_project_id(required=True, action="queue an RTX render job")
        if not project_id:
            return
        payload = build_render_job_payload(
            fps=_model_string(self._render_fps_model, "24"),
            width=_model_string(self._render_width_model, "1920"),
            height=_model_string(self._render_height_model, "1080"),
        )
        try:
            export_response = self._backend.export_project_scene_plan(project_id)
            self._set_summary(summarize_scene_plan_response(export_response).status_text)
            response = self._backend.create_project_render_job(project_id, payload)
            self._set_render_summary(summarize_render_job_response(response))
        except BackendClientError as exc:
            self._set_render_summary(f"RTX render job failed: {exc}")

    def _queue_storyboard_movie(self) -> None:
        project_id = self._resolve_project_id(required=True, action="queue an AI storyboard movie")
        if not project_id:
            return
        payload = build_storyboard_movie_payload(
            fps=_model_string(self._render_fps_model, "24"),
            width=_model_string(self._render_width_model, "1920"),
            height=_model_string(self._render_height_model, "1080"),
        )
        try:
            response = self._backend.create_project_storyboard_movie(project_id, payload)
            self._set_render_summary(summarize_storyboard_movie_response(response))
        except BackendClientError as exc:
            self._set_render_summary(f"storyboard movie failed: {exc}")

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

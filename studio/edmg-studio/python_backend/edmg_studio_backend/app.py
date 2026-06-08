from __future__ import annotations

import os
import platform
import mimetypes
import time
import zipfile
import json
import hashlib
import shutil
import subprocess
from copy import deepcopy
import math
import re
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi import Request

try:
    import python_multipart as _multipart  # type: ignore
    HAS_MULTIPART = True
except Exception:
    try:
        import multipart as _multipart  # type: ignore
        HAS_MULTIPART = True
    except Exception:
        _multipart = None
        HAS_MULTIPART = False

try:
    from PIL import Image, ImageFilter, ImageOps  # type: ignore
except Exception:
    Image = None  # type: ignore
    ImageFilter = None  # type: ignore
    ImageOps = None  # type: ignore

from .config import Settings
from .schemas import (
    HealthResponse, ProjectCreateRequest, PlanRequest, ApplyPlanRequest,
    RenderScenesRequest, RenderMotionRequest, AssembleVideoRequest, InternalVideoRenderRequest, TimelineUpdateRequest,
    StoryboardMovieRenderRequest,
    CreativeDirectionApplyRequest, PlannerLabImportRequest, ReactiveLabApplyRequest, ExportDeforumRequest, ExportUnrealBridgeRequest,
    ImportUnrealBridgeReturnRequest,
    BuildUnrealImportPlanRequest,
    StoryboardVariantUpdateRequest,
    CloudAwsTestRequest, CloudAwsBundleRequest, CloudAzureTestRequest, CloudLightningBundleRequest,
    ProjectSnapshot, RenderConductorPlanRequest, RenderIntent, VisualDNAFeedbackRequest,
    UnrealBridgePreviewResponse,
)
from .store.projects import ProjectStore
from .store.jobs import JobStore
from .services.ai_client import build_ai_client
from .services.edmg_core import (
    core_status,
    deforum_template as edmg_deforum_template,
    install_core as edmg_install_core,
    selfcheck as edmg_selfcheck,
)
from .integrations import comfyui as comfy
from .integrations.comfyui_pool import ComfyUINodePool
from .services.worker_manager import WorkerManager
from .services.ffmpeg import assemble_slideshow, assemble_image_sequence, concat_videos, interpolate_video_fps, mux_audio
from .services.internal_video import (
    InternalVideoSettings,
    _scene_keyframe_times,
    describe_internal_render_cache,
    describe_proxy_render_cache,
    render_internal_still_image,
    render_internal_video_variant,
    render_internal_proxy_video_variant,
    render_stability_hosted_video_variant,
    render_internal_diffusion_preview_segment,
)
from .services.compositor import apply_timeline_layers
from .integrations import aws as aws_integration
from .integrations import azure as azure_integration
from .integrations import lightning as lightning_integration
from .utils.path import safe_join
from .errors import UserFacingError, hint_from_exception
from .services.model_manager import ModelManager
from .services.secrets import SecretStore
from .services.render_settings import (
    RenderSettingsStore,
    STABILITY_SD3_MODELS,
    STABILITY_SERVICES,
    STABILITY_STYLE_PRESETS,
)
from .services.nvidia_profile import nvidia_profile_status
from .services.nvidia_diagnostics import nvidia_diagnostics, nvidia_host_gpu_status
from .services.nvidia_optional_services import (
    NvidiaOptionalServiceError,
    ace_generate,
    audio2face_generate,
    cosmos_infer,
    invoke_nvidia_service,
    normalize_nvidia_service_id,
    triton_infer,
    triton_shot_features,
)
from .services.nvidia_riva import RivaTranscriptionError, riva_asr_status, transcribe_with_riva
from .services.nvidia_render_job import build_rtx_render_job
from .services.nvidia_scene_plan import (
    normalize_scene_plan,
    plan_response_to_scene_plan,
    scene_plan_usd_metadata,
    scene_plan_usda_text,
    validate_scene_plan,
)
from .services.workbench_bridge import (
    build_unreal_bridge_export_payloads,
    build_unreal_bridge_preview,
    merge_reactive_lab_into_timeline,
    planner_lab_to_canonical_plan,
    planner_lab_to_project_analysis,
)
from .services.unreal_bridge_consumer import (
    build_unreal_sequence_import_plan,
    write_unreal_sequence_import_plan,
)
from .services.visual_dna import (
    build_prompt_hints as build_visual_dna_prompt_hints,
    ingest_planner_payload as ingest_visual_dna_planner_payload,
    ingest_reactive_payload as ingest_visual_dna_reactive_payload,
    load_visual_dna,
    record_render_feedback as record_visual_dna_feedback,
    save_visual_dna,
)
from .render_conductor.planner import build_advisory_render_plan
from .services.setup_wizard import (
    SetupTaskManager,
    check_backend_bundle,
    check_ollama,
    download_and_install_ollama,
    pull_ollama_model,
    download_and_extract_portable,
    ComfyPortableProcess,
    OllamaManagedProcess,
    check_ffmpeg,
    comfy_portable_installed,
    comfy_portable_root,
    download_and_install_7zip,
    install_backend_bundle,
    _find_ollama_exe,
    _find_7z_exe,
    managed_ollama_launch_script_path,
)

settings = Settings()


class JobCanceled(Exception):
    """Raised when a running job is canceled and should stop promptly."""


settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.models_dir.mkdir(parents=True, exist_ok=True)
settings.cache_dir.mkdir(parents=True, exist_ok=True)
settings.logs_dir.mkdir(parents=True, exist_ok=True)
settings.external_dir.mkdir(parents=True, exist_ok=True)
settings.ollama_models_dir.mkdir(parents=True, exist_ok=True)

store = ProjectStore(settings.data_dir)
jobs = JobStore(store.projects_dir)

# Multi-node ComfyUI pool (supports EDMG_COMFYUI_URLS)
comfy_pool = ComfyUINodePool(settings.load_comfyui_nodes(), default_max_inflight=settings.comfyui_node_concurrency)

# Always-on worker manager
worker = None  # set after _execute_job is defined
ai = build_ai_client(settings.ai_mode, settings.ai_base_url, settings.ai_timeout_s)

setup_tasks = SetupTaskManager()
secrets = SecretStore(settings.data_dir)
render_settings = RenderSettingsStore(settings.data_dir)
models = ModelManager(
    settings.data_dir,
    settings.models_dir,
    settings.external_dir,
    settings.comfyui_url,
    os.getenv('EDMG_AI_OLLAMA_URL','http://127.0.0.1:11434'),
    secrets=secrets,
)

comfy_portable = ComfyPortableProcess()
ollama_managed = OllamaManagedProcess()

@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    if settings.worker_autostart:
        worker.start()
    try:
        yield
    finally:
        try:
            worker.stop()
        except Exception:
            pass


app = FastAPI(title="EDMG Studio Backend", version="1.1.0", lifespan=_app_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(UserFacingError)
async def _user_facing_error(_req: Request, exc: UserFacingError):
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.to_dict()})


@app.exception_handler(HTTPException)
async def _http_exception(_req: Request, exc: HTTPException):
    msg = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    hint = hint_from_exception(Exception(msg))
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": {"message": msg, "hint": hint, "code": "HTTP_ERROR"}})


@app.exception_handler(Exception)
async def _unhandled_exception(_req: Request, exc: Exception):
    msg = str(exc) or "Internal error"
    hint = hint_from_exception(exc) or "Open Render Queue → Log for details, then retry."
    return JSONResponse(status_code=500, content={"ok": False, "error": {"message": msg, "hint": hint, "code": "INTERNAL"}})


def _require_multipart() -> None:
    if not HAS_MULTIPART:
        raise UserFacingError(
            "File upload support is unavailable because python-multipart is not installed.",
            hint="Install backend dependencies with `pip install -e .` or add `python-multipart`, then restart EDMG Studio.",
            code="MISSING_MULTIPART",
            status_code=503,
        )


def _stable_seed(project_id: str, variant_index: int, scene_index: int) -> int:
    h = hashlib.md5(f"{project_id}:{variant_index}:{scene_index}".encode("utf-8")).hexdigest()[:8]
    return int(h, 16)


def _analysis_duration_s(analysis: Any) -> float | None:
    if not isinstance(analysis, dict):
        return None
    features = analysis.get("features") if isinstance(analysis.get("features"), dict) else {}
    candidates = (
        analysis.get("duration_s"),
        analysis.get("duration"),
        features.get("duration_s"),
        features.get("duration"),
    )
    for candidate in candidates:
        try:
            value = float(candidate)
        except Exception:
            continue
        if value > 0:
            return value
    return None


def _resolved_project_duration_s(proj: Any, variant: dict[str, Any], scenes: list[dict[str, Any]]) -> float:
    analysis_duration = _analysis_duration_s(getattr(proj, "meta", {}).get("analysis"))
    if analysis_duration:
        return float(analysis_duration)
    if variant.get("duration_s"):
        return float(variant.get("duration_s") or 0.0)
    if scenes:
        return float(scenes[-1].get("end_s") or 60.0)
    return 60.0

@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(ok=True)


def _request_payload(model: Any) -> dict[str, Any]:
    dump = getattr(model, "model_dump", None)
    if callable(dump):
        return dump()
    legacy = getattr(model, "dict", None)
    if callable(legacy):
        return legacy()
    raise TypeError(f"Object {type(model)!r} is not a supported request model")


def _catalog_entry(model_id: str | None) -> dict[str, Any] | None:
    if not model_id:
        return None
    catalog_payload = models.catalog()
    all_entries = list(catalog_payload.get("catalog") or []) + list(catalog_payload.get("user") or [])
    return next((e for e in all_entries if isinstance(e, dict) and e.get("id") == model_id), None)


def _catalog_render_metadata(entry: dict[str, Any] | None) -> dict[str, Any]:
    render = (entry or {}).get("render") or {}
    return render if isinstance(render, dict) else {}


def _catalog_entry_engine(entry: dict[str, Any] | None) -> str:
    render = _catalog_render_metadata(entry)
    target = (entry or {}).get("target") or {}
    if not isinstance(target, dict):
        target = {}
    engine = str((entry or {}).get("engine") or render.get("engine") or target.get("engine") or "comfyui").strip().lower()
    return engine or "comfyui"


def _catalog_entry_family(entry: dict[str, Any] | None) -> str | None:
    render = _catalog_render_metadata(entry)
    family = str((entry or {}).get("family") or render.get("family") or "").strip().lower()
    return family or None


def _catalog_supports_workflow(entry: dict[str, Any] | None, workflow_family: str) -> bool:
    family = str(workflow_family or "txt2img").strip().lower()
    if family == "auto":
        family = "txt2img"
    if family == "txt2img":
        return bool((entry or {}).get("supports_txt2img", True))
    if family == "img2img":
        return bool((entry or {}).get("supports_img2img", False))
    if family == "inpaint":
        return bool((entry or {}).get("supports_inpaint", False))
    if family == "outpaint":
        return bool((entry or {}).get("supports_outpaint", False))
    if family == "controlnet":
        return bool((entry or {}).get("supports_controlnet", False))
    return False


def _safe_name_tag(value: str | None, fallback: str = "default") -> str:
    raw = str(value or "").strip()
    if not raw:
        raw = fallback
    tag = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()
    return tag[:32] or fallback


def _extract_comfy_checkpoint_names(object_info: dict[str, Any] | None) -> list[str]:
    info = object_info or {}
    loader = info.get("CheckpointLoaderSimple")
    if not isinstance(loader, dict):
        return []
    input_info = loader.get("input")
    if not isinstance(input_info, dict):
        return []
    required = input_info.get("required")
    if not isinstance(required, dict):
        return []
    ckpt_field = required.get("ckpt_name")
    if not isinstance(ckpt_field, list) or not ckpt_field:
        return []
    options = ckpt_field[0]
    if not isinstance(options, list):
        return []
    return [str(item).strip() for item in options if str(item).strip()]


def _resolve_comfy_checkpoint_name(
    preferred: str | None,
    *,
    allow_auto_fallback: bool,
) -> tuple[str, str | None]:
    requested = str(preferred or settings.comfyui_checkpoint or "").strip()
    available: list[str] = []
    for url in settings.resolved_comfyui_urls():
        try:
            names = _extract_comfy_checkpoint_names(comfy.get_object_info(url))
        except Exception:
            continue
        for name in names:
            if name not in available:
                available.append(name)
    if requested and requested in available:
        return requested, None
    if allow_auto_fallback and available:
        fallback = available[0]
        if fallback != requested:
            return fallback, requested or None
        return fallback, None
    return requested, None


def _catalog_comfy_asset_filename(
    entry: dict[str, Any] | None,
    *,
    folder: str,
    allowed_kinds: set[str] | None = None,
) -> str:
    if not isinstance(entry, dict) or not entry.get("id"):
        return ""
    target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
    engine = str(target.get("engine") or entry.get("engine") or "comfyui").strip().lower()
    target_folder = str(target.get("folder") or "checkpoints").strip().lower()
    if engine != "comfyui" or target_folder != str(folder or "").strip().lower():
        return ""
    asset = models.resolve_comfy_asset(str(entry.get("id") or ""), folder=folder, allowed_kinds=allowed_kinds)
    return str(asset.get("filename") or entry.get("filename") or "").strip()


def _resolve_comfy_still_selection(
    *,
    model_id: str | None,
    checkpoint: str | None,
    workflow_family: str | None,
    controlnet_model: str | None,
    reference_asset: str | None,
    conditioning_mode: str | None,
    controlnet_units: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entry = _catalog_entry(model_id)
    entry_data = entry if isinstance(entry, dict) else {}
    render = _catalog_render_metadata(entry)

    explicit_checkpoint = str(checkpoint or "").strip()
    catalog_checkpoint = str(render.get("checkpoint_name") or entry_data.get("filename") or "").strip()
    if not explicit_checkpoint:
        materialized_checkpoint = _catalog_comfy_asset_filename(
            entry_data,
            folder="checkpoints",
            allowed_kinds={"checkpoint"},
        )
        if materialized_checkpoint:
            catalog_checkpoint = materialized_checkpoint
    chosen_checkpoint, fallback_checkpoint = _resolve_comfy_checkpoint_name(
        explicit_checkpoint or catalog_checkpoint or settings.comfyui_checkpoint,
        allow_auto_fallback=not explicit_checkpoint and not catalog_checkpoint,
    )
    family = str(workflow_family or "auto").strip().lower()
    supported_families = {"auto", "txt2img", "img2img", "inpaint", "outpaint", "controlnet"}
    if family not in supported_families:
        family = "auto"

    control_entry = _catalog_entry(controlnet_model)
    control_entry_data = control_entry if isinstance(control_entry, dict) else {}
    control_render = _catalog_render_metadata(control_entry)
    controlnet_name = str(
        control_render.get("controlnet_name")
        or control_entry_data.get("filename")
        or render.get("controlnet_name")
        or ""
    ).strip()
    if control_entry_data:
        materialized_controlnet = _catalog_comfy_asset_filename(
            control_entry_data,
            folder="controlnet",
            allowed_kinds={"controlnet"},
        )
        if materialized_controlnet:
            controlnet_name = materialized_controlnet
    has_controlnet_units = any(
        isinstance(unit, dict) and str(unit.get("model") or unit.get("controlnet_name") or "").strip()
        for unit in (controlnet_units or [])
    )
    if family == "auto":
        if controlnet_name or reference_asset or has_controlnet_units or str(entry_data.get("kind") or "") == "controlnet":
            family = "controlnet"
        else:
            family = str(render.get("workflow_family") or "txt2img").strip().lower()
    if family not in supported_families - {"auto"}:
        family = "txt2img"

    if family == "controlnet" and not controlnet_name and not has_controlnet_units and str(entry_data.get("kind") or "") == "controlnet":
        controlnet_name = str(entry_data.get("filename") or "")
    if family == "controlnet" and not controlnet_name and not has_controlnet_units:
        raise UserFacingError(
            "No ControlNet model selected",
            hint="Install a Studio ControlNet model in Models, then choose it on the Render page.",
            code="CONTROLNET_MISSING",
            status_code=400,
        )
    if family == "controlnet" and not reference_asset and not has_controlnet_units:
        raise UserFacingError(
            "No reference image selected",
            hint="Upload or pick a project reference image before running a ControlNet still render.",
            code="REFERENCE_IMAGE_MISSING",
            status_code=400,
        )

    return {
        "entry": entry,
        "checkpoint": chosen_checkpoint,
        "checkpoint_fallback_from": fallback_checkpoint,
        "workflow_family": family,
        "controlnet_name": controlnet_name or None,
        "conditioning_mode": str(
            conditioning_mode
            or control_render.get("conditioning_mode")
            or render.get("conditioning_mode")
            or "raw"
        ).strip().lower(),
    }


def _resolve_comfy_motion_selection(
    *,
    model_id: str | None,
    checkpoint: str | None,
    svd_model_id: str | None,
    svd_checkpoint: str | None = None,
) -> dict[str, Any]:
    entry = _catalog_entry(model_id)
    entry_data = entry if isinstance(entry, dict) else {}
    render = _catalog_render_metadata(entry)
    explicit_checkpoint = str(checkpoint or "").strip()
    catalog_checkpoint = str(render.get("checkpoint_name") or entry_data.get("filename") or "").strip()
    if not explicit_checkpoint:
        materialized_checkpoint = _catalog_comfy_asset_filename(
            entry_data,
            folder="checkpoints",
            allowed_kinds={"checkpoint"},
        )
        if materialized_checkpoint:
            catalog_checkpoint = materialized_checkpoint
    base_checkpoint, fallback_checkpoint = _resolve_comfy_checkpoint_name(
        explicit_checkpoint or catalog_checkpoint or settings.comfyui_checkpoint,
        allow_auto_fallback=not explicit_checkpoint and not catalog_checkpoint,
    )

    svd_entry = _catalog_entry(svd_model_id)
    svd_entry_data = svd_entry if isinstance(svd_entry, dict) else {}
    svd_render = _catalog_render_metadata(svd_entry)
    resolved_svd = str(
        svd_checkpoint
        or svd_render.get("svd_checkpoint")
        or svd_entry_data.get("filename")
        or "svd_xt.safetensors"
    )
    if not svd_checkpoint:
        materialized_svd = _catalog_comfy_asset_filename(
            svd_entry_data,
            folder="checkpoints",
            allowed_kinds={"checkpoint", "motion_module"},
        )
        if materialized_svd:
            resolved_svd = materialized_svd
    return {
        "entry": entry,
        "svd_entry": svd_entry,
        "checkpoint": base_checkpoint,
        "checkpoint_fallback_from": fallback_checkpoint,
        "svd_checkpoint": resolved_svd,
    }


def _resolve_still_scene_selection(
    *,
    model_id: str | None,
    checkpoint: str | None,
    workflow_family: str | None,
    controlnet_model: str | None,
    reference_asset: str | None,
    conditioning_mode: str | None,
    controlnet_units: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entry = _catalog_entry(model_id)
    if entry is None:
        return {
            **_resolve_comfy_still_selection(
                model_id=model_id,
                checkpoint=checkpoint,
                workflow_family=workflow_family,
                controlnet_model=controlnet_model,
                reference_asset=reference_asset,
                conditioning_mode=conditioning_mode,
                controlnet_units=controlnet_units,
            ),
            "engine": "comfyui",
            "family": None,
            "model_path": None,
        }

    engine = _catalog_entry_engine(entry)
    family = _catalog_entry_family(entry)
    render = _catalog_render_metadata(entry)
    requested_family = str(workflow_family or "auto").strip().lower()
    if requested_family not in {"auto", "txt2img", "img2img", "inpaint", "outpaint", "controlnet"}:
        requested_family = "auto"
    if requested_family == "auto":
        has_controlnet_units = any(
            isinstance(unit, dict) and str(unit.get("model") or unit.get("controlnet_name") or "").strip()
            for unit in (controlnet_units or [])
        )
        if controlnet_model or reference_asset or has_controlnet_units:
            requested_family = "controlnet"
        else:
            requested_family = str(render.get("workflow_family") or "txt2img").strip().lower()
            if requested_family == "diffusers":
                requested_family = "txt2img"
    if requested_family not in {"txt2img", "img2img", "inpaint", "outpaint", "controlnet"}:
        requested_family = "txt2img"

    if not _catalog_supports_workflow(entry, requested_family):
        raise UserFacingError(
            "The selected still model does not support this workflow.",
            hint="Choose a compatible still model or switch to a supported workflow family.",
            code="WORKFLOW_UNSUPPORTED",
            status_code=400,
        )

    if engine == "internal":
        model_path = models.installed_path(str(entry.get("id") or ""))
        if model_path is None:
            raise UserFacingError(
                "Internal still model is not installed",
                hint="Install the selected internal diffusers model in Models, then retry.",
                code="MODEL_NOT_INSTALLED",
                status_code=400,
            )
        return {
            "entry": entry,
            "engine": "internal",
            "family": family,
            "workflow_family": requested_family,
            "model_path": model_path,
            "checkpoint": None,
            "conditioning_mode": str(conditioning_mode or "raw").strip().lower() or "raw",
            "controlnet_name": None,
        }

    comfy_selection = _resolve_comfy_still_selection(
        model_id=model_id,
        checkpoint=checkpoint,
        workflow_family=requested_family,
        controlnet_model=controlnet_model,
        reference_asset=reference_asset,
        conditioning_mode=conditioning_mode,
        controlnet_units=controlnet_units,
    )
    return {
        **comfy_selection,
        "engine": "comfyui",
        "family": family,
        "model_path": None,
    }


def _resolve_project_reference_path(project_id: str, reference_asset: str | None) -> Path | None:
    raw = str(reference_asset or "").strip()
    if not raw:
        return None
    project_dir = store.project_dir(project_id)
    direct = _safe_project_path(project_dir, raw)
    if direct is not None and direct.exists() and direct.is_file():
        return direct
    refs_dir = project_dir / "assets" / "refs"
    fallback = refs_dir / Path(raw).name
    if fallback.exists() and fallback.is_file():
        return fallback
    return None


def _resolve_project_mask_path(project_id: str, mask_asset: str | None) -> Path | None:
    raw = str(mask_asset or "").strip()
    if not raw:
        return None
    project_dir = store.project_dir(project_id)
    direct = _safe_project_path(project_dir, raw)
    if direct is not None and direct.exists() and direct.is_file():
        return direct
    masks_dir = project_dir / "assets" / "masks"
    fallback = masks_dir / Path(raw).name
    if fallback.exists() and fallback.is_file():
        return fallback
    return None


def _normalize_outpaint(raw: Any) -> dict[str, int] | None:
    if not isinstance(raw, dict):
        return None
    out = {
        "top_px": max(0, int(raw.get("top_px", 0) or 0)),
        "right_px": max(0, int(raw.get("right_px", 0) or 0)),
        "bottom_px": max(0, int(raw.get("bottom_px", 0) or 0)),
        "left_px": max(0, int(raw.get("left_px", 0) or 0)),
    }
    if any(value > 0 for value in out.values()):
        return out
    return None


def _prepare_outpaint_assets(
    project_id: str,
    *,
    source_asset: str,
    outpaint: dict[str, int] | None = None,
    mask_asset: str | None = None,
) -> dict[str, Any]:
    if Image is None:
        raise UserFacingError(
            "Pillow is not installed",
            hint="Install backend deps including Pillow, then retry.",
            code="INTERNAL_DEPS",
            status_code=500,
        )

    source_path = _resolve_project_reference_path(project_id, source_asset)
    if source_path is None:
        raise UserFacingError(
            "Source image not found",
            hint="Upload or choose a valid project source image before running the render.",
            code="SOURCE_IMAGE_NOT_FOUND",
            status_code=400,
        )

    explicit_mask_path = _resolve_project_mask_path(project_id, mask_asset)
    margins = _normalize_outpaint(outpaint) or {"top_px": 0, "right_px": 0, "bottom_px": 0, "left_px": 0}

    cache_dir = store.project_dir(project_id) / "cache" / "outpaint_inputs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(
        json.dumps(
            {
                "source": str(source_path),
                "source_mtime": source_path.stat().st_mtime if source_path.exists() else 0,
                "mask": str(explicit_mask_path) if explicit_mask_path else None,
                "mask_mtime": explicit_mask_path.stat().st_mtime if explicit_mask_path and explicit_mask_path.exists() else 0,
                "margins": margins,
            },
            sort_keys=True,
        ).encode("utf-8", errors="ignore")
    ).hexdigest()[:12]
    prepared_source = cache_dir / f"{source_path.stem}_{digest}_source.png"
    prepared_mask = cache_dir / f"{source_path.stem}_{digest}_mask.png"

    if prepared_source.exists() and prepared_mask.exists():
        mask_source = "explicit_mask" if explicit_mask_path else "generated_outpaint"
        if explicit_mask_path and any(value > 0 for value in margins.values()):
            mask_source = "explicit_mask_with_margins"
        return {
            "source_path": prepared_source,
            "mask_path": prepared_mask,
            "mask_source": mask_source,
            "outpaint": margins if any(value > 0 for value in margins.values()) else None,
        }

    with Image.open(source_path) as source_image:
        source = source_image.convert("RGB")
        source_w, source_h = source.size
        if explicit_mask_path:
            with Image.open(explicit_mask_path) as mask_image:
                mask = mask_image.convert("L")
                if any(value > 0 for value in margins.values()):
                    canvas_w = source_w + int(margins["left_px"]) + int(margins["right_px"])
                    canvas_h = source_h + int(margins["top_px"]) + int(margins["bottom_px"])
                    canvas = Image.new("RGB", (canvas_w, canvas_h), (0, 0, 0))
                    canvas.paste(source, (int(margins["left_px"]), int(margins["top_px"])))
                    if mask.size != canvas.size:
                        mask = mask.resize(canvas.size, resample=Image.BILINEAR)
                    source = canvas
                elif mask.size != source.size:
                    canvas = Image.new("RGB", mask.size, (0, 0, 0))
                    x = max(0, int((mask.size[0] - source_w) / 2))
                    y = max(0, int((mask.size[1] - source_h) / 2))
                    canvas.paste(source, (x, y))
                    source = canvas
                prepared = mask
                mask_source = "explicit_mask" if not any(value > 0 for value in margins.values()) else "explicit_mask_with_margins"
        else:
            if not any(value > 0 for value in margins.values()):
                raise UserFacingError(
                    "Outpaint margins are missing",
                    hint="Set at least one outpaint edge expansion or choose an explicit outpaint mask.",
                    code="OUTPAINT_MARGINS_MISSING",
                    status_code=400,
                )
            canvas_w = source_w + int(margins["left_px"]) + int(margins["right_px"])
            canvas_h = source_h + int(margins["top_px"]) + int(margins["bottom_px"])
            canvas = Image.new("RGB", (canvas_w, canvas_h), (0, 0, 0))
            canvas.paste(source, (int(margins["left_px"]), int(margins["top_px"])))
            prepared = Image.new("L", (canvas_w, canvas_h), 255)
            prepared.paste(0, (int(margins["left_px"]), int(margins["top_px"]), int(margins["left_px"]) + source_w, int(margins["top_px"]) + source_h))
            source = canvas
            mask_source = "generated_outpaint"

        source.save(prepared_source)
        prepared.save(prepared_mask)

    return {
        "source_path": prepared_source,
        "mask_path": prepared_mask,
        "mask_source": mask_source,
        "outpaint": margins if any(value > 0 for value in margins.values()) else None,
    }


def _prepare_condition_image(project_id: str, source_path: Path, mode: str) -> Path:
    mode_l = str(mode or "raw").strip().lower()
    if mode_l in {"raw", "external"}:
        return source_path
    if Image is None or ImageFilter is None or ImageOps is None:
        return source_path

    cache_dir = store.project_dir(project_id) / "cache" / "control_inputs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = source_path.suffix if source_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
    out_path = cache_dir / f"{source_path.stem}_{mode_l}{suffix}"
    if out_path.exists():
        return out_path

    with Image.open(source_path) as image:
        base = image.convert("RGB")
        if mode_l == "blur":
            prepared = base.filter(ImageFilter.GaussianBlur(radius=8))
        elif mode_l == "edge":
            edge = base.convert("L").filter(ImageFilter.FIND_EDGES)
            edge = ImageOps.autocontrast(edge)
            prepared = edge.point(lambda v: 255 if v >= 48 else 0).convert("RGB")
        else:
            prepared = base
        prepared.save(out_path)
    return out_path


def _fallback_comfy_input_image(image_path: Path, project_id: str) -> str:
    if comfy_portable_installed(settings.external_dir, settings.data_dir):
        input_dir = comfy_portable_root(settings.external_dir, settings.data_dir) / "ComfyUI" / "input" / "edmg" / project_id
        input_dir.mkdir(parents=True, exist_ok=True)
        dest = input_dir / image_path.name
        if not dest.exists() or dest.stat().st_mtime < image_path.stat().st_mtime:
            shutil.copy2(image_path, dest)
        return f"edmg/{project_id}/{dest.name}".replace("\\", "/")
    return str(image_path)


def _prepare_comfy_reference_image(project_id: str, node_url: str, reference_asset: str, conditioning_mode: str) -> str:
    source_path = _resolve_project_reference_path(project_id, reference_asset)
    if source_path is None:
        raise UserFacingError(
            "Reference image not found",
            hint="Upload the reference into the project first, then pick it again on the Render page.",
            code="REFERENCE_IMAGE_NOT_FOUND",
            status_code=400,
        )

    prepared = _prepare_condition_image(project_id, source_path, conditioning_mode)
    try:
        uploaded = comfy.upload_input_image(node_url, str(prepared), subfolder=f"edmg/{project_id}", overwrite=True)
        name = str(uploaded.get("name") or uploaded.get("filename") or prepared.name).strip()
        subfolder = str(uploaded.get("subfolder") or f"edmg/{project_id}").strip().strip("/")
        return f"{subfolder}/{name}".replace("\\", "/") if subfolder else name
    except Exception:
        return _fallback_comfy_input_image(prepared, project_id)


def _resolve_optional_comfy_asset_name(
    ref: str | None,
    *,
    folder: str,
    allowed_kinds: set[str] | None = None,
) -> str | None:
    raw = str(ref or "").strip()
    if not raw:
        return None
    asset = models.resolve_comfy_asset(raw, folder=folder, allowed_kinds=allowed_kinds)
    return str(asset.get("filename") or raw).strip() or None


def _normalize_render_loras(raw_loras: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_loras, list):
        return []
    items = [item for item in raw_loras if isinstance(item, dict)]
    return models.resolve_loras(items)


def _normalize_controlnet_units(
    raw_units: Any,
    *,
    engine: str = "comfyui",
    family: str | None = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(raw_units, list):
        return normalized
    for unit in raw_units:
        if not isinstance(unit, dict):
            continue
        model_ref = str(unit.get("model") or unit.get("controlnet_name") or "").strip()
        reference_asset = str(unit.get("reference_asset") or "").strip()
        if not model_ref or not reference_asset:
            continue
        if engine == "internal":
            asset = models.resolve_internal_asset(model_ref, folder="controlnet", allowed_kinds={"controlnet"})
            asset_family = str(asset.get("family") or "").strip().lower()
            if family and asset_family and asset_family != str(family).strip().lower():
                raise UserFacingError(
                    "ControlNet family is incompatible with the selected internal still model.",
                    hint=f"Pick an internal {family.upper()} ControlNet for this still model.",
                    code="CONTROLNET_FAMILY_MISMATCH",
                    status_code=400,
                )
            normalized.append(
                {
                    "model": model_ref,
                    "id": asset.get("id"),
                    "name": asset.get("name") or model_ref,
                    "path": str(asset.get("path") or ""),
                    "family": asset_family or family,
                    "engine": "internal",
                    "reference_asset": reference_asset,
                    "conditioning_mode": str(unit.get("conditioning_mode") or "raw").strip().lower() or "raw",
                    "strength": float(unit.get("strength", 0.8)),
                    "start_percent": float(unit.get("start_percent", 0.0)),
                    "end_percent": float(unit.get("end_percent", 1.0)),
                }
            )
        else:
            asset = models.resolve_comfy_asset(model_ref, folder="controlnet", allowed_kinds={"controlnet"})
            asset_entry = _catalog_entry(str(asset.get("id") or "")) if asset.get("id") else None
            asset_family = _catalog_entry_family(asset_entry)
            if family and asset_family and asset_family != str(family).strip().lower():
                raise UserFacingError(
                    "ControlNet family is incompatible with the selected still model.",
                    hint=f"Pick a {family.upper()} ControlNet model for this still render.",
                    code="CONTROLNET_FAMILY_MISMATCH",
                    status_code=400,
                )
            normalized.append(
                {
                    "model": model_ref,
                    "name": asset.get("name") or Path(str(asset.get("filename") or model_ref)).stem,
                    "controlnet_name": str(asset.get("filename") or model_ref),
                    "family": asset_family or family,
                    "engine": "comfyui",
                    "reference_asset": reference_asset,
                    "conditioning_mode": str(unit.get("conditioning_mode") or "raw").strip().lower() or "raw",
                    "strength": float(unit.get("strength", 0.8)),
                    "start_percent": float(unit.get("start_percent", 0.0)),
                    "end_percent": float(unit.get("end_percent", 1.0)),
                }
            )
    return normalized


def _output_metadata_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.name}.json")


def _project_relative_path(project_id: str, path: Path | str | None) -> str | None:
    if path is None:
        return None
    try:
        return str(Path(path).resolve().relative_to(store.project_dir(project_id).resolve()))
    except Exception:
        try:
            return str(Path(path).relative_to(store.project_dir(project_id)))
        except Exception:
            return str(path)


def _build_generation_metadata(
    *,
    project_id: str,
    job_id: str,
    output_path: Path,
    payload: dict[str, Any],
    workflow_family: str,
    checkpoint: str,
    loras: list[dict[str, Any]] | None = None,
    controlnet_units: list[dict[str, Any]] | None = None,
    vae_name: str | None = None,
    prompt_id: str | None = None,
    comfyui_image: dict[str, Any] | None = None,
    node_url: str | None = None,
    backend: str = "comfyui",
    engine: str | None = None,
    model_family: str | None = None,
    resolved_model_asset: str | None = None,
    mask_source: str | None = None,
    outpaint: dict[str, Any] | None = None,
    device: str | None = None,
    cached: bool = False,
    artifact_key: str = "image",
) -> dict[str, Any]:
    metadata_path = _output_metadata_path(output_path)
    rel_output = _project_relative_path(project_id, output_path)
    rel_metadata = _project_relative_path(project_id, metadata_path)
    return {
        "kind": "studio_diffusion_output",
        "project_id": project_id,
        "job_id": job_id,
        "variant_index": int(payload.get("variant_index", 0)),
        "scene_index": int(payload.get("scene_index", 0)),
        "workflow_family": str(workflow_family or "txt2img"),
        "prompt": str(payload.get("prompt") or ""),
        "negative_prompt": str(payload.get("negative_prompt") or ""),
        "seed": int(payload.get("seed") or 0),
        "steps": int(payload.get("steps") or 0),
        "cfg_scale": float(payload.get("cfg") or 0.0),
        "sampler": str(payload.get("sampler") or "euler"),
        "width": int(payload.get("width") or 0),
        "height": int(payload.get("height") or 0),
        "denoise_strength": float(payload.get("denoise_strength") or 0.0),
        "base_model": {
            "model_id": payload.get("model_id"),
            "engine": str(engine or backend or "comfyui"),
            "family": model_family,
            "checkpoint": checkpoint,
            "resolved_model_asset": resolved_model_asset or checkpoint,
            "vae": vae_name,
        },
        "loras": list(loras or []),
        "controlnet_units": list(controlnet_units or []),
        "source_asset": payload.get("source_asset"),
        "reference_asset": payload.get("reference_asset"),
        "inpaint_mask": payload.get("inpaint_mask"),
        "mask_source": mask_source,
        "outpaint": outpaint,
        "hires_fix": payload.get("hires_fix"),
        "refiner": payload.get("refiner"),
        "upscaler": payload.get("upscaler"),
        "output": {
            str(artifact_key or "image"): rel_output,
            "metadata": rel_metadata,
            "cached": bool(cached),
            "comfyui_prompt_id": prompt_id,
            "comfyui_image": comfyui_image or None,
        },
        "provenance": {
            "app": "DWCT Generative Sound Studio",
            "backend": backend,
            "device": device,
            "node_url": node_url,
            "captured_at": time.time(),
        },
    }


def _write_generation_metadata(output_path: Path, metadata: dict[str, Any]) -> Path:
    metadata_path = _output_metadata_path(output_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata_path


def _render_checkpoint_path(video_path: Path) -> Path:
    return video_path.with_suffix(".checkpoint.json")


def _load_render_checkpoint(video_path: Path) -> dict[str, Any] | None:
    cp = _render_checkpoint_path(video_path)
    if not cp.exists():
        return None
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _job_checkpoint_extra(mode: str, model_id: str, runtime_checkpoint: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"mode": mode, "model_id": model_id}
    if runtime_checkpoint:
        payload["runtime_checkpoint"] = runtime_checkpoint
    payload.update(extra)
    return payload




def _runtime_checkpoint_from_job(project_id: str, job: Any | None) -> dict[str, Any] | None:
    if not job:
        return None
    progress = job.progress if isinstance(getattr(job, "progress", None), dict) else {}
    runtime = progress.get("runtime_checkpoint")
    if isinstance(runtime, dict) and runtime:
        return dict(runtime)
    result = job.result if isinstance(getattr(job, "result", None), dict) else {}
    runtime = result.get("runtime_checkpoint")
    if isinstance(runtime, dict) and runtime:
        return dict(runtime)
    rel_video = result.get("video") if isinstance(result, dict) else None
    if isinstance(rel_video, str) and rel_video:
        try:
            video_path = safe_join(store.project_dir(project_id), rel_video)
        except Exception:
            video_path = None
        if video_path is not None and video_path.exists():
            cp = _load_render_checkpoint(video_path)
            if cp:
                return cp
    return None


def _read_log_tail(project_id: str, job_id: str, *, tail_lines: int = 80) -> dict[str, Any]:
    lp = jobs.log_path(project_id, job_id)
    if not lp.exists():
        return {"log": "", "log_tail": "", "log_path": str(lp), "log_exists": False, "log_line_count": 0}
    raw = lp.read_text(encoding="utf-8", errors="ignore")
    lines = raw.splitlines()
    tail = max(1, int(tail_lines or 80))
    tail_text = "\n".join(lines[-tail:])
    return {
        "log": raw,
        "log_tail": tail_text,
        "log_path": str(lp),
        "log_exists": True,
        "log_line_count": len(lines),
    }


def _job_output_metadata(project_id: str, job: Any | None, runtime_checkpoint: dict[str, Any] | None = None) -> dict[str, Any]:
    result = job.result if job and isinstance(getattr(job, "result", None), dict) else {}
    progress = job.progress if job and isinstance(getattr(job, "progress", None), dict) else {}
    project_dir = store.project_dir(project_id)

    rel_video = result.get("video") or progress.get("video")
    video_abs = result.get("video_abs")
    checkpoint_outputs = runtime_checkpoint.get("outputs") if isinstance(runtime_checkpoint, dict) else {}
    checkpoint_json = checkpoint_outputs.get("checkpoint_json") if isinstance(checkpoint_outputs, dict) else None

    video_path = None
    if isinstance(rel_video, str) and rel_video:
        try:
            video_path = safe_join(project_dir, rel_video)
        except Exception:
            video_path = None
    elif isinstance(video_abs, str) and video_abs:
        video_path = Path(video_abs)
        try:
            rel_video = str(video_path.relative_to(project_dir))
        except Exception:
            pass

    if not checkpoint_json and video_path is not None:
        try:
            checkpoint_json = str(_render_checkpoint_path(video_path).relative_to(project_dir))
        except Exception:
            checkpoint_json = str(_render_checkpoint_path(video_path))

    checkpoint_path = None
    if isinstance(checkpoint_json, str) and checkpoint_json:
        try:
            checkpoint_path = safe_join(project_dir, checkpoint_json)
        except Exception:
            checkpoint_path = Path(checkpoint_json)

    render_meta = None
    render_meta_path = None
    if video_path is not None:
        render_meta_path = video_path.with_suffix('.render.json')
        if render_meta_path.exists():
            try:
                render_meta = json.loads(render_meta_path.read_text(encoding='utf-8'))
            except Exception:
                render_meta = None

    cache_paths = {}
    if isinstance(render_meta, dict):
        outputs = render_meta.get('outputs') if isinstance(render_meta.get('outputs'), dict) else {}
        frames = render_meta.get('frames') if isinstance(render_meta.get('frames'), dict) else {}
        cache_paths = {
            'frames_dir': frames.get('dir'),
            'raw_mp4': outputs.get('raw_mp4'),
            'interp_mp4': outputs.get('interp_mp4'),
            'final_mp4': outputs.get('final_mp4'),
            'checkpoint_json': outputs.get('checkpoint_json') or checkpoint_json,
        }
    elif checkpoint_path is not None:
        base = checkpoint_path.with_suffix('')
        cache_paths = {
            'checkpoint_json': str(checkpoint_path),
            'final_mp4': str(base.with_suffix('.mp4')),
        }

    return {
        'video_relpath': rel_video,
        'video_abspath': str(video_path) if video_path is not None else video_abs,
        'checkpoint_json_relpath': checkpoint_json,
        'checkpoint_json_abspath': str(checkpoint_path) if checkpoint_path is not None else None,
        'checkpoint_exists': bool(checkpoint_path and checkpoint_path.exists()),
        'render_meta_path': str(render_meta_path) if render_meta_path is not None else None,
        'render_meta_exists': bool(render_meta_path and render_meta_path.exists()),
        'render_meta': render_meta,
        'cache_paths': cache_paths,
    }


def _job_detail_payload(project_id: str, job: Any, *, tail_lines: int = 80) -> dict[str, Any]:
    runtime_checkpoint = _runtime_checkpoint_from_job(project_id, job)
    log = _read_log_tail(project_id, job.id, tail_lines=tail_lines)
    outputs = _job_output_metadata(project_id, job, runtime_checkpoint)
    return {
        'ok': True,
        'job': job.__dict__,
        'runtime_checkpoint': runtime_checkpoint,
        'log': log['log'],
        'log_tail': log['log_tail'],
        'log_path': log['log_path'],
        'log_exists': log['log_exists'],
        'log_line_count': log['log_line_count'],
        'outputs': outputs,
        'resume_ready': bool((runtime_checkpoint or {}).get('can_resume')),
    }






def _job_runtime_checkpoint_paths(project_id: str, job: Any | None) -> dict[str, Any]:
    runtime_checkpoint = _runtime_checkpoint_from_job(project_id, job)
    outputs = _job_output_metadata(project_id, job, runtime_checkpoint)
    cache_paths = dict(outputs.get("cache_paths") or {})
    return {
        "project_dir": store.project_dir(project_id),
        "runtime_checkpoint": runtime_checkpoint,
        "outputs": outputs,
        "frames_dir": cache_paths.get("frames_dir"),
        "raw_mp4": cache_paths.get("raw_mp4"),
        "interp_mp4": cache_paths.get("interp_mp4"),
        "final_mp4": cache_paths.get("final_mp4") or outputs.get("video_abspath"),
        "checkpoint_json": outputs.get("checkpoint_json_abspath"),
        "render_meta_path": outputs.get("render_meta_path"),
    }


def _safe_project_path(project_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    try:
        return safe_join(project_dir, value)
    except Exception:
        p = Path(value)
        try:
            p.resolve().relative_to(project_dir.resolve())
            return p
        except Exception:
            return None


def _apply_runtime_checkpoint_state(project_id: str, job: Any, runtime_checkpoint: dict[str, Any] | None) -> Any:
    if isinstance(job.progress, dict):
        progress = dict(job.progress)
        if runtime_checkpoint is None:
            progress.pop("runtime_checkpoint", None)
        else:
            progress["runtime_checkpoint"] = dict(runtime_checkpoint)
        job.progress = progress
    if isinstance(job.result, dict):
        result = dict(job.result)
        if runtime_checkpoint is None:
            result.pop("runtime_checkpoint", None)
        else:
            result["runtime_checkpoint"] = dict(runtime_checkpoint)
        job.result = result
    jobs.save(job)

    proj = store.get(project_id)
    if proj is not None:
        targets = []
        latest = proj.meta.get("last_internal_render")
        if isinstance(latest, dict):
            targets.append(latest)
        hist = proj.meta.get("internal_render_history")
        if isinstance(hist, list):
            targets.extend([entry for entry in hist if isinstance(entry, dict)])
        video_rel = None
        if isinstance(getattr(job, "result", None), dict):
            video_rel = job.result.get("video")
        for entry in targets:
            same_video = bool(video_rel and entry.get("video") == video_rel)
            same_source = bool(entry.get("source_job_id") and str(entry.get("source_job_id")) == str(job.id))
            if same_video or same_source:
                if runtime_checkpoint is None:
                    entry.pop("runtime_checkpoint", None)
                else:
                    entry["runtime_checkpoint"] = dict(runtime_checkpoint)
        store.save(proj)
    return jobs.get(project_id, job.id) or job


def _remove_path(path: Path | None) -> bool:
    if path is None or not path.exists():
        return False
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=False)
        return True
    path.unlink(missing_ok=True)
    return True


def _mutate_internal_job_artifacts(project_id: str, job: Any, *, clear_cached_frames: bool = False, drop_checkpoint: bool = False) -> dict[str, Any]:
    if getattr(job, "type", None) != "internal_video":
        raise HTTPException(400, "Artifact maintenance is only available for internal render jobs")
    if getattr(job, "status", None) in ("queued", "running"):
        raise HTTPException(409, "Stop the active job before modifying cached frames or checkpoints")

    info = _job_runtime_checkpoint_paths(project_id, job)
    project_dir = info["project_dir"]
    runtime_checkpoint = dict(info.get("runtime_checkpoint") or {}) if info.get("runtime_checkpoint") else None
    removed: list[str] = []

    frames_dir = _safe_project_path(project_dir, info.get("frames_dir"))
    raw_mp4 = _safe_project_path(project_dir, info.get("raw_mp4"))
    interp_mp4 = _safe_project_path(project_dir, info.get("interp_mp4"))
    render_meta_path = _safe_project_path(project_dir, info.get("render_meta_path"))
    checkpoint_json = _safe_project_path(project_dir, info.get("checkpoint_json"))
    final_mp4 = _safe_project_path(project_dir, info.get("final_mp4"))

    if clear_cached_frames:
        for label, target in (("frames_dir", frames_dir), ("raw_mp4", raw_mp4), ("interp_mp4", interp_mp4), ("render_meta_path", render_meta_path)):
            if _remove_path(target):
                removed.append(label)

    if drop_checkpoint and _remove_path(checkpoint_json):
        removed.append("checkpoint_json")

    if runtime_checkpoint is not None:
        outputs = dict(runtime_checkpoint.get("outputs") or {})
        if clear_cached_frames:
            outputs["raw_exists"] = bool(raw_mp4 and raw_mp4.exists())
            outputs["interp_exists"] = bool(interp_mp4 and interp_mp4.exists())
            outputs["final_exists"] = bool(final_mp4 and final_mp4.exists())
            runtime_checkpoint["can_resume"] = False
            runtime_checkpoint["resume_recommended"] = False
            runtime_checkpoint["message"] = "Cached frames and intermediates cleared"
            runtime_checkpoint["maintenance_action"] = "clear_cached_frames"
        if drop_checkpoint:
            outputs["checkpoint_json"] = None
            runtime_checkpoint["can_resume"] = False
            runtime_checkpoint["resume_recommended"] = False
            runtime_checkpoint["message"] = "Checkpoint file removed" if not clear_cached_frames else "Cached frames cleared and checkpoint removed"
            runtime_checkpoint["maintenance_action"] = "drop_checkpoint" if not clear_cached_frames else "clear_cached_frames+drop_checkpoint"
        runtime_checkpoint["outputs"] = outputs
        runtime_checkpoint["updated_at"] = time.time()
        runtime_checkpoint["checkpoint_present"] = bool(checkpoint_json and checkpoint_json.exists())
        if checkpoint_json and checkpoint_json.exists():
            try:
                checkpoint_json.write_text(json.dumps(runtime_checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        job = _apply_runtime_checkpoint_state(project_id, job, runtime_checkpoint)
    else:
        job = jobs.get(project_id, job.id) or job

    detail = _job_detail_payload(project_id, job, tail_lines=80)
    return {
        "ok": True,
        "job": job.__dict__,
        "removed": removed,
        "detail": detail,
    }


def _enqueue_internal_job_from_source(project_id: str, source_job: Any, *, resume_existing_frames: bool, queue_action: str) -> dict[str, Any]:
    payload = deepcopy(getattr(source_job, "payload", None) or {})
    payload["resume_existing_frames"] = bool(resume_existing_frames)
    payload["queue_action"] = str(queue_action)
    payload["source_job_id"] = str(source_job.id)
    if not resume_existing_frames:
        payload["queue_clean_restart"] = True

    preflight = _internal_render_preflight_data(project_id, payload)
    mode = str(preflight.get("mode") or payload.get("render_mode") or "auto")
    model_id = str(preflight.get("model_id") or payload.get("model_id") or ("proxy_draft" if mode == "proxy" else "auto"))
    checkpoint = _runtime_checkpoint_from_job(project_id, source_job)
    total = max(1, int(preflight.get("estimated_frames", 1)) + 3)
    job = jobs.create(project_id, "internal_video", payload)
    message = (
        f"Queued resume from checkpoint for model {model_id}"
        if resume_existing_frames
        else f"Queued clean restart for model {model_id}"
    )
    job.progress = {
        "stage": "queued",
        "current": 0,
        "total": total,
        "percent": 0.0,
        "message": message,
        **_job_checkpoint_extra(
            "proxy" if mode == "proxy" else "internal",
            model_id,
            checkpoint,
            queue_action=queue_action,
            source_job_id=str(source_job.id),
            resume_existing_frames=bool(resume_existing_frames),
        ),
    }
    jobs.save(job)
    jobs.append_log(project_id, job.id, f"Queued {queue_action} from job {source_job.id}")
    if checkpoint:
        jobs.append_log(
            project_id,
            job.id,
            f"Checkpoint summary: status={checkpoint.get('status')} resume_percent={checkpoint.get('resume_percent')} chunks={checkpoint.get('completed_chunks')}/{checkpoint.get('estimated_chunks')}",
        )
    proj = store.get(project_id)
    if proj:
        proj.meta.setdefault("jobs", []).append(job.__dict__)
        store.save(proj)
    return {"ok": True, "job": job.__dict__, "preflight": preflight, "source_job": source_job.__dict__}


def _tier_rank(name: str) -> int:
    return {"draft": 0, "balanced": 1, "quality": 2}.get(str(name or "draft").lower(), 0)


def _internal_render_defaults_for_tier(tier: str, hw: dict[str, Any], *, duration_s: float | None = None) -> dict[str, Any]:
    tier_l = str(tier or "draft").lower()
    backend = str(hw.get("backend") or "cpu").lower()
    if tier_l == "quality":
        defaults: dict[str, Any] = {
            "fps_output": 24,
            "fps_render": 4,
            "width": 1024,
            "height": 576,
            "steps": 24,
            "cfg": 7.2,
            "keyframe_interval_s": 4.0,
            "interpolation_engine": "auto",
            "temporal_mode": "frame_img2img" if backend == "cuda" else "keyframes",
            "temporal_steps": 18,
            "refine_every_n_frames": 1,
            "anchor_strength": 0.20,
            "prompt_blend": True,
        }
    elif tier_l == "balanced":
        defaults = {
            "fps_output": 24,
            "fps_render": 2,
            "width": 768,
            "height": 432,
            "steps": 15 if backend == "cpu" else 16,
            "cfg": 6.8,
            "keyframe_interval_s": 5.0,
            "interpolation_engine": "auto",
            "temporal_mode": "keyframes",
            "temporal_steps": 12,
            "refine_every_n_frames": 2,
            "anchor_strength": 0.18,
            "prompt_blend": True,
        }
    else:
        defaults = {
            "fps_output": 24,
            "fps_render": 1,
            "width": 640,
            "height": 360,
            "steps": 10,
            "cfg": 6.0,
            "keyframe_interval_s": 6.0,
            "interpolation_engine": "auto",
            "temporal_mode": "keyframes",
            "temporal_steps": 8,
            "refine_every_n_frames": 3,
            "anchor_strength": 0.12,
            "prompt_blend": True,
        }
    if duration_s and duration_s > 120.0:
        defaults["fps_render"] = min(int(defaults["fps_render"]), 2)
        defaults["keyframe_interval_s"] = max(float(defaults["keyframe_interval_s"]), 6.0)
    return defaults


def _build_render_chunk_plan(
    hw: dict[str, Any] | None = None,
    *,
    applied_tier: str = "draft",
    duration_s: float | None = None,
    total_frames: int | None = None,
    fps_render: int | None = None,
    render_mode: str = "diffusion",
) -> dict[str, Any]:
    hw = dict(hw or {})
    backend_family = str(hw.get("backend_family") or "cpu_only").lower()
    applied = str(applied_tier or "draft").lower()
    fps_r = max(1, int(fps_render or 1))
    total_frames_i = max(0, int(total_frames or 0))
    duration = float(duration_s or 0.0)
    if duration <= 0.0 and total_frames_i > 0:
        duration = float(total_frames_i) / float(fps_r)
    mode_l = str(render_mode or "diffusion").lower()
    notes: list[str] = []

    enabled = False
    strategy = "single_pass"
    checkpoint_interval_frames = max(1, min(60, fps_r * 15))
    if backend_family == "cpu_only":
        threshold_s = 45.0 if mode_l == "diffusion" else 90.0
        frames_per_chunk = 90 if applied == "balanced" else 120
        if total_frames_i >= frames_per_chunk * 2 or duration >= threshold_s:
            enabled = True
            strategy = "resume_friendly_chunks"
            notes.append("CPU-only system detected; using resume-friendly chunk guidance for long renders.")
    elif backend_family == "integrated_gpu":
        threshold_s = 75.0 if mode_l == "diffusion" else 120.0
        frames_per_chunk = 120 if applied == "balanced" else 180
        if total_frames_i >= frames_per_chunk * 2 or duration >= threshold_s:
            enabled = True
            strategy = "integrated_gpu_chunks"
            notes.append("Integrated-graphics system detected; chunk guidance is enabled to keep long renders recoverable.")
    else:
        frames_per_chunk = 240 if mode_l == "diffusion" else 360
        if total_frames_i >= frames_per_chunk * 3 and applied != "quality":
            enabled = True
            strategy = "throughput_chunks"
            notes.append("Long render on discrete GPU; chunk checkpoints will improve retryability.")

    if enabled:
        estimated_chunks = max(1, math.ceil(total_frames_i / max(1, frames_per_chunk)))
    else:
        estimated_chunks = 1
        frames_per_chunk = max(total_frames_i, 1)

    seconds_per_chunk = round(float(frames_per_chunk) / float(fps_r), 2)
    return {
        "enabled": enabled,
        "strategy": strategy,
        "resume_recommended": bool(enabled or backend_family != "discrete_gpu"),
        "frames_per_chunk": int(frames_per_chunk),
        "seconds_per_chunk": seconds_per_chunk,
        "estimated_chunks": int(estimated_chunks),
        "checkpoint_interval_frames": int(checkpoint_interval_frames),
        "notes": notes,
    }


@lru_cache(maxsize=1)
def _windows_video_controllers() -> list[dict[str, Any]]:
    if platform.system().lower() != "windows":
        return []
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json -Compress",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=6, check=False)
        if result.returncode != 0:
            return []
        raw = str(result.stdout or "").strip()
        if not raw:
            return []
        data = json.loads(raw)
        items = data if isinstance(data, list) else [data]
        out: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("Name") or item.get("name") or "").strip()
            if not name:
                continue
            adapter_ram = item.get("AdapterRAM") or item.get("adapterRam") or 0
            try:
                vram_gb = round(float(adapter_ram) / float(1024 ** 3), 2) if adapter_ram else 0.0
            except Exception:
                vram_gb = 0.0
            vendor = "unknown"
            name_l = name.lower()
            if "nvidia" in name_l:
                vendor = "nvidia"
            elif "amd" in name_l or "radeon" in name_l:
                vendor = "amd"
            elif "intel" in name_l:
                vendor = "intel"
            out.append({"name": name, "vendor": vendor, "vram_gb": vram_gb})
        return out
    except Exception:
        return []


def _pick_windows_accel_gpu() -> dict[str, Any] | None:
    gpus = _windows_video_controllers()
    if not gpus:
        return None
    preferred = [g for g in gpus if g.get("vendor") in {"amd", "nvidia", "intel"}]
    ordered = preferred or gpus
    ordered = sorted(ordered, key=lambda item: (0 if item.get("vendor") == "amd" else 1, -float(item.get("vram_gb") or 0.0)))
    return ordered[0] if ordered else None


def _directml_runtime_status() -> dict[str, Any]:
    out: dict[str, Any] = {
        "available": False,
        "runtime_ready": False,
        "provider": "DmlExecutionProvider",
        "providers": [],
        "device_name": None,
        "vendor": None,
        "vram_gb": 0.0,
        "integrated": False,
        "error": None,
    }
    if platform.system().lower() != "windows":
        out["error"] = "DirectML is only available on Windows."
        return out
    try:
        import onnxruntime as ort  # type: ignore

        providers = list(ort.get_available_providers() or [])
        out["providers"] = providers
        out["runtime_ready"] = "DmlExecutionProvider" in providers
    except Exception as e:
        out["error"] = str(e)
        return out

    gpu = _pick_windows_accel_gpu()
    if gpu:
        out["device_name"] = gpu.get("name")
        out["vendor"] = gpu.get("vendor")
        out["vram_gb"] = float(gpu.get("vram_gb") or 0.0)
        out["integrated"] = bool(gpu.get("vendor") == "intel")
    out["available"] = bool(out["runtime_ready"])
    return out


def _backend_family_for(backend: str, *, integrated: bool = False) -> str:
    backend_l = str(backend or "cpu").lower()
    if backend_l in {"cuda"}:
        return "discrete_gpu"
    if backend_l in {"mps"}:
        return "integrated_gpu"
    if backend_l == "directml":
        return "integrated_gpu" if integrated else "discrete_gpu"
    return "cpu_only"


def _parse_gpu_memory_gb(value: Any) -> float:
    text = str(value or "").strip().lower().replace(",", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([a-z]+)?", text)
    if not match:
        return 0.0
    try:
        amount = float(match.group(1))
    except Exception:
        return 0.0
    unit = str(match.group(2) or "mib")
    if unit in {"gb", "gib"}:
        return round(amount, 2)
    if unit in {"mb", "mib"}:
        return round(amount / 1024.0, 2)
    return round(amount, 2)


def _build_internal_render_plan(hw: dict[str, Any] | None = None, *, requested_tier: str = "auto", duration_s: float | None = None) -> dict[str, Any]:
    hw = dict(hw or {})
    backend = str(hw.get("backend") or "cpu").lower()
    backend_family = str(hw.get("backend_family") or _backend_family_for(backend, integrated=bool(hw.get("integrated_acceleration")))).lower()
    vram_gb = float(hw.get("vram_gb") or 0.0)
    ram_gb = float(hw.get("ram_gb") or 0.0)
    cpu_threads = int(hw.get("cpu_threads") or 1)
    notes: list[str] = []

    if backend == "cuda":
        if vram_gb >= 10.0:
            recommended = "quality"
            max_supported = "quality"
        elif vram_gb >= 6.0:
            recommended = "balanced"
            max_supported = "balanced"
            notes.append("Mid-range CUDA GPU detected; balanced tier is the safest default.")
        else:
            recommended = "draft"
            max_supported = "draft"
            notes.append("Low-VRAM CUDA GPU detected; use draft settings for reliable renders.")
        device_preference = "cuda"
    elif backend == "mps":
        recommended = "balanced" if ram_gb >= 16.0 else "draft"
        max_supported = "balanced"
        device_preference = "mps"
        notes.append("Apple Silicon acceleration detected; balanced tier is recommended for sustained laptop rendering.")
    elif backend == "directml":
        if backend_family == "discrete_gpu":
            recommended = "balanced" if (vram_gb >= 6.0 or ram_gb >= 16.0) else "draft"
            max_supported = "balanced"
            notes.append("DirectML acceleration detected; SDXL is the preferred AMD / Windows internal path.")
        else:
            recommended = "draft"
            max_supported = "balanced"
            notes.append("Integrated DirectML acceleration detected; draft or balanced tiers are the safest choice.")
        device_preference = "directml"
    else:
        if ram_gb >= 24.0 and cpu_threads >= 12:
            recommended = "balanced"
            max_supported = "balanced"
            notes.append("High-core CPU system detected; balanced tier is viable but slower than GPU rendering.")
        else:
            recommended = "draft"
            max_supported = "draft"
            notes.append("CPU-only or low-power system detected; draft tier is recommended for responsiveness.")
        device_preference = "cpu"

    requested = str(requested_tier or "auto").strip().lower()
    if requested not in {"auto", "draft", "balanced", "quality"}:
        requested = "auto"
    applied = recommended if requested == "auto" else requested
    if _tier_rank(applied) > _tier_rank(max_supported):
        notes.append(f"Requested tier '{applied}' exceeds current hardware ceiling; capping to {max_supported}.")
        applied = max_supported

    defaults = _internal_render_defaults_for_tier(applied, hw, duration_s=duration_s)
    chunk_plan = _build_render_chunk_plan(
        hw,
        applied_tier=applied,
        duration_s=duration_s,
        fps_render=int(defaults.get("fps_render", 1)),
        render_mode="diffusion",
    )
    if chunk_plan["resume_recommended"]:
        defaults["resume_existing_frames"] = True
    if chunk_plan["enabled"] and backend_family == "cpu_only":
        defaults["interpolation_engine"] = "fps"
        if float(duration_s or 0.0) >= 90.0 and _tier_rank(applied) <= _tier_rank("balanced"):
            defaults["temporal_mode"] = "off"
            defaults["refine_every_n_frames"] = max(int(defaults.get("refine_every_n_frames", 1)), 3)
            notes.append("Long CPU render detected; using chunk-friendly temporal defaults to make resumes cheaper.")
    elif chunk_plan["enabled"] and backend_family == "integrated_gpu":
        if _tier_rank(applied) <= _tier_rank("balanced"):
            defaults["temporal_mode"] = "keyframes"
            defaults["refine_every_n_frames"] = max(int(defaults.get("refine_every_n_frames", 1)), 2)
            notes.append("Integrated GPU path favors keyframe continuity over denser temporal refinement on long renders.")

    if backend == "cuda" and vram_gb >= 14.0 and _tier_rank(applied) >= _tier_rank("quality"):
        preferred_internal_model = "hf_sd35_medium_internal"
    elif backend == "cuda" and _tier_rank(applied) >= _tier_rank("balanced"):
        preferred_internal_model = "hf_sdxl_internal"
    elif backend == "directml":
        preferred_internal_model = "hf_sdxl_internal" if _tier_rank(applied) >= _tier_rank("balanced") else "hf_sd15_internal"
    else:
        preferred_internal_model = "hf_sd15_internal"
    return {
        "requested_tier": requested,
        "recommended_tier": recommended,
        "max_supported_tier": max_supported,
        "applied_tier": applied,
        "device_preference": device_preference,
        "preferred_internal_model": preferred_internal_model,
        "defaults": defaults,
        "chunk_plan": chunk_plan,
        "notes": notes + list(chunk_plan.get("notes") or []),
        "hardware_backend": backend,
        "supports_proxy_render": True,
    }


def _hardware_profile() -> dict[str, Any]:
    """Best-effort local hardware detection used for auto tiering."""
    cpu_threads = max(1, int(os.cpu_count() or 1))
    out: dict[str, Any] = {
        "backend": "cpu",
        "device": "cpu",
        "device_name": "CPU",
        "runtime_device_name": "CPU",
        "available_backends": ["cpu"],
        "vram_gb": 0.0,
        "ram_gb": 0.0,
        "cpu_threads": cpu_threads,
        "platform": platform.system().lower(),
        "machine": platform.machine().lower(),
        "integrated_acceleration": False,
        "gpu_vendor": None,
        "supports_directml": False,
        "directml_runtime_ready": False,
        "directml_device_name": None,
        "cuda_runtime_ready": False,
        "host_gpu_visible": False,
        "host_gpu_name": None,
        "host_gpu_vram_gb": 0.0,
        "host_gpus": [],
        "nvidia_driver_version": None,
    }
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        phys_pages = int(os.sysconf("SC_PHYS_PAGES"))
        out["ram_gb"] = round((page_size * phys_pages) / float(1024 ** 3), 2)
    except Exception:
        try:
            import psutil  # type: ignore
            out["ram_gb"] = round(float(psutil.virtual_memory().total) / float(1024 ** 3), 2)
        except Exception:
            out["ram_gb"] = 0.0
    try:
        import torch  # type: ignore
        if getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
            out["backend"] = "cuda"
            out["device"] = "cuda"
            out["device_name"] = "CUDA GPU"
            out["runtime_device_name"] = out["device_name"]
            out["cuda_runtime_ready"] = True
            out["available_backends"].append("cuda")
            try:
                props = torch.cuda.get_device_properties(0)
                out["device_name"] = getattr(props, "name", "cuda")
                out["runtime_device_name"] = out["device_name"]
                out["vram_gb"] = round(float(getattr(props, "total_memory", 0.0)) / float(1024 ** 3), 2)
            except Exception:
                pass
        else:
            try:
                mps = getattr(getattr(torch, "backends", None), "mps", None)
                if mps is not None and mps.is_available():
                    out["backend"] = "mps"
                    out["device"] = "mps"
                    out["device_name"] = "Apple Silicon GPU"
                    out["runtime_device_name"] = out["device_name"]
                    out["available_backends"].append("mps")
                    out["integrated_acceleration"] = True
                    out["gpu_vendor"] = "apple"
            except Exception:
                pass
    except Exception:
        pass

    try:
        nvidia_gpu = nvidia_host_gpu_status()
    except Exception:
        nvidia_gpu = {"available": False, "ok": False, "gpus": [], "error": ""}
    host_gpus = [gpu for gpu in list(nvidia_gpu.get("gpus") or []) if isinstance(gpu, dict)]
    if host_gpus:
        primary_gpu = host_gpus[0]
        host_name = str(primary_gpu.get("name") or "NVIDIA GPU")
        host_vram_gb = _parse_gpu_memory_gb(primary_gpu.get("memory_total"))
        out["host_gpu_visible"] = bool(nvidia_gpu.get("ok"))
        out["host_gpu_name"] = host_name
        out["host_gpu_vram_gb"] = host_vram_gb
        out["host_gpus"] = host_gpus
        out["nvidia_driver_version"] = primary_gpu.get("driver_version")
        if not out.get("gpu_vendor"):
            out["gpu_vendor"] = "nvidia"
        if str(out.get("backend") or "cpu").lower() == "cpu":
            out["device_name"] = host_name
            out["vram_gb"] = max(float(out.get("vram_gb") or 0.0), host_vram_gb)

    directml = _directml_runtime_status()
    out["supports_directml"] = bool(directml.get("available"))
    out["directml_runtime_ready"] = bool(directml.get("runtime_ready"))
    out["directml_device_name"] = directml.get("device_name")
    if directml.get("available"):
        if "directml" not in out["available_backends"]:
            out["available_backends"].append("directml")
        if out["backend"] == "cpu":
            out["backend"] = "directml"
            out["device"] = "directml"
            out["device_name"] = str(directml.get("device_name") or "DirectML GPU")
            out["runtime_device_name"] = out["device_name"]
            out["vram_gb"] = max(float(out.get("vram_gb") or 0.0), float(directml.get("vram_gb") or 0.0))
            out["integrated_acceleration"] = bool(directml.get("integrated"))
            out["gpu_vendor"] = directml.get("vendor")
        elif not out.get("gpu_vendor") and directml.get("vendor"):
            out["gpu_vendor"] = directml.get("vendor")

    out["backend_family"] = _backend_family_for(
        str(out.get("backend") or "cpu"),
        integrated=bool(out.get("integrated_acceleration")),
    )
    plan = _build_internal_render_plan(out, requested_tier="auto")
    out["recommended_tier"] = plan["recommended_tier"]
    out["max_supported_tier"] = plan["max_supported_tier"]
    out["preferred_internal_model"] = plan["preferred_internal_model"]
    out["device_preference"] = plan["device_preference"]
    out["supports_internal_diffusion"] = True
    out["supports_proxy_render"] = True
    return out


def _render_profiles_for_hardware(hw: dict[str, Any] | None = None) -> dict[str, Any]:
    hw = dict(hw or _hardware_profile())
    recommended_tier = str(hw.get("recommended_tier") or "draft")
    backend_family = str(hw.get("backend_family") or "cpu_only")
    profiles = {
        "laptop_safe": {
            "label": "Laptop-safe",
            "description": "Fastest and safest defaults for CPU-only and integrated-GPU systems.",
            "render_preset": "fast",
            "internal_render_tier": "draft",
            "resume_existing_frames": True,
        },
        "balanced_auto": {
            "label": "Balanced auto",
            "description": "Recommended general-purpose defaults that follow current hardware planning.",
            "render_preset": "balanced",
            "internal_render_tier": "auto",
            "resume_existing_frames": True,
        },
        "high_quality": {
            "label": "High quality",
            "description": "Higher output quality for stronger GPUs and patient renders.",
            "render_preset": "quality",
            "internal_render_tier": "quality",
            "resume_existing_frames": True,
        },
    }
    recommended_profile = "balanced_auto"
    if backend_family in {"cpu_only", "integrated_gpu"} or recommended_tier == "draft":
        recommended_profile = "laptop_safe"
    elif recommended_tier == "quality":
        recommended_profile = "high_quality"
    return {"ok": True, "recommended_profile": recommended_profile, "profiles": profiles, "hardware": hw}


@app.get("/v1/settings/render_profiles")
def render_profiles():
    return _render_profiles_for_hardware()


@app.get("/v1/hardware")
def hardware():
    hw = _hardware_profile()
    return {"ok": True, "hardware": hw, "render_tier_plan": _build_internal_render_plan(hw, requested_tier="auto")}


def _render_provider_status(hw: dict[str, Any] | None = None) -> dict[str, Any]:
    hw = dict(hw or _hardware_profile())
    cfg = render_settings.get()
    stability_cfg = dict(cfg.get("stability") or {})
    directml_cfg = dict(cfg.get("directml") or {})
    has_stability_key = bool(secrets.get("stability_api_key"))
    stability_enabled = bool(stability_cfg.get("enabled"))
    stability_service = str(stability_cfg.get("service") or "sd3")
    stability_model = str(stability_cfg.get("model") or "sd3.5-large-turbo")
    directml_available = bool(hw.get("supports_directml"))
    directml_enabled = bool(directml_cfg.get("enabled"))
    return {
        "ok": True,
        "settings": cfg,
        "stability": {
            "provider": "stability",
            "configured": bool(has_stability_key),
            "enabled": stability_enabled,
            "visible": bool(has_stability_key and stability_enabled),
            "has_api_key": has_stability_key,
            "allow_auto_fallback": bool(stability_cfg.get("allow_auto_fallback", True)),
            "service": stability_service,
            "model": stability_model,
            "style_preset": str(stability_cfg.get("style_preset") or "none"),
            "output_format": str(stability_cfg.get("output_format") or "png"),
            "supports_video_api": False,
            "note": "Studio uses the current public Stability image API for hosted keyframes, then assembles video locally. A public hosted video route was not found in the current API spec.",
        },
        "directml": {
            "provider": "onnxruntime-directml",
            "enabled": directml_enabled,
            "available": directml_available,
            "active": bool(directml_enabled and str(hw.get("backend") or "cpu").lower() == "directml"),
            "runtime_ready": bool(hw.get("directml_runtime_ready")),
            "device_name": hw.get("directml_device_name") or hw.get("device_name"),
            "preferred_model": str(directml_cfg.get("preferred_model") or "auto"),
            "allow_auto_selection": bool(directml_cfg.get("allow_auto_selection", True)),
        },
        "stability_services": list(STABILITY_SERVICES),
        "stability_models": list(STABILITY_SD3_MODELS),
        "style_presets": list(STABILITY_STYLE_PRESETS),
        "hardware": hw,
    }


def _hosted_stability_ready(payload: dict[str, Any] | None = None) -> bool:
    payload = payload or {}
    provider = _render_provider_status().get("stability") or {}
    if not provider.get("configured") or not provider.get("enabled"):
        return False
    requested_mode = str(payload.get("render_mode") or "auto").strip().lower()
    if requested_mode == "hosted":
        return True
    return bool(provider.get("allow_auto_fallback")) and bool(payload.get("allow_hosted_fallback", True))


@app.get("/v1/settings/render_providers")
def get_render_providers():
    return _render_provider_status()


@app.post("/v1/settings/render_providers")
def set_render_providers(payload: dict[str, Any]):
    saved = render_settings.update(payload)
    return {
        "ok": True,
        "settings": saved,
        "status": _render_provider_status(),
    }


@app.get("/v1/config")
def get_config():
    provider_status = _render_provider_status()
    nvidia_profile = nvidia_profile_status()
    return {
        "studio_home": str(settings.studio_home),
        "data_dir": str(settings.data_dir),
        "models_dir": str(settings.models_dir),
        "ollama_models_dir": str(settings.ollama_models_dir),
        "cache_dir": str(settings.cache_dir),
        "logs_dir": str(settings.logs_dir),
        "external_dir": str(settings.external_dir),
        "ai_mode": settings.ai_mode,
        "ai_base_url": settings.ai_base_url,
        "ai_timeout_s": settings.ai_timeout_s,
        "ai_provider": os.getenv("EDMG_AI_PROVIDER", "openai_compat").strip().lower() or "openai_compat",
        "ai_ollama_url": os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434").strip(),
        "ai_ollama_model": os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen3:8b").strip(),
        "ai_openai_compat_base_url": os.getenv("EDMG_AI_OPENAI_COMPAT_BASE_URL", "http://127.0.0.1:8001/v1").strip(),
        "ai_openai_compat_model": os.getenv("EDMG_AI_OPENAI_COMPAT_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1.5").strip(),
        "ai_openai_compat_api_key_configured": bool(
            secrets.get("openai_compat_api_key") or os.getenv("EDMG_AI_OPENAI_COMPAT_API_KEY")
        ),
        "stability_api_key_configured": bool(secrets.get("stability_api_key")),
        "comfyui_url": settings.comfyui_url,
        "comfyui_urls": list(settings.resolved_comfyui_urls()),
        "comfyui_node_concurrency": settings.comfyui_node_concurrency,
        "comfyui_checkpoint": settings.comfyui_checkpoint,
        "ffmpeg_path": settings.ffmpeg_path,
        "worker_autostart": settings.worker_autostart,
        "worker_concurrency": settings.worker_concurrency,
        "worker_poll_interval_s": settings.worker_poll_interval_s,
        "secrets_store": secrets.status().store,
        "render_provider_settings": provider_status.get("settings"),
        "render_provider_status": provider_status,
        "nvidia_profile": nvidia_profile,
        "nvidia_mode": nvidia_profile.get("enabled"),
        "nvidia_profile_name": nvidia_profile.get("profile"),
    }


@app.get("/v1/settings/secrets/status")
def secrets_status():
    """Return whether optional tokens are configured (never returns the values)."""
    st = secrets.status()
    return {
        "ok": True,
        "store": st.store,
        "available": st.available,
        "has_hf_token": st.has_hf_token,
        "has_civitai_api_key": st.has_civitai_api_key,
        "has_openai_compat_api_key": st.has_openai_compat_api_key,
        "has_stability_api_key": st.has_stability_api_key,
        "note": st.note,
    }


@app.post("/v1/settings/secrets/set")
def secrets_set(payload: dict[str, Any]):
    name = str((payload or {}).get("name") or "").strip().lower()
    value = str((payload or {}).get("value") or "")
    if name not in ("hf_token", "civitai_api_key", "openai_compat_api_key", "stability_api_key"):
        raise UserFacingError(
            "Unknown secret",
            hint="Supported: hf_token, civitai_api_key, openai_compat_api_key, stability_api_key",
        )
    if not value:
        raise UserFacingError("Missing value", hint="Paste the token/key value, then click Save.")
    secrets.set(name, value)
    return {"ok": True}


@app.post("/v1/settings/secrets/clear")
def secrets_clear(payload: dict[str, Any]):
    name = str((payload or {}).get("name") or "").strip().lower()
    if name not in ("hf_token", "civitai_api_key", "openai_compat_api_key", "stability_api_key"):
        raise UserFacingError(
            "Unknown secret",
            hint="Supported: hf_token, civitai_api_key, openai_compat_api_key, stability_api_key",
        )
    secrets.delete(name)
    return {"ok": True}


def _setup_ai_config() -> dict[str, Any]:
    ai_mode = (settings.ai_mode or "local").strip().lower() or "local"
    ai_provider = (os.getenv("EDMG_AI_PROVIDER", "openai_compat").strip().lower() or "openai_compat")
    nvidia_profile = nvidia_profile_status()
    ollama_url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    ollama_model = os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen3:8b")
    openai_compat_base_url = os.getenv("EDMG_AI_OPENAI_COMPAT_BASE_URL", "http://127.0.0.1:8001/v1")
    openai_compat_model = os.getenv("EDMG_AI_OPENAI_COMPAT_MODEL", "nvidia/llama-3.3-nemotron-super-49b-v1.5")
    openai_compat_api_key_configured = bool(
        secrets.get("openai_compat_api_key") or os.getenv("EDMG_AI_OPENAI_COMPAT_API_KEY")
    )

    if ai_mode in ("http", "remote"):
        return {
            "mode": "http",
            "provider": "remote_ai_service",
            "label": "Remote AI service",
            "ollama_required": False,
            "model_required": False,
            "base_url": settings.ai_base_url,
            "hint": "Studio planning is configured to call a separate EDMG AI service over HTTP.",
        }

    if ai_provider in ("openai_compat", "openai-compatible", "openai"):
        label = "NVIDIA NIM / OpenAI-compatible provider" if nvidia_profile.get("enabled") else "Local OpenAI-compatible provider"
        return {
            "mode": "local",
            "provider": "openai_compat",
            "label": label,
            "ollama_required": False,
            "model_required": False,
            "base_url": openai_compat_base_url,
            "model": openai_compat_model,
            "openai_compat_api_key_configured": openai_compat_api_key_configured,
            "nvidia_profile": nvidia_profile,
            "hint": (
                "Studio planning is configured for a NVIDIA/NIM-style OpenAI-compatible endpoint."
                if nvidia_profile.get("enabled")
                else "Studio planning is configured for an OpenAI-compatible endpoint instead of Ollama."
            ),
        }

    if ai_provider == "rule_based":
        return {
            "mode": "local",
            "provider": "rule_based",
            "label": "Rule-based fallback",
            "ollama_required": False,
            "model_required": False,
            "hint": "Studio planning is configured for the built-in rule-based fallback. Ollama is optional.",
        }

    return {
        "mode": "local",
        "provider": "ollama",
        "label": "Local Ollama",
        "ollama_required": True,
        "model_required": True,
        "base_url": ollama_url,
        "model": ollama_model,
        "hint": "Studio planning is configured for local Ollama.",
    }


@app.get("/v1/setup/status")
def setup_status():
    """Installer GUI status for required components."""
    is_windows = platform.system() == "Windows"
    ai_config = _setup_ai_config()
    ollama_url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    ollama_model = os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen3:8b")
    ollama = check_ollama(ollama_url, ollama_model)
    ollama_exe = None
    ollama_exe_error = None
    try:
        ollama_exe = _find_ollama_exe(settings.external_dir)
    except Exception as e:
        ollama_exe_error = str(e)
    ollama["managed_models_dir"] = str(settings.ollama_models_dir)
    ollama["managed_launch_script"] = str(managed_ollama_launch_script_path(settings.external_dir))
    ollama["launch_available"] = bool(ollama_exe)
    ollama["ollama_exe"] = ollama_exe
    ollama["managed_running"] = bool(ollama_managed.running())
    if ollama_exe_error and not ollama.get("ok"):
        ollama["launch_hint"] = ollama_exe_error
    elif ollama_exe and not ollama.get("ok"):
        ollama["hint"] = (
            f"Studio can start Ollama with models stored under {settings.ollama_models_dir}. "
            "Use Start Studio-managed Ollama, or run the helper script after installing Ollama."
        )
    elif not ollama.get("ok"):
        ollama["hint"] = (
            (
                f"Studio can install Ollama into {settings.external_dir / 'ollama'} and keep models under "
                f"{settings.ollama_models_dir}."
            )
            if is_windows
            else "Install Ollama system-wide, or set EDMG_OLLAMA_PATH to your ollama binary, then point Studio at the running Ollama service."
        )

    # ComfyUI availability
    try:
        resolved_checkpoint, fallback_from = _resolve_comfy_checkpoint_name(
            settings.comfyui_checkpoint,
            allow_auto_fallback=True,
        )
        diag = comfy_pool.diagnose({"checkpoint": resolved_checkpoint})
        comfy_ok = bool(diag.get("compatible") or diag.get("busy_compatible"))
        if comfy_ok and fallback_from:
            comfy_hint = (
                f"Configured checkpoint `{fallback_from}` is unavailable; Studio will use `{resolved_checkpoint}` until the configured checkpoint is installed."
            )
        else:
            comfy_hint = None if comfy_ok else (
                "Install and start ComfyUI (Portable) or ComfyUI Desktop, then ensure it is reachable at the configured URL(s)."
                if is_windows
                else "Install and start ComfyUI, then ensure it is reachable at the configured URL(s)."
            )
        comfy_status = {
            "ok": comfy_ok,
            "url": settings.resolved_comfyui_urls()[0] if settings.resolved_comfyui_urls() else settings.comfyui_url,
            "checkpoint": resolved_checkpoint,
            "requested_checkpoint": settings.comfyui_checkpoint,
            "checkpoint_fallback_from": fallback_from,
            "diagnose": diag,
            "portable_installed": comfy_portable_installed(settings.external_dir, settings.data_dir),
            "hint": comfy_hint,
        }
    except Exception as e:
        comfy_status = {
            "ok": False,
            "url": settings.comfyui_url,
            "checkpoint": settings.comfyui_checkpoint,
            "portable_installed": comfy_portable_installed(settings.external_dir, settings.data_dir),
            "error": str(e),
            "hint": (
                "Configure EDMG_COMFYUI_URL to a running ComfyUI instance, or install ComfyUI Portable via this wizard."
                if is_windows
                else "Configure EDMG_COMFYUI_URL to a running ComfyUI instance."
            ),
        }

    ff = check_ffmpeg(settings.ffmpeg_path)
    backend_bundle = check_backend_bundle()
    backend_bundle_directml = check_backend_bundle("studio_bundle_directml")
    edmg = core_status()
    if not edmg.get("available"):
        edmg.setdefault(
            "hint",
            "Studio backend installs should include EDMG Core by default. Use this wizard to repair the backend environment if Core is missing.",
        )

    
    # 7-Zip CLI (needed to extract some .7z archives, e.g., ComfyUI Portable BCJ2)
    if not is_windows:
        seven = {
            "ok": True,
            "path": shutil.which("7z") or shutil.which("7zz"),
            "hint": "Portable 7-Zip install is only needed for the Windows ComfyUI Portable workflow.",
        }
    else:
        try:
            seven_path = _find_7z_exe(settings.external_dir, settings.data_dir)
            seven = {"ok": True, "path": seven_path, "hint": None}
        except Exception:
            seven = {"ok": False, "path": None, "hint": "Download the portable 7-Zip CLI into the Studio external tools folder, or set EDMG_7Z_PATH."}

    hw = _hardware_profile()
    return {
            "ok": True,
            "ai_config": ai_config,
            "nvidia_profile": nvidia_profile_status(),
            "backend_bundle": backend_bundle,
            "backend_bundle_directml": backend_bundle_directml,
            "ollama": ollama,
            "comfyui": comfy_status,
            "ffmpeg": ff,
            "edmg": edmg,
            "sevenzip": seven,
            "hardware": hw,
            "tasks": [t.to_dict() for t in setup_tasks.list()[:10]],
        }


@app.post("/v1/setup/tasks/{task_id}/cancel")
def setup_task_cancel(task_id: str):
    task = setup_tasks.cancel(task_id)
    if task is None:
        raise HTTPException(404, f"Setup task not found: {task_id}")
    return {"ok": True, "task": task.to_dict()}


@app.post("/v1/setup/ollama/install_managed")
def setup_ollama_install_managed():
    dest = settings.external_dir / "_installers"
    url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    task = setup_tasks.start(
        "install_managed_ollama",
        download_and_install_ollama,
        dest,
        settings.external_dir,
        settings.models_dir,
        url,
    )
    return {"ok": True, "task": task.to_dict()}


@app.post("/v1/setup/ollama/download_and_run")
def setup_ollama_download_and_run():
    return setup_ollama_install_managed()


@app.post("/v1/setup/ollama/start_managed")
def setup_ollama_start_managed():
    url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    task = setup_tasks.start(
        "start_managed_ollama",
        ollama_managed.start,
        settings.external_dir,
        settings.models_dir,
        url,
    )
    return {"ok": True, "task": task.to_dict()}


@app.post("/v1/setup/ollama/pull")
def setup_ollama_pull(payload: dict[str, Any]):
    import os

    model = (payload or {}).get("model") or os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen3:8b")
    url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    task = setup_tasks.start(f"pull_model:{model}", pull_ollama_model, url, model)
    return {"ok": True, "task": task.to_dict()}

@app.post("/v1/setup/7zip/install")
def setup_7zip_install():
    """Download the portable 7-Zip CLI (required for extracting some .7z archives)."""
    task = setup_tasks.start("install_7zip", download_and_install_7zip, settings.external_dir, settings.data_dir)
    return {"ok": True, "task": task.to_dict()}

@app.post("/v1/setup/backend/install")
def setup_backend_install(payload: dict[str, Any]):
    bundle = str((payload or {}).get("bundle") or "studio_bundle").strip() or "studio_bundle"
    task = setup_tasks.start(f"install_backend_bundle:{bundle}", install_backend_bundle, bundle)
    return {"ok": True, "task": task.to_dict()}

@app.post("/v1/setup/full/install")
def setup_full_install(payload: dict[str, Any]):
    """Run one-click setup for the NVIDIA-first stack plus local fallback engines."""
    import os

    flavor = (payload or {}).get("flavor") or "cpu"
    port = int((payload or {}).get("comfy_port") or 8188)
    bundle = str((payload or {}).get("bundle") or "studio_bundle").strip() or "studio_bundle"
    if flavor == "amd" and bundle == "studio_bundle":
        bundle = "studio_bundle_directml"
    model = (payload or {}).get("model") or os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen3:8b")
    ollama_url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    ai_config = _setup_ai_config()

    def _run(task):
        # 1) Ensure backend runtime bundle is present for audio, ASR, and render support paths.
        SetupTaskManager.check_canceled(task, "Full setup canceled.")
        if not check_backend_bundle(bundle).get("ok"):
            install_backend_bundle(task, bundle)
        else:
            SetupTaskManager.log(task, f"Backend runtime bundle `{bundle}` already installed.")

        # 2) Ensure 7-Zip for .7z extraction
        SetupTaskManager.check_canceled(task, "Full setup canceled.")
        try:
            _find_7z_exe(settings.external_dir, settings.data_dir)
        except Exception:
            download_and_install_7zip(task, settings.external_dir, settings.data_dir)

        # 3) Ollama install/model only when the active AI path actually uses Ollama as fallback.
        SetupTaskManager.check_canceled(task, "Full setup canceled.")
        if ai_config.get("ollama_required"):
            ollama_status = check_ollama(ollama_url, model)
            if not ollama_status.get("ok"):
                try:
                    ollama_managed.start(task, settings.external_dir, settings.models_dir, ollama_url)
                except Exception:
                    dest = settings.external_dir / "_installers"
                    download_and_install_ollama(task, dest, settings.external_dir, settings.models_dir, ollama_url)
                    ollama_managed.start(task, settings.external_dir, settings.models_dir, ollama_url)
            else:
                SetupTaskManager.log(task, "Ollama is already reachable.")

            ollama_status = check_ollama(ollama_url, model)
            if not ollama_status.get("model_present"):
                pull_ollama_model(task, ollama_url, model)
            else:
                SetupTaskManager.log(task, f"Ollama model {model} is already present.")
        else:
            SetupTaskManager.log(
                task,
                f"Skipping Ollama install because Studio AI is configured for {ai_config.get('label')}.",
            )

        # 4) ComfyUI Portable secondary workflow install + start
        SetupTaskManager.check_canceled(task, "Full setup canceled.")
        if not comfy_portable_installed(settings.external_dir, settings.data_dir):
            download_and_extract_portable(task, settings.external_dir, flavor, settings.data_dir, settings.models_dir)
        else:
            SetupTaskManager.log(task, "ComfyUI Portable is already installed.")

        comfy_ready = False
        try:
            diag = comfy_pool.diagnose({})
            comfy_ready = bool(diag.get("compatible") or diag.get("busy_compatible"))
        except Exception:
            comfy_ready = False

        if comfy_ready:
            SetupTaskManager.log(task, "ComfyUI is already reachable.")
        else:
            comfy_portable.start(task, settings.external_dir, flavor, "127.0.0.1", port, settings.data_dir, settings.models_dir)

    task = setup_tasks.start(f"full_setup:{flavor}:{ai_config.get('provider')}", _run)
    return {"ok": True, "task": task.to_dict()}


@app.post("/v1/setup/comfyui/portable/install")
def setup_comfyui_portable_install(payload: dict[str, Any]):
    flavor = (payload or {}).get("flavor") or "cpu"
    task = setup_tasks.start(
        f"install_comfyui_portable:{flavor}",
        download_and_extract_portable,
        settings.external_dir,
        flavor,
        settings.data_dir,
        settings.models_dir,
    )
    return {"ok": True, "task": task.to_dict()}


@app.post("/v1/setup/comfyui/portable/start")
def setup_comfyui_portable_start(payload: dict[str, Any]):
    flavor = (payload or {}).get("flavor") or "cpu"
    port = int((payload or {}).get("port") or 8188)
    task = setup_tasks.start(
        f"start_comfyui_portable:{flavor}",
        comfy_portable.start,
        settings.external_dir,
        flavor,
        "127.0.0.1",
        port,
        settings.data_dir,
        settings.models_dir,
    )
    return {"ok": True, "task": task.to_dict()}


@app.post("/v1/setup/comfyui/portable/stop")
def setup_comfyui_portable_stop():
    comfy_portable.stop()
    return {"ok": True}


@app.post("/v1/setup/edmg/install")
def setup_edmg_install(payload: dict[str, Any]):
    mode = str((payload or {}).get("mode") or "standard").strip().lower() or "standard"
    backend = str((payload or {}).get("backend") or "cpu").strip().lower() or "cpu"
    task = setup_tasks.start(f"install_edmg_core:{mode}:{backend}", edmg_install_core, settings.data_dir, mode=mode, backend=backend)
    return {"ok": True, "task": task.to_dict()}


@app.get("/v1/ai/status")
def ai_status():
    return {"ok": True, "ai": ai.status()}

@app.get("/v1/nvidia/status")
def nvidia_status():
    return {"ok": True, "nvidia": nvidia_profile_status()}

@app.get("/v1/nvidia/diagnostics")
def nvidia_diagnostics_route():
    return {"ok": True, "nvidia": nvidia_diagnostics()}


@app.get("/v1/nvidia/services")
def nvidia_services_route():
    diagnostics = nvidia_diagnostics()
    return {
        "ok": True,
        "services": diagnostics.get("services", {}),
        "readiness": diagnostics.get("readiness", {}),
        "profile": diagnostics.get("profile", {}),
    }


@app.get("/v1/nvidia/services/{service_id}/status")
def nvidia_service_status_route(service_id: str):
    normalized = normalize_nvidia_service_id(service_id)
    diagnostics = nvidia_diagnostics()
    services = diagnostics.get("services", {}) if isinstance(diagnostics.get("services"), dict) else {}
    service = services.get(normalized)
    if not isinstance(service, dict):
        raise HTTPException(404, f"NVIDIA service '{service_id}' was not found")
    return {
        "ok": True,
        "service_id": normalized,
        "service": service,
        "profile": diagnostics.get("profile", {}),
    }


@app.post("/v1/nvidia/services/{service_id}/invoke")
def nvidia_service_invoke_route(service_id: str, payload: dict[str, Any] | None = None):
    body = payload or {}
    if not isinstance(body, dict):
        raise HTTPException(400, "NVIDIA service invoke request root must be an object")
    path = str(body.get("path") or body.get("endpoint") or "").strip()
    method = str(body.get("method") or "POST").strip().upper()
    request_payload = body.get("payload", body.get("body"))
    try:
        timeout_s = float(body.get("timeout_s") or 30.0)
    except Exception as exc:
        raise HTTPException(400, "timeout_s must be numeric") from exc
    try:
        result = invoke_nvidia_service(
            service_id,
            path,
            payload=request_payload,
            method=method,
            timeout_s=timeout_s,
        )
    except NvidiaOptionalServiceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"ok": bool(result.get("ok")), "result": result, "service_id": result.get("service_id")}


def _nvidia_timeout(body: dict[str, Any], default: float) -> float:
    try:
        return float(body.get("timeout_s") or default)
    except Exception as exc:
        raise HTTPException(400, "timeout_s must be numeric") from exc


@app.post("/v1/nvidia/audio2face/generate")
def nvidia_audio2face_generate_route(payload: dict[str, Any] | None = None):
    body = payload or {}
    if not isinstance(body, dict):
        raise HTTPException(400, "Audio2Face request root must be an object")
    try:
        result = audio2face_generate(body, timeout_s=_nvidia_timeout(body, 60.0))
    except NvidiaOptionalServiceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"ok": bool(result.get("ok")), "audio2face": result}


@app.post("/v1/nvidia/ace/generate")
def nvidia_ace_generate_route(payload: dict[str, Any] | None = None):
    body = payload or {}
    if not isinstance(body, dict):
        raise HTTPException(400, "ACE request root must be an object")
    try:
        result = ace_generate(body, timeout_s=_nvidia_timeout(body, 60.0))
    except NvidiaOptionalServiceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"ok": bool(result.get("ok")), "ace": result}


@app.post("/v1/nvidia/cosmos/infer")
def nvidia_cosmos_infer_route(payload: dict[str, Any] | None = None):
    body = payload or {}
    if not isinstance(body, dict):
        raise HTTPException(400, "Cosmos inference request root must be an object")
    try:
        result = cosmos_infer(body, timeout_s=_nvidia_timeout(body, 120.0))
    except NvidiaOptionalServiceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"ok": bool(result.get("ok")), "cosmos": result}


@app.post("/v1/nvidia/triton/infer")
def nvidia_triton_infer_route(payload: dict[str, Any] | None = None):
    body = payload or {}
    if not isinstance(body, dict):
        raise HTTPException(400, "Triton inference request root must be an object")
    try:
        timeout_s = float(body.get("timeout_s") or 30.0)
    except Exception as exc:
        raise HTTPException(400, "timeout_s must be numeric") from exc
    try:
        result = triton_infer(
            model=str(body.get("model") or "").strip(),
            inputs=body.get("inputs"),
            outputs=body.get("outputs"),
            parameters=body.get("parameters"),
            timeout_s=timeout_s,
        )
    except NvidiaOptionalServiceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"ok": bool(result.get("ok")), "triton": result}


@app.post("/v1/nvidia/triton/shot-features")
def nvidia_triton_shot_features_route(payload: dict[str, Any] | None = None):
    body = payload or {}
    if not isinstance(body, dict):
        raise HTTPException(400, "Triton shot-feature request root must be an object")
    try:
        result = triton_shot_features(body, timeout_s=_nvidia_timeout(body, 30.0))
    except NvidiaOptionalServiceError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"ok": bool(result.get("ok")), "triton": result}


@app.get("/v1/nvidia/riva/status")
def nvidia_riva_status_route():
    return {"ok": True, "riva": riva_asr_status(), "nvidia": nvidia_profile_status()}


def _persist_nvidia_scene_plan(
    project_id: str,
    *,
    request_payload: dict[str, Any],
    planner_summary: dict[str, Any],
    scene_plan: dict[str, Any],
    usd_metadata: dict[str, Any],
    usd_stage: dict[str, Any],
) -> dict[str, Any]:
    project_id = str(project_id or "").strip()
    if not project_id:
        return {"persisted": False, "reason": "project_id_missing"}

    proj = store.get(project_id)
    if not proj:
        return {"persisted": False, "project_id": project_id, "reason": "project_not_found"}

    saved_at = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "project_id": project_id,
        "saved_at": saved_at,
        "request": request_payload,
        "planner": planner_summary,
        "scene_plan": scene_plan,
        "usd_metadata": usd_metadata,
        "usd_stage": usd_stage,
        "nvidia_profile": nvidia_profile_status(),
    }
    proj.meta["last_nvidia_scene_plan"] = entry
    history = proj.meta.setdefault("nvidia_scene_plan_history", [])
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "saved_at": saved_at,
            "title": scene_plan.get("title"),
            "scene_count": len(scene_plan.get("scenes") or []),
            "provider": scene_plan.get("provider"),
            "planner": planner_summary,
        }
    )
    proj.meta["nvidia_scene_plan_history"] = history[-10:]
    store.save(proj)
    return {"persisted": True, "project_id": project_id, "saved_at": saved_at}


@app.post("/v1/nvidia/scene-plan")
def nvidia_generate_scene_plan(payload: dict[str, Any]):
    if not isinstance(payload, dict):
        raise HTTPException(400, "NVIDIA scene-plan request root must be an object")

    try:
        variant_index = int(payload.get("variant_index") or 0)
        num_variants = max(1, min(10, int(payload.get("num_variants") or 1)))
        max_scenes = max(1, min(64, int(payload.get("max_scenes") or 6)))
    except Exception as exc:
        raise HTTPException(400, "variant_index, num_variants, and max_scenes must be integers") from exc

    planner_payload = {
        "title": payload.get("title") or "EDMG NVIDIA Scene Plan",
        "user_notes": payload.get("user_notes") or payload.get("notes") or "",
        "duration_s": payload.get("duration_s") or 60,
        "bpm": payload.get("bpm"),
        "lyrics": payload.get("lyrics") or "",
        "tags": payload.get("tags") if isinstance(payload.get("tags"), list) else [],
        "style_prefs": payload.get("style_prefs") or "",
        "num_variants": num_variants,
        "max_scenes": max_scenes,
    }

    try:
        planner_response = ai.plan(planner_payload)
    except Exception as exc:
        raise HTTPException(
            502,
            {
                "message": "The configured NVIDIA/NIM-compatible planner is not available.",
                "error": str(exc),
                "nvidia": nvidia_profile_status(),
            },
        ) from exc

    if not isinstance(planner_response, dict):
        raise HTTPException(502, "NVIDIA planner returned a non-object response")

    try:
        scene_plan = plan_response_to_scene_plan(
            planner_response,
            payload,
            variant_index=variant_index,
            project_id=str(payload.get("project_id") or "").strip() or None,
        )
    except Exception as exc:
        raise HTTPException(
            502,
            {
                "message": "The NVIDIA planner response could not be converted into a USD scene plan.",
                "error": str(exc),
                "planner": {
                    "provider": planner_response.get("provider"),
                    "model": planner_response.get("model"),
                    "variant_index": variant_index,
                },
            },
        ) from exc

    normalized_scene_plan = normalize_scene_plan(scene_plan)
    usd_metadata = scene_plan_usd_metadata(scene_plan)
    usd_stage = {
        "format": "usda",
        "text": scene_plan_usda_text(scene_plan),
    }
    planner_summary = {
        "provider": planner_response.get("provider"),
        "model": planner_response.get("model"),
        "variant_index": variant_index,
        "variant_count": len(planner_response.get("variants") or []),
    }
    project_status = _persist_nvidia_scene_plan(
        str(payload.get("project_id") or "").strip(),
        request_payload=planner_payload,
        planner_summary=planner_summary,
        scene_plan=normalized_scene_plan,
        usd_metadata=usd_metadata,
        usd_stage=usd_stage,
    )

    return {
        "ok": True,
        "request": planner_payload,
        "planner": planner_summary,
        "scene_plan": normalized_scene_plan,
        "usd_metadata": usd_metadata,
        "usd_stage": usd_stage,
        "project": project_status,
        "nvidia": nvidia_profile_status(),
    }

@app.post("/v1/usd/scene-plan")
def nvidia_usd_scene_plan(payload: dict[str, Any]):
    if not isinstance(payload, dict):
        raise HTTPException(400, "scene plan root must be an object")
    errors = validate_scene_plan(payload)
    if errors:
        raise HTTPException(400, {"message": "Invalid NVIDIA scene plan", "errors": errors})
    return {
        "ok": True,
        "scene_plan": normalize_scene_plan(payload),
        "usd_metadata": scene_plan_usd_metadata(payload),
        "usd_stage": {
            "format": "usda",
            "text": scene_plan_usda_text(payload),
        },
        "nvidia": nvidia_profile_status(),
    }

@app.get("/v1/worker/status")
def worker_status():
    if worker is None:
        return {"ok": True, "running": False}
    st = worker.status()
    return {"ok": True, **st.__dict__}

@app.get("/v1/comfyui/nodes")
def comfyui_nodes():
    return {"ok": True, "nodes": comfy_pool.snapshot()}


@app.get("/v1/comfyui/object_info")
def comfyui_object_info():
    try:
        primary = settings.resolved_comfyui_urls()[0]
        return comfy.get_object_info(primary)
    except Exception as e:
        raise HTTPException(502, f"ComfyUI error: {e}")

@app.get("/v1/comfyui/capabilities")
def comfyui_capabilities():
    try:
        primary = settings.resolved_comfyui_urls()[0]
        obj = comfy.get_object_info(primary)
    except Exception as e:
        raise HTTPException(502, f"ComfyUI error: {e}")

    ad_ok, ad_missing = comfy.has_nodes(obj, ["ADE_AnimateDiffLoaderGen1", "ADE_StandardStaticContextOptions"])
    svd_ok, svd_missing = comfy.has_nodes(obj, ["SVDSimpleImg2Vid"])
    controlnet_ok, controlnet_missing = comfy.has_nodes(obj, ["LoadImage", "ControlNetLoader", "ControlNetApplyAdvanced"])
    detected_checkpoints = sorted(
        list(set(comfy_pool._extract_checkpoint_names(obj)[0]))  # type: ignore[attr-defined]
    )
    return {
        "comfyui_url": settings.comfyui_url,
        "comfyui_urls": list(settings.resolved_comfyui_urls()),
        "comfyui_node_concurrency": settings.comfyui_node_concurrency,
        "animatediff": {"available": ad_ok, "missing_nodes": ad_missing},
        "svd": {"available": svd_ok, "missing_nodes": svd_missing},
        "controlnet": {"available": controlnet_ok, "missing_nodes": controlnet_missing},
        "detected_checkpoints": detected_checkpoints,
    }

@app.get("/v1/edmg/status")
def edmg_status():
    return core_status()

@app.post("/v1/edmg/verify")
def edmg_verify():
    return edmg_selfcheck()

@app.get("/v1/edmg/deforum_template")
def edmg_template():
    try:
        return edmg_deforum_template()
    except Exception:
        # Not fatal; return minimal template so UI doesn't crash
        return {"note": "EDMG Core not installed or template unavailable."}

@app.get("/v1/projects")
def list_projects():
    return {"projects": [p.__dict__ for p in store.list()]}

@app.post("/v1/projects")
def create_project(req: ProjectCreateRequest):
    proj = store.create(req.name)
    return _project_response_payload(proj)

@app.get("/v1/projects/{project_id}")
def get_project(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    return _project_response_payload(proj)


@app.get("/v1/projects/{project_id}/nvidia/scene-plan")
def get_project_nvidia_scene_plan(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    entry = proj.meta.get("last_nvidia_scene_plan") if isinstance(proj.meta, dict) else None
    if not isinstance(entry, dict):
        raise HTTPException(404, "NVIDIA scene plan not found")

    history = proj.meta.get("nvidia_scene_plan_history") if isinstance(proj.meta, dict) else []
    return {
        "ok": True,
        "project_id": project_id,
        "scene_plan": entry.get("scene_plan"),
        "usd_metadata": entry.get("usd_metadata"),
        "usd_stage": entry.get("usd_stage"),
        "planner": entry.get("planner"),
        "request": entry.get("request"),
        "saved_at": entry.get("saved_at"),
        "history": history if isinstance(history, list) else [],
        "nvidia": nvidia_profile_status(),
    }


@app.post("/v1/projects/{project_id}/nvidia/scene-plan")
def generate_project_nvidia_scene_plan(project_id: str, payload: dict[str, Any]):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    if not isinstance(payload, dict):
        raise HTTPException(400, "NVIDIA scene-plan request root must be an object")

    request_payload = {**payload, "project_id": project_id}
    if not str(request_payload.get("title") or "").strip():
        request_payload["title"] = proj.name
    return nvidia_generate_scene_plan(request_payload)


@app.post("/v1/projects/{project_id}/nvidia/scene-plan/save")
def save_project_nvidia_scene_plan(project_id: str, payload: dict[str, Any] | None = None):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    body = payload or {}
    if not isinstance(body, dict):
        raise HTTPException(400, "NVIDIA scene-plan save request root must be an object")

    scene_plan = body.get("scene_plan") if isinstance(body.get("scene_plan"), dict) else body
    if not isinstance(scene_plan, dict):
        raise HTTPException(400, "scene_plan must be an object")
    scene_plan = {**scene_plan, "project_id": project_id}
    if not str(scene_plan.get("title") or "").strip():
        scene_plan["title"] = proj.name

    errors = validate_scene_plan(scene_plan)
    if errors:
        raise HTTPException(400, {"message": "Invalid NVIDIA scene plan", "errors": errors})

    normalized_scene_plan = normalize_scene_plan(scene_plan)
    usd_metadata = scene_plan_usd_metadata(normalized_scene_plan)
    usd_stage = {
        "format": "usda",
        "text": scene_plan_usda_text(normalized_scene_plan),
    }
    planner_payload = body.get("planner") if isinstance(body.get("planner"), dict) else {}
    planner_summary = {
        "provider": str(planner_payload.get("provider") or body.get("provider") or "manual-scene-plan").strip(),
        "model": str(planner_payload.get("model") or body.get("model") or "").strip(),
        "variant_index": int(planner_payload.get("variant_index") or 0),
        "variant_count": int(planner_payload.get("variant_count") or 1),
    }
    request_payload = body.get("request") if isinstance(body.get("request"), dict) else scene_plan
    project_status = _persist_nvidia_scene_plan(
        project_id,
        request_payload=request_payload,
        planner_summary=planner_summary,
        scene_plan=normalized_scene_plan,
        usd_metadata=usd_metadata,
        usd_stage=usd_stage,
    )
    return {
        "ok": True,
        "project": project_status,
        "planner": planner_summary,
        "scene_plan": normalized_scene_plan,
        "usd_metadata": usd_metadata,
        "usd_stage": usd_stage,
        "nvidia": nvidia_profile_status(),
    }


@app.post("/v1/projects/{project_id}/nvidia/scene-plan/export")
def export_project_nvidia_scene_plan(project_id: str, _payload: dict[str, Any] | None = None):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    entry = proj.meta.get("last_nvidia_scene_plan") if isinstance(proj.meta, dict) else None
    if not isinstance(entry, dict):
        raise HTTPException(404, "NVIDIA scene plan not found")

    scene_plan = entry.get("scene_plan") if isinstance(entry.get("scene_plan"), dict) else {}
    if not scene_plan:
        raise HTTPException(404, "NVIDIA scene plan not found")
    try:
        usda_text = scene_plan_usda_text(scene_plan)
        entry["usd_metadata"] = scene_plan_usd_metadata(scene_plan)
        entry["usd_stage"] = {
            "format": "usda",
            "text": usda_text,
            "bytes": len(usda_text.encode("utf-8")),
        }
        proj.meta["last_nvidia_scene_plan"] = entry
    except Exception:
        usd_stage = entry.get("usd_stage") if isinstance(entry.get("usd_stage"), dict) else {}
        usda_text = str(usd_stage.get("text") or "")
    if not usda_text.strip():
        raise HTTPException(400, "NVIDIA scene plan does not include a USDA preview")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    stem = _safe_name_tag(str(scene_plan.get("title") or proj.name or "nvidia_scene_plan"), "nvidia_scene_plan")
    pdir = store.project_dir(project_id)
    out_root = pdir / "outputs" / "nvidia"
    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / f"{stem}_{stamp}.usda"
    suffix = 2
    while out_path.exists():
        out_path = out_root / f"{stem}_{stamp}_{suffix}.usda"
        suffix += 1
    out_path.write_text(usda_text, encoding="utf-8", newline="\n")

    export_entry = {
        "path": out_path.relative_to(pdir).as_posix(),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scene_count": len(scene_plan.get("scenes") or []),
        "provider": scene_plan.get("provider"),
        "planner": entry.get("planner"),
        "source_saved_at": entry.get("saved_at"),
    }
    metadata_path = _output_metadata_path(out_path)
    metadata_path.write_text(json.dumps(export_entry, ensure_ascii=False, indent=2), encoding="utf-8")
    export_entry["metadata_path"] = metadata_path.relative_to(pdir).as_posix()

    exports = proj.meta.setdefault("exports", {})
    if not isinstance(exports, dict):
        exports = {}
    nvidia_exports = exports.setdefault("nvidia_scene_plans", [])
    if not isinstance(nvidia_exports, list):
        nvidia_exports = []
    nvidia_exports.append(export_entry)
    exports["nvidia_scene_plans"] = nvidia_exports[-20:]
    proj.meta["exports"] = exports
    store.save(proj)
    response_export = {
        **export_entry,
        "absolute_path": str(out_path.resolve()),
        "metadata_absolute_path": str(metadata_path.resolve()),
    }

    return {
        "ok": True,
        "project_id": project_id,
        "export": response_export,
    }


@app.post("/v1/projects/{project_id}/nvidia/riva/transcribe")
def transcribe_project_audio_with_riva(project_id: str, payload: dict[str, Any] | None = None):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    audio_meta = proj.meta.get("audio") if isinstance(proj.meta, dict) else None
    if not isinstance(audio_meta, dict) or not audio_meta.get("filename"):
        raise HTTPException(400, "No audio uploaded")

    audio_path = store.project_dir(project_id) / "assets" / "audio" / str(audio_meta["filename"])
    if not audio_path.exists() or not audio_path.is_file():
        raise HTTPException(404, "Audio file missing on disk")

    language_code = ""
    if isinstance(payload, dict):
        language_code = str(payload.get("language_code") or "").strip()
    try:
        transcript = transcribe_with_riva(
            audio_path,
            language_code=language_code or None,
            require_enabled=False,
        )
    except RivaTranscriptionError as exc:
        raise HTTPException(502, {"message": "NVIDIA Riva transcription failed", "error": str(exc)}) from exc

    saved_at = time.strftime("%Y-%m-%d %H:%M:%S")
    proj.meta["last_nvidia_riva_transcript"] = {
        "saved_at": saved_at,
        "audio": audio_meta,
        "transcript": transcript,
    }
    store.save(proj)
    return {
        "ok": True,
        "project_id": project_id,
        "saved_at": saved_at,
        "transcript": transcript,
        "riva": riva_asr_status(),
    }


@app.post("/v1/projects/{project_id}/nvidia/render-job")
def create_project_nvidia_render_job(project_id: str, payload: dict[str, Any] | None = None):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    entry = proj.meta.get("last_nvidia_scene_plan") if isinstance(proj.meta, dict) else None
    if not isinstance(entry, dict):
        raise HTTPException(404, "NVIDIA scene plan not found")
    scene_plan = entry.get("scene_plan") if isinstance(entry.get("scene_plan"), dict) else {}
    if not scene_plan:
        raise HTTPException(404, "NVIDIA scene plan not found")

    options = payload if isinstance(payload, dict) else {}
    try:
        fps = max(1, min(240, int(options.get("fps") or 24)))
        width = max(1, min(16384, int(options.get("width") or 1920)))
        height = max(1, min(16384, int(options.get("height") or 1080)))
    except Exception as exc:
        raise HTTPException(400, "fps, width, and height must be integers") from exc

    exports = proj.meta.get("exports") if isinstance(proj.meta.get("exports"), dict) else {}
    nvidia_scene_exports = exports.get("nvidia_scene_plans") if isinstance(exports.get("nvidia_scene_plans"), list) else []
    latest_usda = ""
    if nvidia_scene_exports:
        latest = nvidia_scene_exports[-1]
        if isinstance(latest, dict):
            latest_usda = str(latest.get("path") or "")

    job = build_rtx_render_job(
        scene_plan,
        project_id=project_id,
        project_name=proj.name,
        fps=fps,
        width=width,
        height=height,
        usda_path=str(options.get("usda_path") or latest_usda),
    )
    stamp = time.strftime("%Y%m%d_%H%M%S")
    stem = _safe_name_tag(str(scene_plan.get("title") or proj.name or "nvidia_render_job"), "nvidia_render_job")
    pdir = store.project_dir(project_id)
    out_root = pdir / "outputs" / "nvidia" / "render_jobs"
    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / f"{stem}_{stamp}.json"
    suffix = 2
    while out_path.exists():
        out_path = out_root / f"{stem}_{stamp}_{suffix}.json"
        suffix += 1
    job_outputs = job.get("outputs") if isinstance(job.get("outputs"), dict) else {}
    job_outputs["movie"] = f"outputs/nvidia/renders/{out_path.stem}.mp4"
    job["outputs"] = job_outputs
    out_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    export_entry = {
        "path": out_path.relative_to(pdir).as_posix(),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scene_count": int(job.get("scene_count") or 0),
        "duration_s": float(job.get("duration_s") or 0.0),
        "fps": fps,
        "resolution": job.get("renderer", {}).get("resolution") if isinstance(job.get("renderer"), dict) else {},
        "source_saved_at": entry.get("saved_at"),
    }
    exports = proj.meta.setdefault("exports", {})
    if not isinstance(exports, dict):
        exports = {}
    render_jobs = exports.setdefault("nvidia_render_jobs", [])
    if not isinstance(render_jobs, list):
        render_jobs = []
    render_jobs.append(export_entry)
    exports["nvidia_render_jobs"] = render_jobs[-20:]
    proj.meta["exports"] = exports
    store.save(proj)
    outputs_absolute = {
        str(key): str(safe_join(pdir, value))
        for key, value in job_outputs.items()
        if isinstance(key, str) and isinstance(value, str) and value.strip()
    }
    response_job = {**job}
    if outputs_absolute:
        response_job["outputs_absolute"] = outputs_absolute
    response_export = {
        **export_entry,
        "absolute_path": str(out_path.resolve()),
    }
    return {
        "ok": True,
        "project_id": project_id,
        "job": response_job,
        "export": response_export,
    }


@app.post("/v1/projects/{project_id}/nvidia/render-job/result")
def report_project_nvidia_render_job_result(project_id: str, payload: dict[str, Any] | None = None):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    body = payload if isinstance(payload, dict) else {}
    job_rel = str(body.get("job_path") or body.get("path") or "").strip().replace("\\", "/")
    if not job_rel:
        raise HTTPException(400, "job_path is required")

    allowed_statuses = {"queued", "running", "completed", "failed", "canceled"}
    status = str(body.get("status") or "").strip().lower()
    if status not in allowed_statuses:
        raise HTTPException(400, f"status must be one of: {', '.join(sorted(allowed_statuses))}")

    pdir = store.project_dir(project_id)
    try:
        job_path = safe_join(pdir, job_rel)
        jobs_root = (pdir / "outputs" / "nvidia" / "render_jobs").resolve()
        job_name = job_path.name.lower()
        if (
            job_path.suffix.lower() != ".json"
            or job_name.endswith(".result.json")
            or job_name.endswith(".json.json")
            or jobs_root not in job_path.resolve().parents
        ):
            raise ValueError("job_path must point to outputs/nvidia/render_jobs/*.json")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not job_path.exists() or not job_path.is_file():
        raise HTTPException(404, "Render job not found")

    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(400, "Render job JSON is invalid") from exc
    if not isinstance(job, dict):
        raise HTTPException(400, "Render job JSON must be an object")

    reported_at = time.strftime("%Y-%m-%d %H:%M:%S")
    raw_outputs = body.get("outputs")
    outputs = raw_outputs if isinstance(raw_outputs, dict) else {}
    result = {
        "schema_version": "edmg.nvidia.render_result.v1",
        "kind": "nvidia_rtx_render_result",
        "project_id": project_id,
        "job_path": job_path.relative_to(pdir).as_posix(),
        "status": status,
        "message": str(body.get("message") or ""),
        "outputs": outputs,
        "metrics": body.get("metrics") if isinstance(body.get("metrics"), dict) else {},
        "worker": body.get("worker") if isinstance(body.get("worker"), dict) else {},
        "reported_at": reported_at,
    }
    started_at = str(body.get("started_at") or "").strip()
    completed_at = str(body.get("completed_at") or "").strip()
    if started_at:
        result["started_at"] = started_at
    if completed_at:
        result["completed_at"] = completed_at

    result_path = job_path.with_name(f"{job_path.stem}.result.json")
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    job["status"] = status
    job["result"] = result
    job["updated_at"] = reported_at
    if status in {"completed", "failed", "canceled"}:
        job["completed_at"] = completed_at or reported_at
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    metadata_path = _output_metadata_path(job_path)
    metadata_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    exports = proj.meta.get("exports") if isinstance(proj.meta.get("exports"), dict) else {}
    render_jobs = exports.get("nvidia_render_jobs") if isinstance(exports.get("nvidia_render_jobs"), list) else []
    for entry in render_jobs:
        if isinstance(entry, dict) and str(entry.get("path") or "").replace("\\", "/") == job_rel:
            entry["status"] = status
            entry["result_path"] = result_path.relative_to(pdir).as_posix()
            entry["metadata_path"] = metadata_path.relative_to(pdir).as_posix()
            entry["updated_at"] = reported_at
            if outputs:
                entry["outputs"] = outputs
            break
    exports["nvidia_render_jobs"] = render_jobs[-20:] if isinstance(render_jobs, list) else []
    proj.meta["exports"] = exports
    proj.meta["last_nvidia_render_result"] = result
    store.save(proj)

    return {
        "ok": True,
        "project_id": project_id,
        "job_path": job_path.relative_to(pdir).as_posix(),
        "result_path": result_path.relative_to(pdir).as_posix(),
        "metadata_path": metadata_path.relative_to(pdir).as_posix(),
        "result": result,
    }


@app.get("/v1/projects/{project_id}/visual_dna")
def get_project_visual_dna(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    dna = _load_project_visual_dna(proj)
    return {
        "ok": True,
        "visual_dna": dna.model_dump(mode="json"),
        "prompt_hints": build_visual_dna_prompt_hints(dna),
    }


@app.post("/v1/projects/{project_id}/visual_dna/feedback")
def post_project_visual_dna_feedback(project_id: str, req: VisualDNAFeedbackRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    dna = _load_project_visual_dna(proj)
    updated = record_visual_dna_feedback(dna, feedback=req.feedback)
    saved = _save_project_visual_dna(proj, updated)
    return {
        "ok": True,
        "visual_dna": saved.model_dump(mode="json"),
        "prompt_hints": build_visual_dna_prompt_hints(saved),
    }

@app.get("/v1/projects/{project_id}/timeline")
def get_timeline(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    return {"ok": True, "timeline": proj.meta.get("timeline") or {"layers": []}}

@app.post("/v1/projects/{project_id}/timeline")
def set_timeline(project_id: str, req: TimelineUpdateRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    proj.meta["timeline"] = req.timeline or {"layers": []}
    store.save(proj)
    return {"ok": True, "timeline": proj.meta["timeline"]}
@app.get("/v1/projects/{project_id}/preview/frame")
def preview_frame(project_id: str, t: float = 0.0, w: int = 768, h: int = 432, force: int = 0):
    """Render a low-res cached preview frame for timeline scrubbing (no diffusion)."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)
    timeline = proj.meta.get("timeline") or {}

    try:
        from PIL import Image  # type: ignore
    except Exception as e:
        raise HTTPException(500, f"Pillow not installed: {e}")

    cache_dir = (pdir / "outputs" / "previews" / f"{int(w)}x{int(h)}").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"t{int(float(t) * 1000):010d}.png"
    out = cache_dir / key

    if out.exists() and not force:
        return FileResponse(str(out), media_type="image/png")

    base = Image.new("RGB", (int(w), int(h)), color=(18, 18, 22))
    try:
        img = apply_timeline_layers(base, project_dir=pdir, timeline=timeline, t=float(t))
    except Exception:
        img = base
    img.save(out)
    

    return FileResponse(str(out), media_type="image/png")
@app.get("/v1/projects/{project_id}/preview/segment")
def preview_segment(
    project_id: str,
    start_s: float = 0.0,
    end_s: float = 5.0,
    w: int = 768,
    h: int = 432,
    fps: int = 6,
    force: int = 0,
):
    """Render a low-res cached proxy preview clip for timeline scrubbing (no diffusion).

    This is intended for fast iteration:
      - overlays/text/masks are applied (same compositor as internal render)
      - audio is not muxed (UI plays audio separately)

    Cache key includes a hash of the current timeline.
    """
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)
    timeline = proj.meta.get("timeline") or {}

    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except Exception as e:
        raise HTTPException(500, f"Pillow not installed: {e}")

    start = max(0.0, float(start_s))
    end = max(start + 0.05, float(end_s))
    # protect the server: cap clip length
    end = min(end, start + 30.0)
    fps_i = max(1, min(24, int(fps)))

    tl_hash = hashlib.sha1(json.dumps(timeline, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]
    cache_dir = (pdir / "outputs" / "previews" / f"seg_{int(w)}x{int(h)}").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"seg_{int(start*1000):010d}_{int(end*1000):010d}_{fps_i}fps_{tl_hash}.mp4"
    out_mp4 = cache_dir / key

    if out_mp4.exists() and not force:
        return FileResponse(str(out_mp4), media_type="video/mp4")

    frames_dir = cache_dir / f"_tmp_{out_mp4.stem}"
    if frames_dir.exists():
        try:
            for f in frames_dir.glob("*.png"):
                f.unlink(missing_ok=True)
        except Exception:
            pass
    frames_dir.mkdir(parents=True, exist_ok=True)

    n = int(math.ceil((end - start) * fps_i))
    font = None
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    for i in range(n):
        t = start + (i / fps_i)
        base = Image.new("RGB", (int(w), int(h)), color=(18, 18, 22))
        # small time stamp (helps debugging scrubs)
        try:
            d = ImageDraw.Draw(base)
            d.text((10, 10), f"t={t:.2f}s", fill=(240, 240, 240), font=font)
        except Exception:
            pass
        try:
            img = apply_timeline_layers(base, project_dir=pdir, timeline=timeline, t=float(t))
        except Exception:
            img = base
        img.save(frames_dir / f"frame_{i:06d}.png")

    assemble_image_sequence(
        ffmpeg_path=settings.ffmpeg_path,
        frames_dir=frames_dir,
        out_mp4=out_mp4,
        fps=fps_i,
        glob_pattern="frame_*.png",
        audio_path=None,
    )

    # cleanup tmp frames (keep only mp4)
    try:
        for f in frames_dir.glob("*.png"):
            f.unlink(missing_ok=True)
        frames_dir.rmdir()
    except Exception:
        pass

    return FileResponse(str(out_mp4), media_type="video/mp4")






@app.get("/v1/projects/{project_id}/preview/diffusion_segment")
def preview_diffusion_segment(
    project_id: str,
    start_s: float = 0.0,
    end_s: float = 2.0,
    w: int = 512,
    h: int = 512,
    fps: int = 2,
    steps: int = 6,
    cfg: float = 7.0,
    strength: float = 0.45,
    model_id: str = "auto",
    variant_index: int = 0,
    seed: int = 1337,
    prompt: str | None = None,
    force: int = 0,
):
    """Render a short cached diffusion preview clip (low-cost 'look' preview).

    Notes:
      - capped length to protect slow machines
      - no audio mux (Timeline page plays audio separately)
      - uses the internal Diffusers engine (SD1.5 / SDXL / SD3.5) if installed
    """
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)
    timeline = proj.meta.get("timeline") or {}

    # Scenes from last plan are optional; timeline prompt track takes precedence anyway.
    scenes: list[dict[str, Any]] = []
    try:
        plan = proj.meta.get("last_plan") or {}
        vars_ = plan.get("variants") if isinstance(plan, dict) else None
        if isinstance(vars_, list) and vars_:
            vi = max(0, min(int(variant_index), len(vars_) - 1))
            scenes = (vars_[vi] or {}).get("scenes") or []
            if not isinstance(scenes, list):
                scenes = []
    except Exception:
        scenes = []

    start = max(0.0, float(start_s))
    end = max(start + 0.05, float(end_s))
    end = min(end, start + 10.0)

    fps_i = max(1, min(12, int(fps)))
    steps_i = max(1, min(30, int(steps)))
    w_i = max(256, min(1536, int(w)))
    h_i = max(256, min(1536, int(h)))

    # Resolve internal model
    mid = str(model_id or "auto")
    if mid == "auto":
        preferred = _hardware_profile().get("preferred_internal_model") or "hf_sd15_internal"
        mid = preferred
        if models.installed_path(mid) is None:
            # fallback
            mid = "hf_sd15_internal" if preferred != "hf_sd15_internal" else "hf_sdxl_internal"
    model_dir = models.installed_path(mid)
    if not model_dir or not model_dir.exists():
        raise UserFacingError(
            "Internal model is not installed.",
            hint="Go to Models and install an internal model such as SD 1.5, SDXL, or SD3.5 Medium, then retry.",
            code="MODEL_MISSING",
            status_code=400,
        )

    # Cache
    tl_hash = hashlib.sha1(json.dumps(timeline, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]
    p_hash = hashlib.sha1((prompt or "").encode("utf-8")).hexdigest()[:8]
    cache_dir = (pdir / "outputs" / "previews" / f"diff_{w_i}x{h_i}").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"diff_{int(start*1000):010d}_{int(end*1000):010d}_{fps_i}fps_{steps_i}s_{int(cfg*10):03d}c_{int(strength*100):03d}st_{mid}_{tl_hash}_{p_hash}.mp4"
    out_mp4 = cache_dir / key

    if out_mp4.exists() and not force:
        return FileResponse(str(out_mp4), media_type="video/mp4")

    s = InternalVideoSettings(
        fps_render=fps_i,
        fps_output=fps_i,
        width=w_i,
        height=h_i,
        steps=steps_i,
        cfg=float(cfg),
        interpolation_engine="fps",
        model_id=mid,
        temporal_mode="frame_img2img",
        temporal_strength=float(strength),
    )

    render_internal_diffusion_preview_segment(
        ffmpeg_path=settings.ffmpeg_path,
        project_dir=pdir,
        scenes=scenes,
        model_dir=Path(model_dir),
        settings=s,
        timeline=timeline,
        start_s=start,
        end_s=end,
        fps=fps_i,
        out_mp4=out_mp4,
        prompt_override=prompt,
        seed=int(seed),
        force=bool(force),
    )
    return FileResponse(str(out_mp4), media_type="video/mp4")



if HAS_MULTIPART:
    @app.post("/v1/projects/{project_id}/assets/audio")
    async def upload_audio(project_id: str, file: UploadFile = File(...)):
        proj = store.get(project_id)
        if not proj:
            raise HTTPException(404, "Project not found")
        pdir = store.project_dir(project_id)
        audio_dir = pdir / "assets" / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        name = (file.filename or "audio.wav").replace("\\", "_").replace("/", "_")
        out = audio_dir / name
        size = 0
        with out.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                size += len(chunk)
        try:
            await file.close()
        except Exception:
            pass
        store.set_audio(project_id, name, size)
        return {"ok": True, "path": str(out)}
else:
    @app.post("/v1/projects/{project_id}/assets/audio")
    async def upload_audio(project_id: str):
        _require_multipart()


@app.get("/v1/projects/{project_id}/audio")
def get_project_audio(project_id: str):
    """Serve the project's primary uploaded audio file (Timeline playback)."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    audio_meta = proj.meta.get("audio") or {}
    fn = str(audio_meta.get("filename") or "").strip()
    if not fn:
        raise HTTPException(404, "No audio uploaded")

    audio_path = store.project_dir(project_id) / "assets" / "audio" / fn
    if not audio_path.exists() or not audio_path.is_file():
        raise HTTPException(404, "Audio file missing on disk")

    mt, _ = mimetypes.guess_type(str(audio_path))
    return FileResponse(str(audio_path), media_type=mt or "application/octet-stream")

if HAS_MULTIPART:
    @app.post("/v1/projects/{project_id}/assets/overlay")
    async def upload_overlay_asset(project_id: str, file: UploadFile = File(...)):
        proj = store.get(project_id)
        if not proj:
            raise HTTPException(404, "Project not found")
        pdir = store.project_dir(project_id)
        overlays_dir = pdir / "assets" / "overlays"
        overlays_dir.mkdir(parents=True, exist_ok=True)
        name = (file.filename or "overlay.png").replace("\\", "_").replace("/", "_")
        out = overlays_dir / name
        data = await file.read()
        out.write_bytes(data)
        proj.meta.setdefault("assets", {}).setdefault("overlays", []).append(name)
        store.save(proj)
        return {"ok": True, "asset": name, "path": str(out)}
else:
    @app.post("/v1/projects/{project_id}/assets/overlay")
    async def upload_overlay_asset(project_id: str):
        _require_multipart()


if HAS_MULTIPART:
    @app.post("/v1/projects/{project_id}/assets/mask")
    async def upload_mask_asset(project_id: str, file: UploadFile = File(...)):
        proj = store.get(project_id)
        if not proj:
            raise HTTPException(404, "Project not found")
        pdir = store.project_dir(project_id)
        masks_dir = pdir / "assets" / "masks"
        masks_dir.mkdir(parents=True, exist_ok=True)
        name = (file.filename or "mask.png").replace("\\", "_").replace("/", "_")
        out = masks_dir / name
        data = await file.read()
        out.write_bytes(data)
        proj.meta.setdefault("assets", {}).setdefault("masks", []).append(name)
        store.save(proj)
        return {"ok": True, "asset": name, "path": str(out)}
else:
    @app.post("/v1/projects/{project_id}/assets/mask")
    async def upload_mask_asset(project_id: str):
        _require_multipart()


@app.post("/v1/projects/{project_id}/analyze_audio")
def analyze_audio(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    audio_meta = proj.meta.get("audio")
    if not audio_meta:
        raise HTTPException(400, "No audio uploaded")
    audio_path = store.project_dir(project_id) / "assets" / "audio" / audio_meta["filename"]

    feats = _collect_audio_analysis_features(audio_path)
    try:
        transcript_result = ai.transcribe(str(audio_path), model_size="turbo")
        trans = transcript_result if isinstance(transcript_result, dict) else {"text": str(transcript_result or "")}
    except Exception as e:
        trans = {"error": f"transcribe failed: {e}"}

    analysis = _enrich_project_audio_analysis(
        getattr(proj, "name", "Untitled project"),
        {"features": feats, "transcript": trans, "timestamp": time.time()},
    )
    duration_s = _analysis_duration_s(analysis)
    if duration_s:
        analysis["duration_s"] = float(duration_s)
    analysis_path = _write_project_analysis_snapshot(project_id, analysis)
    if analysis_path:
        analysis["analysis_path"] = analysis_path
    proj.meta["analysis"] = analysis
    store.save(proj)
    return {"ok": True, "analysis": analysis}


@app.get("/v1/projects/{project_id}/creative_direction")
def get_creative_direction(project_id: str, variant_index: int = 0, preset: str = "cinematic", sensitivity: float = 1.0):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    safe_preset = preset if preset in {"cinematic", "psychedelic", "ambient"} else "cinematic"
    payload = _build_creative_direction_payload(proj, variant_index=variant_index, preset=safe_preset, sensitivity=sensitivity)
    return {"ok": True, "creative_direction": payload}


@app.post("/v1/projects/{project_id}/creative_direction/apply_timeline_patch")
def apply_creative_direction_timeline_patch(project_id: str, req: CreativeDirectionApplyRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    payload = _build_creative_direction_payload(
        proj,
        variant_index=int(req.variant_index or 0),
        preset=str(req.preset or "cinematic"),
        sensitivity=float(req.sensitivity or 1.0),
    )
    patch_timeline = (
        payload.get("timeline_patch", {}).get("timeline")
        if isinstance(payload.get("timeline_patch"), dict)
        else {}
    )
    if not isinstance(patch_timeline, dict) or not patch_timeline:
        raise HTTPException(400, "Creative direction timeline patch is unavailable")

    base_timeline = proj.meta.get("timeline") if isinstance(proj.meta.get("timeline"), dict) else {}
    merged = _merge_creative_timeline_patch(
        base_timeline,
        patch_timeline,
        overwrite_tracks=bool(req.overwrite_tracks),
        overwrite_camera=bool(req.overwrite_camera),
    )
    proj.meta["timeline"] = merged
    proj.meta["last_creative_direction"] = {
        "variant_index": int(req.variant_index or 0),
        "preset": str(req.preset or "cinematic"),
        "sensitivity": float(req.sensitivity or 1.0),
        "applied_at": time.time(),
    }
    store.save(proj)
    return {"ok": True, "timeline": merged, "creative_direction": payload}


def _analysis_transcript_text(analysis: dict[str, Any]) -> str:
    raw = (analysis or {}).get("transcript")
    if isinstance(raw, dict):
        text = str(raw.get("text") or "").strip()
        if text:
            return text
        segments = raw.get("segments") if isinstance(raw.get("segments"), list) else []
        return " ".join(
            [str(seg.get("text") or "").strip() for seg in segments if isinstance(seg, dict) and str(seg.get("text") or "").strip()]
        ).strip()
    if isinstance(raw, str):
        return raw
    return ""


def _analysis_transcript_segments(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (analysis or {}).get("transcript")
    if isinstance(raw, dict) and isinstance(raw.get("segments"), list):
        out: list[dict[str, Any]] = []
        for item in raw.get("segments") or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            try:
                start = float(item.get("start") or 0.0)
            except Exception:
                start = 0.0
            try:
                end = float(item.get("end") or start)
            except Exception:
                end = start
            out.append({"start": max(0.0, start), "end": max(start, end), "text": text})
        return out
    return []


def _normalize_curve(values: Any) -> list[float]:
    out = _coerce_float_list(values)
    if not out:
        return []
    mn = min(out)
    mx = max(out)
    if mx > mn:
        out = [(float(v) - mn) / (mx - mn) for v in out]
    return [max(0.0, min(1.0, float(v))) for v in out]


def _collect_audio_analysis_features(audio_path: Path) -> dict[str, Any]:
    try:
        from enhanced_deforum_music_generator.core.audio_analyzer import AudioAnalyzer  # type: ignore
        from enhanced_deforum_music_generator.config.config_system import AudioConfig  # type: ignore

        analyzer = AudioAnalyzer(AudioConfig())
        af = analyzer.analyze_features(str(audio_path))
        return {
            "duration_s": float(getattr(af, "duration", 0.0) or 0.0),
            "bpm": float(getattr(af, "tempo", 0.0) or 0.0),
            "tempo_bpm": float(getattr(af, "tempo", 0.0) or 0.0),
            "beats": [float(x) for x in (getattr(af, "beats", []) or [])],
            "energy": _normalize_curve(getattr(af, "energy", []) or []),
            "onset_strength": _normalize_curve(getattr(af, "onset_strength", []) or []),
            "onset_times": [float(x) for x in (getattr(af, "onset_times", []) or [])],
            "spectral_centroid": [float(x) for x in (getattr(af, "spectral_centroid", []) or [])],
            "spectral_rolloff": [float(x) for x in (getattr(af, "spectral_rolloff", []) or [])],
            "rms_energy": _normalize_curve(getattr(af, "rms_energy", []) or []),
        }
    except Exception:
        try:
            from edmg_ai_service.audio import lightweight_audio_features  # type: ignore

            return lightweight_audio_features(str(audio_path))
        except Exception as e:
            return {"error": f"audio_features failed: {e}"}


def _normalize_transcript_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = {"text": raw}
    elif not isinstance(raw, dict):
        raw = {}

    text = str(raw.get("text") or "").strip()
    segments: list[dict[str, Any]] = []
    if isinstance(raw.get("segments"), list):
        for item in raw.get("segments") or []:
            if not isinstance(item, dict):
                continue
            seg_text = str(item.get("text") or "").strip()
            if not seg_text:
                continue
            try:
                start = float(item.get("start") or 0.0)
            except Exception:
                start = 0.0
            try:
                end = float(item.get("end") or start)
            except Exception:
                end = start
            segments.append({"start": max(0.0, start), "end": max(start, end), "text": seg_text})

    if not text and segments:
        text = "\n".join(seg["text"] for seg in segments).strip()

    duration_s = _pick_raw_number(raw, ["duration_s", "duration"])
    duration_after_vad_s = _pick_raw_number(raw, ["duration_after_vad_s"])
    word_count = int(raw.get("word_count") or len(text.split()))
    return {
        "text": text,
        "segments": segments,
        "language": str(raw.get("language") or ""),
        "duration_s": float(duration_s or 0.0),
        "duration_after_vad_s": float(duration_after_vad_s or 0.0),
        "segment_count": int(raw.get("segment_count") or len(segments)),
        "word_count": word_count,
        "model_size": str(raw.get("model_size") or "small"),
        "source": str(raw.get("source") or "transcribe"),
        **({"error": str(raw.get("error"))} if raw.get("error") else {}),
        **({"note": str(raw.get("note"))} if raw.get("note") else {}),
    }


def _analysis_top_keywords(text: str, limit: int = 12) -> list[str]:
    counts: dict[str, int] = {}
    for token in _creative_tokenize(text):
        counts[token] = counts.get(token, 0) + 1
    return [token for token, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def _analysis_theme_terms(text: str, limit: int = 8) -> list[str]:
    try:
        from enhanced_deforum_music_generator.core.nlp_processor import NLPProcessor  # type: ignore

        terms = NLPProcessor({"max_themes": limit}).extract_themes(text)
    except Exception:
        terms = []
    merged: list[str] = []
    for token in list(terms or []) + _analysis_top_keywords(text, limit=limit):
        clean = str(token or "").strip().lower()
        if clean and clean not in merged:
            merged.append(clean)
        if len(merged) >= limit:
            break
    return merged


_AUDIO_ONLY_SUMMARY_SUFFIX = (
    "Studio is still able to build audio-reactive sections and a first creative direction from rhythm, energy, and spectral movement."
)


def _analysis_audio_only_status(summary_prefix: str) -> str:
    clean = str(summary_prefix or "").strip()
    if not clean:
        clean = "No transcript is available for this track yet."
    elif clean[-1] not in ".!?":
        clean = f"{clean}."
    return f"{clean} {_AUDIO_ONLY_SUMMARY_SUFFIX}"


def _analysis_summary_text(transcript: dict[str, Any], text: str, segments: list[dict[str, Any]]) -> str:
    candidates: list[str] = []
    if segments:
        picks = [segments[0], segments[len(segments) // 2], segments[-1]]
        for seg in picks:
            cue = str(seg.get("text") or "").strip()
            if cue and cue not in candidates:
                candidates.append(cue)
    if not candidates:
        for sentence in _analysis_transcript_sentences({"transcript": {"text": text}}):
            if sentence not in candidates:
                candidates.append(sentence)
            if len(candidates) >= 3:
                break
    if not candidates:
        note = str((transcript or {}).get("note") or "").strip()
        error = str((transcript or {}).get("error") or "").strip()
        if note:
            return _analysis_audio_only_status(note)
        if error:
            return _analysis_audio_only_status("Transcription failed.")
        return _analysis_audio_only_status("No transcript is available for this track yet.")
    return " ".join(candidates[:3]).strip()


def _derive_longform_analysis_sections(
    title: str,
    analysis: dict[str, Any],
    tags: list[str],
    *,
    preset: str = "cinematic",
    sensitivity: float = 1.0,
    max_sections: int = 12,
) -> list[dict[str, Any]]:
    segments = _analysis_transcript_segments(analysis)
    duration_s = _analysis_duration_s(analysis) or 0.0
    if duration_s <= 0.0 and segments:
        duration_s = max(float(seg.get("end") or 0.0) for seg in segments)
    overall = _infer_reactivity_metrics(analysis)
    energy_curve = list(overall.get("energy_curve") or [])

    if not segments:
        return _derive_reactive_sections(
            overall,
            duration_s,
            _analysis_transcript_sentences(analysis),
            tags[:8],
            title,
            preset,
            sensitivity,
            max_sections=min(8, max(3, int(max_sections))),
        )

    desired = max(3, min(int(max_sections), int(math.ceil(max(duration_s, 1.0) / 60.0))))
    window_s = max(20.0, duration_s / max(1, desired))
    sections: list[dict[str, Any]] = []
    for index in range(desired):
        start_s = float(index) * window_s
        end_s = duration_s if index == desired - 1 else min(duration_s, float(index + 1) * window_s)
        bucket = [
            seg for seg in segments
            if float(seg.get("end") or 0.0) > start_s and float(seg.get("start") or 0.0) < end_s
        ]
        if not bucket:
            midpoint = (start_s + end_s) / 2.0
            nearest = min(segments, key=lambda seg: abs((((float(seg.get("start") or 0.0) + float(seg.get("end") or 0.0)) / 2.0) - midpoint)))
            bucket = [nearest]
        cue_text = " ".join(str(seg.get("text") or "").strip() for seg in bucket[:3]).strip()
        bucket_tags = _analysis_top_keywords(cue_text, limit=4) or list(tags[:4])
        metrics = _scene_metrics_from_curve(
            index,
            desired,
            {"start_s": start_s, "end_s": end_s},
            overall,
            duration_s,
            energy_curve,
        )
        band_scores = {
            "bass": float(metrics.get("bass") or 0.0),
            "mid": float(metrics.get("mid") or 0.0),
            "treble": float(metrics.get("treble") or 0.0),
        }
        band = max(band_scores.items(), key=lambda item: item[1])[0]
        label = _creative_section_label(index, desired, float(metrics.get("energy") or 0.0), band)
        camera_hint, motion_hint_base = _creative_section_hints(label, band)
        params = _compute_reactive_params(metrics, preset, sensitivity)
        motion_hint = f"{motion_hint_base} {_creative_motion_hint(params)}".strip()
        focus = ", ".join(bucket_tags[:3]) if bucket_tags else ", ".join(tags[:3]) or "cinematic continuity"
        prompt = (
            f"{title or 'Untitled project'}, {label.lower()}, {band}-led motion language, "
            f"{preset} music-film framing, themes: {focus}"
        )
        sections.append(
            {
                "index": index,
                "name": label,
                "start_s": start_s,
                "end_s": max(start_s + 0.2, end_s),
                "duration_s": max(0.2, end_s - start_s),
                "energy": float(metrics.get("energy") or 0.0),
                "energy_label": _creative_energy_label(float(metrics.get("energy") or 0.0)),
                "prompt": prompt,
                "transcript_cue": cue_text or "No transcript cue available; drive the section from the energy arc.",
                "camera_hint": camera_hint,
                "motion_hint": motion_hint,
                "band": band,
                "keywords": bucket_tags,
                "avg_energy": float(metrics.get("energy") or 0.0),
                "peak_energy": float(metrics.get("energy") or 0.0),
                "reactive_params": params,
                "scene_source": "analysis_fallback",
            }
        )
    return sections


def _enrich_project_audio_analysis(title: str, analysis: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "features": dict((analysis or {}).get("features") or {}),
        "transcript": _normalize_transcript_payload((analysis or {}).get("transcript")),
        "timestamp": float((analysis or {}).get("timestamp") or time.time()),
    }
    transcript_text = _analysis_transcript_text(normalized)
    transcript_segments = _analysis_transcript_segments(normalized)
    tags = _analysis_top_keywords(transcript_text, limit=12)
    themes = _analysis_theme_terms(transcript_text, limit=8)
    emotion_scores = _creative_emotion_scores(_creative_tokenize(transcript_text), limit=4)
    normalized["summary"] = _analysis_summary_text(normalized.get("transcript") or {}, transcript_text, transcript_segments)
    normalized["tags"] = list(dict.fromkeys([*themes, *tags]))[:12]
    normalized["themes"] = themes
    normalized["emotions"] = emotion_scores
    normalized["sections"] = _derive_longform_analysis_sections(title, normalized, normalized["tags"])
    normalized["transcript"]["segment_count"] = len(transcript_segments)
    normalized["transcript"]["word_count"] = int(normalized["transcript"].get("word_count") or len(transcript_text.split()))
    return normalized


def _write_project_analysis_snapshot(project_id: str, analysis: dict[str, Any]) -> str | None:
    try:
        pdir = store.project_dir(project_id)
        rel = Path("analysis") / "audio_analysis.json"
        target = pdir / rel
        tmp = target.with_suffix(".json.tmp")
        payload = json.dumps(analysis, ensure_ascii=False, indent=2)
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        return str(rel).replace("\\", "/")
    except Exception:
        return None

def _coerce_float_list(v: Any) -> list[float]:
    if not v:
        return []
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            try:
                out.append(float(x))
            except Exception:
                continue
        return out
    return []


def _build_public_audio_analysis(proj: Any) -> Any:
    """Build enhanced_deforum_music_generator.public_api.AudioAnalysis from project meta."""
    analysis = (proj.meta.get("analysis") or {}) if hasattr(proj, "meta") else {}
    feats = (analysis.get("features") or {}) if isinstance(analysis, dict) else {}

    duration = float(feats.get("duration_s") or feats.get("duration") or 0.0)
    bpm = float(feats.get("bpm") or feats.get("tempo_bpm") or feats.get("tempo") or 0.0)

    beats = _coerce_float_list(feats.get("beats") or feats.get("beat_times") or feats.get("beat_timestamps"))
    energy = _coerce_float_list(feats.get("energy") or feats.get("energy_curve") or feats.get("energy_envelope") or feats.get("onset_strength"))

    # normalize energy to 0..1
    if energy:
        mn = min(energy)
        mx = max(energy)
        if mx > mn:
            energy = [(e - mn) / (mx - mn) for e in energy]
        energy = [max(0.0, min(1.0, float(e))) for e in energy]

    transcript = _analysis_transcript_text(analysis)

    try:
        from enhanced_deforum_music_generator.public_api import AudioAnalysis  # type: ignore
        aa = AudioAnalysis(filepath="", duration=duration, tempo_bpm=bpm, beats=beats, energy=energy)
        # soft-attach lyrics if present; orchestrator may use lyric_segments
        setattr(aa, "lyrics", transcript)
        return aa
    except Exception:
        return {"duration": duration, "tempo_bpm": bpm, "beats": beats, "energy": energy, "lyrics": transcript}


_CREATIVE_DIRECTION_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it", "its",
    "of", "on", "or", "that", "the", "their", "this", "to", "with", "your", "you", "about", "after",
    "before", "during", "through", "scene", "shot", "visual", "video", "music", "audio", "render",
    "track", "variant", "project", "style", "look", "high", "detail", "coherent", "consistent",
}

_CREATIVE_EMOTION_WORDS: dict[str, set[str]] = {
    "euphoria": {"light", "higher", "rise", "alive", "open", "glow", "gold", "electric", "dance", "rush"},
    "longing": {"echo", "late", "ghost", "after", "distance", "remember", "missing", "fade", "lost", "again"},
    "tension": {"edge", "fall", "smoke", "storm", "shadow", "break", "pressure", "night", "wire", "warning"},
    "intimacy": {"skin", "breath", "close", "touch", "hand", "heart", "whisper", "inside"},
    "defiance": {"burn", "riot", "wild", "fight", "loud", "rough", "fire", "run"},
    "wonder": {"sky", "stars", "ocean", "dream", "horizon", "infinite", "blue", "sun", "neon", "glass"},
}


def _creative_tokenize(text: str) -> list[str]:
    return [
        token
        for token in re.sub(r"[^a-z0-9\s'-]", " ", str(text or "").lower()).split()
        if len(token) > 2 and token not in _CREATIVE_DIRECTION_STOPWORDS
    ]


def _creative_emotion_scores(tokens: list[str], limit: int = 4) -> list[dict[str, Any]]:
    if not tokens:
        return []

    raw_scores = [
        (emotion, sum(1 for token in tokens if token in words))
        for emotion, words in _CREATIVE_EMOTION_WORDS.items()
    ]
    peak = max([score for _emotion, score in raw_scores] or [0])
    if peak <= 0:
        return []

    return [
        {"emotion": emotion, "score": round(float(score) / float(peak), 3)}
        for emotion, score in sorted(raw_scores, key=lambda item: (-item[1], item[0]))
        if score > 0
    ][:limit]


def _creative_hooks(sentences: list[str], limit: int = 3) -> list[str]:
    picks: list[str] = []
    for sentence in list(sentences[:2]) + list(sentences[-1:]):
        clean = str(sentence or "").strip()
        if clean and clean not in picks:
            picks.append(clean)
        if len(picks) >= limit:
            break
    return picks


def _creative_average(values: list[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _creative_provider_mode(plan: dict[str, Any]) -> str:
    source = str((plan or {}).get("source") or "").strip().lower()
    provider = os.getenv("EDMG_AI_PROVIDER", "openai_compat").strip().lower()
    if source == "ai":
        if provider == "ollama":
            return "ollama-contract"
        if provider in {"openai_compat", "openai-compatible", "openai"}:
            return "openai-contract"
        return f"{provider}-contract" if provider else "provider-contract"
    return "local-heuristic"


def _normalize_unit(value: Any, mode: str = "unit") -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if mode == "tempo":
        return max(0.0, min(1.0, (number - 60.0) / 120.0))
    if mode == "centroid":
        return max(0.0, min(1.0, number / 5000.0))
    if abs(number) <= 1.0:
        return max(0.0, min(1.0, number))
    return max(0.0, min(1.0, number / 100.0))


def _pick_feature_number(source: dict[str, Any], keys: list[str], mode: str = "unit") -> float | None:
    for key in keys:
        if key not in source:
            continue
        normalized = _normalize_unit(source.get(key), mode)
        if normalized is not None:
            return normalized
    return None


def _pick_raw_number(source: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        try:
            return float(source.get(key))
        except Exception:
            continue
    return None


def _feature_series(source: dict[str, Any], keys: list[str]) -> list[float]:
    for key in keys:
        values = source.get(key)
        if isinstance(values, (list, tuple)):
            out: list[float] = []
            for item in values:
                try:
                    out.append(float(item))
                except Exception:
                    continue
            if out:
                return out
    return []


def _bucket_curve(values: list[float], buckets: int = 96) -> list[float]:
    if not values:
        return []
    target = max(16, int(buckets))
    step = max(1, int(math.ceil(len(values) / target)))
    out: list[float] = []
    for start in range(0, len(values), step):
        chunk = values[start:start + step]
        if not chunk:
            continue
        peak = max(abs(float(v)) for v in chunk)
        out.append(max(0.0, min(1.0, peak)))
    return out[:target]


def _analysis_transcript_sentences(analysis: dict[str, Any]) -> list[str]:
    text = _analysis_transcript_text(analysis).strip()
    if not text:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def _has_usable_transcript(analysis: dict[str, Any]) -> bool:
    text = _analysis_transcript_text(analysis).strip()
    if text:
        return True
    raw = (analysis or {}).get("transcript")
    if isinstance(raw, dict) and isinstance(raw.get("segments"), list):
        return any(str(seg.get("text") or "").strip() for seg in raw.get("segments") if isinstance(seg, dict))
    return False


def _usable_transcript_overlay_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    fallback_prefixes = (
        "no transcript cue available",
        "transcription unavailable",
        "audio-only analysis",
        "drive the section from the energy arc",
        "drive the scene from the prompt and energy arc",
    )
    if any(lowered.startswith(prefix) for prefix in fallback_prefixes):
        return ""
    return text[:180]


def _analysis_motifs(variant: dict[str, Any], transcript_text: str, limit: int = 8) -> list[str]:
    feed: list[str] = [transcript_text]
    for scene in list(variant.get("scenes") or []):
        feed.append(str(scene.get("name") or ""))
        feed.append(str(scene.get("prompt") or ""))

    counts: dict[str, int] = {}
    for value in feed:
        tokens = re.sub(r"[^a-z0-9\s-]", " ", str(value).lower()).split()
        for token in tokens:
            if len(token) <= 2 or token in _CREATIVE_DIRECTION_STOPWORDS:
                continue
            counts[token] = counts.get(token, 0) + 1

    return [token for token, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def _infer_reactivity_metrics(analysis: dict[str, Any]) -> dict[str, Any]:
    feats = (analysis.get("features") or {}) if isinstance(analysis, dict) else {}
    duration_s = (
        _pick_raw_number(feats, ["duration_s", "duration", "audio_duration_s"])
        or _pick_raw_number(analysis, ["duration_s", "duration"])
        or 0.0
    )
    energy_curve = _feature_series(feats, ["energy", "energy_curve", "energy_envelope", "onset_strength"])
    scalar_energy = _pick_feature_number(feats, ["energy", "rms_energy", "loudness_norm", "dynamic_energy"])
    if scalar_energy is None and energy_curve:
        scalar_energy = max(0.0, min(1.0, sum(energy_curve) / max(1, len(energy_curve))))
    energy = scalar_energy if scalar_energy is not None else 0.45
    bass = _pick_feature_number(feats, ["bass", "bass_energy", "low_frequency_energy", "kick_energy"])
    if bass is None:
        bass = max(0.0, min(1.0, 0.32 + energy * 0.45))
    mid = _pick_feature_number(feats, ["mid", "mid_energy", "spectral_flatness", "harmonic_energy"])
    if mid is None:
        mid = max(0.0, min(1.0, 0.38 + energy * 0.34))
    treble = _pick_feature_number(feats, ["treble", "brightness", "high_frequency_energy"])
    if treble is None:
        treble = _pick_feature_number(feats, ["spectral_centroid"], mode="centroid")
    if treble is None:
        tempo = _pick_feature_number(feats, ["tempo_bpm", "bpm", "tempo"], mode="tempo") or 0.2
        treble = max(0.0, min(1.0, 0.25 + energy * 0.18 + tempo * 0.3))

    return {
        "energy": float(energy),
        "bass": float(bass),
        "mid": float(mid),
        "treble": float(treble),
        "duration_s": float(duration_s),
        "source": "analysis",
        "waveform": _bucket_curve(energy_curve, 96),
        "energy_curve": [max(0.0, min(1.0, float(v))) for v in energy_curve],
    }


def _compute_reactive_params(metrics: dict[str, Any], preset: str, sensitivity: float) -> dict[str, float]:
    sens = max(0.1, min(3.0, float(sensitivity or 1.0)))
    energy = max(0.0, min(1.0, float(metrics.get("energy") or 0.0)))
    bass = max(0.0, min(1.0, float(metrics.get("bass") or 0.0)))
    mid = max(0.0, min(1.0, float(metrics.get("mid") or 0.0)))
    treble = max(0.0, min(1.0, float(metrics.get("treble") or 0.0)))
    progress = max(0.0, min(1.0, float(metrics.get("progress") or 0.0)))
    lateral_phase = math.sin(progress * math.pi * 2.0)
    vertical_phase = math.cos(progress * math.pi * 1.5)
    orbit_phase = math.sin(progress * math.pi)

    if preset == "psychedelic":
        return {
            "zoom": 1.0 + (0.08 + energy * 0.16 + orbit_phase * 0.04) * sens,
            "rotation_x": (energy * 64.0 + vertical_phase * 16.0) * sens,
            "rotation_y": (bass * 92.0 + lateral_phase * 26.0) * sens,
            "rotation_z": (treble * 28.0 + lateral_phase * 8.0) * sens,
            "translation_x": (lateral_phase * (10.0 + bass * 12.0) + math.sin(mid * 6.0) * 6.0) * sens,
            "translation_y": (vertical_phase * (6.0 + treble * 8.0) + orbit_phase * 4.0) * sens,
            "translation_z": -(18.0 + energy * 20.0 + bass * 5.0) * sens,
            "cfg_scale": 6.8 + mid * sens * 2.5,
            "strength": 0.56 + treble * sens * 0.22,
            "brightness": 0.48 + mid * sens * 0.36,
            "contrast": 1.0 + energy * sens * 0.72,
        }
    if preset == "ambient":
        return {
            "zoom": 1.0 + (0.03 + energy * 0.08) * sens,
            "rotation_x": (bass * 6.0 + vertical_phase * 3.0) * sens,
            "rotation_y": (mid * 10.0 + lateral_phase * 4.0) * sens,
            "rotation_z": (treble * 5.0 + orbit_phase * 2.0) * sens,
            "translation_x": lateral_phase * (4.0 + mid * 5.0) * sens,
            "translation_y": vertical_phase * (3.0 + treble * 4.0) * sens,
            "translation_z": -(6.0 + energy * 8.0) * sens,
            "cfg_scale": 6.0 + treble * sens * 1.8,
            "strength": 0.5 + mid * sens * 0.16,
            "brightness": 0.42 + mid * sens * 0.22,
            "contrast": 0.95 + energy * sens * 0.24,
        }
    return {
        "zoom": 1.0 + (0.05 + energy * 0.14 + bass * 0.02) * sens,
        "rotation_x": (mid * 8.0 + vertical_phase * 4.0) * sens,
        "rotation_y": (math.sin(bass * 4.0) * 16.0 + lateral_phase * 9.0) * sens,
        "rotation_z": (treble * 7.0 + orbit_phase * 3.5) * sens,
        "translation_x": (lateral_phase * (6.0 + mid * 8.0 + bass * 5.0)) * sens,
        "translation_y": (vertical_phase * (3.0 + treble * 5.0)) * sens,
        "translation_z": -(12.0 + energy * 18.0 + bass * 4.0) * sens,
        "cfg_scale": 7.0 + mid * sens * 2.4,
        "strength": 0.62 + treble * sens * 0.21,
        "brightness": 0.45 + energy * sens * 0.16,
        "contrast": 1.02 + energy * sens * 0.4,
    }


def _creative_energy_label(value: float) -> str:
    if value >= 0.82:
        return "surge"
    if value >= 0.64:
        return "lift"
    if value >= 0.42:
        return "steady"
    return "breath"


def _creative_camera_hint(value: float) -> str:
    if value >= 0.82:
        return "Aggressive push with a lateral sweep, stronger parallax, sharper light contrast, and a quicker axis reset on the cut."
    if value >= 0.64:
        return "Tracking medium shot with progressive push, controlled side travel, and bolder edge lighting around the subject."
    if value >= 0.42:
        return "Measured dolly, orbit, or lateral pan with restrained motion blur and stable framing for continuity."
    return "Wide or medium-wide hold with soft side drift, longer lens settle, and more negative space."


def _creative_motion_hint(params: dict[str, float]) -> str:
    return (
        f"Zoom {params['zoom']:.2f}, pan X {params['translation_x']:.1f}, pan Y {params['translation_y']:.1f}, "
        f"roll {params['rotation_z']:.1f}, Z travel {params['translation_z']:.1f}, cfg {params['cfg_scale']:.1f}, strength {params['strength']:.2f}."
    )


def _creative_section_label(index: int, total: int, energy: float, band: str) -> str:
    if index == 0:
        return "Arrival" if energy < 0.42 else "Cold Open"
    if index == max(0, total - 1):
        return "Resolve" if energy > 0.68 else "Afterglow"
    if energy > 0.82 and band == "bass":
        return "Drop"
    if energy > 0.68 and band == "mid":
        return "Lift"
    if energy < 0.34:
        return "Breath"
    if band == "treble":
        return "Spark"
    if band == "bass":
        return "Drive"
    return "Build"


def _creative_section_hints(label: str, band: str) -> tuple[str, str]:
    if label == "Drop":
        return (
            "Fast push with a lateral sweep, foreground occlusion, and sharper light separation.",
            "Push zoom selectively, extend side travel, and use transient shake accents around impact.",
        )
    if label == "Breath":
        return (
            "Locked or gently drifting frame with longer lens settle and a soft side drift.",
            "Small XY drift, softer contrast, and more negative space.",
        )
    if band == "treble":
        return (
            "Lateral glide with highlight streaks, cleaner silhouette edges, and subject or light passes across frame.",
            "Particle flicker, quicker spin accents, and brighter edge energy without losing the camera axis.",
        )
    if band == "bass":
        return (
            "Low-angle arc with grounded perspective, denser foreground depth, and weighty side-to-side travel.",
            "Scale pulses, front-to-back travel, and weighty motion ramps with occasional lateral shove.",
        )
    return (
        "Steadicam reveal with measured parallax depth, a controlled lateral pan, and subtle height changes.",
        "Blend orbit, rise, and moderate contrast ramps while preserving continuity.",
    )


def _fallback_scene_metrics(index: int, total: int, overall: dict[str, Any]) -> dict[str, Any]:
    ratio = float(index) / max(1.0, float(total - 1)) if total > 1 else 0.0
    curve = math.sin(ratio * math.pi)
    energy = max(0.0, min(1.0, float(overall["energy"]) * 0.72 + curve * 0.26 + ratio * 0.06))
    return {
        "energy": energy,
        "bass": max(0.0, min(1.0, float(overall["bass"]) * 0.7 + curve * 0.22)),
        "mid": max(0.0, min(1.0, float(overall["mid"]) * 0.8 + (1.0 - abs(0.5 - ratio) * 2.0) * 0.14)),
        "treble": max(0.0, min(1.0, float(overall["treble"]) * 0.72 + ratio * 0.18)),
        "duration_s": float(overall.get("duration_s") or 0.0),
        "source": "analysis",
        "progress": ratio,
    }


def _derive_reactive_sections(
    overall: dict[str, Any],
    duration_s: float,
    transcript_sentences: list[str],
    motifs: list[str],
    title: str,
    preset: str,
    sensitivity: float,
    max_sections: int = 6,
) -> list[dict[str, Any]]:
    if duration_s <= 0:
        duration_s = max(12.0, float(len(transcript_sentences) or 3) * 6.0)
    desired = max(3, min(8, int(max_sections)))
    curve = [max(0.0, min(1.0, float(v))) for v in list(overall.get("energy_curve") or [])]

    ordered: list[int] = [0]
    if len(curve) >= 4:
        min_gap = max(2, len(curve) // max(3, desired + 1))
        candidates = sorted(
            [
                (index, abs(curve[index] - curve[index - 1]))
                for index in range(1, len(curve) - 1)
            ],
            key=lambda item: item[1],
            reverse=True,
        )
        for index, _score in candidates:
            if len(ordered) >= desired:
                break
            if all(abs(index - existing) >= min_gap for existing in ordered):
                ordered.append(index)
        if len(ordered) < desired:
            step = max(1, len(curve) // desired)
            for index in range(step, len(curve) - 1, step):
                if len(ordered) >= desired:
                    break
                if all(abs(index - existing) >= min_gap for existing in ordered):
                    ordered.append(index)
        ordered.append(len(curve) - 1)
    else:
        total_points = max(desired * 4, 16)
        ordered.extend([int(round((index / float(desired)) * (total_points - 1))) for index in range(1, desired)])
        ordered.append(total_points - 1)

    ordered = sorted(set(max(0, int(value)) for value in ordered))
    if len(ordered) < 2:
        ordered = [0, max(1, len(curve) - 1 if curve else desired * 3)]

    total_points = max(ordered[-1], len(curve) - 1, 1)
    sections: list[dict[str, Any]] = []
    for index, start_idx in enumerate(ordered[:-1]):
        end_idx = max(start_idx + 1, ordered[index + 1])
        if curve:
            chunk = curve[start_idx : min(len(curve), end_idx + 1)]
        else:
            span = max(1, end_idx - start_idx)
            chunk = [
                max(0.0, min(1.0, float(overall.get("energy") or 0.45) * 0.75 + math.sin((start_idx + offset) / max(1.0, total_points) * math.pi) * 0.22))
                for offset in range(span)
            ]
        avg_energy = _creative_average(chunk)
        peak_energy = max(chunk) if chunk else float(overall.get("energy") or 0.45)
        ratio = float(index) / max(1.0, float(len(ordered) - 2)) if len(ordered) > 2 else 0.0
        band = (
            "bass"
            if float(overall.get("bass") or 0.0) + peak_energy * 0.12 >= max(float(overall.get("mid") or 0.0) + avg_energy * 0.08, float(overall.get("treble") or 0.0) + ratio * 0.1)
            else "mid"
            if float(overall.get("mid") or 0.0) + avg_energy * 0.08 >= float(overall.get("treble") or 0.0) + ratio * 0.1
            else "treble"
        )
        label = _creative_section_label(index, len(ordered) - 1, avg_energy, band)
        metrics = {
            "energy": avg_energy,
            "bass": max(0.0, min(1.0, float(overall.get("bass") or 0.0) * 0.85 + peak_energy * 0.12)),
            "mid": max(0.0, min(1.0, float(overall.get("mid") or 0.0) * 0.85 + avg_energy * 0.12)),
            "treble": max(0.0, min(1.0, float(overall.get("treble") or 0.0) * 0.82 + ratio * 0.08 + peak_energy * 0.1)),
            "duration_s": max(0.2, (end_idx - start_idx) / max(1.0, total_points) * duration_s),
            "source": "analysis",
            "progress": ratio,
        }
        params = _compute_reactive_params(metrics, preset, sensitivity)
        camera_hint, motion_hint = _creative_section_hints(label, band)
        start_s = float(start_idx) / float(total_points) * duration_s
        end_s = min(duration_s, max(start_s + 0.2, float(end_idx) / float(total_points) * duration_s))
        cue_index = min(len(transcript_sentences) - 1, int(round(ratio * max(0, len(transcript_sentences) - 1)))) if transcript_sentences else -1
        transcript_cue = transcript_sentences[cue_index] if cue_index >= 0 else "No transcript cue available; drive the section from the energy arc."
        prompt = (
            f"{title or 'Untitled project'}, {label.lower()} section, {band}-led motion language, "
            f"{preset} music-film framing, motifs: {', '.join(motifs[:3]) or 'cinematic continuity'}"
        )
        sections.append(
            {
                "index": index,
                "name": label,
                "start_s": start_s,
                "end_s": end_s,
                "duration_s": max(0.2, end_s - start_s),
                "energy": float(avg_energy),
                "energy_label": _creative_energy_label(float(avg_energy)),
                "prompt": prompt,
                "transcript_cue": transcript_cue,
                "camera_hint": camera_hint,
                "motion_hint": f"{motion_hint} {_creative_motion_hint(params)}",
                "band": band,
                "avg_energy": float(avg_energy),
                "peak_energy": float(peak_energy),
                "reactive_params": params,
                "scene_source": "analysis_fallback",
            }
        )
    return sections


def _scene_metrics_from_curve(
    index: int,
    total: int,
    scene: dict[str, Any],
    overall: dict[str, Any],
    duration_s: float,
    energy_curve: list[float],
) -> dict[str, Any]:
    if duration_s <= 0 or not energy_curve:
        return _fallback_scene_metrics(index, total, overall)

    start_s = float(scene.get("start_s") or 0.0)
    end_s = float(scene.get("end_s") or (start_s + 5.0))
    start_idx = max(0, min(len(energy_curve) - 1, int((start_s / max(duration_s, 0.001)) * len(energy_curve))))
    end_idx = max(start_idx + 1, min(len(energy_curve), int(math.ceil((end_s / max(duration_s, 0.001)) * len(energy_curve)))))
    chunk = energy_curve[start_idx:end_idx]
    if not chunk:
        return _fallback_scene_metrics(index, total, overall)

    energy = max(0.0, min(1.0, sum(chunk) / max(1, len(chunk))))
    peak = max(chunk)
    ratio = float(index) / max(1.0, float(total - 1)) if total > 1 else 0.0
    return {
        "energy": energy,
        "bass": max(0.0, min(1.0, float(overall["bass"]) * 0.82 + peak * 0.14)),
        "mid": max(0.0, min(1.0, float(overall["mid"]) * 0.82 + energy * 0.18)),
        "treble": max(0.0, min(1.0, float(overall["treble"]) * 0.78 + ratio * 0.08 + peak * 0.1)),
        "duration_s": max(0.2, end_s - start_s),
        "source": "analysis",
        "progress": ratio,
    }


def _dedupe_camera_keyframes(keyframes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dedup: dict[float, dict[str, Any]] = {}
    for keyframe in keyframes:
        try:
            t = round(float(keyframe.get("t") or 0.0), 3)
        except Exception:
            continue
        dedup[t] = {**keyframe, "t": t}
    return [dedup[t] for t in sorted(dedup.keys())]


def _build_creative_timeline_patch(
    packed_scenes: list[dict[str, Any]],
    duration_s: float,
    negative_prompt: str,
) -> dict[str, Any]:
    prompt_track = {
        "id": "track_prompt",
        "name": "Prompts",
        "type": "prompt",
        "clips": [],
    }
    motion_track = {
        "id": "track_motion",
        "name": "Motion",
        "type": "motion",
        "clips": [],
    }
    layers: list[dict[str, Any]] = []
    camera_keyframes: list[dict[str, Any]] = []
    prev_zoom = 1.0
    prev_pan_x = 0.0
    prev_pan_y = 0.0
    prev_rotation = 0.0

    for index, scene in enumerate(packed_scenes):
        start_s = float(scene.get("start_s") or 0.0)
        end_s = max(start_s + 0.2, float(scene.get("end_s") or (start_s + 5.0)))
        params = scene.get("reactive_params") if isinstance(scene.get("reactive_params"), dict) else {}
        zoom = float(params.get("zoom") or prev_zoom or 1.0)
        zoom_start = prev_zoom
        zoom_end = max(zoom, zoom_start + max(0.01, float(scene.get("energy") or 0.0) * 0.025))
        pan_x_start = prev_pan_x
        pan_y_start = prev_pan_y
        pan_x_end = float(params.get("translation_x") or 0.0)
        pan_y_end = float(params.get("translation_y") or 0.0)
        rotation_target = float(params.get("rotation_z") or 0.0) + float(params.get("rotation_y") or 0.0) * 0.18
        rotation_start = prev_rotation
        rotation_end = rotation_target

        prompt_track["clips"].append(
            {
                "id": f"creative_prompt_{index}",
                "start_s": start_s,
                "end_s": end_s,
                "data": {
                    "prompt": str(scene.get("prompt_pack") or scene.get("prompt") or "").strip(),
                    "negative_prompt": negative_prompt,
                },
            }
        )
        motion_track["clips"].append(
            {
                "id": f"creative_motion_{index}",
                "start_s": start_s,
                "end_s": end_s,
                "data": {
                    "zoom_start": zoom_start,
                    "zoom_end": zoom_end,
                    "pan_x_start": pan_x_start,
                    "pan_x_end": pan_x_end,
                    "pan_y_start": pan_y_start,
                    "pan_y_end": pan_y_end,
                    "rotation_start": rotation_start,
                    "rotation_end": rotation_end,
                    "strength": float(params.get("strength") or 0.35),
                    "cfg": float(params.get("cfg_scale") or 7.0),
                    "steps": 12,
                },
            }
        )

        cue_text = _usable_transcript_overlay_text(scene.get("transcript_cue"))
        if cue_text:
            layers.append(
                {
                    "id": f"creative_overlay_{index}",
                    "type": "text",
                    "text": cue_text[:180],
                    "start_s": start_s,
                    "end_s": end_s,
                    "x": 24,
                    "y": 24 + (index % 3) * 92,
                    "w": 420,
                    "h": 84,
                    "size": 32,
                    "color": "#ffffff",
                    "stroke_color": "#000000",
                    "stroke_width": 2,
                    "opacity": 0.94 if float(scene.get("energy") or 0.0) >= 0.5 else 0.82,
                    "blend_mode": "normal",
                    "z": 20 + index,
                }
            )

        camera_keyframes.extend(
            [
                {
                    "t": start_s,
                    "zoom": zoom_start,
                    "pan_x": pan_x_start,
                    "pan_y": pan_y_start,
                    "rotation_deg": rotation_start,
                },
                {
                    "t": end_s,
                    "zoom": zoom_end,
                    "pan_x": pan_x_end,
                    "pan_y": pan_y_end,
                    "rotation_deg": rotation_end,
                },
            ]
        )
        prev_zoom = zoom_end
        prev_pan_x = pan_x_end
        prev_pan_y = pan_y_end
        prev_rotation = rotation_end

    return {
        "ok": bool(packed_scenes),
        "timeline": {
            "tracks": [prompt_track, motion_track],
            "layers": layers,
            "camera": {"keyframes": _dedupe_camera_keyframes(camera_keyframes)},
            "render": {"fps_output": 24},
            "duration_s": duration_s,
        },
        "notes": [
            "Prompt and motion tracks match the canonical Studio timeline schema.",
            "Lyric and transcript cues are converted into compositor text layers instead of a parallel overlay-track format.",
        ],
    }


def _build_creative_deforum_preview(
    packed_scenes: list[dict[str, Any]],
    duration_s: float,
    negative_prompt: str,
    fps: int = 30,
) -> dict[str, Any]:
    total_frames = max(1, int(round(max(duration_s, 1.0) * max(1, fps))))
    prompts: dict[str, str] = {}
    zoom_pairs: list[tuple[int, float]] = []
    angle_pairs: list[tuple[int, float]] = []
    translation_pairs: list[tuple[int, float]] = []
    translation_x_pairs: list[tuple[int, float]] = []
    translation_y_pairs: list[tuple[int, float]] = []
    rotation_x_pairs: list[tuple[int, float]] = []
    rotation_y_pairs: list[tuple[int, float]] = []
    cfg_pairs: list[tuple[int, float]] = []
    strength_pairs: list[tuple[int, float]] = []
    contrast_pairs: list[tuple[int, float]] = []

    for index, scene in enumerate(packed_scenes):
        start_frame = max(0, int(round(float(scene.get("start_s") or 0.0) * fps)))
        end_frame = max(start_frame + 1, int(round(float(scene.get("end_s") or 0.0) * fps)))
        params = scene.get("reactive_params") if isinstance(scene.get("reactive_params"), dict) else {}
        prompts[str(start_frame)] = str(scene.get("prompt") or "cinematic").strip() or "cinematic"
        zoom = float(params.get("zoom") or 1.0)
        angle = float(params.get("rotation_y") or params.get("rotation_z") or 0.0)
        rotation_x = float(params.get("rotation_x") or 0.0)
        rotation_y = float(params.get("rotation_y") or 0.0)
        translation = float(params.get("translation_z") or 0.0)
        translation_x = float(params.get("translation_x") or 0.0)
        translation_y = float(params.get("translation_y") or 0.0)
        cfg = float(params.get("cfg_scale") or 7.0)
        strength = float(params.get("strength") or 0.35)
        contrast = float(params.get("contrast") or 1.0)
        zoom_pairs.extend([(start_frame, zoom), (end_frame, zoom + max(0.01, float(scene.get("energy") or 0.0) * 0.02))])
        angle_pairs.extend([(start_frame, angle), (end_frame, angle + float(scene.get("energy") or 0.0) * 2.0)])
        rotation_x_pairs.extend([(start_frame, rotation_x), (end_frame, rotation_x)])
        rotation_y_pairs.extend([(start_frame, rotation_y), (end_frame, rotation_y)])
        translation_pairs.extend([(start_frame, translation), (end_frame, translation - float(scene.get("energy") or 0.0) * 2.0)])
        translation_x_pairs.extend([(start_frame, translation_x), (end_frame, translation_x)])
        translation_y_pairs.extend([(start_frame, translation_y), (end_frame, translation_y)])
        cfg_pairs.extend([(start_frame, cfg), (end_frame, cfg)])
        strength_pairs.extend([(start_frame, strength), (end_frame, strength)])
        contrast_pairs.extend([(start_frame, contrast), (end_frame, contrast)])

    schedules = {
        "zoom": _format_schedule_pairs(zoom_pairs) if zoom_pairs else "",
        "angle": _format_schedule_pairs(angle_pairs) if angle_pairs else "",
        "rotation_3d_x": _format_schedule_pairs(rotation_x_pairs) if rotation_x_pairs else "",
        "rotation_3d_y": _format_schedule_pairs(rotation_y_pairs) if rotation_y_pairs else "",
        "translation_x": _format_schedule_pairs(translation_x_pairs) if translation_x_pairs else "",
        "translation_y": _format_schedule_pairs(translation_y_pairs) if translation_y_pairs else "",
        "translation_z": _format_schedule_pairs(translation_pairs) if translation_pairs else "",
        "cfg_scale_schedule": _format_schedule_pairs(cfg_pairs) if cfg_pairs else "",
        "strength_schedule": _format_schedule_pairs(strength_pairs) if strength_pairs else "",
        "contrast_schedule": _format_schedule_pairs(contrast_pairs) if contrast_pairs else "",
    }

    return {
        "ok": bool(packed_scenes),
        "settings": {
            "animation_mode": "3D",
            "fps": fps,
            "max_frames": total_frames,
            "prompts": prompts or {"0": "cinematic"},
            "negative_prompts": {"0": negative_prompt},
            **{key: value for key, value in schedules.items() if value},
            "schedules": schedules,
        },
    }


def _build_creative_contract(
    proj: Any,
    plan: dict[str, Any],
    transcript_text: str,
    packed_scenes: list[dict[str, Any]],
    motifs: list[str],
    hooks: list[str],
    duration_s: float,
    bpm: float,
    provider_mode: str,
) -> dict[str, Any]:
    mode = "lyric-film" if transcript_text else "music-video"
    visual_tone = str(
        (plan.get("variants") or [{}])[0].get("mood")
        if isinstance((plan.get("variants") or [{}])[0], dict)
        else ""
    ).strip() or "cinematic reactive framing"

    return {
        "ok": True,
        "endpoint": "/v1/projects/:project_id/narrative_direction",
        "provider_mode": provider_mode,
        "request": {
            "title": str(getattr(proj, "name", "") or "Untitled project"),
            "transcript": transcript_text,
            "duration_s": duration_s,
            "bpm": bpm,
            "scene_count": len(packed_scenes),
            "mode": mode,
            "visual_tone": visual_tone,
            "anchors": motifs[:5],
            "hooks": hooks,
        },
        "expected_response_shape": {
            "ok": True,
            "creative_direction": {
                "scenes": [
                    {
                        "name": "string",
                        "start_s": 0,
                        "end_s": 0,
                        "prompt": "string",
                        "camera_hint": "string",
                        "motion_hint": "string",
                        "transcript_cue": "string",
                    }
                ]
            },
            "timeline_patch": {
                "timeline": {
                    "tracks": [{"type": "prompt"}, {"type": "motion"}],
                    "layers": [{"type": "text"}],
                }
            },
        },
    }


def _merge_creative_timeline_patch(
    base_timeline: dict[str, Any],
    patch_timeline: dict[str, Any],
    *,
    overwrite_tracks: bool,
    overwrite_camera: bool,
) -> dict[str, Any]:
    merged = {**(base_timeline or {})}
    base_tracks = [track for track in list(merged.get("tracks") or []) if isinstance(track, dict)]
    patch_tracks = [track for track in list(patch_timeline.get("tracks") or []) if isinstance(track, dict)]

    for patch_track in patch_tracks:
        track_type = str(patch_track.get("type") or "").lower()
        idx = next(
            (index for index, track in enumerate(base_tracks) if str(track.get("type") or "").lower() == track_type),
            -1,
        )
        if idx >= 0:
            if overwrite_tracks:
                base_tracks[idx] = patch_track
            else:
                existing_clips = [clip for clip in list(base_tracks[idx].get("clips") or []) if isinstance(clip, dict)]
                merged_clips = {str(clip.get("id") or f"clip_{index}"): clip for index, clip in enumerate(existing_clips)}
                for clip_index, clip in enumerate(list(patch_track.get("clips") or [])):
                    if not isinstance(clip, dict):
                        continue
                    merged_clips[str(clip.get("id") or f"patch_{clip_index}")] = clip
                base_tracks[idx] = {**base_tracks[idx], **patch_track, "clips": list(merged_clips.values())}
        else:
            base_tracks.append(patch_track)

    merged["tracks"] = base_tracks

    base_layers = [layer for layer in list(merged.get("layers") or []) if isinstance(layer, dict)]
    patch_layers = [layer for layer in list(patch_timeline.get("layers") or []) if isinstance(layer, dict)]
    merged_layers = {str(layer.get("id") or f"layer_{index}"): layer for index, layer in enumerate(base_layers)}
    for index, layer in enumerate(patch_layers):
        merged_layers[str(layer.get("id") or f"patch_layer_{index}")] = layer
    merged["layers"] = list(merged_layers.values())

    patch_camera = patch_timeline.get("camera") if isinstance(patch_timeline.get("camera"), dict) else {}
    base_camera = merged.get("camera") if isinstance(merged.get("camera"), dict) else {}
    if overwrite_camera or not list(base_camera.get("keyframes") or []):
        merged["camera"] = patch_camera or base_camera
    else:
        merged_keyframes = _dedupe_camera_keyframes(
            [keyframe for keyframe in list(base_camera.get("keyframes") or []) if isinstance(keyframe, dict)]
            + [keyframe for keyframe in list(patch_camera.get("keyframes") or []) if isinstance(keyframe, dict)]
        )
        merged["camera"] = {**base_camera, **patch_camera, "keyframes": merged_keyframes}

    patch_render = patch_timeline.get("render") if isinstance(patch_timeline.get("render"), dict) else {}
    if patch_render:
        merged["render"] = {**(merged.get("render") if isinstance(merged.get("render"), dict) else {}), **patch_render}

    if isinstance(patch_timeline.get("duration_s"), (int, float)):
        merged["duration_s"] = float(patch_timeline.get("duration_s"))
    return merged


def _build_creative_direction_payload(proj: Any, variant_index: int, preset: str, sensitivity: float) -> dict[str, Any]:
    analysis_raw = (proj.meta.get("analysis") or {}) if hasattr(proj, "meta") else {}
    analysis = analysis_raw if isinstance(analysis_raw, dict) else {}
    plan_raw = (proj.meta.get("last_plan") or {}) if hasattr(proj, "meta") else {}
    plan = plan_raw if isinstance(plan_raw, dict) else {}
    variants = list(plan.get("variants") or [])
    variant = variants[variant_index] if 0 <= variant_index < len(variants) else {}
    scenes = list(variant.get("scenes") or []) if isinstance(variant, dict) else []
    transcript_text = _analysis_transcript_text(analysis).strip()
    transcript_sentences = _analysis_transcript_sentences(analysis)
    hooks = _creative_hooks(transcript_sentences)
    saved_tags = list(analysis.get("tags") or []) if isinstance(analysis, dict) else []
    motifs = list(dict.fromkeys([*saved_tags, *_analysis_motifs(variant if isinstance(variant, dict) else {}, transcript_text)]))[:8]
    has_transcript = _has_usable_transcript(analysis)
    emotion_tokens = _creative_tokenize(" ".join([transcript_text, *[str(scene.get("prompt") or "") for scene in scenes if isinstance(scene, dict)]]))
    emotions = _creative_emotion_scores(emotion_tokens)
    overall = _infer_reactivity_metrics(analysis if isinstance(analysis, dict) else {})
    energy_curve = list(overall.get("energy_curve") or [])
    waveform = list(overall.get("waveform") or [])
    duration_s = float(overall.get("duration_s") or 0.0)
    saved_sections = list(analysis.get("sections") or []) if isinstance(analysis, dict) and isinstance(analysis.get("sections"), list) else []
    fallback_sections = saved_sections or _derive_reactive_sections(
        overall,
        duration_s,
        transcript_sentences,
        motifs,
        str(getattr(proj, "name", "") or "Untitled project"),
        preset,
        sensitivity,
        max_sections=min(8, max(3, len(scenes) or 6)),
    )
    source_scenes: list[dict[str, Any]] = scenes if scenes else fallback_sections
    scene_source = "plan" if scenes else "analysis_fallback" if fallback_sections else "none"
    provider_mode = _creative_provider_mode(plan)
    negative_prompt = next(
        (
            str(scene.get("negative_prompt") or "").strip()
            for scene in source_scenes
            if isinstance(scene, dict) and str(scene.get("negative_prompt") or "").strip()
        ),
        "blurry, low quality, watermark, text, logo",
    )

    packed_scenes: list[dict[str, Any]] = []
    for index, scene in enumerate(source_scenes):
        name = str(scene.get("name") or f"Scene {index + 1}")
        start_s = float(scene.get("start_s") or index * 5.0)
        end_s = float(scene.get("end_s") or (start_s + 5.0))
        if scene_source == "analysis_fallback" and isinstance(scene.get("reactive_params"), dict):
            metrics = {
                "energy": float(scene.get("energy") or 0.0),
                "bass": max(0.0, min(1.0, float(overall.get("bass") or 0.0) * 0.85 + float(scene.get("peak_energy") or scene.get("energy") or 0.0) * 0.12)),
                "mid": max(0.0, min(1.0, float(overall.get("mid") or 0.0) * 0.85 + float(scene.get("avg_energy") or scene.get("energy") or 0.0) * 0.12)),
                "treble": max(0.0, min(1.0, float(overall.get("treble") or 0.0) * 0.85 + float(scene.get("peak_energy") or scene.get("energy") or 0.0) * 0.1)),
                "duration_s": max(0.2, end_s - start_s),
                "source": "analysis",
            }
            params = {key: float(value) for key, value in dict(scene.get("reactive_params") or {}).items() if isinstance(value, (int, float))}
            transcript_cue = str(scene.get("transcript_cue") or "").strip() if has_transcript else ""
            energy_label = str(scene.get("energy_label") or _creative_energy_label(float(metrics["energy"])))
            camera_hint = str(scene.get("camera_hint") or _creative_camera_hint(float(metrics["energy"])))
            motion_hint = str(scene.get("motion_hint") or _creative_motion_hint(params))
        else:
            metrics = _scene_metrics_from_curve(index, len(source_scenes) or 1, scene, overall, duration_s, energy_curve)
            params = _compute_reactive_params(metrics, preset, sensitivity)
            cue_index = (
                min(len(transcript_sentences) - 1, int((index / max(1, len(source_scenes) - 1)) * len(transcript_sentences)))
                if transcript_sentences and has_transcript else -1
            )
            transcript_cue = transcript_sentences[cue_index] if cue_index >= 0 else ""
            energy_label = _creative_energy_label(float(metrics["energy"]))
            camera_hint = _creative_camera_hint(float(metrics["energy"]))
            motion_hint = _creative_motion_hint(params)
        prompt = str(scene.get("prompt") or "").strip() or "Cinematic image sequence with a coherent subject and controlled atmosphere."
        scene_tokens = _analysis_top_keywords(" ".join([name, prompt, transcript_cue]), limit=5)
        scene_motifs = list(
            dict.fromkeys(
                [
                    *scene_tokens[:3],
                    *motifs[(index * 2): (index * 2) + 2],
                    *motifs[: max(0, 2 - len(scene_tokens[:3]))],
                ]
            )
        )[:4]
        phase_hint = (
            "Open the visual world clearly before adding pressure."
            if index == 0 else
            "Resolve the sequence with a release image and clean afterglow."
            if index == max(0, len(source_scenes) - 1) else
            "Push into a distinct section change instead of repeating the previous beat."
            if float(metrics["energy"]) >= 0.68 else
            "Use this section to vary texture, framing, or environment while holding continuity."
        )
        continuity_hint = (
            "Continuity: establish subject, palette, and world."
            if index == 0 else
            f"Continuity: retain the strongest subject and palette cues from scene {index}."
        )
        audio_anchor = (
            f"Audio anchor: follow the {energy_label.lower()} section arc with {scene_motifs[0]}."
            if not transcript_cue and scene_motifs
            else "Audio anchor: let motion and framing follow the section energy arc."
            if not transcript_cue
            else ""
        )
        prompt_pack = " ".join(
            [
                prompt,
                f"Energy profile: {energy_label}.",
                camera_hint,
                f"Motion recipe: {motion_hint}",
                f"Scene motifs: {', '.join(scene_motifs)}." if scene_motifs else "",
                phase_hint,
                continuity_hint,
                f"Narrative cue: {transcript_cue}" if transcript_cue else "",
                audio_anchor,
            ]
        ).strip()
        packed_scenes.append(
            {
                "index": index,
                "name": name,
                "start_s": start_s,
                "end_s": end_s,
                "duration_s": max(0.2, end_s - start_s),
                "energy": float(metrics["energy"]),
                "energy_label": energy_label,
                "prompt": prompt,
                "transcript_cue": transcript_cue,
                "camera_hint": camera_hint,
                "motion_hint": motion_hint,
                "prompt_pack": prompt_pack,
                "reactive_params": params,
                "scene_source": scene_source,
            }
        )

    export_text = "\n\n".join(
        [
            (
                f"{scene['index'] + 1}. {scene['name']} ({scene['start_s']:.2f}s - {scene['end_s']:.2f}s)\n"
                f"{scene['prompt_pack']}"
            )
            for scene in packed_scenes
        ]
    )
    timeline_patch = _build_creative_timeline_patch(packed_scenes, duration_s or max([float(scene.get("end_s") or 0.0) for scene in packed_scenes] or [0.0]), negative_prompt)
    deforum_preview = _build_creative_deforum_preview(
        packed_scenes,
        duration_s or max([float(scene.get("end_s") or 0.0) for scene in packed_scenes] or [0.0]),
        negative_prompt,
        fps=30,
    )
    bpm = float(
        _pick_raw_number((analysis.get("features") or {}) if isinstance(analysis, dict) else {}, ["bpm", "tempo_bpm", "tempo"])
        or 0.0
    )
    narrative_analysis = {
        "ok": bool(transcript_text or motifs or packed_scenes),
        "title": str(getattr(proj, "name", "") or "Untitled project"),
        "provider_mode": provider_mode,
        "scene_source": scene_source,
        "emotions": emotions,
        "hooks": hooks,
        "motifs": motifs,
        "transcript_line_count": len(transcript_sentences),
        "segment_count": len(_analysis_transcript_segments(analysis)),
        "section_count": len(fallback_sections),
        "themes": list(analysis.get("themes") or []) if isinstance(analysis, dict) else [],
    }
    llm_contract = _build_creative_contract(
        proj,
        plan,
        transcript_text,
        packed_scenes,
        motifs,
        hooks,
        duration_s,
        bpm,
        provider_mode,
    )

    missing: list[str] = []
    if not analysis:
        missing.append("analysis")
    if not variants:
        missing.append("plan")
    ready = bool(packed_scenes or fallback_sections or transcript_text)
    if analysis and scenes:
        status = "Creative direction is being derived on the backend from the saved Overview analysis and plan. Planner extends that base, and Reactive Lab can add motion scheduling on top."
    elif analysis and fallback_sections:
        status = "Plan not found. Using audio-reactive fallback sections derived from saved Overview analysis."
    elif scenes:
        status = "Audio analysis not found. Using saved plan scenes with narrative fallbacks."
    else:
        status = "Run audio analysis and generate a plan variant to unlock creative direction guidance."

    return {
        "ready": ready,
        "missing": missing,
        "preset": preset,
        "sensitivity": float(sensitivity),
        "provider_mode": provider_mode,
        "scene_source": scene_source,
        "metrics": {
            "energy": float(overall["energy"]),
            "bass": float(overall["bass"]),
            "mid": float(overall["mid"]),
            "treble": float(overall["treble"]),
            "duration_s": duration_s,
            "source": "analysis",
        },
        "waveform": waveform,
        "motifs": motifs,
        "transcript_text": transcript_text,
        "transcript_summary": str(analysis.get("summary") or "").strip() or " ".join(transcript_sentences[:3]),
        "narrative_analysis": narrative_analysis,
        "sections": fallback_sections,
        "scenes": packed_scenes,
        "export_text": export_text,
        "timeline_patch": timeline_patch,
        "deforum_preview": deforum_preview,
        "llm_contract": llm_contract,
        "notes": [
            "Creative direction now carries audio-reactive sections, timeline patch data, and a Deforum-aligned preview in one Studio-native payload.",
            "Prompt and motion tracks stay in the canonical timeline schema, while lyric cues are translated into compositor text layers.",
            "Overview analysis remains the canonical source. Planner enriches the storyboard, and Reactive Lab adds motion schedules without replacing the saved story pass.",
        ],
        "status": status,
    }


def _format_schedule_pairs(pairs: list[tuple[int, float]]) -> str:
    try:
        from enhanced_deforum_music_generator.core.deforum_schedule_format import format_schedule  # type: ignore
        return format_schedule(pairs)
    except Exception:
        # fallback: "f:(v), ..."
        return ", ".join([f"{int(f)}:({float(v):.4f})" for f, v in pairs])


def _derive_steps_and_denoise_schedules(analysis_obj: Any, *, fps: int, base_steps: int = 15) -> tuple[str, str]:
    """Heuristic schedules from energy: higher energy -> more steps + higher denoise."""
    dur = float(getattr(analysis_obj, "duration", 0.0) or 0.0)
    energy = list(getattr(analysis_obj, "energy", []) or [])
    if not dur or not energy:
        # safe defaults
        steps = _format_schedule_pairs([(0, float(base_steps))])
        denoise = _format_schedule_pairs([(0, 0.35)])
        return steps, denoise

    n = min(64, max(8, len(energy)))
    pairs_steps: list[tuple[int, float]] = []
    pairs_d: list[tuple[int, float]] = []

    for i in range(n):
        u = i / max(1, n - 1)
        idx = int(round(u * (len(energy) - 1)))
        e = float(energy[idx])
        frame = int(round((u * dur) * fps))

        # steps: 10..28 around base_steps
        steps_v = max(8.0, min(36.0, float(base_steps) * (0.70 + 0.90 * e)))
        # denoise/strength: 0.20..0.85
        den_v = max(0.15, min(0.90, 0.20 + 0.65 * e))

        pairs_steps.append((frame, steps_v))
        pairs_d.append((frame, den_v))

    return _format_schedule_pairs(pairs_steps), _format_schedule_pairs(pairs_d)


def _local_plan_from_project(proj: Any, *, title: str, style_prefs: str, num_variants: int, max_scenes: int) -> dict[str, Any]:
    """Deterministic (no-LLM) plan builder using EDMG-core orchestrators."""
    analysis_obj = _build_public_audio_analysis(proj)
    analysis_meta = (proj.meta.get("analysis") or {}) if hasattr(proj, "meta") else {}
    fps = 24

    from enhanced_deforum_music_generator.core.prompt_orchestrator import PromptOrchestrator, OrchestrationConfig  # type: ignore
    from enhanced_deforum_music_generator.core.motion_orchestrator import MotionConfig, motion_schedules  # type: ignore

    orch = PromptOrchestrator(provider=None, cfg=OrchestrationConfig(fps=fps, max_scenes=max_scenes))
    motion = motion_schedules(analysis_obj, cfg=MotionConfig(fps=fps))

    # add steps + denoise schedules
    steps_sched, denoise_sched = _derive_steps_and_denoise_schedules(analysis_obj, fps=fps, base_steps=15)
    motion.setdefault("steps_schedule", steps_sched)
    motion.setdefault("denoise_schedule", denoise_sched)
    transcript_sentences = _analysis_transcript_sentences(analysis_meta)
    tags = list(analysis_meta.get("tags") or []) if isinstance(analysis_meta, dict) else []
    scene_roles = [
        "opening tableau",
        "first lift",
        "world expansion",
        "pressure turn",
        "release peak",
        "afterglow resolve",
    ]

    variants: list[dict[str, Any]] = []
    for vi in range(int(num_variants)):
        base_prompt = "cinematic, coherent subject, high detail, consistent style"
        style_prompt = style_prefs or ""
        out = orch.orchestrate(
            analysis_obj,
            base_prompt=base_prompt,
            style_prompt=style_prompt,
            negative_prompt="blurry, low quality, watermark, text, logo",
            use_ai=False,
        )
        fps_out = int(out.get("fps") or fps) or fps
        frames = [int(s.get("frame", 0)) for s in (out.get("scene_plan") or [])]
        frames = sorted({0, *frames})
        dur_s = float(getattr(analysis_obj, "duration", 0.0) or 0.0) or 60.0
        end_frame = int(round(dur_s * fps_out))
        if frames and frames[-1] < end_frame:
            frames.append(end_frame)

        prompts = out.get("prompts") or {}
        scenes: list[dict[str, Any]] = []
        for i in range(len(frames) - 1):
            a = frames[i]
            b = frames[i + 1]
            start_s = float(a) / float(fps_out)
            end_s = float(b) / float(fps_out)
            prompt_base = str(prompts.get(str(int(a))) or prompts.get(str(int(frames[max(0, i - 1)]))) or base_prompt).strip() or base_prompt
            role = scene_roles[min(len(scene_roles) - 1, int(round((i / max(1, max(1, len(frames) - 2))) * (len(scene_roles) - 1))))]
            cue_index = min(len(transcript_sentences) - 1, i) if transcript_sentences else -1
            narrative_cue = transcript_sentences[cue_index] if cue_index >= 0 else ""
            motif_window = list(dict.fromkeys([*tags[i:i + 3], *tags[: max(0, 3 - len(tags[i:i + 3]))]]))[:3]
            prompt_variant = " ".join(
                [
                    prompt_base,
                    f"section role {role}.",
                    f"scene motifs {', '.join(motif_window)}." if motif_window else "",
                    f"narrative cue {narrative_cue}" if narrative_cue else "",
                ]
            ).strip()
            scenes.append(
                {
                    "name": role.title(),
                    "start_s": start_s,
                    "end_s": end_s,
                    "prompt": prompt_variant,
                    "negative_prompt": "blurry, low quality, watermark, text, logo",
                }
            )

        variants.append(
            {
                "index": vi,
                "fps": fps_out,
                "duration_s": dur_s,
                "scenes": scenes,
                "motion_schedules": motion,
                "source": "local",
            }
        )

    return {"title": title, "duration_s": float(getattr(analysis_obj, "duration", 0.0) or 0.0) or 60.0, "variants": variants, "source": "local"}


def _scene_energy_from_analysis(index: int, total: int, analysis: dict[str, Any]) -> float:
    overall = _infer_reactivity_metrics(analysis if isinstance(analysis, dict) else {})
    curve = list(overall.get("energy_curve") or [])
    if curve:
        pointer = min(len(curve) - 1, max(0, int(round((index / max(1, total - 1)) * (len(curve) - 1)))))
        try:
            return max(0.0, min(1.0, float(curve[pointer])))
        except Exception:
            pass
    return max(0.0, min(1.0, 0.3 + (index / max(1, total - 1)) * 0.45 if total > 1 else 0.5))


def _enrich_normalized_plan(plan: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    plan_out = deepcopy(plan if isinstance(plan, dict) else {})
    variants = list(plan_out.get("variants") or [])
    transcript_sentences = _analysis_transcript_sentences(analysis if isinstance(analysis, dict) else {})
    tags = [str(tag or "").strip() for tag in list((analysis or {}).get("tags") or []) if str(tag or "").strip()]
    scene_roles = [
        "opening tableau",
        "first lift",
        "world expansion",
        "pressure turn",
        "release peak",
        "afterglow resolve",
    ]
    high_energy_moves = [
        "cross-frame tracking push with the subject moving left-to-right",
        "decisive lateral sweep through foreground depth",
        "parallax-heavy drive that resets the camera axis on impact",
    ]
    mid_energy_moves = [
        "measured dolly with lateral travel",
        "steady side-to-side drift through foreground texture",
        "motivated pan that follows the subject through the frame",
    ]
    low_energy_moves = [
        "wide hold with a slow pan reveal",
        "quiet reframing around the subject with soft side drift",
        "negative-space composition with a restrained lateral glide",
    ]
    staging_cues = [
        "use foreground depth and moving light to keep the frame alive",
        "let the environment change the camera lane so the section does not repeat the last one",
        "keep the subject silhouette clear while varying lens distance and frame pressure",
    ]
    palette_defaults = [
        "silver fog and petrol green",
        "desaturated indigo and moonlit white",
        "crimson pulse and black chrome",
        "dusty gold and weathered teal",
    ]
    transition_cues = [
        "bridge into the next beat through motion continuity",
        "shift the camera lane before the next section lands",
        "let atmosphere and edge light carry the cut forward",
        "reset composition pressure on the next downbeat",
    ]

    for variant_index, raw_variant in enumerate(variants):
        if not isinstance(raw_variant, dict):
            continue
        variant = dict(raw_variant)
        scenes = list(variant.get("scenes") or [])
        total = max(1, len(scenes))
        next_scenes: list[dict[str, Any]] = []
        for scene_index, raw_scene in enumerate(scenes):
            if not isinstance(raw_scene, dict):
                continue
            scene = dict(raw_scene)
            role = scene_roles[min(len(scene_roles) - 1, int(round((scene_index / max(1, total - 1)) * (len(scene_roles) - 1))))]
            energy = _scene_energy_from_analysis(scene_index, total, analysis if isinstance(analysis, dict) else {})
            motion = (
                high_energy_moves[(scene_index + variant_index) % len(high_energy_moves)]
                if energy >= 0.72
                else mid_energy_moves[(scene_index + variant_index) % len(mid_energy_moves)]
                if energy >= 0.44
                else low_energy_moves[(scene_index + variant_index) % len(low_energy_moves)]
            )
            staging = staging_cues[(scene_index + variant_index) % len(staging_cues)]
            cue_index = min(len(transcript_sentences) - 1, scene_index) if transcript_sentences else -1
            narrative_cue = transcript_sentences[cue_index] if cue_index >= 0 else ""
            motif_window = list(dict.fromkeys([*tags[scene_index:scene_index + 2], *tags[: max(0, 2 - len(tags[scene_index:scene_index + 2]))]]))[:2]
            palette_note = motif_window[0] if motif_window else palette_defaults[(scene_index + variant_index) % len(palette_defaults)]
            continuity = (
                "continuity: lock the lead subject, palette, and world before introducing stronger motion changes."
                if scene_index == 0
                else f"continuity: retain the lead silhouette and {palette_note} palette from scene {scene_index}, but change the camera lane or staging pressure."
            )
            additions = [
                f"section role {role}.",
                f"camera move {motion}.",
                f"staging {staging}.",
                f"palette emphasis {palette_note}.",
                continuity,
            ]
            if motif_window:
                additions.append(f"scene motifs {', '.join(motif_window)}.")
            if narrative_cue and narrative_cue.lower() not in str(scene.get('prompt') or '').lower():
                additions.append(f"narrative cue {narrative_cue}.")

            prompt = str(scene.get("prompt") or "").strip() or "Cinematic image sequence with a coherent subject and controlled atmosphere."
            scene["prompt"] = " ".join([prompt, *additions]).strip()
            if not str(scene.get("name") or "").strip() or re.fullmatch(r"scene\s*\d+", str(scene.get("name") or "").strip(), re.IGNORECASE):
                scene["name"] = role.title()
            scene["transition"] = str(scene.get("transition") or transition_cues[(scene_index + variant_index) % len(transition_cues)])
            next_scenes.append(scene)
        variant["scenes"] = next_scenes
        variants[variant_index] = variant

    plan_out["variants"] = variants
    return plan_out


def _coerce_scene_time(raw: Any, fallback: float) -> float:
    try:
        return max(0.0, float(raw))
    except Exception:
        return max(0.0, float(fallback))


def _normalize_plan_scene_list(
    scenes: Any,
    *,
    duration_s: float | None,
    max_scenes: int,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    source = scenes if isinstance(scenes, list) else []
    for index, raw_scene in enumerate(source):
        if not isinstance(raw_scene, dict):
            continue
        start_s = _coerce_scene_time(raw_scene.get("start_s"), float(index))
        end_s = _coerce_scene_time(raw_scene.get("end_s"), start_s + 1.0)
        if end_s <= start_s:
            end_s = start_s + 0.5
        scene = dict(raw_scene)
        scene["start_s"] = start_s
        scene["end_s"] = end_s
        scene["prompt"] = str(raw_scene.get("prompt") or "").strip() or "Cinematic image sequence with a coherent subject and controlled atmosphere."
        scene["negative_prompt"] = str(raw_scene.get("negative_prompt") or "blurry, low quality, watermark, text, logo").strip()
        normalized.append(scene)

    normalized.sort(key=lambda scene: (_coerce_scene_time(scene.get("start_s"), 0.0), _coerce_scene_time(scene.get("end_s"), 0.0)))

    if not normalized:
        if duration_s and duration_s > 0:
            return [
                {
                    "start_s": 0.0,
                    "end_s": float(duration_s),
                    "prompt": "Cinematic image sequence with a coherent subject and controlled atmosphere.",
                    "negative_prompt": "blurry, low quality, watermark, text, logo",
                }
            ]
        return []

    limit = max(1, int(max_scenes or len(normalized)))
    if len(normalized) > limit:
        normalized = normalized[:limit]

    final_duration = float(duration_s or 0.0)
    if final_duration <= 0:
        final_duration = max(_coerce_scene_time(scene.get("end_s"), 0.0) for scene in normalized)
    if final_duration <= 0:
        final_duration = max(0.5, float(len(normalized)))

    carry_start = 0.0
    for index, scene in enumerate(normalized):
        scene["start_s"] = carry_start
        if index == len(normalized) - 1:
            scene["end_s"] = max(carry_start + 0.05, final_duration)
        else:
            proposed_end = _coerce_scene_time(scene.get("end_s"), carry_start + 0.5)
            scene["end_s"] = max(carry_start + 0.05, min(proposed_end, final_duration))
        carry_start = float(scene["end_s"])

    return normalized


def _normalize_plan_payload(
    plan: dict[str, Any],
    *,
    requested_variants: int,
    requested_max_scenes: int,
    duration_s_hint: float | None,
) -> dict[str, Any]:
    normalized = dict(plan or {})
    variants_raw = normalized.get("variants") if isinstance(normalized.get("variants"), list) else []
    variants: list[dict[str, Any]] = []
    limit = max(1, int(requested_variants or 1))

    for raw_variant in list(variants_raw)[:limit]:
        if not isinstance(raw_variant, dict):
            continue
        variant = dict(raw_variant)
        variant_duration = _coerce_scene_time(
            raw_variant.get("duration_s") or normalized.get("duration_s") or duration_s_hint,
            duration_s_hint or 0.0,
        )
        variant["duration_s"] = variant_duration
        variant["scenes"] = _normalize_plan_scene_list(
            raw_variant.get("scenes"),
            duration_s=variant_duration or duration_s_hint,
            max_scenes=requested_max_scenes,
        )
        variants.append(variant)

    normalized["variants"] = variants
    if duration_s_hint and duration_s_hint > 0:
        normalized["duration_s"] = float(duration_s_hint)
    elif variants:
        normalized["duration_s"] = max(_coerce_scene_time((variant or {}).get("duration_s"), 0.0) for variant in variants)
    normalized["source"] = str(normalized.get("source") or "local")
    return normalized


def _merge_imported_analysis(base: Any, imported: dict[str, Any]) -> dict[str, Any]:
    current = deepcopy(base) if isinstance(base, dict) else {}
    incoming = imported if isinstance(imported, dict) else {}
    base_features = current.get("features") if isinstance(current.get("features"), dict) else {}
    next_features = incoming.get("features") if isinstance(incoming.get("features"), dict) else {}
    current["features"] = {**base_features, **next_features}

    transcript = incoming.get("transcript")
    if isinstance(transcript, dict) and str(transcript.get("text") or "").strip():
        current["transcript"] = transcript

    tags = []
    for raw in [*(current.get("tags") or []), *(incoming.get("tags") or [])]:
        text = str(raw or "").strip()
        if text and text not in tags:
            tags.append(text)
    if tags:
        current["tags"] = tags

    current["source"] = str(incoming.get("source") or current.get("source") or "imported")
    return current


def _load_project_visual_dna(proj: Any):
    return load_visual_dna(
        store.project_dir(proj.id),
        project_id=str(proj.id),
        project_name=str(getattr(proj, "name", "") or "") or None,
    )


def _save_project_visual_dna(proj: Any, dna):
    return save_visual_dna(store.project_dir(proj.id), dna)


def _project_response_payload(proj: Any) -> dict[str, Any]:
    dna = _load_project_visual_dna(proj)
    return {
        "project": proj.__dict__,
        "visual_dna": dna.model_dump(mode="json"),
        "visual_dna_hints": build_visual_dna_prompt_hints(dna),
    }


def _render_quality_tier_from_preset(preset: str | None) -> str:
    preset_l = str(preset or "balanced").strip().lower()
    if preset_l == "fast":
        return "draft"
    if preset_l == "quality":
        return "quality"
    if preset_l == "ultra":
        return "ultra"
    return "balanced"


def _default_speed_priority(quality_tier: str) -> float:
    return {
        "draft": 0.85,
        "balanced": 0.55,
        "quality": 0.35,
        "ultra": 0.25,
    }.get(str(quality_tier or "balanced"), 0.55)


def _build_render_conductor_intent(project_id: str, proj: Any, req: RenderConductorPlanRequest) -> RenderIntent:
    quality_tier = str(req.quality_tier or _render_quality_tier_from_preset(req.preset))
    dna = _load_project_visual_dna(proj)
    continuity_default = 0.8 if list(dna.continuity.subject_anchors or []) else 0.72
    return RenderIntent.model_validate(
        {
            "project_id": project_id,
            "variant_index": int(req.variant_index or 0),
            "aspect_ratio": req.aspect_ratio,
            "output_mode": req.output_mode,
            "quality_tier": quality_tier,
            "continuity_priority": continuity_default if req.continuity_priority is None else req.continuity_priority,
            "speed_priority": _default_speed_priority(quality_tier) if req.speed_priority is None else req.speed_priority,
            "style_lock_strength": 0.8 if req.style_lock_strength is None else req.style_lock_strength,
            "allowed_engines": list(req.allowed_engines or []),
            "fallback_policy": req.fallback_policy,
            "sections": [section.model_dump(mode="json") for section in list(req.sections or [])],
        }
    )


def _build_render_conductor_environment() -> dict[str, Any]:
    hw = _hardware_profile()
    provider_status = _render_provider_status(hw)
    runtime = _internal_diffusion_runtime_status()
    installed_internal = any(
        bool(models.installed_path(model_id))
        for model_id in ("hf_sd15_internal", "hf_sdxl_internal", "hf_sd35_medium_internal")
    )
    backend_family = str(hw.get("backend_family") or "cpu_only").lower()
    if backend_family == "discrete_gpu":
        internal_quality = 0.92
        internal_speed = 0.74
    elif backend_family == "integrated_gpu":
        internal_quality = 0.8
        internal_speed = 0.56
    else:
        internal_quality = 0.66
        internal_speed = 0.32

    ckpt, _fallback = _resolve_comfy_checkpoint_name(settings.comfyui_checkpoint, allow_auto_fallback=True)
    try:
        base_diag = comfy_pool.diagnose({"checkpoint": ckpt})
    except Exception:
        base_diag = {"compatible": [], "busy_compatible": []}
    base_ok = bool(base_diag.get("compatible") or base_diag.get("busy_compatible"))
    ad_ok = False
    svd_ok = False
    if base_ok:
        try:
            ad_diag = comfy_pool.diagnose(
                {
                    "checkpoint": ckpt,
                    "node_classes": ["ADE_StandardStaticContextOptions", "ADE_AnimateDiffLoaderGen1"],
                    "est_steps": 20,
                    "est_frames": 24,
                }
            )
            ad_ok = bool(ad_diag.get("compatible") or ad_diag.get("busy_compatible"))
        except Exception:
            ad_ok = False
        try:
            svd_diag = comfy_pool.diagnose(
                {
                    "checkpoint": ckpt,
                    "node_classes": ["SVDSimpleImg2Vid"],
                    "est_steps": 20,
                    "est_frames": 14,
                }
            )
            svd_ok = bool(svd_diag.get("compatible") or svd_diag.get("busy_compatible"))
        except Exception:
            svd_ok = False
    try:
        deforum_ok = bool(core_status().get("ok"))
    except Exception:
        deforum_ok = False

    diagnostics = [
        f"internal_runtime={'ready' if runtime.get('ok') else 'missing'}",
        f"internal_models={'installed' if installed_internal else 'missing'}",
        f"comfyui_still={'ready' if base_ok else 'unavailable'}",
        f"comfyui_motion={'ready' if (ad_ok or svd_ok) else 'unavailable'}",
        f"hosted_stability={'ready' if _hosted_stability_ready({'allow_hosted_fallback': True}) else 'unavailable'}",
        f"deforum_export={'ready' if deforum_ok else 'unavailable'}",
    ]
    return {
        "hardware": hw,
        "providers": provider_status,
        "diagnostics": diagnostics,
        "engines": {
            "internal": {
                "available": bool(runtime.get("ok") and installed_internal),
                "quality_score": internal_quality,
                "speed_score": internal_speed,
            },
            "comfyui_still": {
                "available": base_ok,
                "quality_score": 0.84,
                "speed_score": 0.58,
            },
            "comfyui_motion": {
                "available": bool(ad_ok or svd_ok),
                "quality_score": 0.8 if ad_ok else 0.74,
                "speed_score": 0.62 if ad_ok else 0.57,
            },
            "hosted_video": {
                "available": _hosted_stability_ready({"allow_hosted_fallback": True}),
                "quality_score": 0.78,
                "speed_score": 0.82,
            },
            "proxy": {
                "available": True,
                "quality_score": 0.38,
                "speed_score": 0.95,
            },
            "deforum_export": {
                "available": deforum_ok,
                "quality_score": 0.7,
                "speed_score": 0.45,
            },
        },
    }


def _build_project_snapshot(proj: Any, *, dna: Any | None = None) -> ProjectSnapshot:
    visual_dna = dna or _load_project_visual_dna(proj)
    return ProjectSnapshot(
        project_id=str(proj.id),
        project_name=str(getattr(proj, "name", "") or "") or None,
        analysis=(proj.meta.get("analysis") or {}) if isinstance(proj.meta, dict) else {},
        plan=(proj.meta.get("last_plan") or {}) if isinstance(proj.meta, dict) else {},
        timeline=(proj.meta.get("timeline") or {}) if isinstance(proj.meta, dict) else {},
        visual_dna=visual_dna,
    )


def _apply_plan_to_project_timeline(proj: Any, *, variant_index: int, overwrite: bool) -> dict[str, Any]:
    plan = proj.meta.get("last_plan")
    if not isinstance(plan, dict):
        raise HTTPException(400, "No plan. Generate a plan first.")
    variants = plan.get("variants") if isinstance(plan.get("variants"), list) else []
    vi = int(variant_index or 0)
    if not variants or vi < 0 or vi >= len(variants):
        raise HTTPException(400, "Invalid variant_index")
    variant = variants[vi] if isinstance(variants[vi], dict) else {}
    scenes = variant.get("scenes") if isinstance(variant.get("scenes"), list) else []
    duration_s = float(variant.get("duration_s") or plan.get("duration_s") or 60.0)

    timeline = proj.meta.get("timeline") if isinstance(proj.meta.get("timeline"), dict) else {}
    timeline = {**timeline}

    tracks = timeline.get("tracks") if isinstance(timeline.get("tracks"), list) else []
    tracks = [t for t in tracks if isinstance(t, dict)]

    def upsert_track(tid: str, name: str, ttype: str, clips: list[dict[str, Any]]) -> None:
        nonlocal tracks
        idx = next((i for i, t in enumerate(tracks) if str(t.get("id") or "") == tid or str(t.get("type") or "").lower() == ttype.lower()), -1)
        if idx >= 0:
            if overwrite or not tracks[idx].get("clips"):
                tracks[idx] = {**tracks[idx], "id": tid, "name": name, "type": ttype, "clips": clips}
        else:
            tracks.append({"id": tid, "name": name, "type": ttype, "clips": clips})

    prompt_clips: list[dict[str, Any]] = []
    for i, s in enumerate(scenes):
        try:
            ss = float(s.get("start_s", 0.0))
            ee = float(s.get("end_s", ss + 1.0))
        except Exception:
            ss, ee = 0.0, 1.0
        prompt_clips.append(
            {
                "id": f"edmg_prompt_{i}",
                "start_s": ss,
                "end_s": ee,
                "data": {
                    "prompt": str(s.get("prompt") or "").strip(),
                    "negative_prompt": str(s.get("negative_prompt") or "").strip(),
                },
            }
        )
    upsert_track("edmg_prompt", "EDMG Prompts", "prompt", prompt_clips)

    ms = variant.get("motion_schedules") if isinstance(variant.get("motion_schedules"), dict) else {}
    if not ms:
        try:
            aa = _build_public_audio_analysis(proj)
            from enhanced_deforum_music_generator.core.motion_orchestrator import MotionConfig, motion_schedules  # type: ignore
            ms = motion_schedules(aa, cfg=MotionConfig(fps=24))
            steps_sched, denoise_sched = _derive_steps_and_denoise_schedules(aa, fps=24, base_steps=15)
            ms.setdefault("steps_schedule", steps_sched)
            ms.setdefault("denoise_schedule", denoise_sched)
        except Exception:
            ms = {}
    motion_clip = {
        "id": "edmg_motion_0",
        "start_s": 0.0,
        "end_s": duration_s,
        "data": {**ms},
    }
    upsert_track("edmg_motion", "EDMG Motion", "motion", [motion_clip])

    timeline["tracks"] = tracks

    cam = timeline.get("camera") if isinstance(timeline.get("camera"), dict) else {}
    cam = {**cam}
    kfs = cam.get("keyframes") if isinstance(cam.get("keyframes"), list) else []
    if overwrite or not kfs:
        fps = 24
        zoom_s = str(ms.get("zoom") or "")
        ang_s = str(ms.get("angle") or "")

        def _parse_sched(s: str) -> list[tuple[int, float]]:
            pairs = []
            for part in str(s or "").split(","):
                part = part.strip()
                if not part:
                    continue
                m = re.match(r"^(\d+)\s*:\s*\(?\s*([-+]?\d*\.?\d+)\s*\)?$", part)
                if not m:
                    continue
                pairs.append((int(m.group(1)), float(m.group(2))))
            return sorted(pairs, key=lambda x: x[0])

        def _sample(pairs: list[tuple[int, float]], frame: int) -> float:
            if not pairs:
                return 0.0
            if frame <= pairs[0][0]:
                return float(pairs[0][1])
            if frame >= pairs[-1][0]:
                return float(pairs[-1][1])
            for i in range(len(pairs) - 1):
                fa, va = pairs[i]
                fb, vb = pairs[i + 1]
                if fa <= frame <= fb:
                    if fb <= fa:
                        return float(vb)
                    w = (frame - fa) / max(1.0, (fb - fa))
                    return float(va) * (1.0 - w) + float(vb) * w
            return float(pairs[-1][1])

        zp = _parse_sched(zoom_s)
        ap = _parse_sched(ang_s)
        frames = sorted({f for f, _ in zp} | {f for f, _ in ap})
        kfs = []
        if frames:
            for f in frames:
                kfs.append({"t": f / fps, "zoom": _sample(zp, f) or 1.0, "pan_x": 0.0, "pan_y": 0.0, "rotation_deg": _sample(ap, f)})
        elif duration_s > 0:
            kfs = [{"t": 0.0, "zoom": 1.0, "pan_x": 0.0, "pan_y": 0.0, "rotation_deg": 0.0}]
        cam["keyframes"] = kfs
        timeline["camera"] = cam

    proj.meta["timeline"] = timeline
    return timeline


def _storyboard_variant_for_render(proj: Any, variant_index: int) -> tuple[dict[str, Any], list[dict[str, Any]], float]:
    plan = proj.meta.get("last_plan") if isinstance(getattr(proj, "meta", None), dict) else None
    if not isinstance(plan, dict):
        raise HTTPException(400, "No AI storyboard found. Generate a plan first.")
    variants = plan.get("variants") if isinstance(plan.get("variants"), list) else []
    vi = int(variant_index or 0)
    if not variants or vi < 0 or vi >= len(variants):
        raise HTTPException(400, "Invalid variant_index")
    variant = variants[vi] if isinstance(variants[vi], dict) else {}
    scenes = [scene for scene in list(variant.get("scenes") or []) if isinstance(scene, dict)]
    if not scenes:
        raise HTTPException(400, "Selected storyboard variant has no scenes")
    return variant, scenes, _resolved_project_duration_s(proj, variant, scenes)


def _storyboard_scene_bounds(scene: dict[str, Any], *, index: int, total: int, duration_s: float) -> tuple[float, float]:
    try:
        start_s = max(0.0, float(scene.get("start_s") or 0.0))
        end_s = max(0.0, float(scene.get("end_s") or 0.0))
    except Exception:
        start_s, end_s = 0.0, 0.0
    if end_s > start_s:
        return start_s, end_s
    safe_total = max(1, int(total or 1))
    slice_s = max(0.25, float(duration_s or safe_total) / float(safe_total))
    start_s = float(index) * slice_s
    end_s = float(duration_s) if duration_s > 0 and index == safe_total - 1 else start_s + slice_s
    return max(0.0, start_s), max(start_s + 0.25, end_s)


def _storyboard_analysis_features(proj: Any) -> dict[str, Any]:
    analysis = proj.meta.get("analysis") if isinstance(getattr(proj, "meta", None), dict) else {}
    if not isinstance(analysis, dict) or not analysis:
        raise HTTPException(400, "No audio analysis found. Analyze project audio before rendering an audio-reactive storyboard movie.")
    return analysis


def _storyboard_tempo_bpm(analysis: dict[str, Any]) -> float:
    features = analysis.get("features") if isinstance(analysis.get("features"), dict) else {}
    for raw in (
        features.get("bpm"),
        features.get("tempo_bpm"),
        features.get("tempo"),
        analysis.get("bpm"),
        analysis.get("tempo"),
    ):
        try:
            bpm = float(raw)
        except Exception:
            continue
        if bpm > 0:
            return bpm
    return 0.0


def _storyboard_beat_times(analysis: dict[str, Any], *, duration_s: float, bpm: float) -> list[float]:
    features = analysis.get("features") if isinstance(analysis.get("features"), dict) else {}
    raw = features.get("beat_times") or features.get("beats") or analysis.get("beat_times") or analysis.get("beats") or []
    times: list[float] = []
    for item in list(raw or []):
        try:
            value = float(item)
        except Exception:
            continue
        if value >= 0:
            times.append(value)
    if not times and bpm > 0 and duration_s > 0:
        step = 60.0 / max(1e-6, bpm)
        current = 0.0
        while current <= duration_s + 1e-6 and len(times) < 160:
            times.append(round(current, 4))
            current += step
    return sorted({round(t, 4) for t in times if t <= max(duration_s, 0.0) + 1e-6})[:160]


def _storyboard_energy_at_time(overall: dict[str, Any], t: float, duration_s: float) -> float:
    curve = [max(0.0, min(1.0, float(value))) for value in list(overall.get("energy_curve") or []) if isinstance(value, (int, float))]
    if not curve or duration_s <= 0:
        return max(0.0, min(1.0, float(overall.get("energy") or 0.45)))
    idx = max(0, min(len(curve) - 1, int(round((max(0.0, t) / max(0.001, duration_s)) * (len(curve) - 1)))))
    return curve[idx]


def _dedupe_schedule_pairs(pairs: list[tuple[int, float]]) -> list[tuple[int, float]]:
    by_frame: dict[int, float] = {}
    for frame, value in pairs:
        by_frame[max(0, int(frame))] = float(value)
    return [(frame, by_frame[frame]) for frame in sorted(by_frame.keys())]


def _storyboard_upsert_track(timeline: dict[str, Any], track: dict[str, Any], *, overwrite: bool) -> None:
    tracks = [item for item in list(timeline.get("tracks") or []) if isinstance(item, dict)]
    track_id = str(track.get("id") or "")
    track_type = str(track.get("type") or "").lower()
    index = next(
        (
            i
            for i, item in enumerate(tracks)
            if str(item.get("id") or "") == track_id
            or (track_type and str(item.get("type") or "").lower() == track_type)
        ),
        -1,
    )
    if index >= 0:
        if overwrite or not tracks[index].get("clips"):
            tracks[index] = {**tracks[index], **track}
    else:
        tracks.append(track)
    timeline["tracks"] = tracks


def _ensure_storyboard_movie_timeline(
    proj: Any,
    *,
    variant_index: int,
    overwrite: bool,
    fps: int = 24,
) -> dict[str, Any]:
    analysis = _storyboard_analysis_features(proj)
    variant, scenes, duration_s = _storyboard_variant_for_render(proj, variant_index)
    timeline = proj.meta.get("timeline") if isinstance(proj.meta.get("timeline"), dict) else {}
    timeline = deepcopy(timeline)

    overall = _infer_reactivity_metrics(analysis)
    if not float(overall.get("duration_s") or 0.0):
        overall["duration_s"] = duration_s
    energy_curve = [max(0.0, min(1.0, float(value))) for value in list(overall.get("energy_curve") or []) if isinstance(value, (int, float))]
    bpm = _storyboard_tempo_bpm(analysis)
    beat_times = _storyboard_beat_times(analysis, duration_s=duration_s, bpm=bpm)

    prompt_clips: list[dict[str, Any]] = []
    camera_keyframes: list[dict[str, Any]] = []
    zoom_pairs: list[tuple[int, float]] = []
    angle_pairs: list[tuple[int, float]] = []
    tx_pairs: list[tuple[int, float]] = []
    ty_pairs: list[tuple[int, float]] = []
    strength_pairs: list[tuple[int, float]] = []
    cfg_pairs: list[tuple[int, float]] = []
    steps_pairs: list[tuple[int, float]] = []
    denoise_pairs: list[tuple[int, float]] = []
    prev_zoom = 1.0
    prev_pan_x = 0.0
    prev_pan_y = 0.0
    prev_rot = 0.0

    for index, scene in enumerate(scenes):
        start_s, end_s = _storyboard_scene_bounds(scene, index=index, total=len(scenes), duration_s=duration_s)
        start_frame = max(0, int(round(start_s * fps)))
        end_frame = max(start_frame + 1, int(round(end_s * fps)))
        metrics = _scene_metrics_from_curve(index, len(scenes), scene, overall, duration_s, energy_curve)
        params = _compute_reactive_params(metrics, "cinematic", 1.0)

        zoom_end = max(0.75, min(1.45, float(params.get("zoom") or prev_zoom)))
        pan_x_end = max(-80.0, min(80.0, float(params.get("translation_x") or 0.0)))
        pan_y_end = max(-60.0, min(60.0, float(params.get("translation_y") or 0.0)))
        rot_end = max(-18.0, min(18.0, float(params.get("rotation_z") or 0.0) + float(params.get("rotation_y") or 0.0) * 0.12))
        strength = max(0.15, min(0.92, float(params.get("strength") or 0.45)))
        cfg = max(1.0, min(20.0, float(params.get("cfg_scale") or 7.0)))
        steps = max(6.0, min(36.0, 10.0 + float(metrics.get("energy") or 0.45) * 18.0))
        denoise = max(0.15, min(0.9, 0.24 + float(metrics.get("energy") or 0.45) * 0.46))

        prompt_clips.append(
            {
                "id": f"storyboard_prompt_{index}",
                "start_s": start_s,
                "end_s": end_s,
                "data": {
                    "prompt": str(scene.get("prompt") or scene.get("name") or f"Scene {index + 1}").strip(),
                    "negative_prompt": str(scene.get("negative_prompt") or "blurry, low quality, watermark, text, logo").strip(),
                    "scene_id": str(scene.get("id") or index + 1),
                },
            }
        )
        camera_keyframes.extend(
            [
                {"t": start_s, "zoom": prev_zoom, "pan_x": prev_pan_x, "pan_y": prev_pan_y, "rotation_deg": prev_rot},
                {"t": end_s, "zoom": zoom_end, "pan_x": pan_x_end, "pan_y": pan_y_end, "rotation_deg": rot_end},
            ]
        )
        zoom_pairs.extend([(start_frame, prev_zoom), (end_frame, zoom_end)])
        angle_pairs.extend([(start_frame, prev_rot), (end_frame, rot_end)])
        tx_pairs.extend([(start_frame, prev_pan_x), (end_frame, pan_x_end)])
        ty_pairs.extend([(start_frame, prev_pan_y), (end_frame, pan_y_end)])
        strength_pairs.extend([(start_frame, strength), (end_frame, strength)])
        cfg_pairs.extend([(start_frame, cfg), (end_frame, cfg)])
        steps_pairs.extend([(start_frame, steps), (end_frame, steps)])
        denoise_pairs.extend([(start_frame, denoise), (end_frame, denoise)])

        prev_zoom = zoom_end
        prev_pan_x = pan_x_end
        prev_pan_y = pan_y_end
        prev_rot = rot_end

    for beat_index, beat_s in enumerate(beat_times):
        frame = max(0, int(round(beat_s * fps)))
        energy = _storyboard_energy_at_time(overall, beat_s, duration_s)
        pulse = 1.0 + min(0.12, 0.025 + energy * 0.075)
        direction = -1.0 if beat_index % 2 else 1.0
        zoom_pairs.append((frame, pulse))
        strength_pairs.append((frame, max(0.2, min(0.95, 0.28 + energy * 0.56))))
        tx_pairs.append((frame, direction * (2.0 + energy * 10.0)))

    schedules = {
        "zoom": _format_schedule_pairs(_dedupe_schedule_pairs(zoom_pairs)),
        "angle": _format_schedule_pairs(_dedupe_schedule_pairs(angle_pairs)),
        "rotation_schedule": _format_schedule_pairs(_dedupe_schedule_pairs(angle_pairs)),
        "rotation_z_schedule": _format_schedule_pairs(_dedupe_schedule_pairs(angle_pairs)),
        "translation_x": _format_schedule_pairs(_dedupe_schedule_pairs(tx_pairs)),
        "translation_y": _format_schedule_pairs(_dedupe_schedule_pairs(ty_pairs)),
        "pan_x_schedule": _format_schedule_pairs(_dedupe_schedule_pairs(tx_pairs)),
        "pan_y_schedule": _format_schedule_pairs(_dedupe_schedule_pairs(ty_pairs)),
        "strength_schedule": _format_schedule_pairs(_dedupe_schedule_pairs(strength_pairs)),
        "cfg_scale_schedule": _format_schedule_pairs(_dedupe_schedule_pairs(cfg_pairs)),
        "steps_schedule": _format_schedule_pairs(_dedupe_schedule_pairs(steps_pairs)),
        "denoise_schedule": _format_schedule_pairs(_dedupe_schedule_pairs(denoise_pairs)),
    }

    _storyboard_upsert_track(
        timeline,
        {
            "id": "edmg_prompt",
            "name": "AI Storyboard Prompts",
            "type": "prompt",
            "clips": prompt_clips,
        },
        overwrite=overwrite,
    )
    _storyboard_upsert_track(
        timeline,
        {
            "id": "edmg_motion",
            "name": "Audio-Reactive Storyboard Motion",
            "type": "motion",
            "clips": [
                {
                    "id": "edmg_storyboard_movie_motion",
                    "start_s": 0.0,
                    "end_s": duration_s,
                    "data": {
                        "motion_schedules": schedules,
                        **schedules,
                        "reactive_source": "storyboard_movie",
                        "bpm": bpm,
                        "beat_count": len(beat_times),
                        "scene_count": len(scenes),
                    },
                }
            ],
        },
        overwrite=overwrite,
    )

    existing_camera = timeline.get("camera") if isinstance(timeline.get("camera"), dict) else {}
    existing_keyframes = existing_camera.get("keyframes") if isinstance(existing_camera.get("keyframes"), list) else []
    if overwrite or not existing_keyframes:
        timeline["camera"] = {**existing_camera, "keyframes": _dedupe_camera_keyframes(camera_keyframes)}
    else:
        timeline["camera"] = {
            **existing_camera,
            "keyframes": _dedupe_camera_keyframes([*existing_keyframes, *camera_keyframes]),
        }

    render = timeline.get("render") if isinstance(timeline.get("render"), dict) else {}
    timeline["render"] = {**render, "fps_output": fps}
    timeline["fps_output"] = fps
    timeline["duration_s"] = duration_s
    timeline["storyboard_movie"] = {
        "variant_index": int(variant_index or 0),
        "scene_count": len(scenes),
        "duration_s": duration_s,
        "bpm": bpm,
        "beat_count": len(beat_times),
        "audio_reactive": True,
        "source": "ai_storyboard",
        "variant_name": str(variant.get("name") or ""),
    }
    proj.meta["timeline"] = timeline
    return timeline


def _storyboard_movie_payload_from_request(
    proj: Any,
    req: StoryboardMovieRenderRequest,
    *,
    duration_s: float,
) -> dict[str, Any]:
    preset = str(req.preset or "balanced").lower()
    requested_tier = "draft" if preset == "fast" else ("quality" if preset in {"quality", "ultra"} else "auto")
    hw = _hardware_profile()
    provider_status = _render_provider_status(hw)
    tier_plan = _build_internal_render_plan(hw, requested_tier=requested_tier, duration_s=duration_s)
    defaults = dict(tier_plan.get("defaults") or {})
    device_preference = str(tier_plan.get("device_preference") or "auto")
    if device_preference == "directml" and not bool((provider_status.get("directml") or {}).get("enabled", True)):
        device_preference = "cpu"

    payload: dict[str, Any] = {
        "variant_index": int(req.variant_index or 0),
        "fps_output": int(req.fps_output or defaults.get("fps_output") or 24),
        "fps_render": int(req.fps_render or defaults.get("fps_render") or 2),
        "width": int(req.width or defaults.get("width") or 768),
        "height": int(req.height or defaults.get("height") or 432),
        "steps": int(defaults.get("steps") or 15),
        "cfg": float(defaults.get("cfg") or 7.0),
        "keyframe_interval_s": float(req.keyframe_interval_s or defaults.get("keyframe_interval_s") or 5.0),
        "interpolation_engine": str(req.interpolation_engine or defaults.get("interpolation_engine") or "auto"),
        "model_id": str(req.model_id or "auto"),
        "render_mode": str(req.render_mode or "auto"),
        "render_tier": str(tier_plan.get("applied_tier") or requested_tier),
        "device_preference": device_preference,
        "temporal_mode": str(req.temporal_mode or defaults.get("temporal_mode") or "keyframes"),
        "temporal_strength": float(req.temporal_strength or defaults.get("temporal_strength") or 0.35),
        "temporal_steps": int(defaults.get("temporal_steps") or 12),
        "refine_every_n_frames": int(defaults.get("refine_every_n_frames") or 1),
        "anchor_strength": float(defaults.get("anchor_strength") or 0.20),
        "prompt_blend": bool(defaults.get("prompt_blend", True)),
        "allow_hosted_fallback": bool(req.allow_hosted_fallback),
        "allow_proxy_fallback": bool(req.allow_proxy_fallback),
        "resume_existing_frames": bool(req.resume_existing_frames),
        "storyboard_movie": {
            "source": "ai_storyboard",
            "project_id": str(proj.id),
            "project_name": str(getattr(proj, "name", "") or ""),
            "preset": preset,
            "apply_storyboard": bool(req.apply_storyboard),
            "overwrite_timeline": bool(req.overwrite_timeline),
        },
    }
    return payload


@app.post("/v1/projects/{project_id}/plan")
def generate_plan(project_id: str, req: PlanRequest, mode: str = "auto"):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    analysis = proj.meta.get("analysis") or {}
    feats = (analysis.get("features") or {})
    transcript = _analysis_transcript_text(analysis)

    payload = {
        "title": req.title or proj.name,
        "user_notes": req.user_notes,
        "duration_s": feats.get("duration_s") or feats.get("duration"),
        "bpm": feats.get("bpm") or feats.get("tempo_bpm") or feats.get("tempo"),
        "lyrics": transcript,
        "tags": (analysis.get("tags") or []),
        "style_prefs": req.style_prefs,
        "num_variants": req.num_variants,
        "max_scenes": req.max_scenes,
    }

    mode_norm = str(mode or "auto").lower().strip()
    if mode_norm not in ("auto", "ai", "local", "edmg_core"):
        mode_norm = "auto"

    plan = None
    if mode_norm in ("ai", "auto"):
        try:
            plan = ai.plan(payload)
            if isinstance(plan, dict):
                plan.setdefault("source", "ai")
        except Exception as e:
            if mode_norm == "ai":
                # strict AI mode
                raise UserFacingError(
                    message="The configured planning/transcription provider is not available.",
                    hint=(
                        "Fix: If you're using Ollama, make sure it is installed and running (Ollama app or `ollama serve`), "
                        "and that the model is pulled (e.g., `ollama pull qwen3:8b`). "
                        "If you want a remote AI, set EDMG_AI_MODE=http and EDMG_AI_BASE_URL to the running AI service."
                    ),
                    code="AI_UNAVAILABLE",
                    status_code=502,
                )
            plan = None

    if plan is None:
        # deterministic local fallback (no LLM)
        plan = _local_plan_from_project(
            proj,
            title=req.title or proj.name,
            style_prefs=req.style_prefs or "",
            num_variants=req.num_variants,
            max_scenes=req.max_scenes,
        )

    if isinstance(plan, dict):
        plan = _normalize_plan_payload(
            plan,
            requested_variants=req.num_variants,
            requested_max_scenes=req.max_scenes,
            duration_s_hint=_analysis_duration_s(analysis),
        )
        plan = _enrich_normalized_plan(plan, analysis if isinstance(analysis, dict) else {})

    proj.meta["last_plan"] = plan
    store.save(proj)
    return plan


@app.post("/v1/projects/{project_id}/timeline/apply_plan")
def apply_plan_to_timeline(project_id: str, req: ApplyPlanRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    timeline = _apply_plan_to_project_timeline(
        proj,
        variant_index=int(req.variant_index or 0),
        overwrite=bool(req.overwrite),
    )
    store.save(proj)
    return {"ok": True, "timeline": timeline, "variant_index": int(req.variant_index or 0)}


@app.post("/v1/projects/{project_id}/plan/variant")
def update_plan_variant(project_id: str, req: StoryboardVariantUpdateRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    plan = proj.meta.get("last_plan")
    if not isinstance(plan, dict):
        raise HTTPException(400, "No plan. Generate a plan first.")

    variants = plan.get("variants") if isinstance(plan.get("variants"), list) else []
    variant_index = int(req.variant_index or 0)
    if variant_index < 0 or variant_index >= len(variants):
        raise HTTPException(400, "Invalid variant_index")

    variant = variants[variant_index] if isinstance(variants[variant_index], dict) else {}
    duration_hint = _coerce_scene_time(
        variant.get("duration_s") if isinstance(variant, dict) else None,
        _analysis_duration_s(proj.meta.get("analysis") or {}),
    )

    updated_variant = dict(variant)
    updated_variant["scenes"] = _normalize_plan_scene_list(
        req.scenes,
        duration_s=duration_hint,
        max_scenes=max(1, len(req.scenes or [])),
    )
    updated_variant["duration_s"] = max(
        duration_hint,
        max(
            (_coerce_scene_time(scene.get("end_s"), 0.0) for scene in updated_variant["scenes"]),
            default=duration_hint or 0.0,
        ),
    )
    variants[variant_index] = updated_variant

    normalized_plan = _normalize_plan_payload(
        {**plan, "variants": variants},
        requested_variants=max(1, len(variants)),
        requested_max_scenes=max([len((item or {}).get("scenes") or []) for item in variants] or [1]),
        duration_s_hint=_analysis_duration_s(proj.meta.get("analysis") or {}) or plan.get("duration_s"),
    )
    proj.meta["last_plan"] = normalized_plan

    planner_lab = proj.meta.get("last_planner_lab")
    if isinstance(planner_lab, dict):
        planner_plan = planner_lab.get("plan")
        if isinstance(planner_plan, dict):
            planner_variants = planner_plan.get("variants") if isinstance(planner_plan.get("variants"), list) else []
            if 0 <= variant_index < len(planner_variants) and isinstance(planner_variants[variant_index], dict):
                planner_variant = dict(planner_variants[variant_index])
                planner_variant["scenes"] = deepcopy(updated_variant["scenes"])
                planner_variants[variant_index] = planner_variant
                planner_lab["plan"] = {**planner_plan, "variants": planner_variants}

    store.save(proj)
    return {"ok": True, "plan": normalized_plan, "variant_index": variant_index}


@app.post("/v1/projects/{project_id}/planner_lab/import")
def import_planner_lab(project_id: str, req: PlannerLabImportRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    imported_analysis = planner_lab_to_project_analysis(req.analysis)
    if imported_analysis:
        proj.meta["analysis"] = _merge_imported_analysis(proj.meta.get("analysis"), imported_analysis)

    imported_plan = planner_lab_to_canonical_plan(req.analysis, req.plan, req.settings)
    scene_counts = [
        len(variant.get("scenes") or [])
        for variant in list(imported_plan.get("variants") or [])
        if isinstance(variant, dict)
    ]
    normalized_plan = _normalize_plan_payload(
        imported_plan,
        requested_variants=max(1, len(imported_plan.get("variants") or [])),
        requested_max_scenes=max(scene_counts or [1]),
        duration_s_hint=_analysis_duration_s(proj.meta.get("analysis") or imported_analysis),
    )
    proj.meta["last_plan"] = normalized_plan
    proj.meta["last_planner_lab"] = {
        "analysis": deepcopy(req.analysis),
        "plan": deepcopy(req.plan),
        "settings": deepcopy(req.settings),
        "imported_at": time.time(),
    }
    visual_dna = _load_project_visual_dna(proj)
    visual_dna = ingest_visual_dna_planner_payload(
        visual_dna,
        analysis=deepcopy(req.analysis) if isinstance(req.analysis, dict) else {},
        plan=deepcopy(req.plan) if isinstance(req.plan, dict) else {},
        settings=deepcopy(req.settings) if isinstance(req.settings, dict) else {},
    )
    saved_dna = _save_project_visual_dna(proj, visual_dna)

    timeline = None
    if req.apply_timeline:
        timeline = _apply_plan_to_project_timeline(
            proj,
            variant_index=0,
            overwrite=bool(req.overwrite_timeline),
        )

    store.save(proj)
    return {
        "ok": True,
        "plan": normalized_plan,
        "timeline": timeline,
        "visual_dna": saved_dna.model_dump(mode="json"),
        "visual_dna_hints": build_visual_dna_prompt_hints(saved_dna),
    }


@app.post("/v1/projects/{project_id}/reactive_lab/apply")
def apply_reactive_lab(project_id: str, req: ReactiveLabApplyRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    payload = {
        "metadata": deepcopy(req.metadata),
        "keyframes": deepcopy(req.keyframes),
        "beat_markers": deepcopy(req.beat_markers),
        "cue_events": deepcopy(req.cue_events),
        "sections": deepcopy(req.sections),
        "repair_suggestions": deepcopy(req.repair_suggestions),
        "schedules": deepcopy(req.schedules),
        "handoff_manifest": deepcopy(req.handoff_manifest),
    }
    timeline = merge_reactive_lab_into_timeline(
        proj.meta.get("timeline"),
        payload,
        overwrite_motion_track=bool(req.overwrite_motion_track),
        overwrite_camera=bool(req.overwrite_camera),
    )
    proj.meta["timeline"] = timeline
    proj.meta["last_reactive_lab"] = {**payload, "applied_at": time.time()}
    visual_dna = _load_project_visual_dna(proj)
    visual_dna = ingest_visual_dna_reactive_payload(
        visual_dna,
        payload=payload,
    )
    saved_dna = _save_project_visual_dna(proj, visual_dna)
    store.save(proj)
    return {
        "ok": True,
        "timeline": timeline,
        "visual_dna": saved_dna.model_dump(mode="json"),
        "visual_dna_hints": build_visual_dna_prompt_hints(saved_dna),
    }


@app.get("/v1/jobs")
def list_jobs():
    return {"jobs": [j.__dict__ for j in jobs.list_all()]}

@app.get("/v1/projects/{project_id}/jobs")
def list_project_jobs(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    return {"jobs": [j.__dict__ for j in jobs.list_for_project(project_id)]}


@app.get("/v1/projects/{project_id}/jobs/{job_id}")
def get_project_job(project_id: str, job_id: str, tail_lines: int = 80):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    job = jobs.get(project_id, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _job_detail_payload(project_id, job, tail_lines=tail_lines)

@app.post("/v1/projects/{project_id}/jobs/{job_id}/cancel")
def cancel_job(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    job = jobs.cancel(project_id, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"ok": True, "job": job.__dict__}

@app.post("/v1/projects/{project_id}/jobs/{job_id}/retry")
def retry_job(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    job = jobs.retry(project_id, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"ok": True, "job": job.__dict__}


@app.post("/v1/projects/{project_id}/jobs/{job_id}/resume_from_checkpoint")
def resume_internal_job(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    source_job = jobs.get(project_id, job_id)
    if not source_job:
        raise HTTPException(404, "Job not found")
    if source_job.type != "internal_video":
        raise HTTPException(400, "Resume from checkpoint is only available for internal render jobs")
    if source_job.status in ("queued", "running"):
        raise HTTPException(409, "Job is still active. Cancel it before resuming from checkpoint.")
    return _enqueue_internal_job_from_source(project_id, source_job, resume_existing_frames=True, queue_action="resume_from_checkpoint")


@app.post("/v1/projects/{project_id}/jobs/{job_id}/restart_clean")
def restart_internal_job_clean(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    source_job = jobs.get(project_id, job_id)
    if not source_job:
        raise HTTPException(404, "Job not found")
    if source_job.type != "internal_video":
        raise HTTPException(400, "Clean restart is only available for internal render jobs")
    if source_job.status in ("queued", "running"):
        raise HTTPException(409, "Job is still active. Cancel it before starting a clean restart.")
    return _enqueue_internal_job_from_source(project_id, source_job, resume_existing_frames=False, queue_action="restart_clean")


@app.post("/v1/projects/{project_id}/jobs/{job_id}/clear_cached_frames")
def clear_project_job_cached_frames(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    job = jobs.get(project_id, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _mutate_internal_job_artifacts(project_id, job, clear_cached_frames=True, drop_checkpoint=False)


@app.post("/v1/projects/{project_id}/jobs/{job_id}/drop_checkpoint")
def drop_project_job_checkpoint(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    job = jobs.get(project_id, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _mutate_internal_job_artifacts(project_id, job, clear_cached_frames=False, drop_checkpoint=True)


@app.get("/v1/projects/{project_id}/jobs/{job_id}/log")
def get_job_log(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    lp = jobs.log_path(project_id, job_id)
    if not lp.exists():
        return {"ok": True, "log": ""}
    return {"ok": True, "log": lp.read_text(encoding="utf-8", errors="ignore")}

@app.post("/v1/jobs/tick")
def tick_worker():
    """Manual single-step worker tick (useful for debugging)."""
    job = jobs.claim_next_queued()
    if not job:
        return {"ok": True, "note": "no queued jobs"}
    _execute_job(job)
    latest = jobs.get(job.project_id, job.id) or job
    return {"ok": True, "job": latest.__dict__}

def _run_assemble_variant(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = AssembleVideoRequest(**(payload or {}))
    return assemble_video(project_id, req)

def _execute_job(job):
    jobs.append_log(job.project_id, job.id, f"Started job type={job.type}")

    try:
        if job.type == "comfyui_scene":
            res = _run_comfyui_scene(job.project_id, job.id, job.payload)
            job.result = res
            job.status = "succeeded"
        elif job.type == "internal_still_scene":
            res = _run_internal_still_scene(job.project_id, job.id, job.payload)
            job.result = res
            job.status = "succeeded"
        elif job.type == "comfyui_motion_scene":
            res = _run_comfyui_motion_scene(job.project_id, job.id, job.payload)
            job.result = res
            job.status = "succeeded"
        elif job.type == "assemble_variant":
            res = _run_assemble_variant(job.project_id, job.payload)
            job.result = res
            job.status = "succeeded"
        elif job.type == "internal_video":
            res = _run_internal_video(job.project_id, job.id, job.payload)
            latest = jobs.get(job.project_id, job.id)
            if latest and latest.status == "canceled":
                job.status = "canceled"
                job.result = latest.result
            else:
                job.result = res
                job.status = "succeeded"
        else:
            job.status = "failed"
            job.error = f"Unknown job type: {job.type}"
    except JobCanceled as e:
        job.status = "canceled"
        job.error = None
        latest = jobs.get(job.project_id, job.id)
        if latest and latest.result:
            job.result = latest.result
        jobs.append_log(job.project_id, job.id, str(e) or "Job canceled during execution")
    except Exception as e:
        latest = jobs.get(job.project_id, job.id)
        if latest and latest.status == "canceled":
            job.status = "canceled"
            job.error = None
        else:
            job.status = "failed"
            hint = hint_from_exception(e)
            job.error = f"{e}" + (f"\nFix: {hint}" if hint else "")

    jobs.append_log(job.project_id, job.id, f"Finished status={job.status}")
    if job.error:
        jobs.append_log(job.project_id, job.id, f"Error: {job.error}")

    latest = jobs.get(job.project_id, job.id)
    if latest and isinstance(latest.progress, dict):
        job.progress = latest.progress
    jobs.save(job)


# Initialize always-on worker manager now that _execute_job exists
worker = WorkerManager(
    jobs=jobs,
    run_job=_execute_job,
    concurrency=settings.worker_concurrency,
    poll_interval_s=settings.worker_poll_interval_s,
)


def _prepare_still_scene_assets(project_id: str, payload: dict[str, Any], workflow_family: str) -> dict[str, Any]:
    source_asset = str(payload.get("source_asset") or payload.get("reference_asset") or "").strip()
    mask_asset = str(payload.get("inpaint_mask") or "").strip()
    outpaint = _normalize_outpaint(payload.get("outpaint"))

    source_path: Path | None = None
    mask_path: Path | None = None
    mask_source: str | None = None

    if workflow_family == "img2img":
        source_path = _resolve_project_reference_path(project_id, source_asset)
        if source_path is None:
            raise UserFacingError(
                "No source image selected for img2img",
                hint="Upload or choose a project source image before running img2img.",
                code="IMG2IMG_SOURCE_MISSING",
                status_code=400,
            )
    elif workflow_family == "inpaint":
        source_path = _resolve_project_reference_path(project_id, source_asset)
        mask_path = _resolve_project_mask_path(project_id, mask_asset)
        if source_path is None or mask_path is None:
            raise UserFacingError(
                "Source image or mask is missing",
                hint="Choose both a source image and a mask before running an inpaint render.",
                code="INPAINT_ASSETS_MISSING",
                status_code=400,
            )
        mask_source = "explicit_mask"
    elif workflow_family == "outpaint":
        prepared = _prepare_outpaint_assets(
            project_id,
            source_asset=source_asset,
            outpaint=outpaint,
            mask_asset=mask_asset or None,
        )
        source_path = prepared["source_path"]
        mask_path = prepared["mask_path"]
        mask_source = prepared.get("mask_source")
        outpaint = prepared.get("outpaint")

    width = int(payload.get("width") or 0)
    height = int(payload.get("height") or 0)
    if workflow_family == "outpaint" and source_path is not None and Image is not None:
        with Image.open(source_path) as generated_source:
            width, height = generated_source.size

    return {
        "source_path": source_path,
        "mask_path": mask_path,
        "mask_source": mask_source,
        "outpaint": outpaint,
        "width": width,
        "height": height,
    }


def _prepare_internal_controlnet_units(project_id: str, units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for unit in units:
        model_ref = str(unit.get("model") or unit.get("controlnet_name") or "").strip()
        if not model_ref:
            raise UserFacingError(
                "ControlNet model is missing",
                hint="Pick an internal ControlNet model before running the render.",
                code="CONTROLNET_MODEL_MISSING",
                status_code=400,
            )
        asset = models.resolve_internal_asset(model_ref, folder="controlnet", allowed_kinds={"controlnet"})
        ref_path = _resolve_project_reference_path(project_id, str(unit.get("reference_asset") or ""))
        if ref_path is None:
            raise UserFacingError(
                "Reference image not found",
                hint="Upload or choose a valid project reference image before running the ControlNet render.",
                code="REFERENCE_IMAGE_NOT_FOUND",
                status_code=400,
            )
        conditioned = _prepare_condition_image(project_id, ref_path, str(unit.get("conditioning_mode") or "raw"))
        prepared.append(
            {
                **unit,
                "path": str(asset.get("path") or ""),
                "family": asset.get("family"),
                "reference_path": str(conditioned),
            }
        )
    return prepared


def _run_internal_still_scene(project_id: str, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("prompt") or "")
    workflow_family = str(payload.get("workflow_family") or "txt2img")
    model_id = str(payload.get("model_id") or "")
    raw_model_path = str(payload.get("model_path") or "").strip()
    model_path = Path(raw_model_path) if raw_model_path else None
    if model_path is None or not model_path.exists():
        installed = models.installed_path(model_id)
        if installed is None:
            raise UserFacingError(
                "Internal still model is not installed",
                hint="Install the selected internal diffusers model in Models, then retry.",
                code="MODEL_NOT_INSTALLED",
                status_code=400,
            )
        model_path = installed

    out_path = Path(str(payload.get("out_path") or ""))
    if out_path and out_path.exists():
        metadata_path = _output_metadata_path(out_path)
        metadata = None
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                metadata = None
        return {
            "cached": True,
            "saved": str(out_path),
            "metadata_path": str(metadata_path),
            "metadata": metadata,
        }

    prepared_assets = _prepare_still_scene_assets(project_id, payload, workflow_family)
    actual_width = int(prepared_assets.get("width") or payload.get("width") or 1024)
    actual_height = int(prepared_assets.get("height") or payload.get("height") or 576)
    controlnet_units = _prepare_internal_controlnet_units(project_id, list(payload.get("controlnet_units") or []))
    resolved_refiner = dict(payload.get("refiner")) if isinstance(payload.get("refiner"), dict) else None
    if resolved_refiner is not None:
        resolved_refiner["base_path"] = str(model_path)
        refiner_model = str(resolved_refiner.get("model") or "").strip()
        if refiner_model:
            refiner_asset = models.resolve_internal_asset(refiner_model, folder="diffusers", allowed_kinds={"diffusers"})
            resolved_refiner["path"] = str(refiner_asset.get("path") or "")
            resolved_refiner["family"] = refiner_asset.get("family")
    settings_obj = InternalVideoSettings(
        width=actual_width,
        height=actual_height,
        steps=int(payload.get("steps") or 28),
        cfg=float(payload.get("cfg") or 7.0),
        sampler=str(payload.get("sampler") or "euler"),
        seed=(int(payload["seed"]) if payload.get("seed") is not None else None),
        negative_prompt=str(payload.get("negative_prompt") or ""),
        model_id=model_id or str(payload.get("family") or "internal_still"),
        loras=tuple(_normalize_render_loras(payload.get("loras"))),
        vae=str(payload.get("vae") or "").strip() or None,
        hires_fix=dict(payload.get("hires_fix")) if isinstance(payload.get("hires_fix"), dict) else None,
        refiner=resolved_refiner,
        upscaler=str(payload.get("upscaler") or "").strip() or None,
        device_preference="auto",
    )

    jobs.update_progress(project_id, job_id, stage="rendering", current=0, total=1, message=f"Running internal {workflow_family} render")
    if settings_obj.loras:
        lora_log = ", ".join(
            f"{str(item.get('filename') or item.get('name') or 'lora')}@{float(item.get('weight', 1.0)):.2f}"
            for item in settings_obj.loras
        )
        jobs.append_log(project_id, job_id, f"LoRAs: {lora_log}")
    if settings_obj.hires_fix and settings_obj.hires_fix.get("enabled", True):
        jobs.append_log(
            project_id,
            job_id,
            f"Hires fix: scale {float(settings_obj.hires_fix.get('scale', 1.5)):.2f} • denoise {float(settings_obj.hires_fix.get('denoise', 0.35)):.2f}",
        )
    if resolved_refiner:
        jobs.append_log(
            project_id,
            job_id,
            f"Refiner pass: {str(resolved_refiner.get('model') or 'base-model')} • switch {float(resolved_refiner.get('switch_at', 0.8)):.2f}",
        )

    result = render_internal_still_image(
        model_dir=model_path,
        settings=settings_obj,
        workflow_family=workflow_family,
        prompt=prompt,
        source_image_path=prepared_assets.get("source_path"),
        mask_image_path=prepared_assets.get("mask_path"),
        controlnet_units=controlnet_units,
        denoise_strength=float(payload.get("denoise_strength") or 0.75),
        log_fn=lambda message: jobs.append_log(project_id, job_id, str(message)),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    result["image"].save(out_path)
    final_width, final_height = result["image"].size

    metadata = _build_generation_metadata(
        project_id=project_id,
        job_id=job_id,
        output_path=out_path,
        payload={**payload, "width": final_width, "height": final_height, "refiner": resolved_refiner},
        workflow_family=workflow_family,
        checkpoint=str(model_path.name),
        loras=list(settings_obj.loras),
        controlnet_units=controlnet_units,
        vae_name=settings_obj.vae,
        backend="internal_diffusers",
        engine="internal",
        model_family=str(payload.get("family") or ""),
        resolved_model_asset=str(model_path),
        mask_source=str(prepared_assets.get("mask_source") or ""),
        outpaint=prepared_assets.get("outpaint"),
        device=str(result.get("device") or "cpu"),
    )
    metadata_path = _write_generation_metadata(out_path, metadata)
    jobs.update_progress(project_id, job_id, stage="complete", current=1, total=1, message=f"Saved {out_path.name}")
    return {
        "saved": str(out_path),
        "metadata_path": str(metadata_path),
        "metadata": metadata,
        "device": result.get("device"),
        "requested_device": result.get("requested_device"),
    }

def _run_comfyui_scene(project_id: str, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    prompt = payload["prompt"]
    negative_prompt = payload["negative_prompt"]
    seed = int(payload["seed"])
    width = int(payload["width"])
    height = int(payload["height"])
    steps = int(payload["steps"])
    cfg = float(payload["cfg"])
    sampler = str(payload["sampler"])
    scene_index = int(payload["scene_index"])
    variant_index = int(payload["variant_index"])

    out_path = Path(payload.get("out_path") or "")
    if out_path and out_path.exists():
        metadata_path = _output_metadata_path(out_path)
        metadata = None
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                metadata = None
        return {
            "cached": True,
            "saved": str(out_path),
            "metadata_path": str(metadata_path) if metadata_path else None,
            "metadata": metadata,
        }

    raw_controlnet_units = payload.get("controlnet_units") if isinstance(payload.get("controlnet_units"), list) else []
    selection = _resolve_comfy_still_selection(
        model_id=str(payload.get("model_id") or "") or None,
        checkpoint=str(payload.get("checkpoint") or "") or None,
        workflow_family=str(payload.get("workflow_family") or "auto") or None,
        controlnet_model=str(payload.get("controlnet_model") or "") or None,
        reference_asset=str(payload.get("reference_asset") or "") or None,
        conditioning_mode=str(payload.get("conditioning_mode") or "raw") or None,
        controlnet_units=raw_controlnet_units,
    )
    checkpoint = selection["checkpoint"]
    workflow_family = str(selection.get("workflow_family") or "txt2img")
    controlnet_name = str(payload.get("controlnet_name") or selection.get("controlnet_name") or "").strip()
    conditioning_mode = str(selection.get("conditioning_mode") or "raw")
    resolved_loras = _normalize_render_loras(payload.get("loras"))
    vae_name = _resolve_optional_comfy_asset_name(payload.get("vae"), folder="vae", allowed_kinds={"vae"})
    hires_fix = dict(payload.get("hires_fix")) if isinstance(payload.get("hires_fix"), dict) else None
    upscaler = str(payload.get("upscaler") or "").strip() or None
    resolved_refiner = dict(payload.get("refiner")) if isinstance(payload.get("refiner"), dict) else None
    if resolved_refiner is not None:
        refiner_model = str(resolved_refiner.get("model") or "").strip()
        if refiner_model:
            resolved_refiner["checkpoint"] = _resolve_optional_comfy_asset_name(
                refiner_model,
                folder="checkpoints",
                allowed_kinds={"checkpoint"},
            )
    metadata_controlnet_units: list[dict[str, Any]] = []
    prepared_assets = _prepare_still_scene_assets(project_id, payload, workflow_family)
    actual_width = int(prepared_assets.get("width") or width)
    actual_height = int(prepared_assets.get("height") or height)

    req = {"checkpoint": checkpoint, "est_steps": steps, "est_frames": 1}
    if workflow_family == "controlnet":
        req["node_classes"] = ["LoadImage", "ControlNetLoader", "ControlNetApplyAdvanced"]
    elif workflow_family == "img2img":
        req["node_classes"] = ["LoadImage", "VAEEncode"]
    elif workflow_family in {"inpaint", "outpaint"}:
        req["node_classes"] = ["LoadImage", "LoadImageMask", "VAEEncodeForInpaint"]
    try:
        node_url = comfy_pool.acquire(req)
    except Exception as e:
        raise UserFacingError(
            message="No available ComfyUI node could run this job.",
            hint=hint_from_exception(e) or "Check ComfyUI is running and not saturated, then retry.",
            code="COMFYUI_NO_NODE",
            status_code=502,
        )
    jobs.append_log(project_id, job_id, f"Using ComfyUI node: {node_url}".rstrip())
    try:
        if workflow_family == "controlnet":
            controlnet_units = _normalize_controlnet_units(raw_controlnet_units)
            if not controlnet_units and controlnet_name and payload.get("reference_asset"):
                controlnet_units = _normalize_controlnet_units(
                    [
                        {
                            "model": str(payload.get("controlnet_model") or controlnet_name),
                            "reference_asset": str(payload.get("reference_asset") or ""),
                            "conditioning_mode": conditioning_mode,
                            "strength": float(payload.get("controlnet_strength") or 0.8),
                        }
                    ]
                )
            prepared_units = []
            for unit in controlnet_units:
                prepared_units.append(
                    {
                        **unit,
                        "reference_image": _prepare_comfy_reference_image(
                            project_id,
                            node_url,
                            str(unit.get("reference_asset") or ""),
                            str(unit.get("conditioning_mode") or "raw"),
                        ),
                    }
                )
            metadata_controlnet_units = [dict(unit) for unit in prepared_units]
            wf = comfy.controlnet_workflow(
                checkpoint=checkpoint,
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                width=actual_width,
                height=actual_height,
                steps=steps,
                cfg=cfg,
                sampler=sampler,
                controlnet_name=controlnet_name,
                reference_image=str(prepared_units[0].get("reference_image") or "reference.png") if prepared_units else "reference.png",
                controlnet_strength=float(payload.get("controlnet_strength") or 0.8),
                filename_prefix=f"edmg_cn_v{variant_index:02d}_scene{scene_index:03d}_{job_id[:6]}",
                loras=resolved_loras,
                vae_name=vae_name,
                controlnet_units=prepared_units,
                hires_fix=hires_fix,
                refiner=resolved_refiner,
                upscaler=upscaler,
            )
            jobs.append_log(
                project_id,
                job_id,
                f"ControlNet still render using {checkpoint} with {len(prepared_units) or 1} unit(s)",
            )
        elif workflow_family == "img2img":
            source_path = prepared_assets.get("source_path")
            if not isinstance(source_path, Path):
                raise UserFacingError(
                    "No source image selected for img2img",
                    hint="Upload or choose a project source image before running img2img.",
                    code="IMG2IMG_SOURCE_MISSING",
                    status_code=400,
                )
            source_image = _prepare_comfy_reference_image(project_id, node_url, str(source_path), "raw")
            wf = comfy.img2img_workflow(
                checkpoint=checkpoint,
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                width=actual_width,
                height=actual_height,
                steps=steps,
                cfg=cfg,
                sampler=sampler,
                source_image=source_image,
                denoise_strength=float(payload.get("denoise_strength") or 0.75),
                filename_prefix=f"edmg_img2img_v{variant_index:02d}_scene{scene_index:03d}_{job_id[:6]}",
                loras=resolved_loras,
                vae_name=vae_name,
                hires_fix=hires_fix,
                refiner=resolved_refiner,
                upscaler=upscaler,
            )
        elif workflow_family in {"inpaint", "outpaint"}:
            source_path = prepared_assets.get("source_path")
            mask_path = prepared_assets.get("mask_path")
            if not isinstance(source_path, Path) or not isinstance(mask_path, Path):
                raise UserFacingError(
                    "Source image or mask is missing",
                    hint="Choose both a source image and a mask before running an inpaint or outpaint render.",
                    code="INPAINT_ASSETS_MISSING",
                    status_code=400,
                )
            source_image = _prepare_comfy_reference_image(project_id, node_url, str(source_path), "raw")
            mask_image = _prepare_comfy_reference_image(project_id, node_url, str(mask_path), "raw")
            builder = comfy.outpaint_workflow if workflow_family == "outpaint" else comfy.inpaint_workflow
            wf = builder(
                checkpoint=checkpoint,
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                width=actual_width,
                height=actual_height,
                steps=steps,
                cfg=cfg,
                sampler=sampler,
                source_image=source_image,
                mask_image=mask_image,
                denoise_strength=float(payload.get("denoise_strength") or 0.8),
                filename_prefix=f"edmg_{workflow_family}_v{variant_index:02d}_scene{scene_index:03d}_{job_id[:6]}",
                loras=resolved_loras,
                vae_name=vae_name,
                hires_fix=hires_fix,
                refiner=resolved_refiner,
                upscaler=upscaler,
            )
        else:
            wf = comfy.default_workflow(
                checkpoint=checkpoint,
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                width=width,
                height=height,
                steps=steps,
                cfg=cfg,
                sampler=sampler,
                filename_prefix=f"edmg_still_v{variant_index:02d}_scene{scene_index:03d}_{job_id[:6]}",
                loras=resolved_loras,
                vae_name=vae_name,
                hires_fix=hires_fix,
                refiner=resolved_refiner,
                upscaler=upscaler,
            )
        if resolved_loras:
            lora_log = ", ".join(
                f"{str(item.get('filename') or item.get('name') or 'lora')}@{float(item.get('weight', 1.0)):.2f}"
                for item in resolved_loras
            )
            jobs.append_log(
                project_id,
                job_id,
                f"LoRAs: {lora_log}",
            )
        if vae_name:
            jobs.append_log(project_id, job_id, f"VAE override: {vae_name}")
        if hires_fix and hires_fix.get("enabled", True):
            jobs.append_log(
                project_id,
                job_id,
                f"Hires fix: scale {float(hires_fix.get('scale', 1.5)):.2f} • denoise {float(hires_fix.get('denoise', 0.35)):.2f}",
            )
        if resolved_refiner:
            jobs.append_log(
                project_id,
                job_id,
                f"Refiner pass: {str(resolved_refiner.get('model') or resolved_refiner.get('checkpoint') or 'base-checkpoint')} • switch {float(resolved_refiner.get('switch_at', 0.8)):.2f}",
            )

        submit = comfy.submit_prompt(node_url, wf)
        prompt_id = submit.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI submit missing prompt_id: {submit}")

        for _ in range(180):  # up to ~3 min
            hist = comfy.get_history(node_url, prompt_id)
            ims = comfy.extract_output_images(hist)
            err = comfy.extract_execution_error(hist)
            if err:
                raise UserFacingError(
                    message=f"ComfyUI scene render failed: {err}",
                    hint=hint_from_exception(Exception(err)) or "Check ComfyUI History/console, fix the model or nodes, then retry.",
                    code="COMFYUI_EXECUTION_ERROR",
                    status_code=502,
                )
            if ims:
                im = ims[0]
                img_bytes = comfy.download_image_bytes(
                    node_url,
                    filename=im["filename"],
                    subfolder=im.get("subfolder",""),
                    folder_type=im.get("type","output")
                )
                if not out_path:
                    out_dir = store.project_dir(project_id) / "outputs" / "images"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    ext = Path(im["filename"]).suffix or ".png"
                    out_name = f"v{variant_index:02d}_scene{scene_index:03d}_seed{seed}{ext}"
                    out_path = out_dir / out_name
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(img_bytes)
                final_width = actual_width
                final_height = actual_height
                if Image is not None:
                    try:
                        with Image.open(out_path) as generated_image:
                            final_width, final_height = generated_image.size
                    except Exception:
                        final_width = actual_width
                        final_height = actual_height
                metadata = _build_generation_metadata(
                    project_id=project_id,
                    job_id=job_id,
                    output_path=out_path,
                    payload={**payload, "width": final_width, "height": final_height, "refiner": resolved_refiner},
                    workflow_family=workflow_family,
                    checkpoint=checkpoint,
                    loras=resolved_loras,
                    controlnet_units=metadata_controlnet_units,
                    vae_name=vae_name,
                    prompt_id=str(prompt_id),
                    comfyui_image=im,
                    node_url=node_url,
                    backend="comfyui",
                    engine="comfyui",
                    model_family=payload.get("family"),
                    resolved_model_asset=checkpoint,
                    mask_source=prepared_assets.get("mask_source"),
                    outpaint=prepared_assets.get("outpaint"),
                )
                metadata_path = _write_generation_metadata(out_path, metadata)
                return {
                    "prompt_id": prompt_id,
                    "saved": str(out_path),
                    "metadata_path": str(metadata_path),
                    "metadata": metadata,
                    "comfyui_image": im,
                }

            time.sleep(1.0)

        raise UserFacingError(
            message="Timed out waiting for ComfyUI output.",
            hint="ComfyUI may be busy or stuck. Check ComfyUI console, then retry the job.",
            code="COMFYUI_TIMEOUT",
            status_code=504,
        )
    finally:
        comfy_pool.release(node_url)

def _run_comfyui_motion_scene(project_id: str, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Render a short motion clip via ComfyUI and assemble an MP4.

    This intentionally keeps the *runtime UX* simple:
      - jobs always write frames into frames_dir
      - then FFmpeg assembles out_clip
      - if motion capabilities aren't available, it can fall back to a still-based clip
    """

    prompt = payload["prompt"]
    negative_prompt = payload["negative_prompt"]
    seed = int(payload["seed"])
    width = int(payload["width"])
    height = int(payload["height"])
    steps = int(payload["steps"])
    cfg = float(payload["cfg"])
    sampler = str(payload["sampler"])
    scene_index = int(payload["scene_index"])
    variant_index = int(payload["variant_index"])

    engine = str(payload.get("engine") or "animatediff")
    frames = int(payload.get("frames", 24))
    fps = int(payload.get("fps", 12))
    motion_model_name = str(payload.get("motion_model_name") or "mm_sd_v15_v2.ckpt")
    required_tags = payload.get("required_tags") or []
    resolved_loras = _normalize_render_loras(payload.get("loras"))
    vae_name = _resolve_optional_comfy_asset_name(payload.get("vae"), folder="vae", allowed_kinds={"vae"})

    frames_dir = Path(payload.get("frames_dir") or "")
    out_clip = Path(payload.get("out_clip") or "")
    if out_clip and out_clip.exists():
        metadata_path = _output_metadata_path(out_clip)
        metadata = None
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                metadata = None
        return {"cached": True, "saved": str(out_clip), "metadata_path": str(metadata_path), "metadata": metadata}
    if frames_dir and frames_dir.exists() and out_clip:
        # If frames already exist (resume), try assembling.
        try:
            assemble_image_sequence(settings.ffmpeg_path, frames_dir, out_clip, fps=fps)
            return {"cached": True, "saved": str(out_clip)}
        except Exception:
            pass

    checkpoint = payload.get("checkpoint") or settings.comfyui_checkpoint
    filename_prefix = f"edmg_v{variant_index:02d}_scene{scene_index:03d}_{engine}_seed{seed}_{job_id[:6]}"

    # Build workflow and routing requirements.
    if engine == "svd" and hasattr(comfy, "svd_workflow"):
        wf = comfy.svd_workflow(
            checkpoint=checkpoint,
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=seed,
            width=width,
            height=height,
            steps=steps,
            cfg=cfg,
            sampler=sampler,
            svd_checkpoint=str(payload.get("svd_checkpoint") or "svd_xt.safetensors"),
            svd_num_frames=frames,
            svd_num_steps=int(payload.get("svd_num_steps") or 25),
            svd_motion_bucket_id=int(payload.get("svd_motion_bucket_id") or 127),
            svd_fps_id=int(payload.get("svd_fps_id") or 6),
            svd_cond_aug=float(payload.get("svd_cond_aug") or 0.02),
            svd_decoding_t=int(payload.get("svd_decoding_t") or 14),
            device=str(payload.get("device") or "cuda"),
            filename_prefix=filename_prefix,
            loras=resolved_loras,
            vae_name=vae_name,
        )
        req = {
            "checkpoint": checkpoint,
            "est_steps": steps,
            "est_frames": frames,
            "node_classes": ["SVDSimpleImg2Vid"],
            "tags": required_tags,
        }
        expected_frames = frames
    elif engine == "animatediff" and hasattr(comfy, "animatediff_workflow"):
        wf = comfy.animatediff_workflow(
            checkpoint=checkpoint,
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=seed,
            width=width,
            height=height,
            steps=steps,
            cfg=cfg,
            sampler=sampler,
            frames=frames,
            motion_model_name=motion_model_name,
            context_length=int(payload.get("context_length") or 16),
            context_overlap=int(payload.get("context_overlap") or 4),
            beta_schedule=str(payload.get("beta_schedule") or "autoselect"),
            filename_prefix=filename_prefix,
            loras=resolved_loras,
            vae_name=vae_name,
        )
        req = {
            "checkpoint": checkpoint,
            "est_steps": steps,
            "est_frames": frames,
            "node_classes": ["ADE_StandardStaticContextOptions", "ADE_AnimateDiffLoaderGen1"],
            "tags": required_tags,
        }
        expected_frames = frames
    else:
        # Fallback: still workflow (produces 1 image, then we assemble a 1-frame clip)
        wf = comfy.default_workflow(
            checkpoint=checkpoint,
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=seed,
            width=width,
            height=height,
            steps=steps,
            cfg=cfg,
            sampler=sampler,
            filename_prefix=filename_prefix,
            loras=resolved_loras,
            vae_name=vae_name,
        )
        req = {"checkpoint": checkpoint, "est_steps": steps, "est_frames": 1, "tags": required_tags}
        expected_frames = 1

    try:
        node_url = comfy_pool.acquire(req)
    except Exception as e:
        # If motion can't run, fall back to stills and produce a slideshow-like clip.
        if req.get("node_classes"):
            jobs.append_log(project_id, job_id, f"No compatible motion node for {engine}; falling back to stills.")
            wf = comfy.default_workflow(
                checkpoint=checkpoint,
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                width=width,
                height=height,
                steps=steps,
                cfg=cfg,
                sampler=sampler,
                filename_prefix=filename_prefix,
                loras=resolved_loras,
                vae_name=vae_name,
            )
            req = {"checkpoint": checkpoint, "est_steps": steps, "est_frames": 1, "tags": required_tags}
            expected_frames = 1
            try:
                node_url = comfy_pool.acquire(req)
            except Exception as e2:
                raise UserFacingError(
                    message="No available ComfyUI node could run this job.",
                    hint=hint_from_exception(e2) or "Start ComfyUI and retry.",
                    code="COMFYUI_NO_NODE",
                    status_code=502,
                )
        else:
            raise UserFacingError(
                message="No available ComfyUI node could run this job.",
                hint=hint_from_exception(e) or "Start ComfyUI and retry.",
                code="COMFYUI_NO_NODE",
                status_code=502,
            )

    jobs.append_log(project_id, job_id, f"Using ComfyUI node: {node_url}".rstrip())
    if resolved_loras:
        lora_log = ", ".join(
            f"{str(item.get('filename') or item.get('name') or 'lora')}@{float(item.get('weight', 1.0)):.2f}"
            for item in resolved_loras
        )
        jobs.append_log(project_id, job_id, f"LoRAs: {lora_log}")
    if vae_name:
        jobs.append_log(project_id, job_id, f"VAE override: {vae_name}")
    try:
        submit = comfy.submit_prompt(node_url, wf)
        prompt_id = submit.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI submit missing prompt_id: {submit}")

        frames_dir.mkdir(parents=True, exist_ok=True)

        for _ in range(420):  # up to ~7 min
            hist = comfy.get_history(node_url, prompt_id)
            ims_all = comfy.extract_output_images(hist)
            err = comfy.extract_execution_error(hist)
            if err:
                raise UserFacingError(
                    message=f"ComfyUI motion render failed: {err}",
                    hint=hint_from_exception(Exception(err)) or "Check ComfyUI History/console, fix the model or nodes, then retry.",
                    code="COMFYUI_EXECUTION_ERROR",
                    status_code=502,
                )
            ims = [im for im in ims_all if filename_prefix in str(im.get("filename", ""))] or ims_all

            if ims and len(ims) >= expected_frames:
                # Download all frames we have (cap at expected_frames)
                ims = ims[:expected_frames]
                for i, im in enumerate(ims, start=1):
                    ext = Path(im.get("filename", "")).suffix or ".png"
                    frame_path = frames_dir / f"frame_{i:06d}{ext}"
                    if frame_path.exists():
                        continue
                    img_bytes = comfy.download_image_bytes(
                        node_url,
                        filename=im["filename"],
                        subfolder=im.get("subfolder", ""),
                        folder_type=im.get("type", "output"),
                    )
                    frame_path.write_bytes(img_bytes)

                # Assemble clip
                if out_clip:
                    assemble_image_sequence(settings.ffmpeg_path, frames_dir, out_clip, fps=fps)
                    metadata = _build_generation_metadata(
                        project_id=project_id,
                        job_id=job_id,
                        output_path=out_clip,
                        payload=payload,
                        workflow_family=f"motion_{engine}",
                        checkpoint=str(checkpoint),
                        loras=resolved_loras,
                        controlnet_units=[],
                        vae_name=vae_name,
                        prompt_id=str(prompt_id),
                        comfyui_image=ims[0] if ims else None,
                        node_url=node_url,
                        artifact_key="video",
                    )
                    metadata["frames_dir"] = _project_relative_path(project_id, frames_dir)
                    metadata_path = _write_generation_metadata(out_clip, metadata)
                    return {
                        "prompt_id": prompt_id,
                        "saved": str(out_clip),
                        "frames_dir": str(frames_dir),
                        "metadata_path": str(metadata_path),
                        "metadata": metadata,
                    }
                # Fallback: no clip target provided
                return {"prompt_id": prompt_id, "frames_dir": str(frames_dir)}

            time.sleep(1.0)

        raise UserFacingError(
            message="Timed out waiting for ComfyUI frames.",
            hint="ComfyUI may be busy or stuck. Check ComfyUI console, then retry the job.",
            code="COMFYUI_TIMEOUT",
            status_code=504,
        )
    finally:
        comfy_pool.release(node_url)


def _run_internal_video(project_id: str, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    preflight = _internal_render_preflight_data(project_id, payload)
    if preflight.get("mode") == "proxy":
        proj = store.get(project_id)
        if not proj:
            raise UserFacingError("Project not found", hint="Open Projects and select a valid project.")
        plan = proj.meta.get("last_plan")
        if not plan or not (plan.get("variants") or []):
            raise UserFacingError("No plan generated", hint="Run Analyze + Plan first, then retry.")

        variant_index = int(payload.get("variant_index", 0))
        variants = plan["variants"]
        if variant_index < 0 or variant_index >= len(variants):
            raise UserFacingError("variant_index out of range", hint="Pick a valid variant index.")

        variant = variants[variant_index]
        scenes = variant.get("scenes") or []
        pdir = store.project_dir(project_id)
        audio_meta = proj.meta.get("audio")
        audio_path: Path | None = None
        if audio_meta and audio_meta.get("filename"):
            audio_path = pdir / "assets" / "audio" / str(audio_meta["filename"])
            if not audio_path.exists():
                audio_path = None

        settings_obj = _internal_settings_from_payload(
            payload,
            model_id="proxy_draft",
            render_tier=str(payload.get("render_tier") or "auto"),
            device_preference="cpu",
            temporal_mode="off",
        )

        runtime_checkpoint: dict[str, Any] | None = None
        chunk_plan = dict(((preflight.get("tier_plan") or {}).get("chunk_plan") or {}))
        estimated_total = max(1, int(preflight.get("estimated_frames", 1)) + 3)

        def _checkpoint(state: dict[str, Any]) -> None:
            nonlocal runtime_checkpoint
            runtime_checkpoint = dict(state or {})
            latest = jobs.get(project_id, job_id)
            latest_progress = latest.progress if latest and isinstance(latest.progress, dict) else {}
            jobs.update_progress(
                project_id,
                job_id,
                stage=str(latest_progress.get("stage") or runtime_checkpoint.get("stage") or "running"),
                current=int(latest_progress.get("current", 0) or 0),
                total=max(1, int(latest_progress.get("total", estimated_total) or estimated_total)),
                message=str(latest_progress.get("message") or runtime_checkpoint.get("message") or ""),
                extra=_job_checkpoint_extra("proxy", "proxy_draft", runtime_checkpoint),
            )

        def _check_canceled() -> None:
            latest = jobs.get(project_id, job_id)
            if latest and latest.status == "canceled":
                jobs.update_progress(
                    project_id,
                    job_id,
                    stage="canceled",
                    current=int((latest.progress or {}).get("current", 0)),
                    total=max(1, int((latest.progress or {}).get("total", estimated_total) or estimated_total)),
                    message="Cancel requested — stopping after current step",
                    extra=_job_checkpoint_extra("proxy", "proxy_draft", runtime_checkpoint),
                )
                raise JobCanceled("Proxy render canceled")

        def _log(line: str) -> None:
            _check_canceled()
            jobs.append_log(project_id, job_id, line)

        def _progress(stage: str, current: int, total: int, message: str | None = None) -> None:
            _check_canceled()
            jobs.update_progress(
                project_id,
                job_id,
                stage=stage,
                current=current,
                total=total,
                message=message,
                extra=_job_checkpoint_extra("proxy", "proxy_draft", runtime_checkpoint),
            )

        _progress("starting", 0, estimated_total, "Starting proxy draft render")
        variant2 = dict(variant)
        variant2["index"] = variant_index
        variant2["duration_s"] = _resolved_project_duration_s(proj, variant, scenes)

        out = render_internal_proxy_video_variant(
            ffmpeg_path=settings.ffmpeg_path,
            project_dir=pdir,
            variant=variant2,
            scenes=scenes,
            audio_path=audio_path,
            settings=settings_obj,
            timeline=(proj.meta.get("timeline") or None),
            log_fn=_log,
            progress_fn=_progress,
            cancel_check_fn=_check_canceled,
            chunk_plan=chunk_plan,
            checkpoint_fn=_checkpoint,
        )
        checkpoint_summary = runtime_checkpoint or _load_render_checkpoint(out)

        jobs.update_progress(
            project_id,
            job_id,
            stage="complete",
            current=estimated_total,
            total=estimated_total,
            message=f"Saved {out.name}",
            extra=_job_checkpoint_extra("proxy", "proxy_draft", checkpoint_summary, video=str(out)),
        )

        rel_video = str(out.relative_to(pdir))
        videos = proj.meta.setdefault("outputs", {}).setdefault("videos", [])
        if rel_video not in videos:
            videos.append(rel_video)
        render_entry = {
            "video": rel_video,
            "model_id": "proxy_draft",
            "mode": "proxy",
            "fps_render": settings_obj.fps_render,
            "fps_output": settings_obj.fps_output,
            "temporal_mode": "off",
            "resume_existing_frames": settings_obj.resume_existing_frames,
            "variant_index": variant_index,
            "completed_at": time.time(),
            "preflight": preflight,
            "runtime_checkpoint": checkpoint_summary,
        }
        proj.meta["last_internal_render"] = render_entry
        hist = proj.meta.setdefault("internal_render_history", [])
        hist.append(render_entry)
        if isinstance(hist, list) and len(hist) > 20:
            proj.meta["internal_render_history"] = hist[-20:]
        store.save(proj)
        return {"ok": True, "video": rel_video, "video_abs": str(out), "mode": "proxy", "preflight": preflight, "runtime_checkpoint": checkpoint_summary}

    if preflight.get("mode") == "hosted":
        provider_cfg = dict((render_settings.get().get("stability") or {}))
        proj = store.get(project_id)
        if not proj:
            raise UserFacingError("Project not found", hint="Open Projects and select a valid project.")
        plan = proj.meta.get("last_plan")
        if not plan or not (plan.get("variants") or []):
            raise UserFacingError("No plan generated", hint="Run Analyze + Plan first, then retry.")

        variant_index = int(payload.get("variant_index", 0))
        variants = plan["variants"]
        if variant_index < 0 or variant_index >= len(variants):
            raise UserFacingError("variant_index out of range", hint="Pick a valid variant index.")

        variant = variants[variant_index]
        scenes = variant.get("scenes") or []
        pdir = store.project_dir(project_id)
        audio_meta = proj.meta.get("audio")
        audio_path: Path | None = None
        if audio_meta and audio_meta.get("filename"):
            audio_path = pdir / "assets" / "audio" / str(audio_meta["filename"])
            if not audio_path.exists():
                audio_path = None

        hosted_payload = dict(payload)
        hosted_payload.setdefault("cfg", provider_cfg.get("cfg_scale", 6.5))
        hosted_payload.setdefault("temporal_strength", provider_cfg.get("strength", 0.55))
        settings_obj = _internal_settings_from_payload(
            hosted_payload,
            model_id=str(preflight.get("model_id") or "stability:sd3:sd3.5-large-turbo"),
            render_tier=str(payload.get("render_tier") or "auto"),
            device_preference="cpu",
            temporal_mode="keyframes" if str(payload.get("temporal_mode") or "frame_img2img") == "frame_img2img" else str(payload.get("temporal_mode") or "keyframes"),
        )

        runtime_checkpoint: dict[str, Any] | None = None
        chunk_plan = dict(((preflight.get("tier_plan") or {}).get("chunk_plan") or {}))
        estimated_total = max(1, int(preflight.get("estimated_frames", 1)) + 3)

        def _checkpoint(state: dict[str, Any]) -> None:
            nonlocal runtime_checkpoint
            runtime_checkpoint = dict(state or {})
            latest = jobs.get(project_id, job_id)
            latest_progress = latest.progress if latest and isinstance(latest.progress, dict) else {}
            jobs.update_progress(
                project_id,
                job_id,
                stage=str(latest_progress.get("stage") or runtime_checkpoint.get("stage") or "running"),
                current=int(latest_progress.get("current", 0) or 0),
                total=max(1, int(latest_progress.get("total", estimated_total) or estimated_total)),
                message=str(latest_progress.get("message") or runtime_checkpoint.get("message") or ""),
                extra=_job_checkpoint_extra("hosted", settings_obj.model_id, runtime_checkpoint),
            )

        def _check_canceled() -> None:
            latest = jobs.get(project_id, job_id)
            if latest and latest.status == "canceled":
                jobs.update_progress(
                    project_id,
                    job_id,
                    stage="canceled",
                    current=int((latest.progress or {}).get("current", 0)),
                    total=max(1, int((latest.progress or {}).get("total", estimated_total) or estimated_total)),
                    message="Cancel requested — stopping after current step",
                    extra=_job_checkpoint_extra("hosted", settings_obj.model_id, runtime_checkpoint),
                )
                raise JobCanceled("Hosted render canceled")

        def _log(line: str) -> None:
            _check_canceled()
            jobs.append_log(project_id, job_id, line)

        def _progress(stage: str, current: int, total: int, message: str | None = None) -> None:
            _check_canceled()
            jobs.update_progress(
                project_id,
                job_id,
                stage=stage,
                current=current,
                total=total,
                message=message,
                extra=_job_checkpoint_extra("hosted", settings_obj.model_id, runtime_checkpoint),
            )

        _log(
            f"Hosted render: fps_render={settings_obj.fps_render} fps_output={settings_obj.fps_output} "
            f"keyframe_interval_s={settings_obj.keyframe_interval_s} service={preflight.get('hosted_provider', {}).get('service')}"
        )
        if preflight.get("warnings"):
            for warning in preflight["warnings"]:
                _log(f"Warning: {warning}")

        _progress("starting", 0, estimated_total, "Starting hosted Stability render")
        variant2 = dict(variant)
        variant2["index"] = variant_index
        variant2["duration_s"] = _resolved_project_duration_s(proj, variant, scenes)

        out = render_stability_hosted_video_variant(
            ffmpeg_path=settings.ffmpeg_path,
            project_dir=pdir,
            variant=variant2,
            scenes=scenes,
            audio_path=audio_path,
            settings=settings_obj,
            stability_api_key=str(secrets.get("stability_api_key") or ""),
            hosted_settings={
                "service": str(preflight.get("hosted_provider", {}).get("service") or provider_cfg.get("service") or "sd3"),
                "model": str(preflight.get("hosted_provider", {}).get("model") or provider_cfg.get("model") or "sd3.5-large-turbo"),
                "style_preset": str(preflight.get("hosted_provider", {}).get("style_preset") or provider_cfg.get("style_preset") or "none"),
                "output_format": str(preflight.get("hosted_provider", {}).get("output_format") or provider_cfg.get("output_format") or "png"),
                "strength": float(provider_cfg.get("strength", 0.55)),
                "cfg_scale": float(provider_cfg.get("cfg_scale", 6.5)),
            },
            timeline=(proj.meta.get("timeline") or None),
            log_fn=_log,
            progress_fn=_progress,
            cancel_check_fn=_check_canceled,
            chunk_plan=chunk_plan,
            checkpoint_fn=_checkpoint,
        )
        checkpoint_summary = runtime_checkpoint or _load_render_checkpoint(out)

        jobs.update_progress(
            project_id,
            job_id,
            stage="complete",
            current=estimated_total,
            total=estimated_total,
            message=f"Saved {out.name}",
            extra=_job_checkpoint_extra("hosted", settings_obj.model_id, checkpoint_summary, video=str(out)),
        )

        rel_video = str(out.relative_to(pdir))
        videos = proj.meta.setdefault("outputs", {}).setdefault("videos", [])
        if rel_video not in videos:
            videos.append(rel_video)
        render_entry = {
            "video": rel_video,
            "model_id": settings_obj.model_id,
            "mode": "hosted",
            "fps_render": settings_obj.fps_render,
            "fps_output": settings_obj.fps_output,
            "temporal_mode": settings_obj.temporal_mode,
            "resume_existing_frames": settings_obj.resume_existing_frames,
            "variant_index": variant_index,
            "completed_at": time.time(),
            "preflight": preflight,
            "runtime_checkpoint": checkpoint_summary,
            "hosted_provider": preflight.get("hosted_provider"),
        }
        proj.meta["last_internal_render"] = render_entry
        hist = proj.meta.setdefault("internal_render_history", [])
        hist.append(render_entry)
        if isinstance(hist, list) and len(hist) > 20:
            proj.meta["internal_render_history"] = hist[-20:]
        store.save(proj)
        return {"ok": True, "video": rel_video, "video_abs": str(out), "mode": "hosted", "preflight": preflight, "runtime_checkpoint": checkpoint_summary}

    proj, variant, model_id, model_path, settings_obj = _resolve_internal_render_request(project_id, payload)
    scenes = variant.get("scenes") or []
    pdir = store.project_dir(project_id)
    audio_meta = proj.meta.get("audio")
    audio_path: Path | None = None
    if audio_meta and audio_meta.get("filename"):
        audio_path = pdir / "assets" / "audio" / str(audio_meta["filename"])
        if not audio_path.exists():
            audio_path = None

    hw = _hardware_profile()
    runtime_checkpoint: dict[str, Any] | None = None
    chunk_plan = dict(((preflight.get("tier_plan") or {}).get("chunk_plan") or {}))
    estimated_total = max(1, int(preflight.get("estimated_frames", 1)) + 3)

    def _checkpoint(state: dict[str, Any]) -> None:
        nonlocal runtime_checkpoint
        runtime_checkpoint = dict(state or {})
        latest = jobs.get(project_id, job_id)
        latest_progress = latest.progress if latest and isinstance(latest.progress, dict) else {}
        jobs.update_progress(
            project_id,
            job_id,
            stage=str(latest_progress.get("stage") or runtime_checkpoint.get("stage") or "running"),
            current=int(latest_progress.get("current", 0) or 0),
            total=max(1, int(latest_progress.get("total", estimated_total) or estimated_total)),
            message=str(latest_progress.get("message") or runtime_checkpoint.get("message") or ""),
            extra=_job_checkpoint_extra("internal", model_id, runtime_checkpoint),
        )

    def _check_canceled() -> None:
        latest = jobs.get(project_id, job_id)
        if latest and latest.status == "canceled":
            jobs.update_progress(
                project_id,
                job_id,
                stage="canceled",
                current=int((latest.progress or {}).get("current", 0)),
                total=max(1, int((latest.progress or {}).get("total", estimated_total) or estimated_total)),
                message="Cancel requested — stopping after current step",
                extra=_job_checkpoint_extra("internal", model_id, runtime_checkpoint),
            )
            raise JobCanceled("Internal render canceled")

    def _log(line: str) -> None:
        _check_canceled()
        jobs.append_log(project_id, job_id, line)

    def _progress(stage: str, current: int, total: int, message: str | None = None) -> None:
        _check_canceled()
        jobs.update_progress(
            project_id,
            job_id,
            stage=stage,
            current=current,
            total=total,
            message=message,
            extra=_job_checkpoint_extra("internal", model_id, runtime_checkpoint),
        )

    _log(
        f"Internal render: fps_render={settings_obj.fps_render} fps_output={settings_obj.fps_output} "
        f"keyframe_interval_s={settings_obj.keyframe_interval_s} temporal_mode={settings_obj.temporal_mode}"
    )
    _log(f"Hardware: backend={hw.get('backend')} vram_gb={hw.get('vram_gb')}")
    _log(f"Using model_id={model_id} path={model_path}")
    if preflight.get("warnings"):
        for warning in preflight["warnings"]:
            _log(f"Warning: {warning}")

    _progress("starting", 0, estimated_total, "Starting internal render")

    variant2 = dict(variant)
    variant2["index"] = int(payload.get("variant_index", 0))
    variant2["duration_s"] = _resolved_project_duration_s(proj, variant, scenes)

    out = render_internal_video_variant(
        ffmpeg_path=settings.ffmpeg_path,
        project_dir=pdir,
        variant=variant2,
        scenes=scenes,
        audio_path=audio_path,
        model_dir=model_path,
        settings=settings_obj,
        timeline=(proj.meta.get("timeline") or None),
        log_fn=_log,
        progress_fn=_progress,
        cancel_check_fn=_check_canceled,
        chunk_plan=chunk_plan,
        checkpoint_fn=_checkpoint,
    )
    checkpoint_summary = runtime_checkpoint or _load_render_checkpoint(out)

    jobs.update_progress(
        project_id,
        job_id,
        stage="complete",
        current=estimated_total,
        total=estimated_total,
        message=f"Saved {out.name}",
        extra=_job_checkpoint_extra("internal", model_id, checkpoint_summary, video=str(out)),
    )

    rel_video = str(out.relative_to(pdir))
    videos = proj.meta.setdefault("outputs", {}).setdefault("videos", [])
    if rel_video not in videos:
        videos.append(rel_video)
    render_entry = {
        "video": rel_video,
        "model_id": model_id,
        "mode": "diffusion",
        "fps_render": settings_obj.fps_render,
        "fps_output": settings_obj.fps_output,
        "temporal_mode": settings_obj.temporal_mode,
        "resume_existing_frames": settings_obj.resume_existing_frames,
        "variant_index": int(payload.get("variant_index", 0)),
        "completed_at": time.time(),
        "preflight": preflight,
        "runtime_checkpoint": checkpoint_summary,
    }
    proj.meta["last_internal_render"] = render_entry
    hist = proj.meta.setdefault("internal_render_history", [])
    hist.append(render_entry)
    if isinstance(hist, list) and len(hist) > 20:
        proj.meta["internal_render_history"] = hist[-20:]
    store.save(proj)
    return {"ok": True, "video": rel_video, "video_abs": str(out), "mode": "diffusion", "preflight": preflight, "runtime_checkpoint": checkpoint_summary}


@app.post("/v1/projects/{project_id}/render/stills/scenes")
@app.post("/v1/projects/{project_id}/render/comfyui/scenes")
def render_scenes(project_id: str, req: RenderScenesRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")

    variants = plan["variants"]
    if req.variant_index < 0 or req.variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    variant = variants[req.variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise HTTPException(400, "Selected variant has no scenes")

    created = []
    resolved_loras = _normalize_render_loras(getattr(req, "loras", []))
    raw_controlnet_units = _request_payload(req).get("controlnet_units") if isinstance(_request_payload(req).get("controlnet_units"), list) else list(getattr(req, "controlnet_units", []))
    if req.workflow_family == "controlnet" and not raw_controlnet_units and req.controlnet_model and req.reference_asset:
        raw_controlnet_units = [
            {
                "model": req.controlnet_model,
                "reference_asset": req.reference_asset,
                "conditioning_mode": req.conditioning_mode,
                "strength": req.controlnet_strength,
            }
        ]

    selection = _resolve_still_scene_selection(
        model_id=req.model_id,
        checkpoint=req.checkpoint,
        workflow_family=req.workflow_family,
        controlnet_model=req.controlnet_model,
        reference_asset=req.reference_asset,
        conditioning_mode=req.conditioning_mode,
        controlnet_units=raw_controlnet_units,
    )
    controlnet_units = _normalize_controlnet_units(
        raw_controlnet_units,
        engine=str(selection.get("engine") or "comfyui"),
        family=selection.get("family"),
    )
    if str(selection.get("workflow_family") or "") == "controlnet" and not controlnet_units:
        raise UserFacingError(
            "No compatible ControlNet units were selected",
            hint="Attach one or more compatible ControlNet units before running the still render.",
            code="CONTROLNET_MISSING",
            status_code=400,
        )
    vae_name = (
        _resolve_optional_comfy_asset_name(req.vae, folder="vae", allowed_kinds={"vae"})
        if str(selection.get("engine") or "comfyui") == "comfyui"
        else (str(req.vae or "").strip() or None)
    )
    model_tag = _safe_name_tag(req.model_id or selection.get("checkpoint") or "default")
    workflow_tag = _safe_name_tag(selection.get("workflow_family") or "txt2img")
    ref_tag = _safe_name_tag(req.source_asset or req.reference_asset or "noref")
    for idx, sc in enumerate(scenes):
        # Deterministic output path for caching
        out_dir = store.project_dir(project_id) / "outputs" / "images"
        out_dir.mkdir(parents=True, exist_ok=True)
        seed = int(req.seed) + idx if req.seed is not None else _stable_seed(project_id, req.variant_index, idx)
        out_path = out_dir / f"v{req.variant_index:02d}_scene{idx:03d}_{workflow_tag}_{model_tag}_{ref_tag}_seed{seed}.png"
        p = {
            "variant_index": req.variant_index,
            "scene_index": idx,
            "model_id": req.model_id,
            "prompt": sc.get("prompt") or "",
            "negative_prompt": req.negative_prompt,
            "seed": seed,
            "width": req.width,
            "height": req.height,
            "steps": req.steps,
            "cfg": req.cfg,
            "sampler": req.sampler,
            "checkpoint": selection.get("checkpoint"),
            "workflow_family": selection.get("workflow_family"),
            "source_asset": req.source_asset,
            "reference_asset": req.reference_asset,
            "inpaint_mask": req.inpaint_mask,
            "outpaint": _request_payload(req.outpaint) if req.outpaint else None,
            "conditioning_mode": selection.get("conditioning_mode"),
            "controlnet_model": req.controlnet_model,
            "controlnet_name": selection.get("controlnet_name"),
            "controlnet_strength": req.controlnet_strength,
            "controlnet_units": controlnet_units,
            "engine": selection.get("engine"),
            "family": selection.get("family"),
            "model_path": str(selection.get("model_path")) if selection.get("model_path") else None,
            "loras": resolved_loras,
            "vae": vae_name,
            "denoise_strength": req.denoise_strength,
            "hires_fix": _request_payload(req.hires_fix) if req.hires_fix else None,
            "refiner": _request_payload(req.refiner) if req.refiner else None,
            "upscaler": req.upscaler,
            "out_path": str(out_path),
        }
        job_type = "internal_still_scene" if str(selection.get("engine") or "comfyui") == "internal" else "comfyui_scene"
        job = jobs.create(project_id, job_type, p)
        created.append(job.__dict__)

    proj.meta.setdefault("jobs", []).extend(created)
    store.save(proj)

    return {"ok": True, "enqueued": len(created), "jobs": created}



@app.post("/v1/projects/{project_id}/render/internal/video")
def render_internal_video(project_id: str, req: InternalVideoRenderRequest):
    """Enqueue a full internal render job (CPU-safe baseline)."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")

    payload = _request_payload(req)
    preflight = _internal_render_preflight_data(project_id, payload)
    payload["render_mode"] = str(preflight.get("mode") or payload.get("render_mode") or "auto")
    job = jobs.create(project_id, "internal_video", payload)
    job.progress = {
        "stage": "queued",
        "current": 0,
        "total": max(1, int(preflight.get("estimated_frames", 1)) + 3),
        "percent": 0.0,
        "message": f"Queued internal render for model {preflight.get('model_id')}",
    }
    jobs.save(job)
    proj.meta.setdefault("jobs", []).append(job.__dict__)
    store.save(proj)
    return {"ok": True, "job": job.__dict__, "preflight": preflight}




def _internal_model_family(model_path: Path) -> str:
    path_hint = str(model_path.name or "").lower()
    if "sd35" in path_hint or "sd3" in path_hint or "stable-diffusion-3" in path_hint:
        return "sd3"
    if "sdxl" in path_hint:
        return "sdxl"
    mi = model_path / "model_index.json"
    if mi.exists():
        try:
            data = json.loads(mi.read_text(encoding="utf-8"))
            cls = str(data.get("_class_name") or "")
            if "StableDiffusion3" in cls:
                return "sd3"
            if "XL" in cls or "XLPipeline" in cls:
                return "sdxl"
        except Exception:
            pass
    return "sd15"


SD35_INTERNAL_MIN_CUDA_VRAM_GB = 14.0


def _internal_model_family_for_request(model_id: str, model_path: Path) -> str:
    model_id_l = str(model_id or "").lower()
    if "sd35" in model_id_l or "stable-diffusion-3" in model_id_l:
        return "sd3"
    if "sdxl" in model_id_l:
        return "sdxl"
    return _internal_model_family(model_path)


def _internal_model_hardware_issue(
    model_id: str,
    model_family: str,
    hw: dict[str, Any],
    requested_device: str,
) -> dict[str, str] | None:
    family = str(model_family or "").lower()
    model_id_l = str(model_id or "").lower()
    if family not in {"sd3", "sd35"} and "sd35" not in model_id_l:
        return None

    requested = str(requested_device or "auto").strip().lower()
    backend = requested if requested in {"cuda", "cpu", "mps", "directml"} else str(hw.get("backend") or "cpu").lower()
    if backend != "cuda":
        return None

    vram_gb = float(hw.get("vram_gb") or 0.0)
    if vram_gb >= SD35_INTERNAL_MIN_CUDA_VRAM_GB:
        return None

    return {
        "code": "MODEL_UNSUPPORTED_FOR_HARDWARE",
        "message": (
            f"Stable Diffusion 3.5 internal rendering needs at least "
            f"{SD35_INTERNAL_MIN_CUDA_VRAM_GB:.0f} GB of CUDA VRAM for this Studio video path; "
            f"this GPU reports {vram_gb:.1f} GB."
        ),
        "hint": "Use SDXL or SD 1.5 for local CUDA rendering on this GPU, or use hosted Stability for SD3.5 keyframes.",
    }


def _internal_settings_from_payload(
    payload: dict[str, Any],
    *,
    model_id: str,
    render_tier: str,
    device_preference: str,
    temporal_mode: str | None = None,
) -> InternalVideoSettings:
    refiner = payload.get("refiner") if isinstance(payload.get("refiner"), dict) else None
    deforum_override_keys = (
        "deforum_prompts",
        "deforum_negative_prompts",
        "deforum_zoom",
        "deforum_angle",
        "deforum_translation_x",
        "deforum_translation_y",
        "deforum_strength_schedule",
    )
    deforum_overrides = {
        key: payload.get(key)
        for key in deforum_override_keys
        if payload.get(key) is not None
    }
    return InternalVideoSettings(
        fps_render=int(payload.get("fps_render", 2)),
        fps_output=int(payload.get("fps_output", 24)),
        width=int(payload.get("width", 768)),
        height=int(payload.get("height", 432)),
        steps=int(payload.get("steps", 15)),
        cfg=float(payload.get("cfg", 7.0)),
        sampler=str(payload.get("sampler", "euler")),
        seed=(int(payload["seed"]) if payload.get("seed") is not None else None),
        keyframe_interval_s=float(payload.get("keyframe_interval_s", 5.0)),
        interpolation_engine=str(payload.get("interpolation_engine", "auto")),
        negative_prompt=str(payload.get("negative_prompt", "blurry, low quality, watermark, text, logo")),
        model_id=model_id,
        loras=tuple(_normalize_render_loras(payload.get("loras"))),
        vae=str(payload.get("vae") or "").strip() or None,
        refiner=refiner,
        render_tier=render_tier,
        device_preference=device_preference,
        temporal_mode=temporal_mode if temporal_mode is not None else str(payload.get("temporal_mode", "keyframes")),
        temporal_strength=float(payload.get("temporal_strength", 0.35)),
        temporal_steps=(int(payload["temporal_steps"]) if payload.get("temporal_steps") is not None else None),
        refine_every_n_frames=int(payload.get("refine_every_n_frames", 1)),
        anchor_strength=float(payload.get("anchor_strength", 0.20)),
        prompt_blend=bool(payload.get("prompt_blend", True)),
        resume_existing_frames=bool(payload.get("resume_existing_frames", True)),
        deforum_overrides=deforum_overrides or None,
    )


def _resolve_internal_render_request(project_id: str, payload: dict[str, Any]) -> tuple[Any, dict[str, Any], str, Path, InternalVideoSettings]:
    proj = store.get(project_id)
    if not proj:
        raise UserFacingError("Project not found", hint="Open Projects and select a valid project.")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise UserFacingError("No plan generated", hint="Run Analyze + Plan first, then retry.")

    variant_index = int(payload.get("variant_index", 0))
    variants = plan["variants"]
    if variant_index < 0 or variant_index >= len(variants):
        raise UserFacingError("variant_index out of range", hint="Pick a valid variant index.")

    variant = variants[variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise UserFacingError("Selected variant has no scenes", hint="Re-run Plan with at least 1 scene.")

    req_model_id = str(payload.get("model_id") or "hf_sd15_internal")
    hw = _hardware_profile()
    tier_plan = _build_internal_render_plan(hw, requested_tier=str(payload.get("render_tier") or "auto"))
    provider_cfg = _render_provider_status(hw).get("settings") or {}
    directml_cfg = dict(provider_cfg.get("directml") or {})
    directml_enabled = bool(directml_cfg.get("enabled", True))
    requested_device = str(payload.get("device_preference") or tier_plan.get("device_preference") or "auto").strip().lower()
    if requested_device == "directml" and not directml_enabled:
        raise UserFacingError(
            "DirectML is disabled in Settings.",
            hint="Enable AMD / DirectML internal runtime in Settings, or switch the device preference to CPU/CUDA/MPS.",
            code="DIRECTML_DISABLED",
            status_code=400,
        )
    if requested_device == "auto" and str(hw.get("backend") or "").lower() == "directml" and not directml_enabled:
        requested_device = "cpu"
    if requested_device == "auto" and str(hw.get("backend") or "").lower() == "directml" and not bool(directml_cfg.get("allow_auto_selection", True)):
        requested_device = "cpu"

    auto_model_hardware_issue: dict[str, str] | None = None

    def _pick_auto_model() -> str | None:
        nonlocal auto_model_hardware_issue
        preferred = str(tier_plan.get("preferred_internal_model") or hw.get("preferred_internal_model") or "hf_sd15_internal")
        if requested_device == "directml":
            preferred = str(directml_cfg.get("preferred_model") or preferred or "auto").strip().lower()
            if preferred == "auto":
                preferred = "hf_sdxl_internal"
            fallbacks = [preferred, "hf_sdxl_internal", "hf_sd15_internal"]
        else:
            fallbacks = [preferred, "hf_sd35_medium_internal", "hf_sdxl_internal", "hf_sd15_internal"]
        seen: set[str] = set()
        for mid in fallbacks:
            if mid in seen:
                continue
            seen.add(mid)
            installed = models.installed_path(mid)
            if not installed:
                continue
            family = _internal_model_family_for_request(mid, installed)
            hardware_issue = _internal_model_hardware_issue(mid, family, hw, requested_device)
            if hardware_issue:
                auto_model_hardware_issue = hardware_issue
                continue
            return mid
        return None

    model_id = req_model_id
    if req_model_id.lower() in ("auto", "auto_internal"):
        picked = _pick_auto_model()
        if not picked:
            if auto_model_hardware_issue:
                raise UserFacingError(
                    str(auto_model_hardware_issue["message"]),
                    hint=str(auto_model_hardware_issue["hint"]),
                    code=str(auto_model_hardware_issue["code"]),
                    status_code=400,
                )
            raise UserFacingError(
                "No internal diffusion model installed",
                hint="Open Models and install an internal Diffusers model such as SD 1.5, SDXL, or SD3.5 Medium, then retry.",
                code="MODEL_NOT_INSTALLED",
                status_code=400,
            )
        model_id = picked

    model_path = models.installed_path(model_id)
    if not model_path:
        issue = getattr(models, "internal_asset_issue", lambda _model_id: None)(model_id)
        if issue == "incomplete":
            raise UserFacingError(
                "Internal model install is incomplete",
                hint="Open Models and reinstall the requested internal model. The local snapshot is missing required weight files.",
                code="MODEL_NOT_INSTALLED",
                status_code=400,
            )
        raise UserFacingError(
            "Internal model not installed",
            hint="Open Models and install the requested internal model, then retry.",
            code="MODEL_NOT_INSTALLED",
            status_code=400,
        )

    model_family = _internal_model_family_for_request(model_id, model_path)
    effective_device_preference = requested_device
    if requested_device == "directml" and model_family not in {"sd15", "sdxl"}:
        raise UserFacingError(
            "DirectML currently supports SD 1.5 and SDXL only.",
            hint="Use SDXL or SD 1.5 for AMD / DirectML, or switch device preference to CPU for SD3.5.",
            code="DIRECTML_MODEL_UNSUPPORTED",
            status_code=400,
        )
    if requested_device == "auto" and str(hw.get("backend") or "").lower() == "directml" and model_family not in {"sd15", "sdxl"}:
        effective_device_preference = "cpu"
    hardware_issue = _internal_model_hardware_issue(model_id, model_family, hw, requested_device)
    if hardware_issue:
        raise UserFacingError(
            str(hardware_issue["message"]),
            hint=str(hardware_issue["hint"]),
            code=str(hardware_issue["code"]),
            status_code=400,
        )

    settings_obj = _internal_settings_from_payload(
        payload,
        model_id=model_id,
        render_tier=str(tier_plan.get("applied_tier") or payload.get("render_tier") or "auto"),
        device_preference=effective_device_preference,
    )
    return proj, variant, model_id, model_path, settings_obj


def _proxy_render_preflight_data(
    project_id: str,
    payload: dict[str, Any],
    *,
    reason: str | None = None,
    requested_model_id: str | None = None,
) -> dict[str, Any]:
    proj = store.get(project_id)
    if not proj:
        raise UserFacingError("Project not found", hint="Open Projects and select a valid project.")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise UserFacingError("No plan generated", hint="Run Analyze + Plan first, then retry.")

    variant_index = int(payload.get("variant_index", 0))
    variants = plan["variants"]
    if variant_index < 0 or variant_index >= len(variants):
        raise UserFacingError("variant_index out of range", hint="Pick a valid variant index.")

    variant = variants[variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise UserFacingError("Selected variant has no scenes", hint="Re-run Plan with at least 1 scene.")

    settings_obj = _internal_settings_from_payload(
        payload,
        model_id="proxy_draft",
        render_tier=str(payload.get("render_tier") or "auto"),
        device_preference="cpu",
        temporal_mode="off",
    )

    duration_s = _resolved_project_duration_s(proj, variant, scenes)
    total_frames = int(math.ceil(duration_s * max(1, int(settings_obj.fps_render))))
    hw = _hardware_profile()
    tier_plan = _build_internal_render_plan(hw, requested_tier=str(payload.get("render_tier") or "auto"), duration_s=duration_s)
    tier_plan["chunk_plan"] = _build_render_chunk_plan(hw, applied_tier=str(tier_plan.get("applied_tier") or "draft"), duration_s=duration_s, total_frames=total_frames, fps_render=int(settings_obj.fps_render), render_mode="proxy")
    cache = describe_proxy_render_cache(
        project_dir=store.project_dir(project_id),
        variant_index=variant_index,
        scenes=scenes,
        timeline=(proj.meta.get("timeline") or None),
        settings=settings_obj,
        total_frames=total_frames,
    )
    fallback_reason = (
        "the requested internal diffusion model is unavailable for this render."
        if reason
        else "no internal diffusion model is installed."
    )
    warnings = [
        f"Using proxy draft render because {fallback_reason}",
        "Proxy mode renders pacing, prompts, and timeline overlays locally without ComfyUI or Diffusers.",
    ]
    if reason:
        warnings.insert(0, reason)
    return {
        "ok": True,
        "mode": "proxy",
        "variant_index": variant_index,
        "model_id": "proxy_draft",
        "requested_model_id": str(requested_model_id or payload.get("model_id") or "auto"),
        "model_path": None,
        "duration_s": duration_s,
        "estimated_frames": total_frames,
        "estimated_keyframes": max(1, len(_scene_keyframe_times(scenes, settings_obj.keyframe_interval_s))),
        "device": str(tier_plan.get("device_preference") or "cpu"),
        "hardware": hw,
        "tier_plan": tier_plan,
        "resume_existing_frames": bool(settings_obj.resume_existing_frames),
        "warnings": warnings,
        "cache": cache,
        "installed_internal_models": {
            "hf_sd15_internal": bool(models.installed_path("hf_sd15_internal")),
            "hf_sdxl_internal": bool(models.installed_path("hf_sdxl_internal")),
            "hf_sd35_medium_internal": bool(models.installed_path("hf_sd35_medium_internal")),
        },
        "settings": {
            "fps_render": settings_obj.fps_render,
            "fps_output": settings_obj.fps_output,
            "width": settings_obj.width,
            "height": settings_obj.height,
            "interpolation_engine": settings_obj.interpolation_engine,
            "resume_existing_frames": settings_obj.resume_existing_frames,
            "render_mode": "proxy",
        },
    }


def _hosted_render_preflight_data(
    project_id: str,
    payload: dict[str, Any],
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    provider_status = _render_provider_status()
    stability = dict(provider_status.get("stability") or {})
    provider_cfg = dict((provider_status.get("settings") or {}).get("stability") or {})
    if not stability.get("configured"):
        raise UserFacingError(
            "Stability API key is not configured.",
            hint="Open Settings and save a Stability API key, then retry the hosted render.",
            code="STABILITY_API_KEY_MISSING",
            status_code=400,
        )
    if not stability.get("enabled"):
        raise UserFacingError(
            "Hosted Stability fallback is disabled.",
            hint="Open Settings and enable the Stability hosted fallback, then retry.",
            code="STABILITY_HOSTED_DISABLED",
            status_code=400,
        )

    proj = store.get(project_id)
    if not proj:
        raise UserFacingError("Project not found", hint="Open Projects and select a valid project.")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise UserFacingError("No plan generated", hint="Run Analyze + Plan first, then retry.")

    variant_index = int(payload.get("variant_index", 0))
    variants = plan["variants"]
    if variant_index < 0 or variant_index >= len(variants):
        raise UserFacingError("variant_index out of range", hint="Pick a valid variant index.")

    variant = variants[variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise UserFacingError("Selected variant has no scenes", hint="Re-run Plan with at least 1 scene.")

    hosted_service = str(payload.get("hosted_service") or "default").strip().lower()
    if hosted_service in {"", "default"}:
        hosted_service = str(provider_cfg.get("service") or "sd3")
    hosted_model = str(payload.get("hosted_model") or provider_cfg.get("model") or "sd3.5-large-turbo").strip().lower()
    hosted_style = str(payload.get("hosted_style_preset") or provider_cfg.get("style_preset") or "none").strip().lower()
    hosted_model_id = f"stability:{hosted_service}:{hosted_model if hosted_service == 'sd3' else 'default'}"

    hosted_payload = dict(payload)
    hosted_payload.setdefault("cfg", provider_cfg.get("cfg_scale", 6.5))
    hosted_payload.setdefault("temporal_strength", provider_cfg.get("strength", 0.55))
    settings_obj = _internal_settings_from_payload(
        hosted_payload,
        model_id=hosted_model_id,
        render_tier=str(payload.get("render_tier") or "auto"),
        device_preference="cpu",
        temporal_mode="keyframes" if str(payload.get("temporal_mode") or "frame_img2img") == "frame_img2img" else str(payload.get("temporal_mode") or "keyframes"),
    )

    duration_s = _resolved_project_duration_s(proj, variant, scenes)
    total_frames = int(math.ceil(duration_s * max(1, int(settings_obj.fps_render))))
    keyframes = max(1, len(_scene_keyframe_times(scenes, settings_obj.keyframe_interval_s)))
    hw = _hardware_profile()
    tier_plan = _build_internal_render_plan(hw, requested_tier=str(payload.get("render_tier") or "auto"), duration_s=duration_s)
    tier_plan["chunk_plan"] = _build_render_chunk_plan(hw, applied_tier=str(tier_plan.get("applied_tier") or "draft"), duration_s=duration_s, total_frames=total_frames, fps_render=int(settings_obj.fps_render), render_mode="hosted")
    cache = describe_internal_render_cache(
        project_dir=store.project_dir(project_id),
        variant_index=variant_index,
        variant=variant,
        scenes=scenes,
        timeline=(proj.meta.get("timeline") or None),
        model_dir=Path(f"stability_platform/{hosted_service}/{hosted_model}"),
        settings=settings_obj,
        total_frames=total_frames,
    )
    warnings = [
        "Hosted Stability mode generates keyframes through the public image API, then assembles and muxes the video locally.",
        "Hosted mode does not call a public Stability video endpoint because one was not found in the current public API spec.",
    ]
    if reason:
        warnings.insert(0, reason)
    if str(payload.get("temporal_mode") or "frame_img2img") == "frame_img2img":
        warnings.append("Frame img2img temporal mode is reduced to keyframe continuity in hosted mode to avoid per-frame API calls.")
    return {
        "ok": True,
        "mode": "hosted",
        "variant_index": variant_index,
        "model_id": hosted_model_id,
        "model_path": None,
        "duration_s": duration_s,
        "estimated_frames": total_frames,
        "estimated_keyframes": keyframes,
        "device": "hosted+local_ffmpeg",
        "hardware": hw,
        "tier_plan": tier_plan,
        "resume_existing_frames": bool(settings_obj.resume_existing_frames),
        "warnings": warnings,
        "cache": cache,
        "installed_internal_models": {
            "hf_sd15_internal": bool(models.installed_path("hf_sd15_internal")),
            "hf_sdxl_internal": bool(models.installed_path("hf_sdxl_internal")),
            "hf_sd35_medium_internal": bool(models.installed_path("hf_sd35_medium_internal")),
        },
        "hosted_provider": {
            "provider": "stability",
            "service": hosted_service,
            "model": hosted_model,
            "style_preset": hosted_style,
            "output_format": str(provider_cfg.get("output_format") or "png"),
            "allow_auto_fallback": bool(provider_cfg.get("allow_auto_fallback", True)),
        },
        "settings": {
            "fps_render": settings_obj.fps_render,
            "fps_output": settings_obj.fps_output,
            "width": settings_obj.width,
            "height": settings_obj.height,
            "interpolation_engine": settings_obj.interpolation_engine,
            "resume_existing_frames": settings_obj.resume_existing_frames,
            "render_mode": "hosted",
            "render_tier": settings_obj.render_tier,
        },
    }


def _internal_render_preflight_data(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    requested_mode = str(payload.get("render_mode") or "auto").strip().lower()
    if requested_mode == "proxy":
        return _proxy_render_preflight_data(project_id, payload, reason="Proxy mode requested explicitly.")
    if requested_mode == "hosted":
        return _hosted_render_preflight_data(project_id, payload)

    try:
        proj, variant, model_id, model_path, settings_obj = _resolve_internal_render_request(project_id, payload)
    except UserFacingError as e:
        if e.code in {"MODEL_NOT_INSTALLED", "DIRECTML_MODEL_UNSUPPORTED", "MODEL_UNSUPPORTED_FOR_HARDWARE"} and _hosted_stability_ready(payload):
            return _hosted_render_preflight_data(project_id, payload, reason=e.message)
        allow_proxy = bool(payload.get("allow_proxy_fallback", True))
        if allow_proxy and e.code in {"MODEL_NOT_INSTALLED", "MODEL_UNSUPPORTED_FOR_HARDWARE"}:
            return _proxy_render_preflight_data(project_id, payload, reason=e.message, requested_model_id=str(payload.get("model_id") or "auto"))
        raise

    scenes = variant.get("scenes") or []
    duration_s = _resolved_project_duration_s(proj, variant, scenes)
    fps_render = max(1, int(settings_obj.fps_render))
    total_frames = int(math.ceil(duration_s * fps_render))
    keyframes = max(1, len(_scene_keyframe_times(scenes, settings_obj.keyframe_interval_s)))
    hw = _hardware_profile()
    tier_plan = _build_internal_render_plan(hw, requested_tier=str(payload.get("render_tier") or settings_obj.render_tier or "auto"), duration_s=duration_s)
    tier_plan["chunk_plan"] = _build_render_chunk_plan(hw, applied_tier=str(tier_plan.get("applied_tier") or "draft"), duration_s=duration_s, total_frames=total_frames, fps_render=fps_render, render_mode="diffusion")
    warnings: list[str] = []
    if str(hw.get("backend") or "").lower() == "cpu":
        warnings.append("No GPU acceleration detected; internal diffusion will run on CPU and may be slow on longer renders.")
    elif str(hw.get("backend") or "").lower() == "mps":
        warnings.append("Apple Silicon acceleration detected; balanced settings are recommended for sustained laptop rendering.")
    elif str(hw.get("backend") or "").lower() == "directml":
        warnings.append("DirectML acceleration detected; SDXL and SD 1.5 are the supported AMD / Windows GPU paths.")
    if str(hw.get("backend") or "").lower() == "directml" and str(settings_obj.device_preference or "auto") == "cpu":
        warnings.append("The selected internal model is not DirectML-compatible, so this render will fall back to CPU.")
    if total_frames > 900:
        warnings.append("This render is long for the current FPS render setting; consider lowering FPS render or increasing keyframe interval.")
    if settings_obj.temporal_mode == "frame_img2img" and total_frames > 600:
        warnings.append("Frame img2img temporal mode is the most expensive mode for long clips.")
    if settings_obj.fps_render > settings_obj.fps_output:
        warnings.append("FPS render is higher than FPS output; you may be spending extra time on frames that will be blended down.")
    for note in list(tier_plan.get("notes") or []):
        if note not in warnings:
            warnings.append(str(note))
    timeline = proj.meta.get("timeline") or None
    cache = describe_internal_render_cache(
        project_dir=store.project_dir(project_id),
        variant_index=int(payload.get("variant_index", 0)),
        variant=variant,
        scenes=scenes,
        timeline=timeline if isinstance(timeline, dict) else None,
        model_dir=model_path,
        settings=settings_obj,
        total_frames=total_frames,
    )
    installed_internal = {
        "hf_sd15_internal": bool(models.installed_path("hf_sd15_internal")),
        "hf_sdxl_internal": bool(models.installed_path("hf_sdxl_internal")),
        "hf_sd35_medium_internal": bool(models.installed_path("hf_sd35_medium_internal")),
    }
    return {
        "ok": True,
        "mode": "diffusion",
        "variant_index": int(payload.get("variant_index", 0)),
        "model_id": model_id,
        "model_path": str(model_path),
        "duration_s": duration_s,
        "estimated_frames": total_frames,
        "estimated_keyframes": keyframes,
        "device": str(tier_plan.get("device_preference") or hw.get("backend") or "cpu"),
        "hardware": hw,
        "tier_plan": tier_plan,
        "resume_existing_frames": bool(settings_obj.resume_existing_frames),
        "warnings": warnings,
        "cache": cache,
        "installed_internal_models": installed_internal,
        "settings": {
            "fps_render": settings_obj.fps_render,
            "fps_output": settings_obj.fps_output,
            "width": settings_obj.width,
            "height": settings_obj.height,
            "temporal_mode": settings_obj.temporal_mode,
            "interpolation_engine": settings_obj.interpolation_engine,
            "render_mode": "diffusion",
            "render_tier": settings_obj.render_tier,
            "device_preference": settings_obj.device_preference,
        },
    }


@app.post("/v1/projects/{project_id}/render/internal/preflight")
def render_internal_preflight(project_id: str, req: InternalVideoRenderRequest):
    return _internal_render_preflight_data(project_id, _request_payload(req))


@app.post("/v1/projects/{project_id}/render/storyboard_movie")
def render_storyboard_movie(project_id: str, req: StoryboardMovieRenderRequest):
    """Queue a full MP4 from the saved AI storyboard and audio-reactive timeline."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    _storyboard_analysis_features(proj)
    _variant, scenes, duration_s = _storyboard_variant_for_render(proj, int(req.variant_index or 0))
    if req.apply_storyboard:
        _apply_plan_to_project_timeline(
            proj,
            variant_index=int(req.variant_index or 0),
            overwrite=bool(req.overwrite_timeline),
        )
    timeline = _ensure_storyboard_movie_timeline(
        proj,
        variant_index=int(req.variant_index or 0),
        overwrite=bool(req.overwrite_timeline),
        fps=int(req.fps_output or 24),
    )
    store.save(proj)

    payload = _storyboard_movie_payload_from_request(proj, req, duration_s=duration_s)
    preflight = _internal_render_preflight_data(project_id, payload)
    payload["render_mode"] = str(preflight.get("mode") or payload.get("render_mode") or "auto")
    storyboard_meta = payload.setdefault("storyboard_movie", {})
    storyboard_meta.update(
        {
            "scene_count": len(scenes),
            "duration_s": duration_s,
            "audio_reactive": True,
            "timeline": timeline.get("storyboard_movie"),
            "preflight_mode": preflight.get("mode"),
        }
    )

    job = jobs.create(project_id, "internal_video", payload)
    job.progress = {
        "stage": "queued",
        "current": 0,
        "total": max(1, int(preflight.get("estimated_frames", 1)) + 3),
        "percent": 0.0,
        "message": f"Queued audio-reactive storyboard movie ({preflight.get('mode') or 'auto'})",
    }
    jobs.save(job)

    latest = store.get(project_id) or proj
    latest.meta.setdefault("jobs", []).append(job.__dict__)
    latest.meta["last_storyboard_movie"] = {
        "job_id": job.id,
        "queued_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "variant_index": int(req.variant_index or 0),
        "scene_count": len(scenes),
        "duration_s": duration_s,
        "render_mode": payload["render_mode"],
        "audio_reactive": True,
        "preflight": {
            "mode": preflight.get("mode"),
            "model_id": preflight.get("model_id"),
            "estimated_frames": preflight.get("estimated_frames"),
            "duration_s": preflight.get("duration_s"),
        },
    }
    store.save(latest)

    return {
        "ok": True,
        "mode": "storyboard_movie",
        "render_mode": payload["render_mode"],
        "job": job.__dict__,
        "preflight": preflight,
        "storyboard": {
            "variant_index": int(req.variant_index or 0),
            "scene_count": len(scenes),
            "duration_s": duration_s,
            "audio_reactive": True,
            "timeline": timeline.get("storyboard_movie"),
        },
    }


@app.post("/v1/projects/{project_id}/render/comfyui/motion_scenes")
def render_motion_scenes(project_id: str, req: RenderMotionRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")

    variants = plan["variants"]
    if req.variant_index < 0 or req.variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    variant = variants[req.variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise HTTPException(400, "Selected variant has no scenes")

    created = []
    resolved_loras = _normalize_render_loras(getattr(req, "loras", []))
    vae_name = _resolve_optional_comfy_asset_name(req.vae, folder="vae", allowed_kinds={"vae"})
    motion_selection = _resolve_comfy_motion_selection(
        model_id=req.model_id,
        checkpoint=req.checkpoint,
        svd_model_id=req.svd_model_id,
        svd_checkpoint=req.svd_checkpoint,
    )
    checkpoint = str(motion_selection.get("checkpoint") or settings.comfyui_checkpoint)
    svd_checkpoint = str(motion_selection.get("svd_checkpoint") or req.svd_checkpoint or "svd_xt.safetensors")
    model_tag = _safe_name_tag(req.model_id or checkpoint)
    svd_tag = _safe_name_tag(req.svd_model_id or svd_checkpoint or "svd")
    for idx, sc in enumerate(scenes):
        start = float(sc.get("start_s", idx * 5))
        end = float(sc.get("end_s", start + 5))
        duration_s = max(0.5, end - start)
        frames = max(1, int(round(duration_s * req.fps)))
        frames = min(frames, int(req.max_frames_per_scene))

        # Practical caps for SVD (most setups use 14 or 25 frames)
        if req.engine == "svd":
            frames = min(frames, 25)

        seed = int(req.seed) + idx if req.seed is not None else _stable_seed(project_id, req.variant_index, idx)
        pdir = store.project_dir(project_id)
        frames_dir = pdir / "outputs" / "frames" / f"v{req.variant_index:02d}" / f"scene{idx:03d}" / f"{req.engine}_{model_tag}_{svd_tag}_seed{seed}"
        out_clip = pdir / "outputs" / "clips" / f"v{req.variant_index:02d}_scene{idx:03d}_{req.engine}_{model_tag}_{svd_tag}_seed{seed}.mp4"
        p = {
            "variant_index": req.variant_index,
            "scene_index": idx,
            "model_id": req.model_id,
            "svd_model_id": req.svd_model_id,
            "prompt": sc.get("prompt") or "",
            "negative_prompt": req.negative_prompt,
            "seed": seed,
            "width": req.width,
            "height": req.height,
            "steps": req.steps,
            "cfg": req.cfg,
            "sampler": req.sampler,
            "checkpoint": checkpoint,
            "fps": req.fps,
            "frames": frames,
            "engine": req.engine,
            "frames_dir": str(frames_dir),
            "out_clip": str(out_clip),
            "loras": resolved_loras,
            "vae": vae_name,
            "motion_model_name": req.motion_model_name,
            "context_length": req.context_length,
            "context_overlap": req.context_overlap,
            "beta_schedule": req.beta_schedule,
            "svd_checkpoint": svd_checkpoint,
            "svd_num_steps": req.svd_num_steps,
            "svd_motion_bucket_id": req.svd_motion_bucket_id,
            "svd_fps_id": req.svd_fps_id,
            "svd_cond_aug": req.svd_cond_aug,
            "svd_decoding_t": req.svd_decoding_t,
            "device": req.device,
        }
        job = jobs.create(project_id, "comfyui_motion_scene", p)
        created.append(job.__dict__)

    proj.meta.setdefault("jobs", []).extend(created)
    store.save(proj)

    return {"ok": True, "enqueued": len(created), "jobs": created}


def _preset_defaults(preset: str) -> dict[str, Any]:
    p = (preset or "balanced").lower().strip()
    if p not in ("fast", "balanced", "quality", "ultra"):
        p = "balanced"

    if p == "fast":
        return {"stills": {"width": 640, "height": 360, "steps": 12, "cfg": 6.0, "sampler": "euler"}, "motion": {"fps": 10, "max_frames": 36}}
    if p == "quality":
        return {"stills": {"width": 896, "height": 504, "steps": 26, "cfg": 7.0, "sampler": "euler"}, "motion": {"fps": 12, "max_frames": 60}}
    if p == "ultra":
        return {"stills": {"width": 1024, "height": 576, "steps": 30, "cfg": 7.5, "sampler": "euler"}, "motion": {"fps": 12, "max_frames": 72}}
    # balanced
    return {"stills": {"width": 768, "height": 432, "steps": 20, "cfg": 6.5, "sampler": "euler"}, "motion": {"fps": 12, "max_frames": 48}}


def _internal_diffusion_runtime_status() -> dict[str, Any]:
    try:
        import diffusers  # type: ignore  # noqa: F401
        import torch  # type: ignore  # noqa: F401
        diagnostics = ["internal_runtime=ready"]
        directml = _directml_runtime_status()
        if directml.get("runtime_ready"):
            diagnostics.append("directml_runtime=ready")
        return {"ok": True, "diagnostics": diagnostics}
    except Exception as e:
        return {"ok": False, "error": str(e), "diagnostics": ["internal_runtime=missing"]}


def _recommend_local_fallback(project_id: str, preset: str, *, reason: str) -> dict[str, Any]:
    hw = _hardware_profile()
    provider_status = _render_provider_status(hw)
    directml_status = dict(provider_status.get("directml") or {})
    if str(hw.get("backend") or "").lower() == "directml" and not bool(directml_status.get("enabled", True)):
        hw = dict(hw)
        hw["backend"] = "cpu"
        hw["device"] = "cpu"
        hw["backend_family"] = "cpu_only"
        hw["device_preference"] = "cpu"
        hw["available_backends"] = [b for b in list(hw.get("available_backends") or []) if str(b).lower() != "directml"]
    preset_l = str(preset or "balanced").lower().strip()
    requested_tier = "draft" if preset_l == "fast" else ("quality" if preset_l in ("quality", "ultra") else "auto")
    tier_plan = _build_internal_render_plan(hw, requested_tier=requested_tier)
    preferred = str(tier_plan.get("preferred_internal_model") or hw.get("preferred_internal_model") or "hf_sd15_internal")
    if str(tier_plan.get("device_preference") or "auto") == "directml":
        fallbacks = [preferred, "hf_sdxl_internal", "hf_sd15_internal"]
    else:
        fallbacks = [preferred, "hf_sd15_internal", "hf_sdxl_internal"]
    runtime = _internal_diffusion_runtime_status()
    picked = next((mid for mid in fallbacks if models.installed_path(mid)), None)
    if picked and runtime.get("ok"):
        return {
            "mode": "internal",
            "engine": "diffusion",
            "model_id": picked,
            "reason": f"{reason} Falling back to local internal render.",
            "diagnostics": ["comfyui=unavailable", f"internal_model={picked}", *list(runtime.get("diagnostics") or [])],
            "tier_plan": tier_plan,
        }
    if _hosted_stability_ready({"allow_hosted_fallback": True}):
        stability = provider_status.get("stability") or {}
        diagnostics = ["comfyui=unavailable", "hosted_stability=ready", *list(runtime.get("diagnostics") or [])]
        return {
            "mode": "hosted",
            "engine": "stability",
            "model_id": f"stability:{stability.get('service')}:{stability.get('model')}",
            "reason": f"{reason} Falling back to hosted Stability keyframes.",
            "diagnostics": diagnostics,
            "tier_plan": tier_plan,
            "hosted_provider": stability,
        }
    diagnostics = ["comfyui=unavailable"]
    if picked:
        diagnostics.append(f"internal_model={picked}")
    else:
        diagnostics.append("internal_models=missing")
    diagnostics.extend(list(runtime.get("diagnostics") or []))
    proxy_reason = reason
    if picked and not runtime.get("ok"):
        proxy_reason = f"{reason} Internal diffusion runtime is not installed."
    return {
        "mode": "proxy",
        "engine": "proxy",
        "model_id": "proxy_draft",
        "reason": f"{proxy_reason} Falling back to proxy draft render.",
        "diagnostics": diagnostics + [f"project={project_id}"],
        "tier_plan": tier_plan,
    }


def _recommend_pipeline(project_id: str, preset: str, mode: str = "auto", engine: str = "auto") -> dict[str, Any]:
    ckpt, _fallback_from = _resolve_comfy_checkpoint_name(settings.comfyui_checkpoint, allow_auto_fallback=True)
    mode_l = (mode or "auto").lower().strip()
    engine_l = (engine or "auto").lower().strip()

    if mode_l == "internal":
        return _recommend_local_fallback(project_id, preset, reason="Internal mode requested.")

    # Basic availability (any healthy node)
    base_diag = comfy_pool.diagnose({"checkpoint": ckpt})
    base_ok = bool(base_diag["compatible"] or base_diag["busy_compatible"])
    if not base_ok:
        if mode_l == "auto":
            return _recommend_local_fallback(project_id, preset, reason="ComfyUI is not reachable.")
        raise UserFacingError(
            message="ComfyUI is not reachable (no healthy nodes).",
            hint="Start ComfyUI, then confirm EDMG_COMFYUI_URL points to it (default http://127.0.0.1:8188).",
            code="COMFYUI_UNREACHABLE",
            status_code=502,
        )

    # Motion capabilities
    ad_req = {"checkpoint": ckpt, "node_classes": ["ADE_StandardStaticContextOptions", "ADE_AnimateDiffLoaderGen1"], "est_steps": 20, "est_frames": 24}
    svd_req = {"checkpoint": ckpt, "node_classes": ["SVDSimpleImg2Vid"], "est_steps": 20, "est_frames": 14}
    ad_diag = comfy_pool.diagnose(ad_req)
    svd_diag = comfy_pool.diagnose(svd_req)
    ad_ok = bool(ad_diag["compatible"] or ad_diag["busy_compatible"])
    svd_ok = bool(svd_diag["compatible"] or svd_diag["busy_compatible"])

    diagnostics = [
        f"healthy_nodes={len(base_diag['compatible']) + len(base_diag['busy_compatible'])}",
        f"animatediff_nodes={len(ad_diag['compatible']) + len(ad_diag['busy_compatible'])}",
        f"svd_nodes={len(svd_diag['compatible']) + len(svd_diag['busy_compatible'])}",
    ]

    preset_l = (preset or "balanced").lower().strip()

    # Fast preset intentionally forces stills unless user overrides in Advanced.
    if preset_l == "fast" and mode_l == "auto":
        return {"mode": "stills", "engine": None, "reason": "Fast preset uses stills for speed.", "diagnostics": diagnostics}

    if mode_l == "stills":
        return {"mode": "stills", "engine": None, "reason": "Forced stills mode.", "diagnostics": diagnostics}

    # motion desired (auto or forced)
    chosen = None
    if engine_l in ("auto", "animatediff") and ad_ok:
        chosen = "animatediff"
    elif engine_l in ("auto", "svd") and svd_ok:
        chosen = "svd"
    elif ad_ok:
        chosen = "animatediff"
    elif svd_ok:
        chosen = "svd"

    if chosen:
        return {"mode": "motion", "engine": chosen, "reason": "Motion-capable node detected.", "diagnostics": diagnostics}

    # fallback
    return {"mode": "stills", "engine": None, "reason": "No motion-capable nodes detected; falling back to stills.", "diagnostics": diagnostics}


@app.get("/v1/projects/{project_id}/pipeline/validate")
def validate_pipeline(project_id: str, variant_index: int = 0, preset: str = "balanced", mode: str = "auto", engine: str = "auto"):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")
    rec = _recommend_pipeline(project_id, preset=preset, mode=mode, engine=engine)
    return {"ok": True, "recommended": rec, "hardware": _hardware_profile()}


@app.post("/v1/projects/{project_id}/render/conductor/plan")
def render_conductor_plan(project_id: str, req: RenderConductorPlanRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")

    visual_dna = _load_project_visual_dna(proj)
    intent = _build_render_conductor_intent(project_id, proj, req)
    snapshot = _build_project_snapshot(proj, dna=visual_dna)
    environment = _build_render_conductor_environment()
    advisory_plan = build_advisory_render_plan(intent, snapshot, environment=environment)
    return {
        "ok": True,
        "intent": intent.model_dump(mode="json"),
        "plan": advisory_plan.model_dump(mode="json"),
        "environment": environment,
        "visual_dna_hints": build_visual_dna_prompt_hints(visual_dna),
    }


@app.get("/v1/projects/{project_id}/unreal/preview")
def get_unreal_bridge_preview(project_id: str, variant_index: int = 0):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    variants = plan.get("variants") if isinstance(plan, dict) and isinstance(plan.get("variants"), list) else []
    if not variants:
        raise HTTPException(400, "No plan generated")
    vi = int(variant_index or 0)
    if vi < 0 or vi >= len(variants):
        raise HTTPException(400, "Invalid variant_index")

    preview = UnrealBridgePreviewResponse.model_validate(
        build_unreal_bridge_preview(
            project_id=str(proj.id),
            project_name=str(getattr(proj, "name", "") or "") or None,
            analysis=(proj.meta.get("analysis") or {}) if isinstance(proj.meta, dict) else {},
            plan=plan if isinstance(plan, dict) else {},
            timeline=(proj.meta.get("timeline") or {}) if isinstance(proj.meta, dict) else {},
            variant_index=vi,
        )
    )
    return {"ok": True, "preview": preview.model_dump(mode="json")}


@app.post("/v1/projects/{project_id}/pipeline/run")
def run_pipeline(project_id: str, variant_index: int = 0, preset: str = "balanced", mode: str = "auto", engine: str = "auto"):
    """Enqueue an end-to-end pipeline: render (auto stills/motion) -> assemble final MP4.

    This endpoint is designed for one-click UX. It keeps full functionality internally.
    """
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")

    mode_l = (mode or "auto").lower().strip()
    if mode_l == "internal":
        preset_l = str(preset or "balanced").lower().strip()
        requested_tier = "draft" if preset_l == "fast" else ("quality" if preset_l in ("quality", "ultra") else "auto")
        hw = _hardware_profile()
        provider_status = _render_provider_status(hw)
        tier_plan = _build_internal_render_plan(hw, requested_tier=requested_tier)
        tier_defaults = dict(tier_plan.get("defaults") or {})
        device_preference = str(tier_plan.get("device_preference") or "auto")
        if device_preference == "directml" and not bool((provider_status.get("directml") or {}).get("enabled", True)):
            device_preference = "cpu"
        internal_req = InternalVideoRenderRequest(
            variant_index=variant_index,
            fps_output=int(tier_defaults.get("fps_output", 24)),
            fps_render=int(tier_defaults.get("fps_render", 2)),
            width=int(tier_defaults.get("width", 768)),
            height=int(tier_defaults.get("height", 432)),
            steps=int(tier_defaults.get("steps", 15)),
            cfg=float(tier_defaults.get("cfg", 7.0)),
            keyframe_interval_s=float(tier_defaults.get("keyframe_interval_s", 5.0)),
            interpolation_engine=str(tier_defaults.get("interpolation_engine", os.getenv("EDMG_INTERPOLATION_ENGINE", "auto"))),
            model_id=os.getenv("EDMG_INTERNAL_MODEL_ID", "auto"),
            render_mode="auto",
            render_tier=str(tier_plan.get("applied_tier") or requested_tier),
            device_preference=device_preference,
            temporal_mode=str(tier_defaults.get("temporal_mode", "frame_img2img")),
            temporal_steps=int(tier_defaults.get("temporal_steps", 12)),
            refine_every_n_frames=int(tier_defaults.get("refine_every_n_frames", 1)),
            anchor_strength=float(tier_defaults.get("anchor_strength", 0.20)),
            prompt_blend=bool(tier_defaults.get("prompt_blend", True)),
            allow_proxy_fallback=True,
        )
        res = render_internal_video(project_id, internal_req)
        return {"ok": True, "mode": str(res.get("preflight", {}).get("mode") or "internal"), "job": res.get("job"), "preflight": res.get("preflight")}

    defaults = _preset_defaults(preset)
    rec = _recommend_pipeline(project_id, preset=preset, mode=mode, engine=engine)

    if rec["mode"] in ("internal", "proxy", "hosted"):
        hw = _hardware_profile()
        provider_status = _render_provider_status(hw)
        tier_plan = dict(rec.get("tier_plan") or _build_internal_render_plan(hw, requested_tier=("draft" if preset == "fast" else ("quality" if preset in ("quality", "ultra") else "auto"))))
        tier_defaults = dict(tier_plan.get("defaults") or {})
        device_preference = str(tier_plan.get("device_preference") or "auto")
        if device_preference == "directml" and not bool((provider_status.get("directml") or {}).get("enabled", True)):
            device_preference = "cpu"
        internal_req = InternalVideoRenderRequest(
            variant_index=variant_index,
            fps_output=int(tier_defaults.get("fps_output", 24)),
            fps_render=int(tier_defaults.get("fps_render", 2)),
            width=int(tier_defaults.get("width", defaults["stills"]["width"])),
            height=int(tier_defaults.get("height", defaults["stills"]["height"])),
            steps=int(tier_defaults.get("steps", defaults["stills"]["steps"])),
            cfg=float(tier_defaults.get("cfg", defaults["stills"]["cfg"])),
            keyframe_interval_s=float(tier_defaults.get("keyframe_interval_s", os.getenv("EDMG_INTERNAL_KEYFRAME_INTERVAL_S", "5.0"))),
            interpolation_engine=str(tier_defaults.get("interpolation_engine", os.getenv("EDMG_INTERPOLATION_ENGINE", "auto"))),
            model_id=str(rec.get("model_id") or os.getenv("EDMG_INTERNAL_MODEL_ID", "auto")),
            render_mode=("proxy" if rec["mode"] == "proxy" else ("hosted" if rec["mode"] == "hosted" else "auto")),
            render_tier=str(tier_plan.get("applied_tier") or "auto"),
            device_preference=device_preference,
            temporal_mode=str(tier_defaults.get("temporal_mode", "frame_img2img")),
            temporal_steps=int(tier_defaults.get("temporal_steps", 12)),
            refine_every_n_frames=int(tier_defaults.get("refine_every_n_frames", 1)),
            anchor_strength=float(tier_defaults.get("anchor_strength", 0.20)),
            prompt_blend=bool(tier_defaults.get("prompt_blend", True)),
            allow_hosted_fallback=True,
            allow_proxy_fallback=True,
        )
        res = render_internal_video(project_id, internal_req)
        effective_mode = str(res.get("preflight", {}).get("mode") or rec["mode"])
        selected = dict(rec)
        if effective_mode == "diffusion":
            selected["mode"] = "internal"
            selected["engine"] = "diffusion"
            selected["model_id"] = str(res.get("preflight", {}).get("model_id") or selected.get("model_id") or "auto")
        elif effective_mode in {"proxy", "hosted"}:
            selected["mode"] = effective_mode
        return {
            "ok": True,
            "preset": preset,
            "selected": selected,
            "render_mode": effective_mode,
            "job": res.get("job"),
            "preflight": res.get("preflight"),
        }

    if rec["mode"] == "stills":
        req = RenderScenesRequest(
            variant_index=variant_index,
            negative_prompt="(low quality, worst quality)",
            width=int(defaults["stills"]["width"]),
            height=int(defaults["stills"]["height"]),
            steps=int(defaults["stills"]["steps"]),
            cfg=float(defaults["stills"]["cfg"]),
            sampler=str(defaults["stills"]["sampler"]),
        )
        enq = render_scenes(project_id, req)
        assemble_fps = 24
    else:
        eng = rec["engine"] or "animatediff"
        req = RenderMotionRequest(
            variant_index=variant_index,
            negative_prompt="(low quality, worst quality)",
            width=int(defaults["stills"]["width"]),
            height=int(defaults["stills"]["height"]),
            steps=int(defaults["stills"]["steps"]),
            cfg=float(defaults["stills"]["cfg"]),
            sampler=str(defaults["stills"]["sampler"]),
            fps=int(defaults["motion"]["fps"]),
            max_frames_per_scene=int(defaults["motion"]["max_frames"]),
            engine=eng,
            motion_model_name="mm_sd_v15_v2.ckpt",
            context_length=16,
            context_overlap=4,
            beta_schedule="autoselect",
            svd_checkpoint="svd_xt.safetensors",
            svd_num_steps=25,
            svd_motion_bucket_id=127,
            svd_fps_id=6,
            svd_cond_aug=0.02,
            svd_decoding_t=14,
            device="cuda",
        )
        enq = render_motion_scenes(project_id, req)
        assemble_fps = int(defaults["motion"]["fps"])

    assemble_job = jobs.create(project_id, "assemble_variant", {"variant_index": variant_index, "fps": assemble_fps})
    return {
        "ok": True,
        "preset": preset,
        "selected": rec,
        "render_enqueued": enq.get("enqueued"),
        "assemble_job": assemble_job.__dict__,
    }


@app.get("/v1/projects/{project_id}/assets")
def list_assets(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)
    assets = {"audio": [], "refs": []}
    audio_dir = pdir / "assets" / "audio"
    if audio_dir.exists():
        for p in sorted(audio_dir.glob("*") ):
            if p.is_file():
                assets["audio"].append({"path": str(p.relative_to(pdir))})
    refs_dir = pdir / "assets" / "refs"
    if refs_dir.exists():
        for p in sorted(refs_dir.glob("*") ):
            if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                assets["refs"].append({"path": str(p.relative_to(pdir))})
    return {"project_id": project_id, "assets": assets}


if HAS_MULTIPART:
    @app.post("/v1/projects/{project_id}/assets/refs")
    async def upload_ref(project_id: str, file: UploadFile = File(...)):
        proj = store.get(project_id)
        if not proj:
            raise HTTPException(404, "Project not found")
        pdir = store.project_dir(project_id)
        refs_dir = pdir / "assets" / "refs"
        refs_dir.mkdir(parents=True, exist_ok=True)
        name = (file.filename or "ref.png").replace("\\", "_").replace("/", "_")
        out = refs_dir / name
        data = await file.read()
        out.write_bytes(data)
        proj.meta.setdefault("assets", {}).setdefault("refs", []).append(str(out.relative_to(pdir)))
        store.save(proj)
        return {"ok": True, "path": str(out)}
else:
    @app.post("/v1/projects/{project_id}/assets/refs")
    async def upload_ref(project_id: str):
        _require_multipart()


@app.get("/v1/projects/{project_id}/export/comfyui_workflows")
def export_comfyui_workflows(
    project_id: str,
    variant_index: int = 0,
    model_id: str | None = None,
    workflow_family: str = "auto",
    source_asset: str | None = None,
    reference_asset: str | None = None,
    inpaint_mask: str | None = None,
    controlnet_model: str | None = None,
    conditioning_mode: str = "raw",
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    cfg: float | None = None,
    sampler: str | None = None,
    negative_prompt: str | None = None,
    seed: int | None = None,
    denoise_strength: float | None = None,
    loras_json: str | None = None,
    outpaint_json: str | None = None,
    controlnet_units_json: str | None = None,
    hires_fix_json: str | None = None,
    refiner_json: str | None = None,
    upscaler: str | None = None,
):
    """Compile plan scenes into per-scene ComfyUI workflow JSON files."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")
    variants = plan["variants"]
    if variant_index < 0 or variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")
    variant = variants[variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise HTTPException(400, "Selected variant has no scenes")

    out_dir = store.project_dir(project_id) / "outputs" / "comfyui_workflows" / f"variant_{variant_index:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_files = []
    loras = []
    if loras_json:
        try:
            parsed_loras = json.loads(loras_json)
        except Exception:
            parsed_loras = []
        loras = _normalize_render_loras(parsed_loras)

    raw_controlnet_units: list[dict[str, Any]] = []
    if controlnet_units_json:
        try:
            parsed_units = json.loads(controlnet_units_json)
        except Exception:
            parsed_units = []
        if isinstance(parsed_units, list):
            raw_controlnet_units = [dict(item) for item in parsed_units if isinstance(item, dict)]
    parsed_outpaint = None
    if outpaint_json:
        try:
            parsed_outpaint = json.loads(outpaint_json)
        except Exception:
            raise UserFacingError(
                "Invalid outpaint settings",
                hint="Retry the export after re-entering the outpaint margins.",
                code="OUTPAINT_INVALID",
                status_code=400,
            )
    parsed_hires_fix = None
    if hires_fix_json:
        try:
            parsed_hires_fix = json.loads(hires_fix_json)
        except Exception:
            raise UserFacingError(
                "Invalid hires-fix settings",
                hint="Retry the export after re-entering the hires-fix controls.",
                code="HIRES_FIX_INVALID",
                status_code=400,
            )
    parsed_refiner = None
    if refiner_json:
        try:
            parsed_refiner = json.loads(refiner_json)
        except Exception:
            raise UserFacingError(
                "Invalid refiner settings",
                hint="Retry the export after re-entering the refiner controls.",
                code="REFINER_INVALID",
                status_code=400,
            )
    if isinstance(parsed_refiner, dict):
        refiner_model = str(parsed_refiner.get("model") or "").strip()
        if refiner_model:
            parsed_refiner["checkpoint"] = _resolve_optional_comfy_asset_name(
                refiner_model,
                folder="checkpoints",
                allowed_kinds={"checkpoint"},
            )
    if workflow_family == "controlnet" and not raw_controlnet_units and controlnet_model and reference_asset:
        raw_controlnet_units = [
            {
                "model": controlnet_model,
                "reference_asset": reference_asset,
                "conditioning_mode": conditioning_mode,
                "strength": 0.8,
            }
        ]

    selection = _resolve_still_scene_selection(
        model_id=model_id,
        checkpoint=None,
        workflow_family=workflow_family,
        controlnet_model=controlnet_model,
        reference_asset=reference_asset,
        conditioning_mode=conditioning_mode,
        controlnet_units=raw_controlnet_units,
    )

    if str(selection.get("engine") or "comfyui") != "comfyui":
        raise UserFacingError(
            "ComfyUI workflow export only supports ComfyUI still models.",
            hint="Pick a checkpoint-based still model before exporting ComfyUI workflows.",
            code="EXPORT_ENGINE_UNSUPPORTED",
            status_code=400,
        )

    workflow_kind = str(selection.get("workflow_family") or "txt2img")
    controlnet_units = _normalize_controlnet_units(
        raw_controlnet_units,
        engine="comfyui",
        family=selection.get("family"),
    )
    if workflow_kind == "controlnet" and not controlnet_units:
        raise UserFacingError(
            "No compatible ControlNet units were selected",
            hint="Attach one or more compatible ControlNet units before exporting the workflow.",
            code="CONTROLNET_MISSING",
            status_code=400,
        )

    def _copy_export_asset(src: Path, folder: str) -> str:
        target_dir = out_dir / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / src.name
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
        return str(Path(folder) / target.name).replace("\\", "/")

    prepared_assets = {
        "source_path": None,
        "mask_path": None,
        "mask_source": None,
        "outpaint": None,
        "width": int(width or 0),
        "height": int(height or 0),
    }
    if workflow_kind in {"img2img", "inpaint", "outpaint"}:
        prepared_assets = _prepare_still_scene_assets(
            project_id,
            {
                "source_asset": source_asset,
                "reference_asset": reference_asset,
                "inpaint_mask": inpaint_mask,
                "outpaint": parsed_outpaint,
                "width": width,
                "height": height,
            },
            workflow_kind,
        )

    exported_source_image = (
        _copy_export_asset(Path(str(prepared_assets["source_path"])), "inputs")
        if prepared_assets.get("source_path")
        else None
    )
    exported_mask_image = (
        _copy_export_asset(Path(str(prepared_assets["mask_path"])), "masks")
        if prepared_assets.get("mask_path")
        else None
    )

    exported_controlnet_units: list[dict[str, Any]] = []
    if workflow_kind == "controlnet":
        for unit in controlnet_units:
            ref_path = _resolve_project_reference_path(project_id, str(unit.get("reference_asset") or ""))
            if ref_path is None:
                raise UserFacingError(
                    "Reference image not found",
                    hint="Upload or choose a valid project reference image before exporting the ControlNet workflow.",
                    code="REFERENCE_IMAGE_NOT_FOUND",
                    status_code=400,
                )
            conditioned = _prepare_condition_image(project_id, ref_path, str(unit.get("conditioning_mode") or "raw"))
            exported_controlnet_units.append(
                {
                    **unit,
                    "reference_image": _copy_export_asset(conditioned, "refs"),
                }
            )

    for idx, sc in enumerate(scenes):
        checkpoint = str(selection.get("checkpoint") or sc.get("checkpoint") or settings.comfyui_checkpoint)
        resolved_seed = int(seed if seed is not None else (sc.get("seed") or (idx + 12345)))
        resolved_width = int(prepared_assets.get("width") or width or sc.get("width") or 768)
        resolved_height = int(prepared_assets.get("height") or height or sc.get("height") or 432)
        resolved_steps = int(steps or sc.get("steps") or 20)
        resolved_cfg = float(cfg if cfg is not None else (sc.get("cfg") or 6.5))
        resolved_sampler = str(sampler or sc.get("sampler") or "euler")
        resolved_negative = str(negative_prompt or sc.get("negative_prompt") or "(low quality, worst quality)")
        resolved_denoise = float(denoise_strength if denoise_strength is not None else 0.75)

        if workflow_kind == "controlnet":
            wf = comfy.controlnet_workflow(
                checkpoint=checkpoint,
                prompt=str(sc.get("prompt") or ""),
                negative_prompt=resolved_negative,
                seed=resolved_seed,
                width=resolved_width,
                height=resolved_height,
                steps=resolved_steps,
                cfg=resolved_cfg,
                sampler=resolved_sampler,
                controlnet_name=str(exported_controlnet_units[0].get("controlnet_name") or selection.get("controlnet_name") or ""),
                reference_image=str(exported_controlnet_units[0].get("reference_image") or "reference.png"),
                controlnet_strength=0.8,
                start_percent=float(exported_controlnet_units[0].get("start_percent", 0.0) if exported_controlnet_units else 0.0),
                end_percent=float(exported_controlnet_units[0].get("end_percent", 1.0) if exported_controlnet_units else 1.0),
                filename_prefix=f"scene_{idx:03d}",
                loras=loras,
                controlnet_units=exported_controlnet_units,
                hires_fix=parsed_hires_fix,
                refiner=parsed_refiner,
                upscaler=upscaler,
            )
        elif workflow_kind == "img2img":
            wf = comfy.img2img_workflow(
                checkpoint=checkpoint,
                prompt=str(sc.get("prompt") or ""),
                negative_prompt=resolved_negative,
                seed=resolved_seed,
                width=resolved_width,
                height=resolved_height,
                steps=resolved_steps,
                cfg=resolved_cfg,
                sampler=resolved_sampler,
                source_image=str(exported_source_image or "source.png"),
                denoise_strength=resolved_denoise,
                filename_prefix=f"scene_{idx:03d}",
                loras=loras,
                hires_fix=parsed_hires_fix,
                refiner=parsed_refiner,
                upscaler=upscaler,
            )
        elif workflow_kind == "inpaint":
            wf = comfy.inpaint_workflow(
                checkpoint=checkpoint,
                prompt=str(sc.get("prompt") or ""),
                negative_prompt=resolved_negative,
                seed=resolved_seed,
                width=resolved_width,
                height=resolved_height,
                steps=resolved_steps,
                cfg=resolved_cfg,
                sampler=resolved_sampler,
                source_image=str(exported_source_image or "source.png"),
                mask_image=str(exported_mask_image or "mask.png"),
                denoise_strength=float(denoise_strength if denoise_strength is not None else 0.8),
                filename_prefix=f"scene_{idx:03d}",
                loras=loras,
                hires_fix=parsed_hires_fix,
                refiner=parsed_refiner,
                upscaler=upscaler,
            )
        elif workflow_kind == "outpaint":
            wf = comfy.outpaint_workflow(
                checkpoint=checkpoint,
                prompt=str(sc.get("prompt") or ""),
                negative_prompt=resolved_negative,
                seed=resolved_seed,
                width=resolved_width,
                height=resolved_height,
                steps=resolved_steps,
                cfg=resolved_cfg,
                sampler=resolved_sampler,
                source_image=str(exported_source_image or "source.png"),
                mask_image=str(exported_mask_image or "mask.png"),
                denoise_strength=float(denoise_strength if denoise_strength is not None else 0.8),
                filename_prefix=f"scene_{idx:03d}",
                loras=loras,
                hires_fix=parsed_hires_fix,
                refiner=parsed_refiner,
                upscaler=upscaler,
            )
        else:
            wf = comfy.default_workflow(
                checkpoint=checkpoint,
                prompt=str(sc.get("prompt") or ""),
                negative_prompt=resolved_negative,
                seed=resolved_seed,
                width=resolved_width,
                height=resolved_height,
                steps=resolved_steps,
                cfg=resolved_cfg,
                sampler=resolved_sampler,
                loras=loras,
                hires_fix=parsed_hires_fix,
                refiner=parsed_refiner,
                upscaler=upscaler,
            )
        p = out_dir / f"scene_{idx:03d}.json"
        p.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        out_files.append(str(p.relative_to(store.project_dir(project_id))))

    proj.meta.setdefault("exports", {}).setdefault("comfyui", []).extend(out_files)
    store.save(proj)
    return {"ok": True, "files": out_files}

@app.post("/v1/projects/{project_id}/assemble_video")
def assemble_video(project_id: str, req: AssembleVideoRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")
    variants = plan["variants"]
    if req.variant_index < 0 or req.variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    variant = variants[req.variant_index]
    scenes = variant.get("scenes") or []

    pdir = store.project_dir(project_id)
    audio_meta = proj.meta.get("audio")
    audio_path = None
    if audio_meta:
        audio_path = pdir / "assets" / "audio" / audio_meta["filename"]

    # Prefer motion clips if available
    clips_dir = pdir / "outputs" / "clips"
    clips = []
    if clips_dir.exists():
        clips = sorted([p for p in clips_dir.glob(f"v{req.variant_index:02d}_scene*.mp4") if p.is_file()])

    out_vid = pdir / "outputs" / "videos" / f"variant_{req.variant_index:02d}.mp4"
    out_vid.parent.mkdir(parents=True, exist_ok=True)

    if clips:
        # Assemble motion clips, then optionally interpolate FPS, then mux audio.
        raw_vid = out_vid.parent / f"{out_vid.stem}_raw.mp4"
        concat_videos(
            ffmpeg_path=settings.ffmpeg_path,
            video_paths=clips,
            out_mp4=raw_vid,
            audio_path=None
        )
        # Interpolate to requested FPS (best effort: RIFE -> minterpolate -> fps dup).
        interp_vid = out_vid.parent / f"{out_vid.stem}_interp_{req.fps}fps.mp4"
        interpolate_video_fps(
            ffmpeg_path=settings.ffmpeg_path,
            in_mp4=raw_vid,
            out_mp4=interp_vid,
            fps_out=int(req.fps),
            engine=os.getenv("EDMG_INTERPOLATION_ENGINE", "auto"),
        )
        if audio_path and audio_path.exists():
            mux_audio(ffmpeg_path=settings.ffmpeg_path, video_mp4=interp_vid, audio_path=audio_path, out_mp4=out_vid)
        else:
            out_vid.write_bytes(interp_vid.read_bytes())
        mode = "motion"
    else:
        out_images_dir = pdir / "outputs" / "images"
        imgs = sorted([p for p in out_images_dir.glob(f"v{req.variant_index:02d}_scene*") if p.suffix.lower() in (".png",".jpg",".jpeg",".webp")])
        if not imgs:
            raise HTTPException(400, "No rendered scene images found. Render scenes or motion scenes first.")

        durations = []
        for i in range(len(imgs)):
            if i < len(scenes):
                start = float(scenes[i].get("start_s", i*5))
                end = float(scenes[i].get("end_s", start+5))
                durations.append(max(0.5, end-start))
            else:
                durations.append(5.0)

        assemble_slideshow(
            ffmpeg_path=settings.ffmpeg_path,
            image_paths=imgs,
            durations_s=durations,
            out_mp4=out_vid,
            audio_path=audio_path,
            fps=req.fps
        )
        mode = "slideshow"

    proj.meta.setdefault("outputs", {}).setdefault("videos", []).append(str(out_vid.relative_to(pdir)))
    store.save(proj)

    return {"ok": True, "mode": mode, "video": str(out_vid)}



def _scene_schedule_to_prompts(variant: dict[str, Any], fps: int) -> dict[str, str]:
    scenes = variant.get("scenes") or []
    prompts: dict[str, str] = {}
    for i, sc in enumerate(scenes):
        start_s = float(sc.get("start_s", i * 5))
        frame = max(0, int(round(start_s * fps)))
        prompts[str(frame)] = str(sc.get("prompt") or "").strip() or "cinematic"
    if not prompts:
        prompts["0"] = "cinematic"
    return prompts


def _safe_export_bundle_stem(value: str, fallback: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return stem[:80] or fallback


UNREAL_RETURN_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
UNREAL_RETURN_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _unique_output_artifact_path(target_dir: Path, base_stem: str, suffix: str) -> Path:
    candidate = target_dir / f"{base_stem}{suffix}"
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        candidate = target_dir / f"{base_stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _build_unreal_return_metadata(
    *,
    project_id: str,
    output_path: Path,
    bundle_dir: str,
    source_path: str,
    media_kind: str,
    variant_index: int,
    sequence_name: str,
    manifest_path: str | None,
    source_manifest: dict[str, Any] | None,
    return_contract: dict[str, Any] | None,
    render_handoff: dict[str, Any] | None,
) -> dict[str, Any]:
    rel_output = _project_relative_path(project_id, output_path)
    source_bundle = source_manifest if isinstance(source_manifest, dict) else {}
    contract = return_contract if isinstance(return_contract, dict) else {}
    handoff = render_handoff if isinstance(render_handoff, dict) else {}
    return {
        "kind": "unreal_bridge_return",
        "project_id": project_id,
        "variant_index": int(variant_index or 0),
        "sequence_name": sequence_name,
        "bundle_dir": bundle_dir,
        "source_path": source_path,
        "manifest_path": manifest_path,
        "media_kind": media_kind,
        "output": {media_kind: rel_output},
        "source_bundle": {
            "export_family": str(source_bundle.get("export_family") or "unreal_bridge_bundle"),
            "created_at": source_bundle.get("created_at"),
        },
        "return_contract": {
            "return_owner": str(contract.get("return_owner") or "studio"),
            "assembly_mode": str(contract.get("assembly_mode") or "ffmpeg_back_in_studio"),
            "expected_outputs": list(contract.get("expected_outputs") or []),
        },
        "render_handoff": {
            "render_mode": str(handoff.get("render_mode") or ""),
            "return_owner": str(handoff.get("return_owner") or "studio"),
            "approved_section_ids": list(handoff.get("approved_section_ids") or []),
        },
        "captured_at": time.time(),
    }

@app.post("/v1/projects/{project_id}/export/deforum")
def export_deforum(project_id: str, req: ExportDeforumRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")
    variants = plan["variants"]
    if req.variant_index < 0 or req.variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    variant = variants[req.variant_index]
    safe_preset = str(req.preset or "cinematic")
    if safe_preset not in {"cinematic", "psychedelic", "ambient"}:
        safe_preset = "cinematic"
    creative_payload = _build_creative_direction_payload(
        proj,
        variant_index=req.variant_index,
        preset=safe_preset,
        sensitivity=float(req.sensitivity or 1.0),
    )
    preview_settings = (
        creative_payload.get("deforum_preview", {}).get("settings")
        if isinstance(creative_payload.get("deforum_preview"), dict)
        else {}
    )
    prompts = (
        dict(preview_settings.get("prompts") or {})
        if isinstance(preview_settings, dict) and isinstance(preview_settings.get("prompts"), dict)
        else _scene_schedule_to_prompts(variant, fps=req.fps)
    )

    # Use EDMG Core template if available; otherwise minimal
    try:
        from enhanced_deforum_music_generator.public_api import DeforumMusicGenerator, AudioAnalysis  # type: ignore
        gen = DeforumMusicGenerator()
        analysis = AudioAnalysis()
        settings_dict = gen.build_deforum_settings(analysis, {
            "W": req.width,
            "H": req.height,
            "fps": req.fps,
            "base_prompt": prompts.get("0", "cinematic"),
            "style_prompt": "",
        })
        settings_dict["prompts"] = prompts
    except Exception:
        settings_dict = {
            "W": req.width,
            "H": req.height,
            "fps": req.fps,
            "prompts": prompts,
            "note": "Install EDMG Core for full Deforum template output."
        }

    if isinstance(preview_settings, dict):
        for key in (
            "negative_prompts",
            "zoom",
            "angle",
            "translation_z",
            "cfg_scale_schedule",
            "strength_schedule",
            "contrast_schedule",
            "schedules",
        ):
            value = preview_settings.get(key)
            if value:
                settings_dict[key] = value
    settings_dict["W"] = req.width
    settings_dict["H"] = req.height
    settings_dict["fps"] = req.fps

    out_dir = store.project_dir(project_id) / "outputs" / "deforum"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"variant_{req.variant_index:02d}.deforum.json"
    out_path.write_text(json.dumps(settings_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    rel = str(out_path.relative_to(store.project_dir(project_id)))
    proj.meta.setdefault("exports", {}).setdefault("deforum", []).append(rel)
    store.save(proj)

    return {"ok": True, "path": rel}


@app.post("/v1/projects/{project_id}/export/unreal")
def export_unreal_bridge_bundle(project_id: str, req: ExportUnrealBridgeRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")
    variants = plan["variants"]
    if req.variant_index < 0 or req.variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    preview = UnrealBridgePreviewResponse.model_validate(
        build_unreal_bridge_preview(
            project_id=str(proj.id),
            project_name=str(getattr(proj, "name", "") or "") or None,
            analysis=(proj.meta.get("analysis") or {}) if isinstance(proj.meta, dict) else {},
            plan=plan if isinstance(plan, dict) else {},
            timeline=(proj.meta.get("timeline") or {}) if isinstance(proj.meta, dict) else {},
            variant_index=int(req.variant_index or 0),
        )
    )
    visual_dna = _load_project_visual_dna(proj)
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base_stem = _safe_export_bundle_stem(
        str(req.bundle_name or f"variant_{int(req.variant_index or 0):02d}_unreal_{stamp}"),
        f"unreal_bundle_{stamp}",
    )

    pdir = store.project_dir(project_id)
    out_root = pdir / "outputs" / "unreal"
    out_root.mkdir(parents=True, exist_ok=True)
    bundle_dir = out_root / base_stem
    suffix = 2
    while bundle_dir.exists():
        bundle_dir = out_root / f"{base_stem}_{suffix}"
        suffix += 1
    bundle_dir.mkdir(parents=True, exist_ok=True)

    payloads = build_unreal_bridge_export_payloads(
        project_id=str(proj.id),
        project_name=str(getattr(proj, "name", "") or "") or None,
        variant_index=int(req.variant_index or 0),
        preview=preview.model_dump(mode="json"),
        analysis=(proj.meta.get("analysis") or {}) if isinstance(proj.meta, dict) else {},
        visual_dna=visual_dna.model_dump(mode="json"),
        created_at=created_at,
    )
    for relative_name, payload in payloads.items():
        target = bundle_dir / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    zip_rel: str | None = None
    zip_path: Path | None = None
    if req.include_zip:
        zip_path = out_root / f"{bundle_dir.name}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in sorted(bundle_dir.rglob("*")):
                if file_path.is_file():
                    archive.write(file_path, arcname=str(file_path.relative_to(bundle_dir)))
        zip_rel = zip_path.relative_to(pdir).as_posix()

    manifest_rel = (bundle_dir / "bundle_manifest.json").relative_to(pdir).as_posix()
    bundle_rel = bundle_dir.relative_to(pdir).as_posix()
    export_entry = {
        "bundle_dir": bundle_rel,
        "manifest_path": manifest_rel,
        "zip_path": zip_rel,
        "created_at": created_at,
        "variant_index": int(req.variant_index or 0),
        "sequence_name": str(preview.shot_metadata_export.sequence_name),
        "files": sorted(payloads.keys()),
    }
    proj.meta.setdefault("exports", {}).setdefault("unreal", []).append(export_entry)
    store.save(proj)

    return {
        "ok": True,
        "bundle": export_entry,
    }


@app.post("/v1/projects/{project_id}/import/unreal")
def import_unreal_bridge_return(project_id: str, req: ImportUnrealBridgeReturnRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    pdir = store.project_dir(project_id)
    try:
        bundle_dir = safe_join(pdir, req.bundle_dir)
    except Exception:
        raise HTTPException(400, "Invalid bundle_dir")
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        raise HTTPException(404, "Unreal bundle not found")

    bundle_rel = bundle_dir.relative_to(pdir).as_posix()
    source_rel = str(req.source_dir or f"{bundle_rel}/returned").replace("\\", "/").strip()
    try:
        source_dir = safe_join(pdir, source_rel)
    except Exception:
        raise HTTPException(400, "Invalid source_dir")
    if not source_dir.exists() or not source_dir.is_dir():
        raise HTTPException(400, "Returned media folder not found")

    bundle_manifest_path = bundle_dir / "bundle_manifest.json"
    return_contract_path = bundle_dir / "return_contract.json"
    render_handoff_path = bundle_dir / "render_handoff.json"
    source_manifest = _read_json_dict(bundle_manifest_path)
    return_contract = _read_json_dict(return_contract_path)
    render_handoff = _read_json_dict(render_handoff_path)
    try:
        variant_index = int(source_manifest.get("variant_index") or 0)
    except Exception:
        variant_index = 0
    sequence_name = str(source_manifest.get("sequence_name") or bundle_dir.name)
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")

    imported_media: list[dict[str, Any]] = []
    for source_path in sorted(source_dir.rglob("*")):
        if not source_path.is_file():
            continue
        suffix = source_path.suffix.lower()
        media_kind = ""
        if suffix in UNREAL_RETURN_VIDEO_EXTENSIONS:
            media_kind = "video"
        elif suffix in UNREAL_RETURN_IMAGE_EXTENSIONS:
            media_kind = "image"
        if not media_kind:
            continue

        target_root = pdir / "outputs" / ("videos" if media_kind == "video" else "images")
        target_root.mkdir(parents=True, exist_ok=True)
        base_stem = _safe_export_bundle_stem(
            f"{bundle_dir.name}_{source_path.stem}",
            f"unreal_return_{media_kind}",
        )
        target_path = _unique_output_artifact_path(target_root, base_stem, suffix)
        shutil.copy2(source_path, target_path)

        source_rel_path = source_path.relative_to(pdir).as_posix()
        manifest_rel = bundle_manifest_path.relative_to(pdir).as_posix() if bundle_manifest_path.exists() else None
        metadata = _build_unreal_return_metadata(
            project_id=project_id,
            output_path=target_path,
            bundle_dir=bundle_rel,
            source_path=source_rel_path,
            media_kind=media_kind,
            variant_index=variant_index,
            sequence_name=sequence_name,
            manifest_path=manifest_rel,
            source_manifest=source_manifest,
            return_contract=return_contract,
            render_handoff=render_handoff,
        )
        metadata_path = _write_generation_metadata(target_path, metadata)
        imported_media.append(
            {
                "kind": media_kind,
                "path": target_path.relative_to(pdir).as_posix(),
                "source_path": source_rel_path,
                "metadata_path": metadata_path.relative_to(pdir).as_posix(),
            }
        )

    if not imported_media:
        raise HTTPException(400, "No importable media found in returned folder")

    return_entry = {
        "bundle_dir": bundle_rel,
        "source_dir": source_rel,
        "manifest_path": bundle_manifest_path.relative_to(pdir).as_posix() if bundle_manifest_path.exists() else None,
        "return_contract_path": return_contract_path.relative_to(pdir).as_posix() if return_contract_path.exists() else None,
        "created_at": created_at,
        "variant_index": variant_index,
        "sequence_name": sequence_name,
        "media": imported_media,
    }
    proj.meta.setdefault("exports", {}).setdefault("unreal_returns", []).append(return_entry)
    store.save(proj)

    return {
        "ok": True,
        "imported": return_entry,
    }


@app.post("/v1/projects/{project_id}/unreal/import-plan")
def build_unreal_bridge_import_plan(project_id: str, req: BuildUnrealImportPlanRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    pdir = store.project_dir(project_id)
    try:
        bundle_dir = safe_join(pdir, req.bundle_dir)
    except Exception:
        raise HTTPException(400, "Invalid bundle_dir")
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        raise HTTPException(404, "Unreal bundle not found")

    try:
        plan = build_unreal_sequence_import_plan(
            bundle_dir,
            content_path=req.content_path,
            asset_name=req.asset_name,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    plan_path = bundle_dir / "unreal_import_plan.json"
    write_unreal_sequence_import_plan(plan, plan_path)
    plan_rel = plan_path.relative_to(pdir).as_posix()

    return {
        "ok": True,
        "plan_path": plan_rel,
        "plan": plan.to_dict(),
    }

@app.get("/v1/projects/{project_id}/outputs")
def list_outputs(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)

    def _absolute_project_path(value: Any) -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return str(safe_join(pdir, raw))
        except Exception:
            return None

    def _file_entry(fp: Path) -> dict[str, Any]:
        try:
            st = fp.stat()
            entry = {
                "path": str(fp.relative_to(pdir)),
                "absolute_path": str(fp.resolve()),
                "name": fp.name,
                "size_bytes": int(st.st_size),
                "modified_at": float(st.st_mtime),
            }
            metadata_path = _output_metadata_path(fp)
            if metadata_path.exists():
                entry["metadata_path"] = str(metadata_path.relative_to(pdir))
                entry["metadata_absolute_path"] = str(metadata_path.resolve())
                try:
                    entry["metadata"] = json.loads(metadata_path.read_text(encoding="utf-8"))
                except Exception:
                    entry["metadata_error"] = "invalid_json"
            return entry
        except Exception:
            return {
                "path": str(fp.relative_to(pdir)),
                "absolute_path": str(fp.resolve()),
                "name": fp.name,
            }

    imgs = []
    vids = []
    defs = []
    nvidia_exports = []
    nvidia_render_jobs = []
    unreal_exports = []
    for p in sorted((pdir / "outputs" / "images").glob("*"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            imgs.append(_file_entry(p))
    for p in sorted((pdir / "outputs" / "videos").glob("*"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        if p.suffix.lower() not in UNREAL_RETURN_VIDEO_EXTENSIONS:
            continue
        entry = _file_entry(p)
        name = p.name
        metadata_kind = str((entry.get("metadata") or {}).get("kind") or "")
        if metadata_kind == "unreal_bridge_return":
            entry["kind"] = "unreal_bridge_return"
        elif name.endswith("_raw.mp4"):
            entry["kind"] = "internal_raw"
        elif name.endswith("_interp.mp4"):
            entry["kind"] = "internal_interp"
        elif name.startswith("internal_v"):
            entry["kind"] = "internal_final"
        else:
            entry["kind"] = "video"
        vids.append(entry)
    for p in sorted((pdir / "outputs" / "deforum").glob("*.json"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        defs.append(_file_entry(p))
    for p in sorted((pdir / "outputs" / "nvidia").glob("*.usda"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        nvidia_exports.append(_file_entry(p))
    for p in sorted((pdir / "outputs" / "nvidia" / "render_jobs").glob("*.json"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        if p.name.endswith(".result.json") or p.name.endswith(".json.json"):
            continue
        entry = _file_entry(p)
        if "metadata" not in entry:
            try:
                parsed_job = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(parsed_job, dict):
                    entry["metadata"] = parsed_job
            except Exception:
                entry["metadata_error"] = "invalid_json"
        result_path = p.with_name(f"{p.stem}.result.json")
        if result_path.exists():
            entry["result_path"] = result_path.relative_to(pdir).as_posix()
            entry["result_absolute_path"] = str(result_path.resolve())
            try:
                result_payload = json.loads(result_path.read_text(encoding="utf-8"))
                if isinstance(result_payload, dict):
                    outputs = result_payload.get("outputs")
                    if isinstance(outputs, dict):
                        outputs_absolute = {
                            str(key): resolved
                            for key, value in outputs.items()
                            if isinstance(key, str)
                            for resolved in [_absolute_project_path(value)]
                            if resolved
                        }
                        if outputs_absolute:
                            result_payload["outputs_absolute"] = outputs_absolute
                    entry["result"] = result_payload
                else:
                    entry["result"] = result_payload
            except Exception:
                entry["result_error"] = "invalid_json"
        nvidia_render_jobs.append(entry)
    raw_unreal_exports = []
    raw_unreal_returns = []
    if isinstance(proj.meta, dict):
        raw_exports = proj.meta.get("exports") if isinstance(proj.meta.get("exports"), dict) else {}
        raw_unreal_exports = raw_exports.get("unreal") if isinstance(raw_exports.get("unreal"), list) else []
        raw_unreal_returns = raw_exports.get("unreal_returns") if isinstance(raw_exports.get("unreal_returns"), list) else []
    for raw in reversed(raw_unreal_exports):
        if not isinstance(raw, dict):
            continue
        entry = {
            "bundle_dir": str(raw.get("bundle_dir") or ""),
            "manifest_path": str(raw.get("manifest_path") or ""),
            "zip_path": str(raw.get("zip_path") or "") or None,
            "created_at": str(raw.get("created_at") or ""),
            "variant_index": int(raw.get("variant_index") or 0),
            "sequence_name": str(raw.get("sequence_name") or ""),
            "files": list(raw.get("files") or []),
        }
        manifest_rel = entry["manifest_path"]
        if manifest_rel:
            try:
                manifest_path = safe_join(pdir, manifest_rel)
                if manifest_path.exists() and manifest_path.is_file():
                    entry["manifest_file"] = _file_entry(manifest_path)
                    entry["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        zip_rel = entry["zip_path"]
        if zip_rel:
            try:
                zip_file = safe_join(pdir, zip_rel)
                if zip_file.exists() and zip_file.is_file():
                    entry["zip_file"] = _file_entry(zip_file)
            except Exception:
                pass
        bundle_rel = entry["bundle_dir"]
        if bundle_rel:
            try:
                bundle_path = safe_join(pdir, bundle_rel)
                import_plan_path = bundle_path / "unreal_import_plan.json"
                if import_plan_path.exists() and import_plan_path.is_file():
                    entry["import_plan_path"] = import_plan_path.relative_to(pdir).as_posix()
                    entry["import_plan_file"] = _file_entry(import_plan_path)
                    entry["import_plan"] = json.loads(import_plan_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        unreal_exports.append(entry)
    unreal_returns = []
    for raw in reversed(raw_unreal_returns):
        if not isinstance(raw, dict):
            continue
        entry = {
            "bundle_dir": str(raw.get("bundle_dir") or ""),
            "source_dir": str(raw.get("source_dir") or ""),
            "manifest_path": str(raw.get("manifest_path") or "") or None,
            "return_contract_path": str(raw.get("return_contract_path") or "") or None,
            "created_at": str(raw.get("created_at") or ""),
            "variant_index": int(raw.get("variant_index") or 0),
            "sequence_name": str(raw.get("sequence_name") or ""),
            "media": [],
        }
        for raw_media in list(raw.get("media") or []):
            if not isinstance(raw_media, dict):
                continue
            media_path = str(raw_media.get("path") or "")
            if not media_path:
                continue
            media_entry: dict[str, Any]
            try:
                media_entry = _file_entry(safe_join(pdir, media_path))
            except Exception:
                media_entry = {"path": media_path, "name": Path(media_path).name}
            media_entry["kind"] = str(raw_media.get("kind") or media_entry.get("kind") or "")
            source_path = str(raw_media.get("source_path") or "")
            if source_path:
                media_entry["source_path"] = source_path
            metadata_path = str(raw_media.get("metadata_path") or "")
            if metadata_path:
                media_entry["metadata_path"] = metadata_path
            entry["media"].append(media_entry)
        unreal_returns.append(entry)

    latest_internal = proj.meta.get("last_internal_render") or None
    history = proj.meta.get("internal_render_history") or []
    project_jobs = jobs.list_for_project(project_id)
    active_internal_jobs = [
        j.__dict__
        for j in project_jobs
        if j.type == "internal_video" and j.status in ("queued", "running", "canceled", "failed")
    ][:8]
    return {
        "images": imgs,
        "videos": vids,
        "deforum_exports": defs,
        "nvidia_exports": nvidia_exports,
        "nvidia_render_jobs": nvidia_render_jobs,
        "unreal_exports": unreal_exports,
        "unreal_returns": unreal_returns,
        "project_id": project_id,
        "latest_internal_render": latest_internal,
        "internal_render_history": history[-20:] if isinstance(history, list) else [],
        "active_internal_jobs": active_internal_jobs,
    }

@app.get("/v1/projects/{project_id}/file")
def get_file(project_id: str, path: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)
    try:
        fp = safe_join(pdir, path)
    except Exception:
        raise HTTPException(400, "Invalid path")
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(str(fp))

@app.post("/v1/cloud/aws/test")
def cloud_aws_test(req: CloudAwsTestRequest):
    try:
        res = aws_integration.test_credentials(bucket=req.bucket, prefix=req.prefix)
        return {
            "ok": res.ok,
            "provider": "aws",
            "account": res.account,
            "region": res.region,
            "bucket": req.bucket,
            "prefix": req.prefix or "models",
        }
    except Exception as e:
        raise HTTPException(status_code=501, detail=str(e))

@app.post("/v1/cloud/aws/bundle")
def cloud_aws_bundle(req: CloudAwsBundleRequest):
    data_dir = settings.data_dir
    out_zip = data_dir / "edmg_studio_bundle.zip"
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in data_dir.rglob("*"):
            if p.is_file():
                z.write(p, arcname=str(p.relative_to(data_dir)))

    result = {"ok": True, "bundle_path": str(out_zip)}
    if req.bucket and req.key:
        try:
            up = aws_integration.upload_file_s3(req.bucket, req.key, str(out_zip))
            result["uploaded"] = up
        except Exception as e:
            result["upload_error"] = str(e)
    return result

@app.post("/v1/cloud/azure/test")
def cloud_azure_test(req: CloudAzureTestRequest):
    try:
        return azure_integration.test_credentials(container=req.container, prefix=req.prefix)
    except Exception as e:
        raise HTTPException(status_code=501, detail=str(e))

@app.post("/v1/cloud/lightning/bundle")
def cloud_lightning_bundle(req: CloudLightningBundleRequest):
    try:
        return lightning_integration.generate_lightning_bundle(req.output_dir)
    except Exception as e:
        raise HTTPException(500, str(e))

# ------------------------------
# Model Manager (GUI)
# ------------------------------

@app.get("/v1/models/catalog")
def models_catalog():
    return models.catalog()

@app.get("/v1/models/tasks")
def models_tasks():
    return {"tasks": [t.__dict__ for t in models.tasks.list()]}

@app.post("/v1/models/accept")
def models_accept(req: dict[str, Any]):
    model_id = str(req.get("model_id") or "")
    license_id = str(req.get("license_id") or "")
    models.accept_license(model_id, license_id)
    return {"ok": True}

@app.post("/v1/models/install")
def models_install(req: dict[str, Any]):
    model_id = str(req.get("model_id") or "")
    task = models.install(model_id)
    return {"task": task.__dict__}

@app.post("/v1/models/restore_local")
def models_restore_local(req: dict[str, Any]):
    model_id = str(req.get("model_id") or "")
    task = models.restore_local(model_id)
    return {"task": task.__dict__}

@app.post("/v1/models/install_pack")
def models_install_pack(req: dict[str, Any]):
    pack_id = str(req.get("pack_id") or "")
    tasks = models.install_pack(pack_id)
    return {"tasks": [t.__dict__ for t in tasks]}

@app.post("/v1/models/import/civitai")
def models_import_civitai(req: dict[str, Any]):
    url_or_id = str(req.get("url") or req.get("id") or "")
    entry = models.civitai_import(url_or_id)
    return {"entry": entry}

@app.post("/v1/models/import/local")
def models_import_local(req: dict[str, Any]):
    path = str(req.get("file_path") or "")
    name = req.get("name")
    folder = str(req.get("folder") or "checkpoints")
    entry = models.import_local(path, name=name, folder=folder)
    return {"entry": entry}

@app.post("/v1/models/remove_user")
def models_remove_user(req: dict[str, Any]):
    model_id = str(req.get("model_id") or "")
    models.remove_user_model(model_id)
    return {"ok": True}

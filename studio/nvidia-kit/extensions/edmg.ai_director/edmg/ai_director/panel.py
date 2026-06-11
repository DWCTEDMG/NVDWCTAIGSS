from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ScenePlanSummary:
    title: str
    project_id: str
    scene_count: int
    duration_s: float | None
    provider: str
    saved_at: str
    usda_bytes: int
    export_path: str
    status_text: str


@dataclass(frozen=True)
class NvidiaServicesSummary:
    configured: tuple[str, ...]
    reachable: tuple[str, ...]
    readiness_level: str
    status_text: str


@dataclass(frozen=True)
class ProjectWorkflowSummary:
    project_id: str
    name: str
    status_text: str


OPTIONAL_SERVICE_IDS = ("audio2face", "ace", "cosmos", "triton")

DEFAULT_OPTIONAL_SERVICE_PAYLOADS: dict[str, dict[str, Any]] = {
    "audio2face": {
        "path": "/v1/a2f/jobs",
        "audio_path": "outputs/dialogue.wav",
        "character": "performer",
        "emotion": "focused",
    },
    "ace": {
        "path": "/v1/avatar/animation",
        "avatar_id": "performer",
        "text": "Welcome to the RTX stage.",
    },
    "cosmos": {
        "prompt": "audio-reactive RTX stage world",
        "negative_prompt": "blurry, low quality",
        "seed": 4,
        "guidance_scale": 7.5,
        "steps": 50,
        "video_params": {
            "height": 704,
            "width": 1280,
            "frames_count": 121,
            "frames_per_sec": 24,
        },
    },
    "triton": {
        "model": "edmg_ensemble",
        "inputs": [
            {
                "name": "prompt",
                "shape": [1],
                "datatype": "BYTES",
                "data": ["neon intro"],
            }
        ],
    },
}


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _safe_filename_tag(value: str, default: str = "scene_plan") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-")
    return cleaned or default


def _project_match_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def normalize_optional_service_id(value: Any, default: str = "cosmos") -> str:
    raw = str(value or "").strip()
    if not raw:
        return default
    normalized = re.sub(r"[^a-z0-9]+", "", raw.casefold())
    aliases = {
        "audio2face": "audio2face",
        "audioface": "audio2face",
        "a2f": "audio2face",
        "ace": "ace",
        "cosmos": "cosmos",
        "triton": "triton",
    }
    return aliases.get(normalized, "")


def default_optional_service_payload(service_id: Any) -> dict[str, Any]:
    normalized = normalize_optional_service_id(service_id)
    if normalized not in DEFAULT_OPTIONAL_SERVICE_PAYLOADS:
        supported = ", ".join(OPTIONAL_SERVICE_IDS)
        raise ValueError(f"unsupported NVIDIA service id; use one of: {supported}")
    return json.loads(json.dumps(DEFAULT_OPTIONAL_SERVICE_PAYLOADS[normalized]))


def default_optional_service_payload_text(service_id: Any) -> str:
    return json.dumps(default_optional_service_payload(service_id), separators=(",", ":"))


def build_optional_service_payload(service_id: Any, raw_json: Any) -> dict[str, Any]:
    text = str(raw_json or "").strip()
    if not text:
        return default_optional_service_payload(service_id)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"service payload JSON is invalid: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("service payload JSON root must be an object")
    return payload


def resolve_project_reference(reference: Any, projects_response: dict[str, Any]) -> str:
    raw_reference = str(reference or "").strip()
    if not raw_reference:
        return ""

    projects = projects_response.get("projects") if isinstance(projects_response.get("projects"), list) else []
    normalized_reference = _project_match_key(raw_reference)
    candidates: list[tuple[str, str]] = []
    for item in projects:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or item.get("title") or "").strip()
        if project_id:
            candidates.append((project_id, name))

    for project_id, _name in candidates:
        if raw_reference == project_id:
            return project_id
    for project_id, name in candidates:
        if raw_reference.casefold() == name.casefold():
            return project_id
    for project_id, name in candidates:
        if normalized_reference and normalized_reference in {_project_match_key(project_id), _project_match_key(name)}:
            return project_id

    return raw_reference


def build_generation_payload(
    *,
    title: Any,
    duration_s: Any,
    bpm: Any,
    max_scenes: Any,
    notes: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "num_variants": 1,
        "variant_index": 0,
    }
    title_text = str(title or "").strip()
    if title_text:
        payload["title"] = title_text

    parsed_duration = _float_or_none(duration_s)
    payload["duration_s"] = parsed_duration if parsed_duration is not None else 60.0

    parsed_bpm = _float_or_none(bpm)
    if parsed_bpm is not None:
        payload["bpm"] = parsed_bpm

    payload["max_scenes"] = max(1, min(64, _int_or_default(max_scenes, 6)))

    notes_text = str(notes or "").strip()
    if notes_text:
        payload["user_notes"] = notes_text
    return payload


def build_render_job_payload(*, fps: Any, width: Any, height: Any) -> dict[str, int]:
    return {
        "fps": max(1, min(240, _int_or_default(fps, 24))),
        "width": max(1, min(16384, _int_or_default(width, 1920))),
        "height": max(1, min(16384, _int_or_default(height, 1080))),
    }


def build_storyboard_movie_payload(*, fps: Any, width: Any, height: Any) -> dict[str, Any]:
    return {
        "variant_index": 0,
        "preset": "balanced",
        "render_mode": "auto",
        "apply_storyboard": True,
        "overwrite_timeline": True,
        "allow_hosted_fallback": False,
        "allow_proxy_fallback": False,
        "fps_output": max(1, min(60, _int_or_default(fps, 24))),
        "width": max(256, min(1920, _int_or_default(width, 1920))),
        "height": max(256, min(1080, _int_or_default(height, 1080))),
    }


def _project_object(response: dict[str, Any]) -> dict[str, Any]:
    project = response.get("project") if isinstance(response.get("project"), dict) else response
    return project if isinstance(project, dict) else {}


def project_id_from_response(response: dict[str, Any]) -> str:
    project = _project_object(response)
    return str(response.get("project_id") or project.get("id") or project.get("project_id") or "").strip()


def summarize_project_response(response: dict[str, Any]) -> ProjectWorkflowSummary:
    project = _project_object(response)
    project_id = project_id_from_response(response)
    name = str(project.get("name") or project.get("title") or response.get("name") or "").strip()
    label = name or project_id or "project"
    status = f"Project ready: {label}"
    if project_id:
        status = f"{status} ({project_id})"
    return ProjectWorkflowSummary(project_id=project_id, name=name, status_text=status)


def summarize_audio_upload_response(response: dict[str, Any]) -> str:
    path = str(response.get("path") or "").strip()
    if path:
        return f"Audio uploaded: {Path(path).name}"
    return "Audio uploaded."


def summarize_analysis_response(response: dict[str, Any]) -> str:
    analysis = response.get("analysis") if isinstance(response.get("analysis"), dict) else {}
    features = analysis.get("features") if isinstance(analysis.get("features"), dict) else {}
    duration = _float_or_none(analysis.get("duration_s") or features.get("duration_s") or features.get("duration"))
    bpm = _float_or_none(analysis.get("bpm") or features.get("bpm") or features.get("tempo_bpm"))
    transcript = analysis.get("transcript") if isinstance(analysis.get("transcript"), dict) else {}
    transcript_text = str(transcript.get("text") or "").strip()
    transcript_error = str(transcript.get("error") or "").strip()
    parts = ["Audio analysis ready"]
    if duration is not None:
        parts.append(f"{duration:g}s")
    if bpm is not None:
        parts.append(f"{bpm:g} BPM")
    if transcript_text:
        parts.append("transcript text available")
    elif transcript_error:
        parts.append(transcript_error)
    return f"{parts[0]}: {', '.join(parts[1:])}" if len(parts) > 1 else parts[0]


def summarize_riva_transcript_response(response: dict[str, Any]) -> str:
    transcript = response.get("transcript") if isinstance(response.get("transcript"), dict) else {}
    text = str(transcript.get("text") or "").strip()
    segments = transcript.get("segments") if isinstance(transcript.get("segments"), list) else []
    segment_count = len(segments)
    if text and segment_count:
        return f"Riva transcript ready: {segment_count} segments"
    if text:
        return "Riva transcript ready: text available"
    if segment_count:
        return f"Riva transcript ready: {segment_count} segments"
    return "Riva transcript finished."


def summarize_nvidia_diagnostics_response(response: dict[str, Any]) -> str:
    nvidia = response.get("nvidia") if isinstance(response.get("nvidia"), dict) else {}
    readiness = nvidia.get("readiness") if isinstance(nvidia.get("readiness"), dict) else {}
    level = str(readiness.get("level") or "unknown").strip()
    summary = str(readiness.get("summary") or "").strip()
    gpu = str(nvidia.get("gpu") or nvidia.get("gpu_name") or "").strip()
    gpu_text = f", {gpu}" if gpu else ""
    summary_text = f": {summary}" if summary else ""
    return f"NVIDIA readiness {level}{gpu_text}{summary_text}"


def summarize_render_job_response(response: dict[str, Any]) -> str:
    export = response.get("export") if isinstance(response.get("export"), dict) else {}
    job = response.get("job") if isinstance(response.get("job"), dict) else {}
    renderer = job.get("renderer") if isinstance(job.get("renderer"), dict) else {}
    resolution = renderer.get("resolution") if isinstance(renderer.get("resolution"), dict) else {}
    path = str(export.get("path") or "").strip()
    fps = export.get("fps") or renderer.get("fps")
    width = resolution.get("width")
    height = resolution.get("height")
    parts = ["RTX render job queued"]
    if path:
        parts.append(path)
    if fps and width and height:
        parts.append(f"{width}x{height}@{fps}fps")
    return f"{parts[0]}: {', '.join(parts[1:])}" if len(parts) > 1 else parts[0]


def summarize_storyboard_movie_response(response: dict[str, Any]) -> str:
    job = response.get("job") if isinstance(response.get("job"), dict) else {}
    preflight = response.get("preflight") if isinstance(response.get("preflight"), dict) else {}
    storyboard = response.get("storyboard") if isinstance(response.get("storyboard"), dict) else {}
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
    job_id = str(job.get("id") or job.get("job_id") or "").strip()
    render_mode = str(response.get("render_mode") or payload.get("render_mode") or "").strip()
    preflight_mode = str(preflight.get("mode") or "").strip()
    movie_path = str(outputs.get("movie") or outputs.get("path") or "").strip()
    scene_count = storyboard.get("scene_count")
    duration = _float_or_none(storyboard.get("duration_s") or preflight.get("duration_s"))
    fps = payload.get("fps_output")
    width = payload.get("width")
    height = payload.get("height")
    parts = ["Storyboard movie queued"]
    if job_id:
        parts.append(f"job {job_id}")
    if render_mode or preflight_mode:
        parts.append(render_mode or preflight_mode)
    if scene_count:
        scene_text = f"{scene_count} scenes"
        if duration is not None:
            scene_text = f"{scene_text}, {duration:g}s"
        parts.append(scene_text)
    elif duration is not None:
        parts.append(f"{duration:g}s")
    if fps and width and height:
        parts.append(f"{width}x{height}@{fps}fps")
    if movie_path:
        parts.append(movie_path)
    return f"{parts[0]}: {', '.join(parts[1:])}" if len(parts) > 1 else parts[0]


def extract_usda_text(response: dict[str, Any]) -> str:
    stage = response.get("usd_stage") if isinstance(response.get("usd_stage"), dict) else {}
    if stage.get("format") != "usda":
        return ""
    text = stage.get("text")
    return text if isinstance(text, str) else ""


def summarize_scene_plan_response(response: dict[str, Any]) -> ScenePlanSummary:
    scene_plan = response.get("scene_plan") if isinstance(response.get("scene_plan"), dict) else {}
    planner = response.get("planner") if isinstance(response.get("planner"), dict) else {}
    export = response.get("export") if isinstance(response.get("export"), dict) else {}
    scenes = scene_plan.get("scenes") if isinstance(scene_plan.get("scenes"), list) else []
    scene_count = len(scenes) or int(export.get("scene_count") or 0)
    duration = _float_or_none(scene_plan.get("duration_s"))
    title = str(scene_plan.get("title") or response.get("title") or "EDMG NVIDIA scene plan")
    project_id = str(response.get("project_id") or scene_plan.get("project_id") or "")
    provider = str(
        scene_plan.get("provider")
        or " ".join(str(part) for part in (planner.get("provider"), planner.get("model")) if part)
        or ""
    )
    saved_at = str(response.get("saved_at") or export.get("created_at") or "")
    usda_text = extract_usda_text(response)
    export_path = str(export.get("path") or "")
    duration_text = f", {duration:g}s" if duration is not None else ""
    provider_text = f", {provider}" if provider else ""
    status_text = f"{title}: {scene_count} scenes{duration_text}{provider_text}"
    if export_path:
        status_text = f"{status_text}, exported {export_path}"
    return ScenePlanSummary(
        title=title,
        project_id=project_id,
        scene_count=scene_count,
        duration_s=duration,
        provider=provider,
        saved_at=saved_at,
        usda_bytes=len(usda_text.encode("utf-8")),
        export_path=export_path,
        status_text=status_text,
    )


def summarize_nvidia_services_response(response: dict[str, Any]) -> NvidiaServicesSummary:
    services = response.get("services") if isinstance(response.get("services"), dict) else {}
    readiness = response.get("readiness") if isinstance(response.get("readiness"), dict) else {}
    configured: list[str] = []
    reachable: list[str] = []
    for service_id, service in services.items():
        if not isinstance(service, dict):
            continue
        service_name = str(service_id)
        if service.get("configured"):
            configured.append(service_name)
        probe = service.get("probe") if isinstance(service.get("probe"), dict) else {}
        if probe.get("reachable"):
            reachable.append(service_name)
    readiness_level = str(readiness.get("level") or "unknown")
    configured_text = ", ".join(sorted(configured)) if configured else "none configured"
    reachable_text = ", ".join(sorted(reachable)) if reachable else "none reachable"
    return NvidiaServicesSummary(
        configured=tuple(sorted(configured)),
        reachable=tuple(sorted(reachable)),
        readiness_level=readiness_level,
        status_text=f"NVIDIA services: {configured_text}; reachable: {reachable_text}; readiness {readiness_level}",
    )


def summarize_optional_service_response(service_id: Any, response: dict[str, Any]) -> str:
    normalized = normalize_optional_service_id(service_id)
    result = response.get(normalized) if isinstance(response.get(normalized), dict) else response.get("result")
    if not isinstance(result, dict):
        result = response
    ok = bool(response.get("ok") or result.get("ok"))
    status_code = result.get("status_code")
    model = str(result.get("model") or "").strip()
    response_body = result.get("response")
    response_hint = ""
    if isinstance(response_body, dict):
        keys = ", ".join(sorted(str(key) for key in response_body.keys())[:5])
        response_hint = f", response keys: {keys}" if keys else ""
    elif isinstance(result.get("raw_text"), str):
        response_hint = ", raw text response"
    model_text = f", model {model}" if model else ""
    code_text = f", HTTP {status_code}" if status_code is not None else ""
    return f"{normalized} {'ok' if ok else 'failed'}{code_text}{model_text}{response_hint}"


def write_usda_preview(response: dict[str, Any], *, root: str | Path | None = None) -> Path:
    text = extract_usda_text(response)
    if not text.strip():
        raise ValueError("scene-plan response did not include USDA text")
    scene_plan = response.get("scene_plan") if isinstance(response.get("scene_plan"), dict) else {}
    project_id = str(response.get("project_id") or scene_plan.get("project_id") or "").strip()
    title = str(scene_plan.get("title") or response.get("title") or "scene_plan").strip()
    tag = _safe_filename_tag(project_id or title)
    out_root = Path(root) if root is not None else Path(tempfile.gettempdir()) / "edmg_nvidia_kit"
    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / f"{tag}.usda"
    out_path.write_text(text, encoding="utf-8", newline="\n")
    return out_path

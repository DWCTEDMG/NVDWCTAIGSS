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

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TimelineCue:
    name: str
    start_s: float
    end_s: float
    prompt: str


@dataclass(frozen=True)
class TimelineSummary:
    title: str
    project_id: str
    duration_s: float
    bpm: float | None
    scene_count: int
    cues: tuple[TimelineCue, ...]
    status_text: str


@dataclass(frozen=True)
class DawSessionSummary:
    marker_count: int
    bus_count: int
    stem_count: int
    automation_count: int
    target_lufs: str
    session: dict[str, Any]
    status_text: str


def _float_or_zero(value: Any) -> float:
    try:
        parsed = float(value)
    except Exception:
        return 0.0
    return parsed if parsed > 0 else 0.0


def _float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _scene_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    scene_plan = payload.get("scene_plan") if isinstance(payload.get("scene_plan"), dict) else None
    return scene_plan or payload


def _list_value(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def summarize_scene_plan(payload: dict[str, Any]) -> TimelineSummary:
    plan = _scene_plan_payload(payload)
    scenes = plan.get("scenes") if isinstance(plan.get("scenes"), list) else []
    cues: list[TimelineCue] = []
    for index, raw in enumerate(scenes[:24], start=1):
        if not isinstance(raw, dict):
            continue
        start_s = _float_or_zero(raw.get("start_s"))
        end_s = _float_or_zero(raw.get("end_s"))
        if end_s <= start_s:
            end_s = start_s
        cues.append(
            TimelineCue(
                name=str(raw.get("name") or raw.get("id") or f"Scene {index}"),
                start_s=start_s,
                end_s=end_s,
                prompt=str(raw.get("prompt") or ""),
            )
        )
    duration = _float_or_zero(plan.get("duration_s") or payload.get("duration_s"))
    if duration <= 0 and cues:
        duration = max(cue.end_s for cue in cues)
    title = str(plan.get("title") or payload.get("title") or "EDMG NVIDIA timeline")
    project_id = str(plan.get("project_id") or payload.get("project_id") or "")
    bpm = _float_or_none(plan.get("bpm") or payload.get("bpm"))
    bpm_text = f", {bpm:g} BPM" if bpm is not None else ""
    status_text = f"{title}: {len(cues)} cues, {duration:g}s{bpm_text}"
    return TimelineSummary(
        title=title,
        project_id=project_id,
        duration_s=duration,
        bpm=bpm,
        scene_count=len(cues),
        cues=tuple(cues),
        status_text=status_text,
    )


def scene_markers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    plan = _scene_plan_payload(payload)
    scenes = plan.get("scenes") if isinstance(plan.get("scenes"), list) else []
    markers: list[dict[str, Any]] = []
    for index, raw in enumerate(scenes, start=1):
        if not isinstance(raw, dict):
            continue
        start_s = _float_or_zero(raw.get("start_s"))
        end_s = _float_or_zero(raw.get("end_s"))
        if end_s <= start_s:
            end_s = start_s
        name = str(raw.get("name") or raw.get("id") or f"Scene {index}").strip()
        markers.append(
            {
                "id": str(raw.get("id") or f"scene_{index:02d}"),
                "name": name,
                "start_s": start_s,
                "end_s": end_s,
                "type": "scene",
                "color": raw.get("color") or "#76B900",
                "notes": str(raw.get("prompt") or raw.get("motion") or ""),
            }
        )
    return markers


def default_daw_session(payload: dict[str, Any]) -> dict[str, Any]:
    summary = summarize_scene_plan(payload)
    return {
        "source": "derived_scene_plan",
        "timebase": {
            "fps": 24,
            "bpm": summary.bpm,
            "duration_s": summary.duration_s,
        },
        "markers": scene_markers(payload),
        "buses": [
            {"name": "Dialogue / VO", "role": "dialogue", "target": "-18 LUFS", "monitor": "center"},
            {"name": "Music Stem", "role": "music", "target": "-16 LUFS", "monitor": "stereo"},
            {"name": "SFX / Foley", "role": "effects", "target": "-18 LUFS", "monitor": "stereo"},
            {"name": "Master Print", "role": "master", "target": "-16 LUFS", "monitor": "full_mix"},
        ],
        "stems": [
            {"name": "Dialogue", "bus": "Dialogue / VO", "armed": True},
            {"name": "Music", "bus": "Music Stem", "armed": True},
            {"name": "FX", "bus": "SFX / Foley", "armed": True},
            {"name": "Ambience", "bus": "SFX / Foley", "armed": True},
        ],
        "automation_lanes": [
            {"name": "Energy Envelope", "source": "audio.energy_curve", "target": "visual.intensity"},
            {"name": "Bass Hits", "source": "audio.kick_bass", "target": "lighting.pulse"},
            {"name": "High Detail", "source": "audio.treble", "target": "particles.sparkle"},
            {"name": "Scene Cuts", "source": "markers", "target": "camera.cut_points"},
        ],
        "monitor": {
            "target_lufs": "-16 LUFS",
            "true_peak_db": -1.0,
            "sample_rate_hz": 48000,
            "print_format": "MP4 H.264 preview + WAV stems",
        },
    }


def _normalize_daw_session(session: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    defaults = default_daw_session(payload)
    merged: dict[str, Any] = {**defaults, **session}
    for key in ("markers", "buses", "stems", "automation_lanes"):
        if not isinstance(merged.get(key), list):
            merged[key] = defaults[key]
    monitor = merged.get("monitor")
    if isinstance(monitor, dict):
        merged["monitor"] = {**defaults["monitor"], **monitor}
    else:
        merged["monitor"] = defaults["monitor"]
    return merged


def daw_session_from_scene_plan(payload: dict[str, Any]) -> dict[str, Any]:
    plan = _scene_plan_payload(payload)
    for source in (payload, plan):
        timeline = source.get("timeline") if isinstance(source.get("timeline"), dict) else {}
        daw = timeline.get("daw") if isinstance(timeline.get("daw"), dict) else None
        if daw is not None:
            return _normalize_daw_session(daw, payload)
        direct = source.get("daw") if isinstance(source.get("daw"), dict) else None
        if direct is not None:
            return _normalize_daw_session(direct, payload)
    return default_daw_session(payload)


def summarize_daw_session(payload: dict[str, Any]) -> DawSessionSummary:
    session = daw_session_from_scene_plan(payload)
    markers = _list_value(session.get("markers"))
    buses = _list_value(session.get("buses"))
    stems = _list_value(session.get("stems"))
    automation = _list_value(session.get("automation_lanes"))
    monitor = session.get("monitor") if isinstance(session.get("monitor"), dict) else {}
    target_lufs = str(monitor.get("target_lufs") or "n/a")
    status_text = (
        "Post DAW: "
        f"{len(markers)} markers, {len(buses)} buses, {len(stems)} stems, "
        f"{len(automation)} automation lanes, target {target_lufs}"
    )
    return DawSessionSummary(
        marker_count=len(markers),
        bus_count=len(buses),
        stem_count=len(stems),
        automation_count=len(automation),
        target_lufs=target_lufs,
        session=session,
        status_text=status_text,
    )


def daw_lines(summary: DawSessionSummary, *, limit: int = 5) -> list[str]:
    session = summary.session
    markers = [item for item in _list_value(session.get("markers")) if isinstance(item, dict)]
    buses = [item for item in _list_value(session.get("buses")) if isinstance(item, dict)]
    stems = [item for item in _list_value(session.get("stems")) if isinstance(item, dict)]
    automation = [item for item in _list_value(session.get("automation_lanes")) if isinstance(item, dict)]
    lines: list[str] = []
    for marker in markers[:limit]:
        lines.append(
            f"{_float_or_zero(marker.get('start_s')):g}-{_float_or_zero(marker.get('end_s')):g}s "
            f"{marker.get('name') or marker.get('id') or 'Marker'}"
        )
    if len(markers) > limit:
        lines.append(f"+ {len(markers) - limit} more markers")
    if buses:
        lines.append("Buses: " + ", ".join(str(item.get("name") or item.get("role")) for item in buses[:4]))
    if stems:
        lines.append("Stems: " + ", ".join(str(item.get("name") or item.get("bus")) for item in stems[:4]))
    if automation:
        lines.append("Automation: " + ", ".join(str(item.get("name") or item.get("target")) for item in automation[:3]))
    return lines


def load_scene_plan_payload(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("scene plan root must be an object")
    return payload


def load_scene_plan_summary(path: str | Path) -> TimelineSummary:
    return summarize_scene_plan(load_scene_plan_payload(path))


def cue_lines(summary: TimelineSummary, *, limit: int = 8) -> list[str]:
    lines: list[str] = []
    for cue in summary.cues[:limit]:
        label = f"{cue.start_s:g}-{cue.end_s:g}s {cue.name}"
        if cue.prompt:
            label = f"{label}: {cue.prompt[:96]}"
        lines.append(label)
    if summary.scene_count > limit:
        lines.append(f"+ {summary.scene_count - limit} more cues")
    return lines

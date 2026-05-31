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


def summarize_scene_plan(payload: dict[str, Any]) -> TimelineSummary:
    scenes = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
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
    duration = _float_or_zero(payload.get("duration_s"))
    if duration <= 0 and cues:
        duration = max(cue.end_s for cue in cues)
    title = str(payload.get("title") or "EDMG NVIDIA timeline")
    project_id = str(payload.get("project_id") or "")
    bpm = _float_or_none(payload.get("bpm"))
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


def load_scene_plan_summary(path: str | Path) -> TimelineSummary:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("scene plan root must be an object")
    return summarize_scene_plan(payload)


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

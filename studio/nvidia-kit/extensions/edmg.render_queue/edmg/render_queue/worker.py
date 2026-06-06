from __future__ import annotations

import asyncio
import importlib
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from .panel import RenderJobRecord


class RenderJobExecutionError(RuntimeError):
    pass


def worker_identity() -> dict[str, Any]:
    return {
        "name": "edmg.render_queue",
        "host": socket.gethostname(),
        "runtime": "omniverse-kit",
    }


def _renderer_settings(payload: dict[str, Any]) -> tuple[int, int, int]:
    renderer = payload.get("renderer") if isinstance(payload.get("renderer"), dict) else {}
    resolution = renderer.get("resolution") if isinstance(renderer.get("resolution"), dict) else {}
    try:
        width = max(1, int(resolution.get("width") or 1920))
        height = max(1, int(resolution.get("height") or 1080))
        fps = max(1, int(renderer.get("fps") or 24))
    except Exception:
        width, height, fps = 1920, 1080, 24
    return width, height, fps


def _float_value(value: Any) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    return parsed


def _project_relpath(record: RenderJobRecord, path: Path) -> str:
    try:
        return path.relative_to(record.project_root).as_posix()
    except Exception:
        return path.as_posix()


def _timeline_scenes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    timeline = payload.get("timeline") if isinstance(payload.get("timeline"), list) else []
    scenes = [item for item in timeline if isinstance(item, dict)]
    if scenes:
        return scenes
    return [{"id": "preview", "start_frame": 0, "output": {}}]


def _scene_duration_seconds(scene: dict[str, Any], fps: int) -> float:
    duration_s = _float_value(scene.get("duration_s"))
    if duration_s is not None and duration_s > 0:
        return duration_s

    start_s = _float_value(scene.get("start_s"))
    end_s = _float_value(scene.get("end_s"))
    if start_s is not None and end_s is not None and end_s > start_s:
        return end_s - start_s

    start_frame = _float_value(scene.get("start_frame"))
    end_frame = _float_value(scene.get("end_frame"))
    if start_frame is not None and end_frame is not None and end_frame > start_frame:
        return (end_frame - start_frame) / float(max(1, fps))

    return 1.0


def _scene_frame_bounds(scene: dict[str, Any], fps: int) -> tuple[int, int]:
    start_frame = _float_value(scene.get("start_frame"))
    end_frame = _float_value(scene.get("end_frame"))
    if start_frame is not None and end_frame is not None and end_frame > start_frame:
        return max(0, int(round(start_frame))), max(1, int(round(end_frame)))

    start_s = _float_value(scene.get("start_s"))
    end_s = _float_value(scene.get("end_s"))
    if start_s is not None and end_s is not None and end_s > start_s:
        return max(0, int(round(start_s * fps))), max(1, int(round(end_s * fps)))

    try:
        start = max(0, int(scene.get("start_frame") or 0))
    except Exception:
        start = 0
    duration_frames = max(1, int(round(_scene_duration_seconds(scene, fps) * float(max(1, fps)))))
    return start, start + duration_frames


def _render_samples_per_scene(scene_count: int) -> int:
    raw = (
        _kit_setting_string("/edmg/nvidia/render_samples_per_scene")
        or _kit_setting_string("/edmg/nvidia/render_worker_samples_per_scene")
        or os.environ.get("EDMG_NVIDIA_RENDER_SAMPLES_PER_SCENE", "")
    )
    try:
        requested = int(float(raw))
    except Exception:
        requested = 4
    requested = max(1, min(8, requested))
    if scene_count <= 0:
        return requested
    return max(1, min(requested, max(1, 96 // max(1, scene_count))))


def _timeline_capture_plan(
    timeline: list[dict[str, Any]],
    fps: int,
    samples_per_scene: int,
) -> list[dict[str, Any]]:
    capture_plan: list[dict[str, Any]] = []
    for scene_index, scene in enumerate(timeline, start=1):
        start_frame, end_frame = _scene_frame_bounds(scene, fps)
        end_frame = max(start_frame + 1, end_frame)
        max_unique_frames = max(1, end_frame - start_frame)
        scene_samples = max(1, min(samples_per_scene, max_unique_frames))
        frames: list[int] = []
        for sample_index in range(scene_samples):
            if scene_samples == 1:
                frame = start_frame
            else:
                frame = int(round(start_frame + ((end_frame - start_frame) * sample_index / float(scene_samples))))
            frame = min(max(start_frame, frame), end_frame - 1)
            if frames and frame <= frames[-1]:
                frame = min(end_frame - 1, frames[-1] + 1)
            if frame not in frames:
                frames.append(frame)
        for sample_index, frame in enumerate(frames, start=1):
            next_frame = frames[sample_index] if sample_index < len(frames) else end_frame
            duration_s = max(1.0 / float(max(1, fps)), (next_frame - frame) / float(max(1, fps)))
            capture_plan.append(
                {
                    "scene": scene,
                    "scene_index": scene_index,
                    "sample_index": sample_index,
                    "sample_count": len(frames),
                    "frame": frame,
                    "duration_s": duration_s,
                }
            )
    return capture_plan


def _scene_output_path(
    record: RenderJobRecord,
    scene: dict[str, Any],
    index: int,
    *,
    frame_override: int | None = None,
    sample_index: int | None = None,
) -> Path:
    output = scene.get("output") if isinstance(scene.get("output"), dict) else {}
    sequence_dir = str(output.get("sequence_dir") or "").strip()
    if sequence_dir:
        base = Path(sequence_dir)
        if base.is_absolute():
            out_dir = base
        else:
            out_dir = (record.project_root / sequence_dir).resolve()
    else:
        out_dir = record.still_path.parent
    if frame_override is None:
        try:
            frame = int(scene.get("start_frame") or 0)
        except Exception:
            frame = 0
    else:
        frame = max(0, int(frame_override))
    safe_id = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(scene.get("id") or f"scene_{index}"))
    sample_suffix = f"_{sample_index:02d}" if sample_index is not None else ""
    return out_dir / f"{index:03d}_{safe_id}{sample_suffix}_{frame:06d}.png"


def _resolve_project_output_path(record: RenderJobRecord, value: object) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    candidate = path if path.is_absolute() else (record.project_root / path)
    resolved = candidate.resolve()
    try:
        resolved.relative_to(record.project_root.resolve())
    except Exception as exc:
        raise RenderJobExecutionError(f"Render output path escapes project root: {raw}") from exc
    return resolved


def _movie_output_path(record: RenderJobRecord, timeline: list[dict[str, Any]]) -> Path:
    top_outputs = record.payload.get("outputs") if isinstance(record.payload.get("outputs"), dict) else {}
    for key in ("movie", "mp4", "final_mp4"):
        path = _resolve_project_output_path(record, top_outputs.get(key))
        if path is not None:
            return path

    for scene in timeline:
        output = scene.get("output") if isinstance(scene.get("output"), dict) else {}
        path = _resolve_project_output_path(record, output.get("movie"))
        if path is not None:
            return path

    fallback = record.project_root / "outputs" / "nvidia" / "renders" / f"{record.job_path.stem}.mp4"
    return fallback.resolve()


def _kit_setting_string(path: str) -> str:
    try:
        carb_settings = importlib.import_module("carb.settings")
        settings = carb_settings.get_settings()
        value = settings.get(path)
    except Exception:
        return ""
    return str(value or "").strip()


def _resolve_executable(candidate: str) -> str:
    raw = candidate.strip().strip('"')
    if not raw:
        return ""
    path = Path(raw)
    if path.exists():
        return str(path)
    resolved = shutil.which(raw)
    return resolved or raw


def _ffmpeg_executable() -> str:
    setting = _kit_setting_string("/edmg/nvidia/ffmpeg_path")
    env_value = os.environ.get("EDMG_FFMPEG_PATH", "").strip()
    candidates: list[str] = []
    if setting and setting.lower() not in {"ffmpeg", "ffmpeg.exe"}:
        candidates.append(setting)
    if env_value:
        candidates.append(env_value)
    if setting:
        candidates.append(setting)
    candidates.append("ffmpeg")
    for candidate in candidates:
        resolved = _resolve_executable(candidate)
        if resolved:
            return resolved
    return "ffmpeg"


def _ffmpeg_concat_file_line(path: Path) -> str:
    escaped = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{escaped}'"


def _write_movie_concat_manifest(movie_path: Path, frames: list[Path], durations: list[float]) -> Path:
    if not frames:
        raise RenderJobExecutionError("Cannot assemble movie without captured frames")
    if len(durations) < len(frames):
        durations = durations + [1.0] * (len(frames) - len(durations))

    concat_path = movie_path.with_name(f".{movie_path.stem}.concat.txt")
    lines: list[str] = []
    for frame_path, duration_s in zip(frames, durations):
        lines.append(_ffmpeg_concat_file_line(frame_path))
        lines.append(f"duration {max(0.001, float(duration_s)):.6f}")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return concat_path


def _assemble_movie_from_frames(
    record: RenderJobRecord,
    timeline: list[dict[str, Any]],
    frames: list[Path],
    fps: int,
    durations_override: list[float] | None = None,
) -> tuple[Path, Path]:
    movie_path = _movie_output_path(record, timeline)
    movie_path.parent.mkdir(parents=True, exist_ok=True)
    durations = (
        list(durations_override)
        if durations_override is not None
        else [_scene_duration_seconds(scene, fps) for scene in timeline[: len(frames)]]
    )
    concat_path = _write_movie_concat_manifest(movie_path, frames, durations)
    ffmpeg = _ffmpeg_executable()
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-vf",
        f"fps={max(1, int(fps))},format=yuv420p",
        "-movflags",
        "+faststart",
        str(movie_path),
    ]
    timeout_s = max(120, int(sum(durations) + 120))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout_s)
    except FileNotFoundError as exc:
        raise RenderJobExecutionError(f"FFmpeg executable was not found: {ffmpeg}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderJobExecutionError(f"FFmpeg timed out while assembling {movie_path}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        detail = f": {stderr}" if stderr else ""
        raise RenderJobExecutionError(f"FFmpeg failed while assembling {movie_path}{detail}") from exc
    if not movie_path.exists() or movie_path.stat().st_size <= 0:
        raise RenderJobExecutionError(f"FFmpeg did not create a non-empty movie: {movie_path}")
    return movie_path, concat_path


def _set_kit_renderer_options(width: int, height: int) -> None:
    try:
        carb_settings = importlib.import_module("carb.settings")
        settings = carb_settings.get_settings()
        settings.set("/app/renderer/resolution/width", width)
        settings.set("/app/renderer/resolution/height", height)
        settings.set("/renderer/enabled", "rtx,pxr")
        settings.set("/renderer/active", "rtx")
        settings.set("/rtx/rendermode", "RaytracedLighting")
    except Exception:
        return


def _set_timeline_frame(frame: int, fps: int) -> None:
    try:
        omni_timeline = importlib.import_module("omni.timeline")
        timeline = omni_timeline.get_timeline_interface()
        timeline.set_current_time(float(frame) / float(max(1, fps)))
    except Exception:
        return


def _set_viewport_resolution(viewport: object, width: int, height: int) -> None:
    try:
        viewport.resolution_scale = 1.0  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        viewport.resolution = (width, height)  # type: ignore[attr-defined]
        print(f"[edmg.render_queue] viewport resolution set to {width}x{height}")
    except Exception:
        return


async def _wait_for_viewport_frames(viewport: object, n_frames: int = 4) -> None:
    try:
        viewport_utility = importlib.import_module("omni.kit.viewport.utility")
        next_frame = getattr(viewport_utility, "next_viewport_frame_async")
        await asyncio.wait_for(next_frame(viewport, n_frames=n_frames), timeout=45)
        return
    except TimeoutError:
        print(f"[edmg.render_queue] viewport did not deliver {n_frames} frame(s) before timeout; continuing to capture")
    except Exception:
        await asyncio.sleep(0.25)


async def _open_stage(context: object, stage_path: Path) -> object:
    open_async = getattr(context, "open_stage_async", None)
    if callable(open_async):
        result, error = await open_async(str(stage_path))
        if not result:
            raise RenderJobExecutionError(f"Failed to open USDA stage: {error}")
    else:
        result = context.open_stage(str(stage_path))  # type: ignore[attr-defined]
        if result is False:
            raise RenderJobExecutionError(f"Failed to open USDA stage: {stage_path}")
    stage = context.get_stage()  # type: ignore[attr-defined]
    if stage is None:
        raise RenderJobExecutionError(f"USDA stage opened without an active stage: {stage_path}")
    return stage


def _set_viewport_camera(viewport: object, stage: object) -> str:
    candidates = (
        "/EDMGScenePlan/Cameras/MainCamera",
        "/EDMGScenePlan/MainCamera",
        "/World/MainCamera",
    )
    for camera_path in candidates:
        try:
            prim = stage.GetPrimAtPath(camera_path)  # type: ignore[attr-defined]
            if prim and prim.IsValid():
                try:
                    viewport.camera_path = camera_path  # type: ignore[attr-defined]
                except Exception:
                    set_active_camera = getattr(viewport, "set_active_camera", None)
                    if callable(set_active_camera):
                        set_active_camera(camera_path)
                return camera_path
        except Exception:
            continue
    return ""


async def _capture_viewport_frame(viewport: object, output_path: Path) -> None:
    viewport_utility = importlib.import_module("omni.kit.viewport.utility")
    capture = getattr(viewport_utility, "capture_viewport_to_file")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    capture_helper = capture(viewport, file_path=str(output_path))
    wait_for_result = getattr(capture_helper, "wait_for_result", None)
    if callable(wait_for_result):
        try:
            await asyncio.wait_for(wait_for_result(completion_frames=30), timeout=120)
        except TimeoutError as exc:
            raise RenderJobExecutionError(f"Viewport capture timed out for {output_path}") from exc
    else:
        try:
            renderer_capture = importlib.import_module("omni.kit.renderer_capture")
            renderer_capture.acquire_renderer_capture_interface().wait_async_capture()
        except Exception:
            await asyncio.sleep(0.5)
    if not output_path.exists():
        raise RenderJobExecutionError(f"Viewport capture did not create {output_path}")


async def execute_rtx_render_job(record: RenderJobRecord) -> dict[str, Any]:
    if record.stage_path is None or not record.stage_path.exists():
        raise RenderJobExecutionError(f"USDA stage not found for job {record.job_relpath}")

    width, height, fps = _renderer_settings(record.payload)
    _set_kit_renderer_options(width, height)
    print(f"[edmg.render_queue] opening stage {record.stage_path}")

    try:
        omni_usd = importlib.import_module("omni.usd")
        context = omni_usd.get_context()
        stage = await _open_stage(context, record.stage_path)
    except Exception as exc:
        raise RenderJobExecutionError(f"Failed to open USDA stage: {exc}") from exc

    try:
        viewport_utility = importlib.import_module("omni.kit.viewport.utility")
        viewport = viewport_utility.get_active_viewport()
    except Exception as exc:
        raise RenderJobExecutionError(f"Failed to acquire active viewport: {exc}") from exc
    if viewport is None:
        raise RenderJobExecutionError("No active viewport is available for RTX capture")

    _set_viewport_resolution(viewport, width, height)
    camera_path = _set_viewport_camera(viewport, stage)
    if camera_path:
        print(f"[edmg.render_queue] viewport camera set to {camera_path}")
    await _wait_for_viewport_frames(viewport, n_frames=12)
    await asyncio.sleep(0.5)

    frames: list[str] = []
    frame_paths: list[Path] = []
    frame_durations: list[float] = []
    start = time.monotonic()
    timeline = _timeline_scenes(record.payload)
    samples_per_scene = _render_samples_per_scene(len(timeline))
    capture_plan = _timeline_capture_plan(timeline, fps, samples_per_scene)
    print(
        f"[edmg.render_queue] capture plan: {len(capture_plan)} frame(s), "
        f"{samples_per_scene} sample(s) per scene target"
    )
    for item in capture_plan:
        scene = item["scene"]
        index = int(item["scene_index"])
        frame = int(item["frame"])
        sample_index = int(item["sample_index"])
        _set_timeline_frame(frame, fps)
        await _wait_for_viewport_frames(viewport, n_frames=2)
        out_path = _scene_output_path(record, scene, index, frame_override=frame, sample_index=sample_index)
        print(f"[edmg.render_queue] capturing scene {index} sample {sample_index} frame {frame} to {out_path}")
        await _capture_viewport_frame(viewport, out_path)
        frame_paths.append(out_path)
        frame_durations.append(float(item["duration_s"]))
        frames.append(_project_relpath(record, out_path))

    movie = ""
    movie_concat = ""
    renderer = record.payload.get("renderer") if isinstance(record.payload.get("renderer"), dict) else {}
    if str(renderer.get("output_format") or "").lower() == "mp4":
        print(f"[edmg.render_queue] assembling MP4 from {len(frame_paths)} captured frame(s)")
        movie_path, concat_path = _assemble_movie_from_frames(
            record,
            timeline,
            frame_paths,
            fps,
            durations_override=frame_durations,
        )
        movie = _project_relpath(record, movie_path)
        movie_concat = _project_relpath(record, concat_path)

    return {
        "still": frames[0] if frames else record.still_relpath,
        "frames": frames,
        "movie": movie,
        "movie_concat": movie_concat,
        "stage": record.stage_path.relative_to(record.project_root).as_posix(),
        "rendered_frame_count": len(frames),
        "rendered_scene_count": len(timeline),
        "render_samples_per_scene": samples_per_scene,
        "elapsed_s": round(time.monotonic() - start, 3),
    }

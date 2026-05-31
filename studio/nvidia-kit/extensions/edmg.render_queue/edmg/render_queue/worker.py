from __future__ import annotations

import asyncio
import importlib
import socket
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


def _timeline_scenes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    timeline = payload.get("timeline") if isinstance(payload.get("timeline"), list) else []
    scenes = [item for item in timeline if isinstance(item, dict)]
    if scenes:
        return scenes
    return [{"id": "preview", "start_frame": 0, "output": {}}]


def _scene_output_path(record: RenderJobRecord, scene: dict[str, Any], index: int) -> Path:
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
    try:
        frame = int(scene.get("start_frame") or 0)
    except Exception:
        frame = 0
    safe_id = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(scene.get("id") or f"scene_{index}"))
    return out_dir / f"{index:03d}_{safe_id}_{frame:06d}.png"


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
    start = time.monotonic()
    for index, scene in enumerate(_timeline_scenes(record.payload), start=1):
        try:
            frame = max(0, int(scene.get("start_frame") or 0))
        except Exception:
            frame = 0
        _set_timeline_frame(frame, fps)
        await _wait_for_viewport_frames(viewport, n_frames=2)
        out_path = _scene_output_path(record, scene, index)
        print(f"[edmg.render_queue] capturing frame {frame} to {out_path}")
        await _capture_viewport_frame(viewport, out_path)
        frames.append(out_path.relative_to(record.project_root).as_posix())

    movie = ""
    renderer = record.payload.get("renderer") if isinstance(record.payload.get("renderer"), dict) else {}
    if str(renderer.get("output_format") or "").lower() == "mp4":
        timeline = _timeline_scenes(record.payload)
        first_output = timeline[0].get("output") if timeline and isinstance(timeline[0].get("output"), dict) else {}
        movie = str(first_output.get("movie") or "")

    return {
        "still": frames[0] if frames else record.still_relpath,
        "frames": frames,
        "movie": movie,
        "stage": record.stage_path.relative_to(record.project_root).as_posix(),
        "rendered_frame_count": len(frames),
        "elapsed_s": round(time.monotonic() - start, 3),
    }

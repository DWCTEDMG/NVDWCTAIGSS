from __future__ import annotations

from pathlib import Path

import pytest

from edmg_studio_backend.services import internal_video as internal_video_service


def test_studio_proxy_workflow_smoke_rejects_retired_proxy_path(tmp_path: Path):
    project_dir = tmp_path / "project"
    (project_dir / "outputs" / "videos").mkdir(parents=True, exist_ok=True)

    settings = internal_video_service.InternalVideoSettings(
        fps_render=2,
        fps_output=4,
        width=320,
        height=180,
        interpolation_engine="fps",
        model_id="auto",
        resume_existing_frames=False,
    )

    with pytest.raises(RuntimeError, match="Proxy rendering is disabled"):
        internal_video_service.render_internal_proxy_video_variant(
            ffmpeg_path="ffmpeg",
            project_dir=project_dir,
            variant={"index": 0, "duration_s": 2.0},
            scenes=[{"start_s": 0.0, "end_s": 2.0, "prompt": "retired proxy path"}],
            audio_path=None,
            settings=settings,
            timeline={"layers": []},
        )

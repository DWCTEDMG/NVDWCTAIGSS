from __future__ import annotations

import pytest

from edmg_studio_backend import app as studio_app
from edmg_studio_backend.schemas import InternalVideoRenderRequest
from edmg_studio_backend.services.deforum_motion import evaluate_motion_state
from edmg_studio_backend.services.deforum_normalize import build_deforum_render_context
from edmg_studio_backend.services.deforum_prompt_timeline import resolve_prompt_frame


def test_variant_motion_schedules_feed_unified_internal_context():
    ctx = build_deforum_render_context(
        scenes=[
            {"start_s": 0.0, "end_s": 1.0, "prompt": "opening tableau", "negative_prompt": "blurry"},
            {"start_s": 1.0, "end_s": 2.0, "prompt": "drop sequence", "negative_prompt": "muddy"},
        ],
        timeline={},
        variant={
            "motion_schedules": {
                "zoom": "0:(1.0), 24:(1.2)",
                "angle": "0:(0), 24:(12)",
                "translation_x": "0:(0), 24:(48)",
                "translation_y": "0:(0), 24:(-24)",
                "strength_schedule": "0:(0.25), 24:(0.65)",
            }
        },
        fps=24,
        default_negative_prompt="blurry",
    )

    motion = evaluate_motion_state(12, ctx.motion)

    assert resolve_prompt_frame(ctx.prompts, 0) == "opening tableau"
    assert resolve_prompt_frame(ctx.prompts, 30) == "drop sequence"
    assert motion.zoom == pytest.approx(1.1)
    assert motion.angle == pytest.approx(6.0)
    assert motion.translation_x == pytest.approx(24.0)
    assert motion.translation_y == pytest.approx(-12.0)
    assert motion.strength == pytest.approx(0.45)


def test_internal_video_request_deforum_overrides_take_precedence():
    req = InternalVideoRenderRequest(
        deforum_prompts={"0": "wide neon avenue", "24": "drop city burst"},
        deforum_negative_prompts={"0": "blurry, watermark", "24": "overexposed"},
        deforum_zoom="0:(1.0), 24:(1.24)",
        deforum_angle={"0": 0.0, "24": 8.0},
        deforum_translation_x={"0": 0.0, "24": 24.0},
        deforum_translation_y="0:(0), 24:(-12)",
        deforum_strength_schedule="0:(0.20), 24:(0.60)",
    )

    settings = studio_app._internal_settings_from_payload(
        studio_app._request_payload(req),
        model_id="hf_sd15_internal",
        render_tier="draft",
        device_preference="cuda",
    )
    ctx = build_deforum_render_context(
        scenes=[{"start_s": 0.0, "end_s": 2.0, "prompt": "scene prompt", "negative_prompt": "scene neg"}],
        timeline={"tracks": []},
        variant={"motion_schedules": {"zoom": "0:(1.8)"}},
        fps=24,
        default_negative_prompt=settings.negative_prompt,
        overrides=settings.deforum_overrides,
    )

    motion = evaluate_motion_state(12, ctx.motion)

    assert resolve_prompt_frame(ctx.prompts, 30) == "drop city burst"
    assert resolve_prompt_frame(ctx.negative_prompts, 12) == "blurry, watermark"
    assert motion.zoom == pytest.approx(1.12)
    assert motion.angle == pytest.approx(4.0)
    assert motion.translation_x == pytest.approx(12.0)
    assert motion.translation_y == pytest.approx(-6.0)
    assert motion.strength == pytest.approx(0.4)

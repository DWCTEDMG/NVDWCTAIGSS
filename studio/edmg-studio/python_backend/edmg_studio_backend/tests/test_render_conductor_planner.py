from __future__ import annotations

from edmg_studio_backend.render_conductor.planner import build_advisory_render_plan
from edmg_studio_backend.schemas import (
    EngineOutcomeMemory,
    ProjectSnapshot,
    ProjectVisualDNA,
    RenderIntent,
)


def test_advisory_render_plan_routes_by_scene_profile_and_dna_bias():
    visual_dna = ProjectVisualDNA(
        project_id="proj-123",
        identity={"motifs": ["neon skyline", "lead silhouette"]},
        prompt_guidance={"positive_fragments": ["cinematic night performance"]},
        engine_memory={
            "internal": EngineOutcomeMemory(
                success_rate=0.95,
                best_for=["continuity-heavy sequences"],
            )
        },
        learning_state={"confidence": 0.82},
    )
    snapshot = ProjectSnapshot(
        project_id="proj-123",
        project_name="Neon Drive",
        analysis={
            "sections": [
                {"start_s": 0.0, "end_s": 5.0, "energy": 0.42},
                {"start_s": 5.0, "end_s": 8.0, "energy": 0.92},
            ]
        },
        plan={
            "variants": [
                {
                    "scenes": [
                        {
                            "id": "scene-1",
                            "start_s": 0.0,
                            "end_s": 5.0,
                            "prompt": "keep the same reflective-jacket lead in a neon skyline",
                            "continuity_note": "preserve the same lead silhouette",
                            "approved": True,
                        },
                        {
                            "id": "scene-2",
                            "start_s": 5.0,
                            "end_s": 7.0,
                            "prompt": "hero glitch burst transition with strobe impact",
                        },
                    ]
                }
            ]
        },
        timeline={},
        visual_dna=visual_dna,
    )
    intent = RenderIntent(
        project_id="proj-123",
        variant_index=0,
        quality_tier="balanced",
        continuity_priority=0.6,
        speed_priority=0.8,
        style_lock_strength=0.8,
        sections=[
            {
                "scene_id": "scene-2",
                "continuity_priority": 0.1,
                "creative_goal": "make the transition hit harder than continuity lock",
            }
        ],
    )
    environment = {
        "engines": {
            "internal": {"available": True, "quality_score": 0.9, "speed_score": 0.55},
            "comfyui_motion": {"available": True, "quality_score": 0.82, "speed_score": 0.62},
            "comfyui_still": {"available": True, "quality_score": 0.88, "speed_score": 0.4},
            "deforum_export": {"available": False},
        }
    }

    plan = build_advisory_render_plan(intent, snapshot, environment=environment)

    assert plan.advisory_only is True
    assert len(plan.sections) == 2
    assert plan.sections[0].scene_id == "scene-1"
    assert plan.sections[0].engine == "internal"
    assert any(step.kind == "repair_continuity" for step in plan.sections[0].steps)
    assert "project DNA motifs in play" in plan.sections[0].rationale

    assert plan.sections[1].scene_id == "scene-2"
    assert plan.sections[1].engine == "comfyui_motion"
    assert any(step.kind == "render_motion" for step in plan.sections[1].steps)
    assert plan.fallback_branches[1].reroute_to == "internal"
    assert any(item.startswith("visual_dna_confidence=") for item in plan.diagnostics)
    assert "nvidia_only=true" in plan.diagnostics


def test_advisory_render_plan_fails_closed_to_internal_when_no_engine_is_available():
    snapshot = ProjectSnapshot(
        project_id="proj-789",
        plan={
            "variants": [
                {
                    "scenes": [
                        {
                            "id": 1,
                            "start_s": 0.0,
                            "end_s": 3.0,
                            "prompt": "short test scene",
                        }
                    ]
                }
            ]
        },
    )
    intent = RenderIntent(project_id="proj-789", variant_index=0)

    plan = build_advisory_render_plan(
        intent,
        snapshot,
        environment={
            "engines": {
                "internal": {"available": False},
                "comfyui_still": {"available": False},
                "comfyui_motion": {"available": False},
                "deforum_export": {"available": False},
            }
        },
    )

    assert len(plan.sections) == 1
    assert plan.sections[0].engine == "internal"
    assert plan.fallback_branches == []
    assert "nvidia_only=true" in plan.diagnostics
    assert plan.assembly.expected_output_path.endswith(".mp4")

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from ..schemas import (
    AssemblyPlan,
    EngineKind,
    FallbackBranch,
    ProjectSnapshot,
    ProjectVisualDNA,
    RenderIntent,
    RenderPlan,
    RenderSectionPlan,
    RenderStep,
)

NVIDIA_RENDER_ENGINES: tuple[EngineKind, ...] = (
    "internal",
    "comfyui_motion",
    "comfyui_still",
    "deforum_export",
)


@dataclass
class CapabilityReport:
    engine: str
    available: bool
    continuity_score: float
    motion_score: float
    speed_score: float
    quality_score: float
    requirements: dict[str, Any]
    warnings: list[str]


class RenderAdapter(Protocol):
    name: str

    def probe(self, context: dict[str, Any]) -> CapabilityReport: ...

    def estimate(self, intent: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]: ...

    def build_steps(self, intent: dict[str, Any], section: dict[str, Any]) -> list[dict[str, Any]]: ...

    def execute_step(self, step: dict[str, Any], job_context: dict[str, Any]) -> dict[str, Any]: ...


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clamp_unit(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        number = float(default)
    if number != number:
        number = float(default)
    return max(0.0, min(1.0, number))


def _scene_id(scene: dict[str, Any], index: int) -> str:
    raw = scene.get("id")
    if raw is None:
        return f"scene-{index + 1}"
    return str(raw)


def _quality_multiplier(intent: RenderIntent) -> float:
    return {
        "draft": 0.75,
        "balanced": 1.0,
        "quality": 1.3,
        "ultra": 1.55,
    }.get(intent.quality_tier, 1.0)


def _environment_engines(environment: dict[str, Any] | None, intent: RenderIntent) -> dict[str, dict[str, Any]]:
    payload = environment if isinstance(environment, dict) else {}
    raw_engines = payload.get("engines") if isinstance(payload.get("engines"), dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for engine in _nvidia_allowed_engines(intent.allowed_engines):
        details = raw_engines.get(engine)
        if isinstance(details, dict):
            out[engine] = {"available": bool(details.get("available", True)), **details}
        elif isinstance(details, bool):
            out[engine] = {"available": bool(details)}
        else:
            out[engine] = {"available": engine == "internal"}
    return out


def _nvidia_allowed_engines(engines: list[EngineKind] | tuple[EngineKind, ...]) -> list[EngineKind]:
    selected = [engine for engine in engines if engine in NVIDIA_RENDER_ENGINES]
    return selected or ["internal"]


def _resolve_variant(snapshot: ProjectSnapshot, variant_index: int) -> dict[str, Any]:
    plan = snapshot.plan if isinstance(snapshot.plan, dict) else {}
    variants = plan.get("variants") if isinstance(plan.get("variants"), list) else []
    if not variants:
        return {}
    safe_index = max(0, min(int(variant_index), len(variants) - 1))
    variant = variants[safe_index]
    return variant if isinstance(variant, dict) else {}


def _analysis_sections(snapshot: ProjectSnapshot) -> list[dict[str, Any]]:
    analysis = snapshot.analysis if isinstance(snapshot.analysis, dict) else {}
    sections = analysis.get("sections")
    if isinstance(sections, list):
        return [section for section in sections if isinstance(section, dict)]
    return []


def _scene_energy(scene: dict[str, Any], snapshot: ProjectSnapshot) -> float:
    for key in ("energy", "avgEnergy", "energy_score"):
        if key in scene:
            return _clamp_unit(scene.get(key), 0.5)
    score = scene.get("score") if isinstance(scene.get("score"), dict) else {}
    for key in ("energy", "impact", "pace"):
        if key in score:
            return _clamp_unit(score.get(key), 0.5)
    target_midpoint = ((float(scene.get("start_s") or 0.0) + float(scene.get("end_s") or 0.0)) / 2.0)
    for section in _analysis_sections(snapshot):
        try:
            section_start = float(section.get("start_s") or section.get("startTime") or 0.0)
            section_end = float(section.get("end_s") or section.get("endTime") or section_start)
        except Exception:
            continue
        if section_start <= target_midpoint <= max(section_start, section_end):
            return _clamp_unit(section.get("energy") or section.get("avgEnergy"), 0.5)
    return 0.5


def _motion_complexity(scene: dict[str, Any], energy: float) -> float:
    prompt = " ".join(
        str(scene.get(key) or "")
        for key in ("name", "prompt", "transition_cue", "creative_goal", "continuity_note")
    ).lower()
    duration_s = max(0.0, float(scene.get("end_s") or 0.0) - float(scene.get("start_s") or 0.0))
    complexity = energy * 0.55
    if duration_s <= 4.0:
        complexity += 0.12
    if any(token in prompt for token in ("burst", "drop", "spin", "whip", "glitch", "strobe", "impact", "transition")):
        complexity += 0.22
    if any(token in prompt for token in ("slow", "hold", "portrait", "steady")):
        complexity -= 0.12
    return _clamp_unit(complexity, 0.45)


def _continuity_priority(scene: dict[str, Any], intent: RenderIntent, dna: ProjectVisualDNA | None) -> float:
    prompt = " ".join(str(scene.get(key) or "") for key in ("continuity_note", "prompt", "name")).lower()
    continuity = float(intent.continuity_priority)
    if any(token in prompt for token in ("same", "match", "preserve", "keep")):
        continuity += 0.15
    if dna and dna.continuity.subject_anchors:
        continuity += 0.08
    return _clamp_unit(continuity, intent.continuity_priority)


def _hero_frame(scene: dict[str, Any]) -> float:
    prompt = " ".join(str(scene.get(key) or "") for key in ("name", "prompt", "creative_goal")).lower()
    if any(token in prompt for token in ("hero", "close-up", "poster", "cover", "chorus")):
        return 1.0
    if bool(scene.get("approved")):
        return 0.65
    return 0.25


def _engine_bias_from_dna(
    dna: ProjectVisualDNA | None,
    engine: str,
    *,
    continuity_priority: float,
    motion_complexity: float,
    hero_frame: float,
) -> float:
    if dna is None:
        return 0.0
    memory = dna.engine_memory.get(engine)
    if memory is None:
        return 0.0
    bias = float(memory.success_rate) * 0.18
    context_tags: list[str] = []
    if continuity_priority >= 0.7:
        context_tags.append("continuity")
    if motion_complexity >= 0.65:
        context_tags.append("motion")
    if hero_frame >= 0.8:
        context_tags.append("hero")
    for tag in context_tags:
        if any(tag in item.lower() for item in memory.best_for):
            bias += 0.08
        if any(tag in item.lower() for item in memory.avoid_for):
            bias -= 0.12
    return bias


def _engine_score(
    engine: EngineKind,
    *,
    continuity_priority: float,
    motion_complexity: float,
    hero_frame: float,
    intent: RenderIntent,
    dna: ProjectVisualDNA | None,
    engine_info: dict[str, Any],
    scene: dict[str, Any],
) -> float:
    speed = float(intent.speed_priority)
    style_lock = float(intent.style_lock_strength)
    quality_bonus = (float(_quality_multiplier(intent)) - 1.0) * 0.12
    has_reference = bool(scene.get("reference_asset") or scene.get("source_asset"))
    base = 0.0

    if engine == "internal":
        base = 0.56 + continuity_priority * 0.42 + style_lock * 0.22 - speed * 0.14 + quality_bonus
        if motion_complexity >= 0.85:
            base -= 0.08
    elif engine == "comfyui_motion":
        base = 0.38 + motion_complexity * 0.46 + hero_frame * 0.12 - continuity_priority * 0.12 + speed * 0.06
    elif engine == "comfyui_still":
        base = 0.34 + hero_frame * 0.28 + style_lock * 0.12 + (0.18 if has_reference else 0.0) - motion_complexity * 0.24
    elif engine == "deforum_export":
        base = 0.18 + continuity_priority * 0.08

    base += _engine_bias_from_dna(
        dna,
        engine,
        continuity_priority=continuity_priority,
        motion_complexity=motion_complexity,
        hero_frame=hero_frame,
    )
    base += (float(engine_info.get("quality_score", 0.5)) - 0.5) * 0.1
    base += (float(engine_info.get("speed_score", 0.5)) - 0.5) * 0.06

    if not bool(engine_info.get("available", False)):
        return -1.0
    return base


def _estimate_section(engine: EngineKind, duration_s: float, intent: RenderIntent) -> tuple[float, float]:
    quality = _quality_multiplier(intent)
    if engine == "internal":
        return (duration_s * 110.0 * quality, duration_s * 0.12 * quality)
    if engine == "comfyui_motion":
        return (duration_s * 85.0 * quality, duration_s * 0.18 * quality)
    if engine == "comfyui_still":
        still_factor = max(1.0, duration_s / 3.0)
        return (still_factor * 18.0 * quality, still_factor * 0.16 * quality)
    if engine == "deforum_export":
        return (duration_s * 24.0, duration_s * 0.05)
    return (duration_s * 6.0, duration_s * 0.02)


def _continuity_risk(engine: EngineKind, continuity_priority: float, style_lock: float) -> float:
    baseline = {
        "internal": 0.18,
        "comfyui_motion": 0.42,
        "comfyui_still": 0.62,
        "deforum_export": 0.38,
    }.get(engine, 0.4)
    risk = baseline + continuity_priority * 0.18 - style_lock * 0.1
    return _clamp_unit(risk, baseline)


def _fallback_engine(
    primary: EngineKind,
    engines: dict[str, dict[str, Any]],
    intent: RenderIntent,
) -> EngineKind:
    preferred_orders: dict[EngineKind, list[EngineKind]] = {
        "internal": ["comfyui_motion", "comfyui_still", "deforum_export"],
        "comfyui_motion": ["internal", "comfyui_still", "deforum_export"],
        "comfyui_still": ["internal", "comfyui_motion", "deforum_export"],
        "deforum_export": ["internal", "comfyui_motion", "comfyui_still"],
    }
    for candidate in preferred_orders.get(primary, ["internal"]):
        if candidate in intent.allowed_engines and bool(engines.get(candidate, {}).get("available", False)):
            return candidate
    return primary if primary in NVIDIA_RENDER_ENGINES else "internal"


def _section_steps(
    *,
    scene: dict[str, Any],
    engine: EngineKind,
    continuity_priority: float,
    intent: RenderIntent,
    dna: ProjectVisualDNA | None,
) -> list[RenderStep]:
    scene_id = str(scene.get("id") or scene.get("scene_id"))
    duration_s = max(0.5, float(scene.get("end_s") or 0.0) - float(scene.get("start_s") or 0.0))
    prompt_hints = {
        "motifs": list((dna.identity.motifs if dna else [])[:4]),
        "positive_fragments": list((dna.prompt_guidance.positive_fragments if dna else [])[:4]),
        "negative_fragments": list((dna.prompt_guidance.negative_fragments if dna else [])[:4]),
    }
    steps: list[RenderStep] = [
        RenderStep(
            id=f"{scene_id}-prepare",
            kind="prepare_assets",
            adapter="system",
            inputs={"scene_id": scene_id, "duration_s": duration_s},
            outputs={"asset_bundle": f"scene:{scene_id}:assets"},
            notes=["Resolve audio-reactive context, overlays, and references before engine routing."],
        ),
        RenderStep(
            id=f"{scene_id}-prompt",
            kind="build_prompt",
            adapter="system",
            inputs={"scene_prompt": scene.get("prompt"), "dna_hints": prompt_hints},
            outputs={"prompt_bundle": f"scene:{scene_id}:prompt"},
            notes=["Blend project DNA into the prompt bundle without mutating the saved storyboard."],
        ),
    ]
    if engine == "comfyui_still":
        steps.append(
            RenderStep(
                id=f"{scene_id}-still",
                kind="render_still",
                adapter=engine,
                inputs={"scene_id": scene_id, "duration_s": duration_s},
                outputs={"anchor_frames": f"scene:{scene_id}:anchors"},
                notes=["Use still anchors when the scene benefits from reference-driven hero framing."],
            )
        )
        steps.append(
            RenderStep(
                id=f"{scene_id}-interpolate",
                kind="interpolate",
                adapter="system",
                inputs={"scene_id": scene_id},
                outputs={"clip": f"scene:{scene_id}:clip"},
                notes=["Promote anchor frames into a usable motion section for assembly."],
            )
        )
    else:
        steps.append(
            RenderStep(
                id=f"{scene_id}-motion",
                kind="render_motion",
                adapter=engine,
                inputs={"scene_id": scene_id, "duration_s": duration_s},
                outputs={"clip": f"scene:{scene_id}:clip"},
                notes=["Scene routes through the selected motion-capable engine in advisory mode."],
            )
        )
        if intent.output_mode == "full_video" and engine == "internal":
            steps.append(
                RenderStep(
                    id=f"{scene_id}-interp",
                    kind="interpolate",
                    adapter="system",
                    inputs={"scene_id": scene_id},
                    outputs={"smoothed_clip": f"scene:{scene_id}:clip:interp"},
                    notes=["Interpolation stays explicit so conductor plans can trade speed against polish later."],
                )
            )
    if continuity_priority >= 0.72 and engine != "deforum_export":
        steps.append(
            RenderStep(
                id=f"{scene_id}-repair",
                kind="repair_continuity",
                adapter="system",
                inputs={"scene_id": scene_id, "engine": engine},
                outputs={"repaired_clip": f"scene:{scene_id}:clip:repaired"},
                notes=["Continuity repair is inserted when project identity or subject anchors need protection."],
            )
        )
    steps.append(
        RenderStep(
            id=f"{scene_id}-validate",
            kind="validate",
            adapter="system",
            inputs={"scene_id": scene_id},
            outputs={"validated": True},
            notes=["Validation remains advisory for now; execution is still handled by existing Studio routes."],
        )
    )
    return steps


def build_advisory_render_plan(
    intent: RenderIntent | dict[str, Any],
    snapshot: ProjectSnapshot | dict[str, Any],
    *,
    environment: dict[str, Any] | None = None,
) -> RenderPlan:
    intent_obj = intent if isinstance(intent, RenderIntent) else RenderIntent.model_validate(intent)
    allowed_engines = _nvidia_allowed_engines(intent_obj.allowed_engines)
    snapshot_obj = snapshot if isinstance(snapshot, ProjectSnapshot) else ProjectSnapshot.model_validate(snapshot)
    dna = snapshot_obj.visual_dna
    variant = _resolve_variant(snapshot_obj, intent_obj.variant_index)
    scenes = [scene for scene in list(variant.get("scenes") or []) if isinstance(scene, dict)]
    engines = _environment_engines(environment, intent_obj)

    section_lookup = {section.scene_id: section for section in intent_obj.sections}
    plans: list[RenderSectionPlan] = []
    fallbacks: list[FallbackBranch] = []
    engine_counts: dict[str, int] = {}

    for index, scene in enumerate(scenes):
        scene_id = _scene_id(scene, index)
        override = section_lookup.get(scene_id)
        duration_s = max(0.5, float(scene.get("end_s") or 0.0) - float(scene.get("start_s") or 0.0))
        energy = _scene_energy(scene, snapshot_obj)
        motion_complexity = _motion_complexity(scene, energy)
        continuity_priority = _continuity_priority(scene, intent_obj, dna)
        if override and override.continuity_priority is not None:
            continuity_priority = _clamp_unit(override.continuity_priority, continuity_priority)
        hero_frame = _hero_frame(scene)

        chosen_engine: EngineKind = allowed_engines[0]
        chosen_score = -1.0
        rationale_parts: list[str] = []
        for engine in allowed_engines:
            score = _engine_score(
                engine,
                continuity_priority=continuity_priority,
                motion_complexity=motion_complexity,
                hero_frame=hero_frame,
                intent=intent_obj,
                dna=dna,
                engine_info=engines.get(engine, {"available": False}),
                scene=scene,
            )
            if score > chosen_score:
                chosen_engine = engine
                chosen_score = score
        if not bool(engines.get(chosen_engine, {}).get("available", False)):
            chosen_engine = _fallback_engine(chosen_engine, engines, intent_obj)

        if chosen_engine == "internal":
            rationale_parts.append("continuity bias and style lock favor the internal renderer")
        elif chosen_engine == "comfyui_motion":
            rationale_parts.append("short, kinetic motion profile favors a ComfyUI motion pass")
        elif chosen_engine == "comfyui_still":
            rationale_parts.append("hero framing or reference-driven styling favors a still-anchor path")
        elif chosen_engine == "deforum_export":
            rationale_parts.append("scene is a better fit for Deforum-aligned export than direct execution")
        else:
            rationale_parts.append("NVIDIA execution is required; unavailable adapters fail closed")

        if dna and dna.identity.motifs:
            rationale_parts.append(f"project DNA motifs in play: {', '.join(dna.identity.motifs[:3])}")
        if override and override.creative_goal:
            rationale_parts.append(f"creative goal: {override.creative_goal}")
        steps = _section_steps(
            scene={**scene, "id": scene_id},
            engine=chosen_engine,
            continuity_priority=continuity_priority,
            intent=intent_obj,
            dna=dna,
        )
        estimated_seconds, estimated_cost = _estimate_section(chosen_engine, duration_s, intent_obj)
        continuity_risk = _continuity_risk(
            chosen_engine,
            continuity_priority=continuity_priority,
            style_lock=float(intent_obj.style_lock_strength),
        )
        plans.append(
            RenderSectionPlan(
                scene_id=scene_id,
                engine=chosen_engine,
                rationale=". ".join(rationale_parts),
                estimated_cost=round(float(estimated_cost), 3),
                estimated_seconds=round(float(estimated_seconds), 1),
                continuity_risk=round(float(continuity_risk), 3),
                steps=steps,
                notes=[
                    f"energy={energy:.2f}",
                    f"motion_complexity={motion_complexity:.2f}",
                    f"continuity_priority={continuity_priority:.2f}",
                ],
            )
        )
        fallback = _fallback_engine(chosen_engine, engines, intent_obj)
        if fallback != chosen_engine and fallback in NVIDIA_RENDER_ENGINES:
            fallbacks.append(
                FallbackBranch(
                    trigger=f"{scene_id}:{chosen_engine}:unavailable",
                    reroute_to=fallback,
                    notes=f"Reroute to NVIDIA engine {fallback} if the recommended adapter is unavailable when the plan is executed.",
                )
            )
        engine_counts[chosen_engine] = engine_counts.get(chosen_engine, 0) + 1

    engine_summary = ", ".join(f"{engine} x{count}" for engine, count in sorted(engine_counts.items()))
    diagnostics = [
        "advisory_only=true",
        f"allowed_engines={','.join(allowed_engines)}",
        f"available_engines={','.join(engine for engine, info in engines.items() if info.get('available'))}",
        "nvidia_only=true",
    ]
    if dna is not None:
        diagnostics.append(f"visual_dna_confidence={dna.learning_state.confidence:.2f}")
    assembly_path = (
        f"outputs/videos/conductor_variant_{int(intent_obj.variant_index):02d}.mp4"
        if intent_obj.output_mode == "full_video"
        else f"outputs/scene_batches/conductor_variant_{int(intent_obj.variant_index):02d}"
    )
    summary = (
        f"Advisory render plan for {len(plans)} scenes. "
        f"Recommended engine mix: {engine_summary or 'none'}."
    )
    return RenderPlan(
        plan_id=f"plan-{uuid.uuid4().hex[:12]}",
        project_id=intent_obj.project_id,
        variant_index=int(intent_obj.variant_index),
        created_at=_utc_now(),
        advisory_only=True,
        summary=summary,
        sections=plans,
        assembly=AssemblyPlan(
            mode="audio_mux" if intent_obj.output_mode == "full_video" else "scene_bundle",
            expected_output_path=assembly_path,
        ),
        fallback_branches=fallbacks,
        diagnostics=diagnostics,
    )

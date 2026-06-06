from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Any, Literal

ConditioningMode = Literal["raw", "blur", "edge", "external"]
DiffusionWorkflowFamily = Literal["auto", "txt2img", "img2img", "inpaint", "outpaint", "controlnet"]


class LoraSelection(BaseModel):
    name: str = Field(min_length=1, max_length=260)
    weight: float = Field(default=1.0, ge=-4.0, le=4.0)
    clip_weight: float | None = Field(default=None, ge=-4.0, le=4.0)


class ControlNetUnit(BaseModel):
    model: str = Field(min_length=1, max_length=260)
    reference_asset: str = Field(min_length=1, max_length=1024)
    conditioning_mode: ConditioningMode = "raw"
    strength: float = Field(default=0.8, ge=0.0, le=2.0)
    start_percent: float = Field(default=0.0, ge=0.0, le=1.0)
    end_percent: float = Field(default=1.0, ge=0.0, le=1.0)


class HiresFixSettings(BaseModel):
    enabled: bool = True
    scale: float = Field(default=1.5, ge=1.0, le=4.0)
    steps: int | None = Field(default=None, ge=1, le=80)
    denoise: float = Field(default=0.35, ge=0.0, le=1.0)
    upscaler: str | None = None


class RefinerSettings(BaseModel):
    model: str | None = None
    switch_at: float = Field(default=0.8, ge=0.0, le=1.0)
    steps: int | None = Field(default=None, ge=1, le=80)


class OutpaintSettings(BaseModel):
    top_px: int = Field(default=0, ge=0, le=4096)
    right_px: int = Field(default=0, ge=0, le=4096)
    bottom_px: int = Field(default=0, ge=0, le=4096)
    left_px: int = Field(default=0, ge=0, le=4096)

class HealthResponse(BaseModel):
    ok: bool = True
    version: str = "1.1.0"

class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)

class PlanRequest(BaseModel):
    title: str | None = None
    user_notes: str | None = None
    style_prefs: str | None = None
    num_variants: int = Field(default=3, ge=1, le=10)
    max_scenes: int = Field(default=12, ge=1, le=64)

class ApplyPlanRequest(BaseModel):
    variant_index: int = 0
    overwrite: bool = False


class StoryboardVariantUpdateRequest(BaseModel):
    variant_index: int = 0
    scenes: list[dict[str, Any]] = Field(default_factory=list)

class RenderScenesRequest(BaseModel):
    """Render one still image per scene."""
    variant_index: int = 0
    model_id: str | None = None
    checkpoint: str | None = None  # optional checkpoint filename for ComfyUI
    workflow_family: DiffusionWorkflowFamily = "auto"
    seed: int | None = None
    reference_asset: str | None = None
    source_asset: str | None = None
    inpaint_mask: str | None = None
    outpaint: OutpaintSettings | None = None
    conditioning_mode: ConditioningMode = "raw"
    controlnet_model: str | None = None
    controlnet_strength: float = Field(default=0.8, ge=0.0, le=2.0)
    loras: list[LoraSelection] = Field(default_factory=list)
    controlnet_units: list[ControlNetUnit] = Field(default_factory=list)
    vae: str | None = None
    denoise_strength: float = Field(default=0.75, ge=0.0, le=1.0)
    hires_fix: HiresFixSettings | None = None
    refiner: RefinerSettings | None = None
    upscaler: str | None = None
    width: int = 1024
    height: int = 576
    steps: int = 28
    cfg: float = 7.0
    sampler: str = "euler"
    negative_prompt: str = "blurry, low quality, watermark, text, logo"

MotionEngine = Literal["animatediff","svd"]
CreativePreset = Literal["cinematic", "psychedelic", "ambient"]

class RenderMotionRequest(BaseModel):
    """Render motion clips per scene via ComfyUI (AnimateDiff or SVD)."""
    variant_index: int = 0
    model_id: str | None = None
    checkpoint: str | None = None  # optional base checkpoint filename for ComfyUI
    svd_model_id: str | None = None
    engine: MotionEngine = "animatediff"
    seed: int | None = None

    # Output / timeline
    fps: int = Field(default=12, ge=1, le=60)
    max_frames_per_scene: int = Field(default=240, ge=1, le=4000)  # cap long scenes by default

    # Base SD render settings (for prompts / keyframes)
    width: int = 768
    height: int = 432
    steps: int = 24
    cfg: float = 6.5
    sampler: str = "euler"
    negative_prompt: str = "blurry, low quality, watermark, text, logo"
    loras: list[LoraSelection] = Field(default_factory=list)
    vae: str | None = None

    # AnimateDiff Evolved settings
    motion_model_name: str = "mm_sd_v15_v2.ckpt"
    context_length: int = 16
    context_overlap: int = 4
    beta_schedule: str = "autoselect"

    # SVD settings (only used if engine == 'svd')
    svd_checkpoint: str = "svd_xt.safetensors"
    svd_num_steps: int = 25
    svd_motion_bucket_id: int = 127
    svd_fps_id: int = 6
    svd_cond_aug: float = 0.02
    svd_decoding_t: int = 14
    device: Literal["cuda","cpu"] = "cuda"

class AssembleVideoRequest(BaseModel):
    variant_index: int = 0
    fps: int = 30

class InternalVideoRenderRequest(BaseModel):
    """Render a full video using the internal renderer.

    Modes:
      - auto: prefer diffusion if an internal model is installed, otherwise fall back to proxy
      - diffusion: require an internal diffusion model
      - hosted: use the configured hosted still-image provider for keyframes, then assemble locally
      - proxy: render a local draft video using timeline compositing only
    """
    variant_index: int = 0

    fps_output: int = Field(default=24, ge=1, le=60)
    fps_render: int = Field(default=2, ge=1, le=30)
    width: int = Field(default=768, ge=256, le=1920)
    height: int = Field(default=432, ge=256, le=1080)

    steps: int = Field(default=15, ge=1, le=80)
    cfg: float = Field(default=7.0, ge=1.0, le=20.0)
    sampler: str = "euler"
    seed: int | None = None
    keyframe_interval_s: float = Field(default=5.0, ge=0.5, le=60.0)

    interpolation_engine: Literal["auto","minterpolate","fps","rife"] = "auto"
    model_id: str = "auto"
    render_mode: Literal["auto","diffusion","hosted","proxy"] = "auto"
    render_tier: Literal["auto","draft","balanced","quality"] = "auto"
    device_preference: Literal["auto","cpu","cuda","mps","directml"] = "auto"
    allow_hosted_fallback: bool = True
    allow_proxy_fallback: bool = True
    hosted_service: Literal["default","core","ultra","sd3"] = "default"
    hosted_model: str | None = None
    hosted_style_preset: str | None = None
    negative_prompt: str = "blurry, low quality, watermark, text, logo"
    loras: list[LoraSelection] = Field(default_factory=list)
    vae: str | None = None
    refiner: RefinerSettings | None = None

    temporal_mode: Literal["off","keyframes","frame_img2img"] = "keyframes"
    temporal_strength: float = Field(default=0.35, ge=0.01, le=0.99)
    temporal_steps: int | None = Field(default=None, ge=1, le=80)
    refine_every_n_frames: int = Field(default=1, ge=1, le=30)
    anchor_strength: float = Field(default=0.20, ge=0.0, le=1.0)
    prompt_blend: bool = True
    resume_existing_frames: bool = True
    deforum_prompts: dict[str, str] | None = None
    deforum_negative_prompts: dict[str, str] | None = None
    deforum_zoom: str | dict[str, float] | None = None
    deforum_angle: str | dict[str, float] | None = None
    deforum_translation_x: str | dict[str, float] | None = None
    deforum_translation_y: str | dict[str, float] | None = None
    deforum_strength_schedule: str | dict[str, float] | None = None

class TimelineUpdateRequest(BaseModel):
    timeline: dict[str, Any] = Field(default_factory=dict)

class CreativeDirectionApplyRequest(BaseModel):
    variant_index: int = 0
    preset: CreativePreset = "cinematic"
    sensitivity: float = Field(default=1.0, ge=0.1, le=3.0)
    overwrite_tracks: bool = True
    overwrite_camera: bool = False


class PlannerLabImportRequest(BaseModel):
    analysis: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    apply_timeline: bool = True
    overwrite_timeline: bool = True


class ReactiveLabApplyRequest(BaseModel):
    metadata: dict[str, Any] = Field(default_factory=dict)
    keyframes: list[dict[str, Any]] = Field(default_factory=list)
    beat_markers: list[dict[str, Any]] = Field(default_factory=list)
    cue_events: list[dict[str, Any]] = Field(default_factory=list)
    sections: list[dict[str, Any]] = Field(default_factory=list)
    repair_suggestions: list[dict[str, Any]] = Field(default_factory=list)
    schedules: dict[str, Any] = Field(default_factory=dict)
    handoff_manifest: dict[str, Any] = Field(default_factory=dict)
    overwrite_motion_track: bool = True
    overwrite_camera: bool = True

class ExportDeforumRequest(BaseModel):
    variant_index: int = 0
    fps: int = 30
    width: int = 1024
    height: int = 576
    preset: CreativePreset = "cinematic"
    sensitivity: float = Field(default=1.0, ge=0.1, le=3.0)


class ExportUnrealBridgeRequest(BaseModel):
    variant_index: int = Field(default=0, ge=0)
    bundle_name: str | None = Field(default=None, max_length=120)
    include_zip: bool = True


class ImportUnrealBridgeReturnRequest(BaseModel):
    bundle_dir: str = Field(min_length=1, max_length=260)
    source_dir: str | None = Field(default=None, max_length=260)


class BuildUnrealImportPlanRequest(BaseModel):
    bundle_dir: str = Field(min_length=1, max_length=260)
    content_path: str | None = Field(default=None, max_length=260)
    asset_name: str | None = Field(default=None, max_length=120)

class CloudAwsTestRequest(BaseModel):
    bucket: str | None = None
    prefix: str | None = None

class CloudAwsBundleRequest(BaseModel):
    bucket: str | None = None
    key: str | None = None

class CloudAzureTestRequest(BaseModel):
    container: str | None = None
    prefix: str | None = None

class CloudLightningBundleRequest(BaseModel):
    output_dir: str = "lightning_bundle"


TraitScope = Literal[
    "theme",
    "motif",
    "palette",
    "lighting",
    "camera",
    "texture",
    "positive_prompt",
    "negative_prompt",
    "transition_rule",
    "engine",
    "failure",
]
TraitState = Literal["declared", "observed", "reinforced", "deprecated"]
RenderAspectRatio = Literal["16:9", "9:16", "1:1", "21:9"]
RenderOutputMode = Literal["full_video", "scene_batch", "preview"]
RenderFallbackPolicy = Literal["auto", "strict", "manual"]
EngineKind = Literal[
    "internal",
    "comfyui_still",
    "comfyui_motion",
    "hosted_video",
    "proxy",
    "deforum_export",
]
RenderStepKind = Literal[
    "prepare_assets",
    "build_prompt",
    "render_still",
    "render_motion",
    "repair_continuity",
    "interpolate",
    "upscale",
    "assemble",
    "mux_audio",
    "validate",
]
RenderOutcome = Literal["approved", "rejected", "needs_repair", "unknown"]


class TraitObservation(BaseModel):
    scope: TraitScope
    value: str = Field(min_length=1, max_length=260)
    state: TraitState = "observed"
    weight: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_count: int = Field(default=1, ge=1)
    sources: list[str] = Field(default_factory=list)


class VisualDNAPaletteProfile(BaseModel):
    dominant: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class VisualDNAIdentity(BaseModel):
    core_themes: list[str] = Field(default_factory=list)
    motifs: list[str] = Field(default_factory=list)
    palette: VisualDNAPaletteProfile = Field(default_factory=VisualDNAPaletteProfile)
    lighting_language: list[str] = Field(default_factory=list)
    camera_language: list[str] = Field(default_factory=list)
    texture_language: list[str] = Field(default_factory=list)


class VisualDNASeedLineage(BaseModel):
    preferred_seed_families: list[int] = Field(default_factory=list)
    stable_subject_seed: int | None = None


class VisualDNAContinuity(BaseModel):
    subject_anchors: list[str] = Field(default_factory=list)
    environment_anchors: list[str] = Field(default_factory=list)
    transition_rules: list[str] = Field(default_factory=list)
    seed_lineage: VisualDNASeedLineage = Field(default_factory=VisualDNASeedLineage)


class VisualDNAPromptGuidance(BaseModel):
    positive_fragments: list[str] = Field(default_factory=list)
    negative_fragments: list[str] = Field(default_factory=list)
    style_bias: dict[str, float] = Field(default_factory=dict)


class EngineOutcomeMemory(BaseModel):
    success_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    approved_count: int = Field(default=0, ge=0)
    rejected_count: int = Field(default=0, ge=0)
    repair_count: int = Field(default=0, ge=0)
    best_for: list[str] = Field(default_factory=list)
    avoid_for: list[str] = Field(default_factory=list)


class QualityFailurePattern(BaseModel):
    pattern: str = Field(min_length=1, max_length=260)
    frequency: int = Field(default=1, ge=1)
    mitigation: str | None = Field(default=None, max_length=400)


class SuccessfulRenderCombination(BaseModel):
    engine: str = Field(min_length=1, max_length=80)
    model: str | None = Field(default=None, max_length=160)
    context: str | None = Field(default=None, max_length=260)
    score: float = Field(default=0.0, ge=0.0, le=1.0)


class VisualDNAQualityMemory(BaseModel):
    common_failures: list[QualityFailurePattern] = Field(default_factory=list)
    successful_combinations: list[SuccessfulRenderCombination] = Field(default_factory=list)


class VisualDNAFingerprint(BaseModel):
    render_id: str = Field(min_length=1, max_length=120)
    palette_signature: list[str] = Field(default_factory=list)
    motif_tags: list[str] = Field(default_factory=list)
    motion_profile: str | None = Field(default=None, max_length=160)
    continuity_score: float | None = Field(default=None, ge=0.0, le=1.0)
    user_outcome: RenderOutcome = "unknown"
    engine: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=160)
    created_at: str | None = None


class VisualDNALearningState(BaseModel):
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sources: dict[str, int] = Field(
        default_factory=lambda: {
            "planner_imports": 0,
            "reactive_imports": 0,
            "approved_renders": 0,
            "rejected_renders": 0,
            "repair_renders": 0,
        }
    )
    locked_fields: list[str] = Field(default_factory=list)
    soft_fields: list[str] = Field(
        default_factory=lambda: [
            "camera_language",
            "lighting_language",
            "texture_language",
            "negative_fragments",
            "style_bias",
        ]
    )


class ProjectVisualDNA(BaseModel):
    version: int = Field(default=1, ge=1)
    project_id: str = Field(min_length=1, max_length=120)
    project_name: str | None = Field(default=None, max_length=200)
    updated_at: str = ""
    identity: VisualDNAIdentity = Field(default_factory=VisualDNAIdentity)
    continuity: VisualDNAContinuity = Field(default_factory=VisualDNAContinuity)
    prompt_guidance: VisualDNAPromptGuidance = Field(default_factory=VisualDNAPromptGuidance)
    engine_memory: dict[str, EngineOutcomeMemory] = Field(default_factory=dict)
    quality_memory: VisualDNAQualityMemory = Field(default_factory=VisualDNAQualityMemory)
    trait_memory: list[TraitObservation] = Field(default_factory=list)
    fingerprints: list[VisualDNAFingerprint] = Field(default_factory=list)
    learning_state: VisualDNALearningState = Field(default_factory=VisualDNALearningState)


class ProjectSnapshot(BaseModel):
    project_id: str = Field(min_length=1, max_length=120)
    project_name: str | None = Field(default=None, max_length=200)
    analysis: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] = Field(default_factory=dict)
    timeline: dict[str, Any] = Field(default_factory=dict)
    visual_dna: ProjectVisualDNA | None = None


class RenderIntentSection(BaseModel):
    scene_id: str = Field(min_length=1, max_length=120)
    start_s: float = Field(default=0.0, ge=0.0)
    end_s: float = Field(default=0.0, ge=0.0)
    creative_goal: str | None = Field(default=None, max_length=260)
    continuity_priority: float | None = Field(default=None, ge=0.0, le=1.0)
    speed_priority: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list)


class RenderIntent(BaseModel):
    project_id: str = Field(min_length=1, max_length=120)
    variant_index: int = Field(default=0, ge=0)
    aspect_ratio: RenderAspectRatio = "16:9"
    output_mode: RenderOutputMode = "full_video"
    quality_tier: Literal["draft", "balanced", "quality", "ultra"] = "balanced"
    continuity_priority: float = Field(default=0.75, ge=0.0, le=1.0)
    speed_priority: float = Field(default=0.4, ge=0.0, le=1.0)
    style_lock_strength: float = Field(default=0.8, ge=0.0, le=1.0)
    allowed_engines: list[EngineKind] = Field(
        default_factory=lambda: [
            "internal",
            "comfyui_still",
            "comfyui_motion",
            "hosted_video",
            "proxy",
            "deforum_export",
        ]
    )
    fallback_policy: RenderFallbackPolicy = "auto"
    sections: list[RenderIntentSection] = Field(default_factory=list)


class RenderStep(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    kind: RenderStepKind
    adapter: str = Field(min_length=1, max_length=80)
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class RenderSectionPlan(BaseModel):
    scene_id: str = Field(min_length=1, max_length=120)
    engine: EngineKind
    rationale: str = Field(min_length=1, max_length=1000)
    estimated_cost: float = Field(default=0.0, ge=0.0)
    estimated_seconds: float = Field(default=0.0, ge=0.0)
    continuity_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    steps: list[RenderStep] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AssemblyPlan(BaseModel):
    mode: Literal["timeline_concat", "audio_mux", "scene_bundle"] = "audio_mux"
    expected_output_path: str = Field(min_length=1, max_length=1024)


class FallbackBranch(BaseModel):
    trigger: str = Field(min_length=1, max_length=260)
    reroute_to: EngineKind
    notes: str = Field(min_length=1, max_length=400)


class RenderPlan(BaseModel):
    plan_id: str = Field(min_length=1, max_length=120)
    project_id: str = Field(min_length=1, max_length=120)
    variant_index: int = Field(default=0, ge=0)
    created_at: str = ""
    advisory_only: bool = True
    summary: str = Field(min_length=1, max_length=1000)
    sections: list[RenderSectionPlan] = Field(default_factory=list)
    assembly: AssemblyPlan
    fallback_branches: list[FallbackBranch] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class VisualDNAFeedbackRequest(BaseModel):
    feedback: dict[str, Any] = Field(default_factory=dict)


class RenderConductorPlanRequest(BaseModel):
    variant_index: int = Field(default=0, ge=0)
    preset: Literal["fast", "balanced", "quality", "ultra"] = "balanced"
    aspect_ratio: RenderAspectRatio = "16:9"
    output_mode: RenderOutputMode = "full_video"
    quality_tier: Literal["draft", "balanced", "quality", "ultra"] | None = None
    continuity_priority: float | None = Field(default=None, ge=0.0, le=1.0)
    speed_priority: float | None = Field(default=None, ge=0.0, le=1.0)
    style_lock_strength: float | None = Field(default=None, ge=0.0, le=1.0)
    allowed_engines: list[EngineKind] = Field(
        default_factory=lambda: [
            "internal",
            "comfyui_still",
            "comfyui_motion",
            "hosted_video",
            "proxy",
            "deforum_export",
        ]
    )
    fallback_policy: RenderFallbackPolicy = "auto"
    sections: list[RenderIntentSection] = Field(default_factory=list)


class UnrealBridgeMarker(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    frame: int = Field(default=0, ge=0)
    time_seconds: float = Field(default=0.0, ge=0.0)


class UnrealBridgeShotPreview(BaseModel):
    shot_id: str = Field(min_length=1, max_length=120)
    scene_id: str = Field(min_length=1, max_length=120)
    title: str | None = Field(default=None, max_length=200)
    start_frame: int = Field(default=0, ge=0)
    end_frame: int = Field(default=0, ge=0)
    prompt: str | None = Field(default=None, max_length=4000)
    continuity_tags: list[str] = Field(default_factory=list)
    camera_tags: list[str] = Field(default_factory=list)
    approved: bool = False


class UnrealShotMetadataExportPreview(BaseModel):
    engine: Literal["unreal"] = "unreal"
    handoff_kind: Literal["shot_metadata_export"] = "shot_metadata_export"
    sequence_name: str = Field(min_length=1, max_length=240)
    fps: int = Field(default=24, ge=1, le=240)
    duration_seconds: float = Field(default=0.0, ge=0.0)
    audio_path: str | None = Field(default=None, max_length=1024)
    project_fields: list[str] = Field(
        default_factory=lambda: ["project_id", "project_name", "fps", "audio_path"]
    )
    shot_fields: list[str] = Field(
        default_factory=lambda: [
            "shot_id",
            "scene_id",
            "start_frame",
            "end_frame",
            "prompt",
            "continuity_tags",
        ]
    )
    marker_fields: list[str] = Field(
        default_factory=lambda: ["label", "frame", "time_seconds"]
    )
    shots: list[UnrealBridgeShotPreview] = Field(default_factory=list)
    markers: list[UnrealBridgeMarker] = Field(default_factory=list)


class UnrealRenderHandoffSectionPreview(BaseModel):
    shot_id: str = Field(min_length=1, max_length=120)
    scene_id: str = Field(min_length=1, max_length=120)
    start_frame: int = Field(default=0, ge=0)
    end_frame: int = Field(default=0, ge=0)
    prompt: str | None = Field(default=None, max_length=4000)
    negative_prompt: str | None = Field(default=None, max_length=4000)
    continuity_note: str | None = Field(default=None, max_length=1000)
    approved: bool = False
    engine_hint: str = Field(default="internal", min_length=1, max_length=80)
    repair_actions: list[str] = Field(default_factory=list)


class UnrealRenderHandoffPreview(BaseModel):
    engine: Literal["unreal"] = "unreal"
    handoff_kind: Literal["render_handoff"] = "render_handoff"
    execution_owner: Literal["external_runtime"] = "external_runtime"
    return_owner: Literal["studio"] = "studio"
    render_mode: str = Field(default="", max_length=160)
    schedule_stride: int = Field(default=1, ge=1)
    approved_section_ids: list[str] = Field(default_factory=list)
    expected_inputs: list[str] = Field(
        default_factory=lambda: ["shot_manifest.json", "audio_markers.json", "style_packet.json"]
    )
    expected_outputs: list[str] = Field(
        default_factory=lambda: ["shot_render.mov", "alpha_pass.mov", "metadata.json"]
    )
    assembly_mode: Literal["ffmpeg_back_in_studio"] = "ffmpeg_back_in_studio"
    sections: list[UnrealRenderHandoffSectionPreview] = Field(default_factory=list)


class UnrealBridgeSectionEvent(BaseModel):
    section_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200)
    time_seconds: float = Field(default=0.0, ge=0.0)
    energy: float | None = Field(default=None, ge=0.0, le=1.0)
    continuity_priority: float | None = Field(default=None, ge=0.0, le=1.0)


class UnrealBridgeCueEvent(BaseModel):
    cue_id: str = Field(min_length=1, max_length=120)
    frame: int = Field(default=0, ge=0)
    time_seconds: float = Field(default=0.0, ge=0.0)
    cue_type: str = Field(default="cue", min_length=1, max_length=120)
    instruction: str | None = Field(default=None, max_length=400)


class UnrealLiveControlBridgePreview(BaseModel):
    engine: Literal["unreal"] = "unreal"
    handoff_kind: Literal["live_control_bridge"] = "live_control_bridge"
    transports: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "osc": ["/edmg/section", "/edmg/beat", "/edmg/camera"],
            "websocket": ["section_change", "beat_pulse", "lighting_envelope"],
            "remote_control": ["sequence.PlayRate", "camera.FocalLength", "lights.Intensity"],
        }
    )
    cadence_hz: int = Field(default=30, ge=1, le=240)
    bpm: float = Field(default=0.0, ge=0.0)
    section_payload_fields: list[str] = Field(
        default_factory=lambda: ["section_id", "energy", "continuity_priority"]
    )
    section_events: list[UnrealBridgeSectionEvent] = Field(default_factory=list)
    cue_events: list[UnrealBridgeCueEvent] = Field(default_factory=list)
    beat_times: list[float] = Field(default_factory=list)
    camera_keyframes: list[dict[str, Any]] = Field(default_factory=list)


class UnrealBridgePreviewResponse(BaseModel):
    project_id: str = Field(min_length=1, max_length=120)
    project_name: str | None = Field(default=None, max_length=200)
    variant_index: int = Field(default=0, ge=0)
    source: Literal["studio_project"] = "studio_project"
    diagnostics: list[str] = Field(default_factory=list)
    shot_metadata_export: UnrealShotMetadataExportPreview
    render_handoff: UnrealRenderHandoffPreview
    live_control_bridge: UnrealLiveControlBridgePreview

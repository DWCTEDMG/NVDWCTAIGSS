
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import UserFacingError
from .deforum_motion import DeforumMotionScheduleBundle, evaluate_motion_state
from .deforum_normalize import UnifiedDeforumRenderContext, build_deforum_render_context
from .deforum_prompt_timeline import resolve_prompt_frame
from .deforum_schedule import coerce_schedule_pairs, evaluate_schedule

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore

from .compositor import apply_timeline_layers
from .ffmpeg import assemble_image_sequence, interpolate_video_fps, mux_audio


@dataclass(frozen=True)
class InternalVideoSettings:
    fps_render: int = 2
    fps_output: int = 24
    width: int = 768
    height: int = 432

    steps: int = 15
    cfg: float = 7.0
    sampler: str = "euler"
    seed: int | None = None
    keyframe_interval_s: float = 5.0

    interpolation_engine: str = "auto"  # auto|minterpolate|fps|rife
    negative_prompt: str = "blurry, low quality, watermark, text, logo"
    model_id: str = "hf_sd15_internal"
    loras: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    vae: str | None = None
    hires_fix: dict[str, Any] | None = None
    refiner: dict[str, Any] | None = None
    upscaler: str | None = None
    render_tier: str = "auto"
    device_preference: str = "auto"

    # Temporal consistency
    temporal_mode: str = "frame_img2img"  # off|keyframes|frame_img2img
    temporal_strength: float = 0.35
    temporal_steps: int | None = None
    refine_every_n_frames: int = 1
    anchor_strength: float = 0.20
    prompt_blend: bool = True
    resume_existing_frames: bool = True
    deforum_overrides: dict[str, Any] | None = None


class _PipelineCache:
    _cache: dict[tuple[str, str, str], Any] = {}

    @classmethod
    def get(cls, key: tuple[str, str, str]) -> Any | None:
        return cls._cache.get(key)

    @classmethod
    def set(cls, key: tuple[str, str, str], value: Any) -> None:
        cls._cache[key] = value


class _EmbedCache:
    _cache: dict[tuple[str, str], Any] = {}

    @classmethod
    def get(cls, key: tuple[str, str]) -> Any | None:
        return cls._cache.get(key)

    @classmethod
    def set(cls, key: tuple[str, str], value: Any) -> None:
        cls._cache[key] = value


class _ControlNetCache:
    _cache: dict[tuple[str, str, str], Any] = {}

    @classmethod
    def get(cls, key: tuple[str, str, str]) -> Any | None:
        return cls._cache.get(key)

    @classmethod
    def set(cls, key: tuple[str, str, str], value: Any) -> None:
        cls._cache[key] = value


_STILL_PIPELINE_LOCK = threading.Lock()


def _stable_seed_int(*parts: Any, fallback: int = 0) -> int:
    raw = "|".join(str(part) for part in parts if part is not None)
    if not raw:
        return int(fallback)
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return int(digest, 16)



def _json_digest(value: Any) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        raw = repr(value)
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _timeline_render_fingerprint(timeline: dict[str, Any] | None) -> Any:
    if not isinstance(timeline, dict):
        return None
    cleaned: dict[str, Any] = {}
    for k, v in timeline.items():
        if k in {"trash_layers", "trash_clips", "history", "future", "selection"}:
            continue
        cleaned[k] = v
    return cleaned


def _build_proxy_work_tag(
    *,
    variant_index: int,
    scenes: list[dict[str, Any]],
    timeline: dict[str, Any] | None,
    settings: "InternalVideoSettings",
) -> str:
    payload = {
        "variant_index": int(variant_index),
        "fps_render": int(settings.fps_render),
        "fps_output": int(settings.fps_output),
        "width": int(settings.width),
        "height": int(settings.height),
        "keyframe_interval_s": float(settings.keyframe_interval_s),
        "interpolation_engine": str(settings.interpolation_engine),
        "render_tier": str(settings.render_tier),
        "device_preference": str(settings.device_preference),
        "scene_digest": _json_digest(scenes or []),
        "timeline_digest": _json_digest(_timeline_render_fingerprint(timeline)),
        "mode": "proxy",
    }
    raw = repr(sorted(payload.items())).encode("utf-8", errors="ignore")
    sig = hashlib.sha1(raw).hexdigest()[:10]
    return (
        f"proxy_v{int(variant_index):02d}_"
        f"{int(settings.width)}x{int(settings.height)}_{int(settings.fps_render)}rf_{int(settings.fps_output)}of_{sig}"
    )


def describe_proxy_render_cache(
    *,
    project_dir: Path,
    variant_index: int,
    scenes: list[dict[str, Any]],
    timeline: dict[str, Any] | None,
    settings: "InternalVideoSettings",
    total_frames: int,
) -> dict[str, Any]:
    work_tag = _build_proxy_work_tag(
        variant_index=variant_index,
        scenes=scenes,
        timeline=timeline,
        settings=settings,
    )
    out_frames = project_dir / "outputs" / "frames_proxy" / work_tag
    raw_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_raw.mp4"
    interp_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_interp.mp4"
    final_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}.mp4"
    meta_json = project_dir / "outputs" / "videos" / f"{work_tag}.render.json"
    frame_count = 0
    if out_frames.exists():
        try:
            frame_count = len(list(out_frames.glob("frame_*.png")))
        except Exception:
            frame_count = 0
    return {
        "work_tag": work_tag,
        "frames_dir": str(out_frames),
        "render_meta_path": str(meta_json),
        "raw_mp4": str(raw_mp4),
        "interp_mp4": str(interp_mp4),
        "final_mp4": str(final_mp4),
        "frames_present": frame_count,
        "frames_expected": int(total_frames),
        "frames_complete": bool(frame_count >= int(total_frames)),
        "raw_exists": raw_mp4.exists(),
        "interp_exists": interp_mp4.exists(),
        "final_exists": final_mp4.exists(),
        "render_meta_exists": meta_json.exists(),
    }


def _build_work_tag(
    *,
    variant_index: int,
    variant: dict[str, Any] | None,
    scenes: list[dict[str, Any]],
    timeline: dict[str, Any] | None,
    model_dir: Path,
    settings: "InternalVideoSettings",
) -> str:
    render_sig = _render_signature(
        variant_index=variant_index,
        model_dir=model_dir,
        settings=settings,
        variant=variant,
        scenes=scenes,
        timeline=timeline,
    )
    return (
        f"internal_v{int(variant_index):02d}_"
        f"{int(settings.width)}x{int(settings.height)}_{int(settings.fps_render)}rf_{int(settings.fps_output)}of_{render_sig}"
    )


def describe_internal_render_cache(
    *,
    project_dir: Path,
    variant_index: int,
    variant: dict[str, Any] | None,
    scenes: list[dict[str, Any]],
    timeline: dict[str, Any] | None,
    model_dir: Path,
    settings: "InternalVideoSettings",
    total_frames: int,
) -> dict[str, Any]:
    work_tag = _build_work_tag(
        variant_index=variant_index,
        variant=variant,
        scenes=scenes,
        timeline=timeline,
        model_dir=model_dir,
        settings=settings,
    )
    out_frames = project_dir / "outputs" / "frames_internal" / work_tag
    raw_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_raw.mp4"
    interp_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_interp.mp4"
    final_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}.mp4"
    meta_json = project_dir / "outputs" / "videos" / f"{work_tag}.render.json"
    frame_count = 0
    if out_frames.exists():
        try:
            frame_count = len(list(out_frames.glob("frame_*.png")))
        except Exception:
            frame_count = 0
    return {
        "work_tag": work_tag,
        "frames_dir": str(out_frames),
        "render_meta_path": str(meta_json),
        "raw_mp4": str(raw_mp4),
        "interp_mp4": str(interp_mp4),
        "final_mp4": str(final_mp4),
        "frames_present": frame_count,
        "frames_expected": int(total_frames),
        "frames_complete": bool(frame_count >= int(total_frames)),
        "raw_exists": raw_mp4.exists(),
        "interp_exists": interp_mp4.exists(),
        "final_exists": final_mp4.exists(),
        "render_meta_exists": meta_json.exists(),
    }


def _render_signature(
    *,
    variant_index: int,
    model_dir: Path,
    settings: "InternalVideoSettings",
    variant: dict[str, Any] | None = None,
    scenes: list[dict[str, Any]] | None = None,
    timeline: dict[str, Any] | None = None,
) -> str:
    payload = {
        "variant_index": int(variant_index),
        "model_dir": str(model_dir),
        "fps_render": int(settings.fps_render),
        "fps_output": int(settings.fps_output),
        "width": int(settings.width),
        "height": int(settings.height),
        "steps": int(settings.steps),
        "cfg": float(settings.cfg),
        "sampler": str(settings.sampler),
        "seed": settings.seed,
        "keyframe_interval_s": float(settings.keyframe_interval_s),
        "interpolation_engine": str(settings.interpolation_engine),
        "model_id": str(settings.model_id),
        "loras_digest": _json_digest(list(settings.loras)),
        "vae": str(settings.vae or ""),
        "hires_fix": settings.hires_fix or None,
        "refiner": settings.refiner or None,
        "upscaler": str(settings.upscaler or ""),
        "render_tier": str(settings.render_tier),
        "device_preference": str(settings.device_preference),
        "temporal_mode": str(settings.temporal_mode),
        "temporal_strength": float(settings.temporal_strength),
        "temporal_steps": int(settings.temporal_steps or 0),
        "refine_every_n_frames": int(settings.refine_every_n_frames),
        "anchor_strength": float(settings.anchor_strength),
        "prompt_blend": bool(settings.prompt_blend),
        "deforum_overrides": settings.deforum_overrides or None,
        "variant_motion_digest": _json_digest((variant or {}).get("motion_schedules") if isinstance(variant, dict) else None),
        "variant_prompt_digest": _json_digest((variant or {}).get("prompts") if isinstance(variant, dict) else None),
        "scenes_digest": _json_digest(scenes or []),
        "timeline_digest": _json_digest(_timeline_render_fingerprint(timeline)),
    }
    raw = repr(sorted(payload.items())).encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()[:10]


def _frame_path(frames_dir: Path, fi: int) -> Path:
    return frames_dir / f"frame_{fi:06d}.png"


def _require_pillow() -> None:
    if Image is None:
        raise UserFacingError(
            "Pillow is not installed",
            hint="Install backend deps including Pillow, then retry.",
            code="INTERNAL_DEPS",
            status_code=500,
        )


@dataclass
class _Pipes:
    txt2img: Any
    img2img: Any
    device: str
    inpaint: Any | None = None
    family: str = "sd15"
    backend: str = "diffusers"


def _model_family_from_dir(model_dir: Path) -> str:
    family = "sd15"
    mi = model_dir / "model_index.json"
    if mi.exists():
        try:
            j = json.loads(mi.read_text(encoding="utf-8"))
            cls = str(j.get("_class_name") or "")
            if "StableDiffusion3" in cls:
                family = "sd3"
            elif ("XL" in cls) or ("XLPipeline" in cls):
                family = "sdxl"
        except Exception:
            family = "sd15"
    return family


def _try_load_diffusers(model_dir: Path, device: str, *, role: str = "video") -> _Pipes:
    try:
        import json
        import torch  # type: ignore
        from diffusers import (  # type: ignore
            StableDiffusion3InpaintPipeline,
            StableDiffusion3Img2ImgPipeline,
            StableDiffusion3Pipeline,
            StableDiffusionInpaintPipeline,
            StableDiffusionImg2ImgPipeline,
            StableDiffusionPipeline,
            StableDiffusionXLInpaintPipeline,
            StableDiffusionXLImg2ImgPipeline,
            StableDiffusionXLPipeline,
        )
    except Exception as e:
        raise UserFacingError(
            "Internal diffusion engine is not installed",
            hint="Install internal deps (diffusers + torch). Then download the internal SD model in Models.",
            code="INTERNAL_DEPS",
            status_code=500,
        ) from e

    cache_key = (str(model_dir), device, str(role or "video"))
    cached = _PipelineCache.get(cache_key)
    if cached is not None:
        return cached

    family = _model_family_from_dir(model_dir)

    torch_dtype = torch.float16 if device in ("cuda", "rocm") else torch.float32

    if family == "sd3":
        txt = StableDiffusion3Pipeline.from_pretrained(
            str(model_dir),
            torch_dtype=torch_dtype,
        )
        if hasattr(txt, "enable_attention_slicing"):
            txt.enable_attention_slicing()
        if device == "cuda" and hasattr(txt, "enable_xformers_memory_efficient_attention"):
            try:
                txt.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        txt = txt.to(device)

        img = StableDiffusion3Img2ImgPipeline(**txt.components)
        if hasattr(img, "enable_attention_slicing"):
            img.enable_attention_slicing()
        if device == "cuda" and hasattr(img, "enable_xformers_memory_efficient_attention"):
            try:
                img.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        img = img.to(device)

        inpaint = StableDiffusion3InpaintPipeline(**txt.components)
        if hasattr(inpaint, "enable_attention_slicing"):
            inpaint.enable_attention_slicing()
        if device == "cuda" and hasattr(inpaint, "enable_xformers_memory_efficient_attention"):
            try:
                inpaint.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        inpaint = inpaint.to(device)

        pipes = _Pipes(txt2img=txt, img2img=img, inpaint=inpaint, device=device, family="sd3", backend="diffusers")
    elif family == "sdxl":
        txt = StableDiffusionXLPipeline.from_pretrained(
            str(model_dir),
            torch_dtype=torch_dtype,
            safety_checker=None,
            requires_safety_checker=False,
        )
        if hasattr(txt, "enable_attention_slicing"):
            txt.enable_attention_slicing()
        if device == "cuda" and hasattr(txt, "enable_xformers_memory_efficient_attention"):
            try:
                txt.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        txt = txt.to(device)

        img = StableDiffusionXLImg2ImgPipeline(**txt.components)
        if hasattr(img, "enable_attention_slicing"):
            img.enable_attention_slicing()
        if device == "cuda" and hasattr(img, "enable_xformers_memory_efficient_attention"):
            try:
                img.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        img = img.to(device)

        inpaint = StableDiffusionXLInpaintPipeline(**txt.components)
        if hasattr(inpaint, "enable_attention_slicing"):
            inpaint.enable_attention_slicing()
        if device == "cuda" and hasattr(inpaint, "enable_xformers_memory_efficient_attention"):
            try:
                inpaint.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        inpaint = inpaint.to(device)

        pipes = _Pipes(txt2img=txt, img2img=img, inpaint=inpaint, device=device, family="sdxl", backend="diffusers")
    else:
        txt = StableDiffusionPipeline.from_pretrained(
            str(model_dir),
            torch_dtype=torch_dtype,
            safety_checker=None,
            requires_safety_checker=False,
        )
        if hasattr(txt, "enable_attention_slicing"):
            txt.enable_attention_slicing()
        if device == "cuda" and hasattr(txt, "enable_xformers_memory_efficient_attention"):
            try:
                txt.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        txt = txt.to(device)

        img = StableDiffusionImg2ImgPipeline(**txt.components)
        if hasattr(img, "enable_attention_slicing"):
            img.enable_attention_slicing()
        if device == "cuda" and hasattr(img, "enable_xformers_memory_efficient_attention"):
            try:
                img.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        img = img.to(device)

        inpaint = StableDiffusionInpaintPipeline(**txt.components)
        if hasattr(inpaint, "enable_attention_slicing"):
            inpaint.enable_attention_slicing()
        if device == "cuda" and hasattr(inpaint, "enable_xformers_memory_efficient_attention"):
            try:
                inpaint.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        inpaint = inpaint.to(device)

        pipes = _Pipes(txt2img=txt, img2img=img, inpaint=inpaint, device=device, family="sd15", backend="diffusers")

    _PipelineCache.set(cache_key, pipes)
    return pipes


def _try_load_directml(model_dir: Path, *, role: str = "video") -> _Pipes:
    raise UserFacingError(
        "DirectML rendering is disabled by the NVIDIA-only profile.",
        hint="Use the NVIDIA CUDA runtime profile.",
        code="NVIDIA_ONLY_PROFILE",
        status_code=400,
    )


def _try_load_pipelines(model_dir: Path, device: str, *, role: str = "video") -> _Pipes:
    if device == "directml":
        return _try_load_directml(model_dir, role=role)
    return _try_load_diffusers(model_dir, device=device, role=role)


def _device_auto(preference: str = "auto") -> str:
    pref = str(preference or "auto").strip().lower()
    if pref not in {"auto", "cuda"}:
        raise UserFacingError(
            "Only NVIDIA CUDA device execution is supported by this profile.",
            hint="Use device_preference='auto' or 'cuda'. CPU, MPS, and DirectML execution are disabled.",
            code="NVIDIA_ONLY_PROFILE",
            status_code=400,
        )
    try:
        import torch  # type: ignore
    except Exception:
        torch = None  # type: ignore

    def _cuda_ok() -> bool:
        try:
            return bool(torch is not None and getattr(torch, "cuda", None) is not None and torch.cuda.is_available())
        except Exception:
            return False

    if pref == "cuda" and _cuda_ok():
        return "cuda"
    if _cuda_ok():
        return "cuda"
    raise UserFacingError(
        "NVIDIA CUDA runtime is required for internal rendering.",
        hint="Launch the NVIDIA profile and confirm torch.cuda.is_available() is true.",
        code="NVIDIA_CUDA_REQUIRED",
        status_code=400,
    )


def _encode_prompt(pipes: _Pipes, prompt: str) -> Any:
    """Return an encoded prompt representation.

    SD1.5 path: returns text-encoder embeddings (fast + blendable).
    SDXL / SD3 path: returns the prompt string (we rely on pipeline internal encoding).
    """
    prompt = str(prompt or "").strip() or "cinematic"
    if pipes.family != "sd15" or pipes.backend == "directml":
        # Keep it simple & robust for SDXL: use native pipeline encoding.
        return prompt

    import torch  # type: ignore

    key = (pipes.device, prompt)
    cached = _EmbedCache.get(key)
    if cached is not None:
        return cached

    tokenizer = pipes.txt2img.tokenizer
    text_encoder = pipes.txt2img.text_encoder

    inputs = tokenizer(
        prompt,
        padding="max_length",
        max_length=tokenizer.model_max_length,
        truncation=True,
        return_tensors="pt",
    )
    ids = inputs.input_ids.to(pipes.device)
    with torch.no_grad():
        embeds = text_encoder(ids)[0]
    _EmbedCache.set(key, embeds)
    return embeds


def _blend_embeds(a: Any, b: Any, w: float) -> Any:
    import torch  # type: ignore

    w = float(max(0.0, min(1.0, w)))
    if isinstance(a, str) or isinstance(b, str):
        # SDXL path: we can't blend embeddings safely here; pick a side deterministically.
        return str(b) if w >= 0.5 else str(a)
    return a * (1.0 - w) + b * w


def _ken_burns_frame(
    img: "Image.Image",
    out_w: int,
    out_h: int,
    zoom: float,
    pan_x: float,
    pan_y: float,
    rotation_deg: float = 0.0,
) -> "Image.Image":
    w, h = img.size

    if abs(rotation_deg) > 0.01:
        img = img.rotate(float(rotation_deg), resample=Image.BICUBIC, expand=True)
        w, h = img.size

    zw, zh = int(round(w * zoom)), int(round(h * zoom))
    imz = img.resize((max(1, zw), max(1, zh)), resample=Image.BICUBIC)

    cx, cy = imz.width // 2, imz.height // 2
    x0 = int(round(cx - out_w / 2 + pan_x))
    y0 = int(round(cy - out_h / 2 + pan_y))
    x0 = max(0, min(x0, imz.width - out_w))
    y0 = max(0, min(y0, imz.height - out_h))
    return imz.crop((x0, y0, x0 + out_w, y0 + out_h))



def _generate_txt2img(
    pipes: _Pipes,
    prompt_embeds: Any,
    negative_embeds: Any,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
) -> "Image.Image":
    g = None
    if pipes.device != "directml":
        import torch  # type: ignore

        g = torch.Generator(device=pipes.device if pipes.device != "mps" else "cpu")
        g.manual_seed(int(seed))

    if pipes.family != "sd15" or pipes.backend == "directml" or isinstance(prompt_embeds, str):
        prompt = str(prompt_embeds or "").strip() or "cinematic"
        negative = str(negative_embeds or "").strip()
        kwargs = dict(
            prompt=prompt,
            negative_prompt=negative,
            width=int(width),
            height=int(height),
            num_inference_steps=int(steps),
            guidance_scale=float(cfg),
        )
        if g is not None:
            kwargs["generator"] = g
        out = pipes.txt2img(**kwargs)
        return out.images[0]

    kwargs = dict(
        prompt=None,
        width=int(width),
        height=int(height),
        num_inference_steps=int(steps),
        guidance_scale=float(cfg),
        prompt_embeds=prompt_embeds,
        negative_prompt_embeds=negative_embeds,
    )
    if g is not None:
        kwargs["generator"] = g
    out = pipes.txt2img(**kwargs)
    return out.images[0]


def _generate_img2img(
    pipes: _Pipes,
    init_image: "Image.Image",
    prompt_embeds: Any,
    negative_embeds: Any,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
    strength: float,
) -> "Image.Image":
    g = None
    if pipes.device != "directml":
        import torch  # type: ignore

        g = torch.Generator(device=pipes.device if pipes.device != "mps" else "cpu")
        g.manual_seed(int(seed))

    if pipes.family != "sd15" or pipes.backend == "directml" or isinstance(prompt_embeds, str):
        prompt = str(prompt_embeds or "").strip() or "cinematic"
        negative = str(negative_embeds or "").strip()
        kwargs = dict(
            prompt=prompt,
            negative_prompt=negative,
            image=init_image,
            strength=float(max(0.0, min(1.0, strength))),
            width=int(width),
            height=int(height),
            num_inference_steps=int(steps),
            guidance_scale=float(cfg),
        )
        if g is not None:
            kwargs["generator"] = g
        out = pipes.img2img(**kwargs)
        return out.images[0]

    kwargs = dict(
        prompt=None,
        image=init_image,
        strength=float(max(0.0, min(1.0, strength))),
        width=int(width),
        height=int(height),
        num_inference_steps=int(steps),
        guidance_scale=float(cfg),
        prompt_embeds=prompt_embeds,
        negative_prompt_embeds=negative_embeds,
    )
    if g is not None:
        kwargs["generator"] = g
    out = pipes.img2img(**kwargs)
    return out.images[0]


def _generate_inpaint(
    pipes: _Pipes,
    init_image: "Image.Image",
    mask_image: "Image.Image",
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
    strength: float,
) -> "Image.Image":
    if pipes.inpaint is None:
        raise UserFacingError(
            "Internal inpaint pipeline is unavailable for this model/backend.",
            hint="Use a supported internal diffusers model, or switch the still model to a Comfy checkpoint.",
            code="INTERNAL_INPAINT_UNAVAILABLE",
            status_code=400,
        )

    g = None
    if pipes.device != "directml":
        import torch  # type: ignore

        g = torch.Generator(device=pipes.device if pipes.device != "mps" else "cpu")
        g.manual_seed(int(seed))

    kwargs = dict(
        prompt=str(prompt or "").strip() or "cinematic",
        negative_prompt=str(negative_prompt or "").strip(),
        image=init_image,
        mask_image=mask_image,
        strength=float(max(0.0, min(1.0, strength))),
        width=int(width),
        height=int(height),
        num_inference_steps=int(steps),
        guidance_scale=float(cfg),
    )
    if g is not None:
        kwargs["generator"] = g
    out = pipes.inpaint(**kwargs)
    return out.images[0]


def _load_controlnet_model(model_dir: Path, family: str, device: str) -> Any:
    if family not in {"sd15", "sdxl"}:
        raise UserFacingError(
            "Internal ControlNet is only available for SD 1.5 and SDXL in this phase.",
            hint="Use an SD 1.5 or SDXL internal still model for ControlNet, or switch to a Comfy checkpoint.",
            code="INTERNAL_CONTROLNET_UNSUPPORTED",
            status_code=400,
        )

    cache_key = (str(model_dir), family, device)
    cached = _ControlNetCache.get(cache_key)
    if cached is not None:
        return cached

    try:
        import torch  # type: ignore
        from diffusers import ControlNetModel  # type: ignore
    except Exception as e:
        raise UserFacingError(
            "Internal ControlNet runtime is not installed.",
            hint="Install the internal diffusers runtime and retry.",
            code="INTERNAL_DEPS",
            status_code=500,
        ) from e

    torch_dtype = torch.float16 if device in ("cuda", "rocm") else torch.float32
    controlnet = ControlNetModel.from_pretrained(str(model_dir), torch_dtype=torch_dtype)
    if device != "directml":
        controlnet = controlnet.to(device)
    _ControlNetCache.set(cache_key, controlnet)
    return controlnet


def _build_controlnet_pipeline(
    pipes: _Pipes,
    *,
    controlnet_dirs: list[Path],
) -> Any:
    if pipes.family not in {"sd15", "sdxl"}:
        raise UserFacingError(
            "Internal ControlNet is only available for SD 1.5 and SDXL in this phase.",
            hint="Use an SD 1.5 or SDXL internal still model for ControlNet, or switch to a Comfy checkpoint.",
            code="INTERNAL_CONTROLNET_UNSUPPORTED",
            status_code=400,
        )

    try:
        from diffusers import (  # type: ignore
            MultiControlNetModel,
            StableDiffusionControlNetPipeline,
            StableDiffusionXLControlNetPipeline,
        )
    except Exception as e:
        raise UserFacingError(
            "Internal ControlNet runtime is not installed.",
            hint="Install the internal diffusers runtime and retry.",
            code="INTERNAL_DEPS",
            status_code=500,
        ) from e

    models = [_load_controlnet_model(model_dir, pipes.family, pipes.device) for model_dir in controlnet_dirs]
    if not models:
        raise UserFacingError(
            "No internal ControlNet models were provided.",
            hint="Choose one or more compatible internal ControlNet units before retrying.",
            code="INTERNAL_CONTROLNET_MISSING",
            status_code=400,
        )
    controlnet = models[0] if len(models) == 1 else MultiControlNetModel(models)
    base_components = dict(getattr(pipes.txt2img, "components", {}) or {})
    base_components.pop("controlnet", None)
    if pipes.family == "sdxl":
        pipeline = StableDiffusionXLControlNetPipeline(controlnet=controlnet, **base_components)
    else:
        pipeline = StableDiffusionControlNetPipeline(controlnet=controlnet, **base_components)
    if hasattr(pipeline, "enable_attention_slicing"):
        pipeline.enable_attention_slicing()
    if pipes.device == "cuda" and hasattr(pipeline, "enable_xformers_memory_efficient_attention"):
        try:
            pipeline.enable_xformers_memory_efficient_attention()
        except Exception:
            pass
    if pipes.device != "directml":
        pipeline = pipeline.to(pipes.device)
    return pipeline


def _apply_loras(pipeline: Any, loras: tuple[dict[str, Any], ...]) -> list[str]:
    loaded: list[str] = []
    weights: list[float] = []
    if not loras or not hasattr(pipeline, "load_lora_weights"):
        return loaded
    for idx, item in enumerate(loras):
        lora_path = str(item.get("path") or item.get("filename") or item.get("name") or "").strip()
        if not lora_path:
            continue
        adapter_name = f"edmg_lora_{idx}"
        pipeline.load_lora_weights(lora_path, adapter_name=adapter_name)
        loaded.append(adapter_name)
        weights.append(float(item.get("weight", 1.0)))
    if loaded and hasattr(pipeline, "set_adapters"):
        try:
            pipeline.set_adapters(loaded, adapter_weights=weights)
        except TypeError:
            pipeline.set_adapters(loaded, weights)
    return loaded


def _clear_loras(pipeline: Any, adapter_names: list[str]) -> None:
    if not adapter_names:
        return
    try:
        if hasattr(pipeline, "delete_adapters"):
            pipeline.delete_adapters(adapter_names)
        elif hasattr(pipeline, "unload_lora_weights"):
            pipeline.unload_lora_weights()
    except Exception:
        pass


def _load_render_image(path: Path, *, mode: str, size: tuple[int, int] | None = None) -> "Image.Image":
    _require_pillow()
    with Image.open(path) as image:
        result = image.convert(mode)
        if size is not None and result.size != size:
            resample = Image.BICUBIC if mode != "L" else Image.BILINEAR
            result = result.resize(size, resample=resample)
        return result


def _fit_render_image(image: "Image.Image", *, size: tuple[int, int], mode: str) -> "Image.Image":
    if image.size == size:
        return image.copy()
    target_w, target_h = size
    resample = Image.BICUBIC if mode != "L" else Image.BILINEAR
    scale = max(target_w / max(1, image.width), target_h / max(1, image.height))
    resized = image.resize(
        (max(1, int(round(image.width * scale))), max(1, int(round(image.height * scale)))),
        resample=resample,
    )
    left = max(0, int(round((resized.width - target_w) / 2)))
    top = max(0, int(round((resized.height - target_h) / 2)))
    return resized.crop((left, top, left + target_w, top + target_h))


def _load_render_source_image(path: Path, *, size: tuple[int, int]) -> "Image.Image":
    _require_pillow()
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        return _fit_render_image(rgb, size=size, mode="RGB")


def _pil_upscale_resample(upscaler: str | None) -> int:
    raw = str(upscaler or "").strip().lower()
    if raw.startswith("latent_"):
        raw = raw[len("latent_") :]
    elif raw.startswith("pixel_"):
        raw = raw[len("pixel_") :]
    mapping = {
        "nearest": Image.NEAREST,
        "nearest-exact": Image.NEAREST,
        "nearest_exact": Image.NEAREST,
        "bilinear": Image.BILINEAR,
        "area": Image.BOX,
        "bicubic": Image.BICUBIC,
        "bislerp": Image.BICUBIC,
        "lanczos": Image.LANCZOS,
    }
    return mapping.get(raw, Image.LANCZOS)


def _upscale_render_image(image: "Image.Image", *, scale: float, upscaler: str | None) -> "Image.Image":
    normalized_scale = float(max(1.0, scale))
    target_size = (
        max(1, int(round(image.width * normalized_scale))),
        max(1, int(round(image.height * normalized_scale))),
    )
    if target_size == image.size:
        return image.copy()
    return image.resize(target_size, resample=_pil_upscale_resample(upscaler))


def _apply_hires_fix(
    pipes: _Pipes,
    image: "Image.Image",
    *,
    prompt_embeds: Any,
    negative_embeds: Any,
    settings: InternalVideoSettings,
    seed: int,
) -> "Image.Image":
    hires_cfg = settings.hires_fix if isinstance(settings.hires_fix, dict) and settings.hires_fix.get("enabled", True) else None
    if not hires_cfg:
        return image
    scale = float(hires_cfg.get("scale") or 1.0)
    if scale <= 1.0:
        return image
    upscaled = _upscale_render_image(
        image,
        scale=scale,
        upscaler=str(hires_cfg.get("upscaler") or settings.upscaler or ""),
    )
    return _generate_img2img(
        pipes,
        upscaled,
        prompt_embeds,
        negative_embeds,
        upscaled.width,
        upscaled.height,
        int(hires_cfg.get("steps") or settings.steps),
        float(settings.cfg),
        int(seed) + 1,
        float(max(0.0, min(1.0, hires_cfg.get("denoise", 0.35)))),
    )


def _apply_refiner(
    base_pipes: _Pipes,
    image: "Image.Image",
    *,
    prompt: str,
    negative_prompt: str,
    settings: InternalVideoSettings,
    seed: int,
    device: str,
    log_fn=None,
) -> "Image.Image":
    refiner_cfg = settings.refiner if isinstance(settings.refiner, dict) else None
    if not refiner_cfg:
        return image

    refiner_pipes = base_pipes
    refiner_model = str(refiner_cfg.get("model") or "").strip()
    refiner_path_raw = str(refiner_cfg.get("path") or "").strip()
    if refiner_model and not refiner_path_raw:
        raise UserFacingError(
            "Internal refiner model is not installed",
            hint="Install or select a compatible internal refiner model before enabling the refiner pass.",
            code="INTERNAL_REFINER_MISSING",
            status_code=400,
        )

    if refiner_path_raw:
        refiner_dir = Path(refiner_path_raw)
        if not refiner_dir.exists():
            raise UserFacingError(
                "Internal refiner model path does not exist",
                hint="Reinstall the selected internal refiner model, then retry.",
                code="INTERNAL_REFINER_MISSING",
                status_code=400,
            )
        base_path_raw = str(refiner_cfg.get("base_path") or "").strip()
        should_load_dedicated_refiner = True
        if base_path_raw:
            should_load_dedicated_refiner = refiner_dir.resolve() != Path(base_path_raw).resolve()
        if should_load_dedicated_refiner:
            refiner_pipes = _try_load_pipelines(refiner_dir, device=device, role="still")
            if callable(log_fn):
                log_fn(f"Using dedicated refiner model: {refiner_model or refiner_dir.name}")

    prompt_embeds = _encode_prompt(refiner_pipes, prompt)
    negative_embeds = _encode_prompt(refiner_pipes, negative_prompt) if negative_prompt else ""
    switch_at = float(refiner_cfg.get("switch_at", 0.8))
    switch_at = max(0.0, min(1.0, switch_at))
    refiner_steps = int(refiner_cfg.get("steps") or max(6, round(int(settings.steps) * max(0.2, 1.0 - switch_at))))
    return _generate_img2img(
        refiner_pipes,
        image,
        prompt_embeds,
        negative_embeds,
        image.width,
        image.height,
        refiner_steps,
        float(settings.cfg),
        int(seed) + 2,
        float(max(0.05, min(1.0, 1.0 - switch_at))),
    )


def render_internal_still_image(
    *,
    model_dir: Path,
    settings: InternalVideoSettings,
    workflow_family: str,
    prompt: str,
    source_image_path: Path | None = None,
    mask_image_path: Path | None = None,
    controlnet_units: list[dict[str, Any]] | None = None,
    denoise_strength: float = 0.75,
    log_fn=None,
) -> dict[str, Any]:
    requested_device = _device_auto(settings.device_preference)
    device = requested_device
    pipes = _try_load_pipelines(model_dir, device=device, role="still")
    width = int(settings.width)
    height = int(settings.height)
    negative_prompt = str(settings.negative_prompt or "").strip()
    seed = int(settings.seed if settings.seed is not None else _stable_seed_int(prompt, width, height, workflow_family, fallback=1337))
    prompt_embeds = _encode_prompt(pipes, prompt)
    negative_embeds = _encode_prompt(pipes, negative_prompt) if negative_prompt else ""

    def _log(message: str) -> None:
        if callable(log_fn):
            log_fn(message)

    with _STILL_PIPELINE_LOCK:
        pipeline = None
        adapter_targets: list[tuple[Any, list[str]]] = []

        def _apply_pipeline_loras(target: Any) -> None:
            if target is None:
                return
            if any(existing is target for existing, _ in adapter_targets):
                return
            adapter_targets.append((target, _apply_loras(target, settings.loras)))

        try:
            if workflow_family == "controlnet":
                units = list(controlnet_units or [])
                controlnet_dirs = [Path(str(unit.get("path") or "")) for unit in units if str(unit.get("path") or "").strip()]
                pipeline = _build_controlnet_pipeline(pipes, controlnet_dirs=controlnet_dirs)
            elif workflow_family in {"inpaint", "outpaint"}:
                pipeline = pipes.inpaint
            elif workflow_family == "img2img":
                pipeline = pipes.img2img
            else:
                pipeline = pipes.txt2img
            for candidate in (pipes.txt2img, pipes.img2img, pipes.inpaint, pipeline):
                _apply_pipeline_loras(candidate)

            if workflow_family == "img2img":
                if source_image_path is None:
                    raise UserFacingError(
                        "No source image selected for img2img",
                        hint="Choose a project source image before running img2img.",
                        code="IMG2IMG_SOURCE_MISSING",
                        status_code=400,
                    )
                init_image = _load_render_source_image(source_image_path, size=(width, height))
                image = _generate_img2img(
                    pipes,
                    init_image,
                    prompt_embeds,
                    negative_embeds,
                    width,
                    height,
                    int(settings.steps),
                    float(settings.cfg),
                    seed,
                    float(max(0.0, min(1.0, denoise_strength))),
                )
            elif workflow_family in {"inpaint", "outpaint"}:
                if source_image_path is None or mask_image_path is None:
                    raise UserFacingError(
                        "Source image or mask is missing for inpaint/outpaint",
                        hint="Choose both a source image and a mask before running the render.",
                        code="INPAINT_ASSETS_MISSING",
                        status_code=400,
                    )
                init_image = _load_render_source_image(source_image_path, size=(width, height))
                mask_image = _load_render_image(mask_image_path, mode="L", size=(width, height))
                image = _generate_inpaint(
                    pipes,
                    init_image,
                    mask_image,
                    prompt=str(prompt or ""),
                    negative_prompt=negative_prompt,
                    width=width,
                    height=height,
                    steps=int(settings.steps),
                    cfg=float(settings.cfg),
                    seed=seed,
                    strength=float(max(0.0, min(1.0, denoise_strength))),
                )
            elif workflow_family == "controlnet":
                units = list(controlnet_units or [])
                if not units:
                    raise UserFacingError(
                        "No compatible ControlNet units were provided.",
                        hint="Attach one or more ControlNet units before running the render.",
                        code="CONTROLNET_MISSING",
                        status_code=400,
                    )
                control_images = [
                    _load_render_image(Path(str(unit.get("reference_path") or unit.get("path_reference") or unit.get("reference_image_path") or "")), mode="RGB", size=(width, height))
                    for unit in units
                ]
                scales = [float(unit.get("strength", 0.8)) for unit in units]
                starts = [float(unit.get("start_percent", 0.0)) for unit in units]
                ends = [float(unit.get("end_percent", 1.0)) for unit in units]
                g = None
                if device != "directml":
                    import torch  # type: ignore

                    g = torch.Generator(device=device if device != "mps" else "cpu")
                    g.manual_seed(seed)
                kwargs = {
                    "prompt": str(prompt or "").strip() or "cinematic",
                    "negative_prompt": negative_prompt,
                    "image": control_images[0] if len(control_images) == 1 else control_images,
                    "width": width,
                    "height": height,
                    "num_inference_steps": int(settings.steps),
                    "guidance_scale": float(settings.cfg),
                    "controlnet_conditioning_scale": scales[0] if len(scales) == 1 else scales,
                    "control_guidance_start": starts[0] if len(starts) == 1 else starts,
                    "control_guidance_end": ends[0] if len(ends) == 1 else ends,
                }
                if g is not None:
                    kwargs["generator"] = g
                image = pipeline(**kwargs).images[0]
            else:
                image = _generate_txt2img(
                    pipes,
                    prompt_embeds,
                    negative_embeds,
                    width,
                    height,
                    int(settings.steps),
                    float(settings.cfg),
                    seed,
                )
            image = _apply_hires_fix(
                pipes,
                image,
                prompt_embeds=prompt_embeds,
                negative_embeds=negative_embeds,
                settings=settings,
                seed=seed,
            )
            image = _apply_refiner(
                pipes,
                image,
                prompt=str(prompt or ""),
                negative_prompt=negative_prompt,
                settings=settings,
                seed=seed,
                device=device,
                log_fn=log_fn,
            )
            if device != requested_device:
                _log(f"Internal still render fell back from {requested_device} to {device} for {workflow_family}.")
            return {
                "image": image,
                "device": device,
                "requested_device": requested_device,
                "family": pipes.family,
                "backend": pipes.backend,
                "seed": seed,
            }
        finally:
            for target, adapters in adapter_targets:
                _clear_loras(target, adapters)


def _scene_keyframe_times(scenes: list[dict[str, Any]], interval_s: float) -> list[float]:
    times: list[float] = []
    for sc in scenes:
        start = float(sc.get("start_s", 0.0))
        end = float(sc.get("end_s", start + 5.0))
        t = start
        while t < end - 1e-6:
            times.append(t)
            t += max(0.5, float(interval_s))
    if not times:
        times = [0.0]
    times = sorted(set([round(x, 3) for x in times]))
    return times


def _infer_duration(scenes: list[dict[str, Any]]) -> float:
    if not scenes:
        return 60.0
    return float(scenes[-1].get("end_s", 60.0))


def _prompt_at_time(scenes: list[dict[str, Any]], t: float, timeline: Any | None = None) -> str:
    """Return prompt text at time t.

    Priority:
      1) DAW timeline prompt track (if present): timeline.tracks[*].type=="prompt"
      2) legacy timeline.prompt_regions (if present)
      3) plan scenes
    """
    if timeline:
        # New DAW tracks schema
        tracks = timeline.get("tracks") if isinstance(timeline, dict) else None
        if isinstance(tracks, list):
            for tr in tracks:
                if not isinstance(tr, dict):
                    continue
                if str(tr.get("type") or "").lower() != "prompt":
                    continue
                clips = tr.get("clips")
                if not isinstance(clips, list):
                    continue
                for cl in clips:
                    if not isinstance(cl, dict):
                        continue
                    s = float(cl.get("start_s", 0.0))
                    e = float(cl.get("end_s", s + 5.0))
                    if s <= t < e:
                        data = cl.get("data") or {}
                        p = str((data.get("prompt") if isinstance(data, dict) else "") or "").strip()
                        if p:
                            return p

        # Back-compat: prompt_regions
        regs = timeline.get("prompt_regions") if isinstance(timeline, dict) else None
        if isinstance(regs, list):
            for r in regs:
                if not isinstance(r, dict):
                    continue
                s = float(r.get("start_s", 0.0))
                e = float(r.get("end_s", s + 5.0))
                if s <= t < e:
                    p = str(r.get("prompt") or "").strip()
                    if p:
                        return p

    for sc in scenes:
        s = float(sc.get("start_s", 0.0))
        e = float(sc.get("end_s", s + 5.0))
        if s <= t < e:
            return str(sc.get("prompt") or "").strip()
    return str(scenes[0].get("prompt") or "").strip() if scenes else "cinematic"



def _key_times_bracket(key_times: list[float], t: float) -> tuple[float, float, float]:
    if not key_times:
        return 0.0, 0.0, 0.0
    if t <= key_times[0]:
        return key_times[0], key_times[0], 0.0
    if t >= key_times[-1]:
        return key_times[-1], key_times[-1], 0.0
    a = key_times[0]
    b = key_times[-1]
    for i in range(len(key_times) - 1):
        if key_times[i] <= t <= key_times[i + 1]:
            a, b = key_times[i], key_times[i + 1]
            break
    u = (t - a) / max(1e-9, (b - a))
    w = _ease01(u)
    return a, b, w


def _ease01(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return u * u * (3.0 - 2.0 * u)




def _parse_deforum_schedule(s: str) -> list[tuple[int, float]]:
    """Back-compat wrapper around the shared schedule parser."""
    return coerce_schedule_pairs(s)


def _eval_schedule(pairs: list[tuple[int, float]], frame: int) -> float | None:
    return evaluate_schedule(pairs, frame, default=None)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _build_unified_deforum_context(
    *,
    scenes: list[dict[str, Any]],
    timeline: dict[str, Any] | None,
    variant: dict[str, Any] | None,
    settings: InternalVideoSettings,
    fps: int,
) -> UnifiedDeforumRenderContext:
    return build_deforum_render_context(
        scenes=scenes,
        timeline=timeline,
        variant=variant,
        fps=max(1, int(fps)),
        default_negative_prompt=str(settings.negative_prompt or ""),
        overrides=settings.deforum_overrides,
    )


def _prompt_text_for_frame(
    *,
    frame_idx: int,
    scenes: list[dict[str, Any]],
    timeline: dict[str, Any] | None,
    deforum_context: UnifiedDeforumRenderContext,
    fps: int,
) -> str:
    prompt = resolve_prompt_frame(deforum_context.prompts, frame_idx, default="")
    if str(prompt or "").strip():
        return str(prompt).strip()
    return _prompt_at_time(scenes, float(frame_idx) / float(max(1, fps)), timeline=timeline) or "cinematic"


def _negative_prompt_for_frame(
    *,
    frame_idx: int,
    settings: InternalVideoSettings,
    deforum_context: UnifiedDeforumRenderContext,
) -> str:
    prompt = resolve_prompt_frame(deforum_context.negative_prompts, frame_idx, default=str(settings.negative_prompt or ""))
    return str(prompt or settings.negative_prompt or "")


def _motion_params_at_time(
    t: float,
    timeline: dict[str, Any] | None,
    *,
    deforum_motion: DeforumMotionScheduleBundle | None = None,
    fps: int = 24,
) -> dict[str, float] | None:
    frame = int(round(float(t) * float(max(1, fps))))
    motion = deforum_motion
    if motion is None:
        motion = build_deforum_render_context(
            scenes=[],
            timeline=timeline,
            variant=None,
            fps=max(1, int(fps)),
            default_negative_prompt="",
        ).motion
    if not motion.has_camera_motion() and not motion.has_diffusion_controls():
        return None

    state = evaluate_motion_state(frame, motion)
    out = state.to_renderer_params()
    if "steps" not in out and "strength" in out:
        out["steps"] = _clamp(15.0 * (0.70 + 0.90 * float(out["strength"])), 6.0, 40.0)
    if "denoise" not in out and "strength" in out:
        out["denoise"] = _clamp(float(out["strength"]), 0.01, 0.99)
    return out


def _camera_keyframes_are_actionable(points: list[dict[str, Any]]) -> bool:
    if len(points) >= 2:
        return True
    if len(points) != 1:
        return False
    point = points[0]
    return any(
        abs(float(point.get(key, default)) - float(default)) > 1e-4
        for key, default in (
            ("zoom", 1.0),
            ("pan_x", 0.0),
            ("pan_y", 0.0),
            ("rotation_deg", 0.0),
        )
    )

def _camera_at_time(
    t: float,
    *,
    timeline: dict[str, Any] | None,
    fallback_interval_s: float,
    deforum_motion: DeforumMotionScheduleBundle | None = None,
    fps: int = 24,
) -> tuple[float, float, float, float]:
    """Camera track evaluator.

    Timeline format (optional):
      timeline["camera"]["keyframes"] = [{"t":0,"zoom":1.0,"pan_x":0,"pan_y":0,"rotation_deg":0}, ...]

    If missing, uses a deterministic fallback motion.
    """
    if timeline and isinstance(timeline, dict):
        cam = timeline.get("camera")
        if isinstance(cam, dict):
            kfs = cam.get("keyframes")
            if isinstance(kfs, list):
                pts = [x for x in kfs if isinstance(x, dict) and "t" in x]
                pts.sort(key=lambda d: float(d.get("t", 0.0)))
                if _camera_keyframes_are_actionable(pts):
                    if t <= float(pts[0]["t"]):
                        p = pts[0]
                        return float(p.get("zoom", 1.0)), float(p.get("pan_x", 0.0)), float(p.get("pan_y", 0.0)), float(p.get("rotation_deg", 0.0))
                    if t >= float(pts[-1]["t"]):
                        p = pts[-1]
                        return float(p.get("zoom", 1.0)), float(p.get("pan_x", 0.0)), float(p.get("pan_y", 0.0)), float(p.get("rotation_deg", 0.0))

                    a, b = pts[0], pts[-1]
                    for i in range(len(pts) - 1):
                        ta, tb = float(pts[i]["t"]), float(pts[i + 1]["t"])
                        if ta <= t <= tb:
                            a, b = pts[i], pts[i + 1]
                            break
                    ta, tb = float(a["t"]), float(b["t"])
                    u = (t - ta) / max(1e-9, (tb - ta))
                    w = _ease01(u)
                    zoom = float(a.get("zoom", 1.0)) * (1.0 - w) + float(b.get("zoom", 1.0)) * w
                    pan_x = float(a.get("pan_x", 0.0)) * (1.0 - w) + float(b.get("pan_x", 0.0)) * w
                    pan_y = float(a.get("pan_y", 0.0)) * (1.0 - w) + float(b.get("pan_y", 0.0)) * w
                    rot = float(a.get("rotation_deg", 0.0)) * (1.0 - w) + float(b.get("rotation_deg", 0.0)) * w
                    return zoom, pan_x, pan_y, rot


    # If camera keyframes are missing, fall back to motion track clips (DAW).
    mp = _motion_params_at_time(t, timeline, deforum_motion=deforum_motion, fps=fps)
    if mp:
        return float(mp.get("zoom", 1.0)), float(mp.get("pan_x", 0.0)), float(mp.get("pan_y", 0.0)), float(mp.get("rotation_deg", 0.0))

    # fallback deterministic motion
    phase = (t / max(0.001, fallback_interval_s))
    zoom = 1.0 + 0.06 * _ease01((t % fallback_interval_s) / max(0.001, fallback_interval_s))
    pan_x = 8.0 * math.sin(2.0 * math.pi * phase)
    pan_y = 5.0 * math.sin(2.0 * math.pi * phase + 1.2)
    return zoom, pan_x, pan_y, 0.0


def _write_runtime_checkpoint(checkpoint_json: Path, state: dict[str, Any]) -> None:
    checkpoint_json.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_json.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_checkpoint_emitter(
    *,
    checkpoint_json: Path,
    project_dir: Path,
    work_tag: str,
    render_mode: str,
    variant_index: int,
    total_frames: int,
    fps_render: int,
    chunk_plan: dict[str, Any] | None,
    checkpoint_fn=None,
):
    plan = dict(chunk_plan or {})
    frames_per_chunk = max(1, int(plan.get("frames_per_chunk") or total_frames or 1))
    checkpoint_interval_frames = max(1, int(plan.get("checkpoint_interval_frames") or max(1, fps_render * 15)))
    estimated_chunks = max(1, int(plan.get("estimated_chunks") or math.ceil(max(1, total_frames) / max(1, frames_per_chunk))))
    strategy = str(plan.get("strategy") or ("resume_friendly_chunks" if total_frames > frames_per_chunk else "single_pass"))
    enabled = bool(plan.get("enabled", total_frames > frames_per_chunk))
    notes = list(plan.get("notes") or [])
    state: dict[str, Any] = {
        "status": "pending",
        "render_mode": str(render_mode),
        "work_tag": str(work_tag),
        "variant_index": int(variant_index),
        "total_frames": int(total_frames),
        "fps_render": int(fps_render),
        "frames_rendered": 0,
        "frames_reused": 0,
        "completed_frames": 0,
        "last_completed_frame": -1,
        "next_frame_index": 0,
        "frames_per_chunk": int(frames_per_chunk),
        "estimated_chunks": int(estimated_chunks),
        "completed_chunks": 0,
        "current_chunk_index": 1 if total_frames > 0 else 0,
        "current_chunk_progress_frames": 0,
        "checkpoint_interval_frames": int(checkpoint_interval_frames),
        "resume_recommended": bool(plan.get("resume_recommended", enabled)),
        "chunking_enabled": enabled,
        "chunk_strategy": strategy,
        "notes": notes,
        "can_resume": True,
        "outputs": {
            "checkpoint_json": str(checkpoint_json.relative_to(project_dir)),
            "raw_exists": False,
            "interp_exists": False,
            "final_exists": False,
        },
    }
    last_emitted = {"stage": None, "completed_frames": -1, "ts": 0.0}

    def _emit(
        *,
        stage: str,
        status: str = "running",
        force: bool = False,
        final: bool = False,
        message: str | None = None,
        frame_event: str | None = None,
        rendered_delta: int = 0,
        reused_delta: int = 0,
        extra_outputs: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        if rendered_delta:
            state["frames_rendered"] = min(int(total_frames), int(state.get("frames_rendered", 0)) + int(rendered_delta))
        if reused_delta:
            state["frames_reused"] = min(int(total_frames), int(state.get("frames_reused", 0)) + int(reused_delta))
        completed_frames = min(int(total_frames), int(state.get("frames_rendered", 0)) + int(state.get("frames_reused", 0)))
        state["status"] = str(status or ("complete" if final else "running"))
        state["stage"] = str(stage or "running")
        state["completed_frames"] = completed_frames
        state["last_completed_frame"] = completed_frames - 1 if completed_frames > 0 else -1
        state["next_frame_index"] = min(int(total_frames), completed_frames)
        state["current_chunk_index"] = min(int(estimated_chunks), max(1, (completed_frames // max(1, frames_per_chunk)) + 1)) if total_frames > 0 else 0
        state["completed_chunks"] = min(int(estimated_chunks), completed_frames // max(1, frames_per_chunk))
        if completed_frames >= int(total_frames) and total_frames > 0:
            state["completed_chunks"] = int(estimated_chunks)
        state["current_chunk_progress_frames"] = completed_frames % max(1, frames_per_chunk)
        if completed_frames >= int(total_frames) and total_frames > 0:
            state["current_chunk_progress_frames"] = 0
        percent = 100.0 if total_frames <= 0 else round((completed_frames / float(max(1, total_frames))) * 100.0, 1)
        state["resume_percent"] = percent
        state["updated_at"] = time.time()
        if frame_event:
            state["frame_event"] = str(frame_event)
        if message:
            state["message"] = str(message)
        outputs = dict(state.get("outputs") or {})
        if extra_outputs:
            outputs.update({k: bool(v) for k, v in extra_outputs.items()})
        state["outputs"] = outputs

        should_emit = force or final
        now = time.time()
        if not should_emit:
            if last_emitted["stage"] != stage:
                should_emit = True
            elif completed_frames in (0, int(total_frames)):
                should_emit = True
            elif completed_frames - int(last_emitted["completed_frames"]) >= checkpoint_interval_frames:
                should_emit = True
            elif completed_frames > 0 and (completed_frames % max(1, frames_per_chunk) == 0):
                should_emit = True
            elif frame_event and completed_frames != int(last_emitted["completed_frames"]):
                if completed_frames <= max(12, fps_render * 8):
                    should_emit = True
                elif (now - float(last_emitted["ts"] or 0.0)) >= 1.0:
                    should_emit = True
        if should_emit:
            _write_runtime_checkpoint(checkpoint_json, state)
            if checkpoint_fn:
                checkpoint_fn(dict(state))
            last_emitted["stage"] = str(stage)
            last_emitted["completed_frames"] = int(completed_frames)
            last_emitted["ts"] = float(now)
        return dict(state)

    return _emit





def render_internal_video_variant(
    *,
    ffmpeg_path: str,
    project_dir: Path,
    variant: dict[str, Any],
    scenes: list[dict[str, Any]],
    audio_path: Path | None,
    model_dir: Path,
    settings: InternalVideoSettings,
    timeline: dict[str, Any] | None = None,
    log_fn=None,
    progress_fn=None,
    cancel_check_fn=None,
    chunk_plan: dict[str, Any] | None = None,
    checkpoint_fn=None,
) -> Path:
    """Render an internal baseline music video.

    Modes:
      - off/keyframes: SD keyframes + Ken Burns + optional overlays
      - frame_img2img: sequential img2img refinement per frame for temporal consistency
    """
    _require_pillow()

    device = _device_auto(settings.device_preference)
    pipes = _try_load_pipelines(model_dir, device=device)

    out_w, out_h = settings.width, settings.height
    fps_r = max(1, int(settings.fps_render))
    fps_schedule = max(1, int(settings.fps_output))
    duration_s = float(variant.get("duration_s") or _infer_duration(scenes))
    total_frames = int(math.ceil(duration_s * fps_r))
    deforum_context = _build_unified_deforum_context(
        scenes=scenes,
        timeline=timeline,
        variant=variant,
        settings=settings,
        fps=fps_schedule,
    )

    work_tag = _build_work_tag(
        variant_index=int(variant.get("index", 0)),
        variant=variant,
        scenes=scenes,
        timeline=timeline,
        model_dir=model_dir,
        settings=settings,
    )
    out_frames = project_dir / "outputs" / "frames_internal" / work_tag
    out_frames.mkdir(parents=True, exist_ok=True)

    key_times = _scene_keyframe_times(scenes, settings.keyframe_interval_s)
    total_units = max(1, len(key_times) + total_frames + 3)
    cache_info = describe_internal_render_cache(
        project_dir=project_dir,
        variant_index=int(variant.get("index", 0)),
        variant=variant,
        scenes=scenes,
        timeline=timeline,
        model_dir=model_dir,
        settings=settings,
        total_frames=total_frames,
    )
    raw_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_raw.mp4"
    interp_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_interp.mp4"
    final_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}.mp4"
    meta_json = project_dir / "outputs" / "videos" / f"{work_tag}.render.json"
    checkpoint_json = project_dir / "outputs" / "videos" / f"{work_tag}.checkpoint.json"
    emit_checkpoint = _build_checkpoint_emitter(
        checkpoint_json=checkpoint_json,
        project_dir=project_dir,
        work_tag=work_tag,
        render_mode="diffusion",
        variant_index=int(variant.get("index", 0)),
        total_frames=total_frames,
        fps_render=fps_r,
        chunk_plan=chunk_plan,
        checkpoint_fn=checkpoint_fn,
    )
    if progress_fn:
        progress_fn("preparing", 0, total_units, f"Preparing internal render on {device}")
    emit_checkpoint(stage="preparing", status="running", force=True, message=f"Preparing internal render on {device}")

    default_negative_embeds = _encode_prompt(pipes, settings.negative_prompt)
    if log_fn:
        log_fn(
            f"Render cache tag={work_tag} resume_existing_frames={'yes' if settings.resume_existing_frames else 'no'}"
        )
        log_fn(
            f"Cache status frames={cache_info['frames_present']}/{cache_info['frames_expected']} "
            f"raw={'yes' if cache_info['raw_exists'] else 'no'} "
            f"interp={'yes' if cache_info['interp_exists'] else 'no'} "
            f"final={'yes' if cache_info['final_exists'] else 'no'}"
        )

    if settings.resume_existing_frames and final_mp4.exists():
        final_mtime = final_mp4.stat().st_mtime
        audio_ok = (audio_path is None) or (not audio_path.exists()) or (final_mtime >= audio_path.stat().st_mtime)
        if audio_ok:
            emit_checkpoint(stage="complete", status="complete", force=True, final=True, message=f"Reusing completed render {final_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists(), "final_exists": True})
            if progress_fn:
                progress_fn("complete", total_units, total_units, f"Reusing completed render {final_mp4.name}")
            if log_fn:
                log_fn(f"Reusing completed render {final_mp4.name}")
            return final_mp4


    # Generate temporally consistent keyframes
    key_imgs: dict[float, Image.Image] = {}
    prev_key_img: Image.Image | None = None
    prev_key_prompt: str = ""
    for i, t in enumerate(key_times):
        if cancel_check_fn:
            cancel_check_fn()
        schedule_frame = int(round(float(t) * float(fps_schedule)))
        p = _prompt_text_for_frame(
            frame_idx=schedule_frame,
            scenes=scenes,
            timeline=timeline,
            deforum_context=deforum_context,
            fps=fps_schedule,
        ) or "cinematic"
        negative_prompt = _negative_prompt_for_frame(frame_idx=schedule_frame, settings=settings, deforum_context=deforum_context)
        negative_embeds = (
            default_negative_embeds
            if negative_prompt == settings.negative_prompt
            else _encode_prompt(pipes, negative_prompt)
        )
        seed = _stable_seed_int("key", settings.seed, t, p, work_tag)
        if log_fn:
            log_fn(f"Keyframe {i+1}/{len(key_times)} t={t:.2f}s seed={seed} device={device}")
        if progress_fn:
            progress_fn("keyframes", i, total_units, f"Generating keyframe {i+1}/{len(key_times)}")
        emit_checkpoint(stage="keyframes", status="running", message=f"Generating keyframe {i+1}/{len(key_times)}")
        pe = _encode_prompt(pipes, p)
        mpk = _motion_params_at_time(t, timeline, deforum_motion=deforum_context.motion, fps=fps_schedule)
        cfgk = float((mpk or {}).get('cfg', settings.cfg))
        stepsk = int(float((mpk or {}).get('steps', settings.steps)))
        denk = float((mpk or {}).get('denoise', (mpk or {}).get('strength', settings.temporal_strength)))
        if prev_key_img is None or settings.temporal_mode in ("off",):
            img = _generate_txt2img(pipes, pe, negative_embeds, out_w, out_h, stepsk, cfgk, seed)
        else:
            # Keyframe continuity: anchor to previous keyframe to keep style stable.
            img = _generate_img2img(
                pipes,
                init_image=prev_key_img,
                prompt_embeds=pe,
                negative_embeds=negative_embeds,
                width=out_w,
                height=out_h,
                steps=max(6, int(settings.temporal_steps or max(8, settings.steps - 3))),
                cfg=cfgk,
                seed=seed,
                strength=max(0.05, min(0.95, denk)),
            )
        key_imgs[t] = img
        prev_key_img = img
        prev_key_prompt = p
        if progress_fn:
            progress_fn("keyframes", i + 1, total_units, f"Ready keyframe {i+1}/{len(key_times)}")
        emit_checkpoint(stage="keyframes", status="running", message=f"Ready keyframe {i+1}/{len(key_times)}")

    def _save_frame(img: Image.Image, fi: int, t: float) -> Path:
        if timeline:
            img = apply_timeline_layers(img, project_dir=project_dir, timeline=timeline, t=t)
        p = _frame_path(out_frames, fi)
        img.save(p)
        return p

    frame_paths: list[Path] = []

    if settings.temporal_mode != "frame_img2img":
        for fi in range(total_frames):
            if cancel_check_fn:
                cancel_check_fn()
            t = fi / fps_r
            existing = _frame_path(out_frames, fi)
            if settings.resume_existing_frames and existing.exists():
                frame_paths.append(existing)
                if progress_fn:
                    progress_fn("frames", len(key_times) + fi + 1, total_units, f"Reusing frame {fi+1}/{total_frames}")
                emit_checkpoint(stage="frames", status="running", message=f"Reusing frame {fi+1}/{total_frames}", frame_event="reused", reused_delta=1)
                if log_fn and fi % max(1, fps_r * 10) == 0:
                    log_fn(f"Reused cached frame {fi+1}/{total_frames}")
                continue

            a, b, w = _key_times_bracket(key_times, t)
            src = key_imgs[a].convert("RGB")
            if a != b:
                src = Image.blend(src, key_imgs[b].convert("RGB"), float(w))
            zoom, pan_x, pan_y, rot = _camera_at_time(
                t,
                timeline=timeline,
                fallback_interval_s=settings.keyframe_interval_s,
                deforum_motion=deforum_context.motion,
                fps=fps_schedule,
            )
            fr = _ken_burns_frame(src, out_w, out_h, zoom=zoom, pan_x=pan_x, pan_y=pan_y, rotation_deg=rot)
            frame_paths.append(_save_frame(fr, fi, t))
            if progress_fn:
                progress_fn("frames", len(key_times) + fi + 1, total_units, f"Rendered frame {fi+1}/{total_frames}")
            emit_checkpoint(stage="frames", status="running", message=f"Rendered frame {fi+1}/{total_frames}", frame_event="rendered", rendered_delta=1)
            if log_fn and fi % max(1, fps_r * 3) == 0:
                log_fn(f"Rendered frame {fi+1}/{total_frames}")
    else:
        prev_frame = key_imgs[key_times[0]].resize((out_w, out_h), resample=Image.LANCZOS)
        prev_zoom, prev_px, prev_py, prev_rot = _camera_at_time(
            0.0,
            timeline=timeline,
            fallback_interval_s=settings.keyframe_interval_s,
            deforum_motion=deforum_context.motion,
            fps=fps_schedule,
        )

        refine_every = max(1, int(settings.refine_every_n_frames))
        steps_refine = int(settings.temporal_steps or max(8, settings.steps - 3))

        for fi in range(total_frames):
            if cancel_check_fn:
                cancel_check_fn()
            t = fi / fps_r
            existing = _frame_path(out_frames, fi)
            schedule_frame = int(round(float(t) * float(fps_schedule)))

            a_t, b_t, w = _key_times_bracket(key_times, t)
            zoom, pan_x, pan_y, rot = _camera_at_time(
                t,
                timeline=timeline,
                fallback_interval_s=settings.keyframe_interval_s,
                deforum_motion=deforum_context.motion,
                fps=fps_schedule,
            )

            if settings.resume_existing_frames and existing.exists():
                try:
                    prev_frame = Image.open(existing).convert("RGB").resize((out_w, out_h), resample=Image.LANCZOS)
                    prev_zoom, prev_px, prev_py, prev_rot = zoom, pan_x, pan_y, rot
                    frame_paths.append(existing)
                    if progress_fn:
                        progress_fn("frames", len(key_times) + fi + 1, total_units, f"Reusing frame {fi+1}/{total_frames}")
                    emit_checkpoint(stage="frames", status="running", message=f"Reusing frame {fi+1}/{total_frames}", frame_event="reused", reused_delta=1)
                    if log_fn and fi % max(1, fps_r * 10) == 0:
                        log_fn(f"Reused cached frame {fi+1}/{total_frames}")
                    continue
                except Exception:
                    pass

            mp = _motion_params_at_time(t, timeline, deforum_motion=deforum_context.motion, fps=fps_schedule)

            a_frame = int(round(float(a_t) * float(fps_schedule)))
            b_frame = int(round(float(b_t) * float(fps_schedule)))
            a_prompt = _prompt_text_for_frame(
                frame_idx=a_frame,
                scenes=scenes,
                timeline=timeline,
                deforum_context=deforum_context,
                fps=fps_schedule,
            ) or "cinematic"
            b_prompt = _prompt_text_for_frame(
                frame_idx=b_frame,
                scenes=scenes,
                timeline=timeline,
                deforum_context=deforum_context,
                fps=fps_schedule,
            ) or a_prompt
            a_e = _encode_prompt(pipes, a_prompt)
            b_e = _encode_prompt(pipes, b_prompt)
            pe = _blend_embeds(a_e, b_e, w) if settings.prompt_blend else a_e
            negative_prompt = _negative_prompt_for_frame(frame_idx=schedule_frame, settings=settings, deforum_context=deforum_context)
            negative_embeds = (
                default_negative_embeds
                if negative_prompt == settings.negative_prompt
                else _encode_prompt(pipes, negative_prompt)
            )

            rz = zoom / max(1e-6, prev_zoom)
            dpx = pan_x - prev_px
            dpy = pan_y - prev_py

            init = _ken_burns_frame(prev_frame, out_w, out_h, zoom=rz, pan_x=dpx, pan_y=dpy, rotation_deg=(rot - prev_rot))

            # Blend in keyframe anchors to prevent drift.
            anchor = key_imgs[a_t]
            if a_t != b_t:
                anchor = Image.blend(key_imgs[a_t].convert("RGB"), key_imgs[b_t].convert("RGB"), float(w))
            if settings.anchor_strength > 0:
                init = Image.blend(init.convert("RGB"), anchor.convert("RGB"), float(settings.anchor_strength))

            seed = _stable_seed_int("frame", settings.seed, fi, f"{t:.3f}", work_tag)
            if fi % refine_every == 0:
                if log_fn and fi % max(1, fps_r * 3) == 0:
                    log_fn(f"Refining frame {fi+1}/{total_frames} strength={settings.temporal_strength:.2f} steps={steps_refine}")
                out = _generate_img2img(
                    pipes,
                    init_image=init,
                    prompt_embeds=pe,
                    negative_embeds=negative_embeds,
                    width=out_w,
                    height=out_h,
                    steps=int(float((mp or {}).get('steps', steps_refine))),
                    cfg=float((mp or {}).get('cfg', settings.cfg)),
                    seed=seed,
                    strength=float((mp or {}).get('denoise', (mp or {}).get('strength', settings.temporal_strength))),
                )
                prev_frame = out.resize((out_w, out_h), resample=Image.LANCZOS)
            else:
                prev_frame = init.resize((out_w, out_h), resample=Image.LANCZOS)

            prev_zoom, prev_px, prev_py, prev_rot = zoom, pan_x, pan_y, rot
            frame_paths.append(_save_frame(prev_frame, fi, t))
            if progress_fn:
                progress_fn("frames", len(key_times) + fi + 1, total_units, f"Rendered frame {fi+1}/{total_frames}")
            emit_checkpoint(stage="frames", status="running", message=f"Rendered frame {fi+1}/{total_frames}", frame_event="rendered", rendered_delta=1)

    if cancel_check_fn:
        cancel_check_fn()

    raw_mp4.parent.mkdir(parents=True, exist_ok=True)
    if settings.resume_existing_frames and cache_info["frames_complete"] and raw_mp4.exists():
        if progress_fn:
            progress_fn("assembling", total_units - 2, total_units, f"Reusing raw MP4 {raw_mp4.name}")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Reusing raw MP4 {raw_mp4.name}", extra_outputs={"raw_exists": True})
        if log_fn:
            log_fn(f"Reusing raw MP4 {raw_mp4.name}")
    else:
        if progress_fn:
            progress_fn("assembling", total_units - 2, total_units, "Assembling raw MP4")
        emit_checkpoint(stage="assembling", status="running", force=True, message="Assembling raw MP4")
        if log_fn:
            log_fn("Assembling raw MP4 from rendered frames")
        assemble_image_sequence(
            ffmpeg_path=ffmpeg_path,
            frames_dir=out_frames,
            out_mp4=raw_mp4,
            fps=fps_r,
            glob_pattern="frame_*.png",
            audio_path=None,
        )

    if cancel_check_fn:
        cancel_check_fn()

    if int(settings.fps_output) == int(fps_r):
        if not interp_mp4.exists() or interp_mp4.stat().st_mtime < raw_mp4.stat().st_mtime:
            interp_mp4.write_bytes(raw_mp4.read_bytes())
        if progress_fn:
            progress_fn("assembling", total_units - 1, total_units, f"Keeping FPS at {int(settings.fps_output)}")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Keeping FPS at {int(settings.fps_output)}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": True})
        if log_fn:
            log_fn(f"Skipping interpolation because fps_output matches fps_render ({int(settings.fps_output)})")
    elif settings.resume_existing_frames and interp_mp4.exists() and raw_mp4.exists() and interp_mp4.stat().st_mtime >= raw_mp4.stat().st_mtime:
        if progress_fn:
            progress_fn("assembling", total_units - 1, total_units, f"Reusing interpolated MP4 {interp_mp4.name}")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Reusing interpolated MP4 {interp_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": True})
        if log_fn:
            log_fn(f"Reusing interpolated MP4 {interp_mp4.name}")
    else:
        if progress_fn:
            progress_fn("assembling", total_units - 1, total_units, f"Interpolating to {int(settings.fps_output)} fps")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Interpolating to {int(settings.fps_output)} fps", extra_outputs={"raw_exists": raw_mp4.exists()})
        if log_fn:
            log_fn(f"Interpolating to {int(settings.fps_output)} fps via {settings.interpolation_engine}")
        interpolate_video_fps(
            ffmpeg_path=ffmpeg_path,
            in_mp4=raw_mp4,
            out_mp4=interp_mp4,
            fps_out=int(settings.fps_output),
            engine=settings.interpolation_engine,
        )

    if cancel_check_fn:
        cancel_check_fn()

    if settings.resume_existing_frames and final_mp4.exists():
        final_mtime = final_mp4.stat().st_mtime
        audio_ok = (audio_path is None) or (not audio_path.exists()) or (final_mtime >= audio_path.stat().st_mtime)
        interp_ok = interp_mp4.exists() and final_mtime >= interp_mp4.stat().st_mtime
    else:
        audio_ok = False
        interp_ok = False

    if audio_ok and interp_ok:
        if progress_fn:
            progress_fn("muxing", total_units, total_units, f"Reusing final video {final_mp4.name}")
        emit_checkpoint(stage="muxing", status="running", force=True, message=f"Reusing final video {final_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists(), "final_exists": True})
        if log_fn:
            log_fn(f"Reusing final video {final_mp4.name}")
    else:
        if progress_fn:
            progress_fn("muxing", total_units, total_units, "Muxing audio and finalizing video")
        emit_checkpoint(stage="muxing", status="running", force=True, message="Muxing audio and finalizing video", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists()})
        if audio_path and audio_path.exists():
            mux_audio(ffmpeg_path=ffmpeg_path, video_mp4=interp_mp4, audio_path=audio_path, out_mp4=final_mp4)
        else:
            final_mp4.write_bytes(interp_mp4.read_bytes())
    meta = {
        "work_tag": work_tag,
        "completed_at": __import__("time").time(),
        "variant_index": int(variant.get("index", 0)),
        "settings": {
            "fps_render": int(settings.fps_render),
            "fps_output": int(settings.fps_output),
            "width": int(settings.width),
            "height": int(settings.height),
            "steps": int(settings.steps),
            "cfg": float(settings.cfg),
            "sampler": str(settings.sampler),
            "seed": settings.seed,
            "keyframe_interval_s": float(settings.keyframe_interval_s),
            "interpolation_engine": str(settings.interpolation_engine),
            "temporal_mode": str(settings.temporal_mode),
            "temporal_strength": float(settings.temporal_strength),
            "temporal_steps": int(settings.temporal_steps or 0),
            "refine_every_n_frames": int(settings.refine_every_n_frames),
            "anchor_strength": float(settings.anchor_strength),
            "prompt_blend": bool(settings.prompt_blend),
            "resume_existing_frames": bool(settings.resume_existing_frames),
            "model_id": str(settings.model_id),
            "negative_prompt": str(settings.negative_prompt),
            "loras": list(settings.loras),
            "vae": settings.vae,
            "refiner": settings.refiner,
        },
        "frames": {
            "expected": int(total_frames),
            "present": len(list(out_frames.glob("frame_*.png"))),
            "dir": str(out_frames),
        },
        "outputs": {
            "raw_mp4": str(raw_mp4),
            "interp_mp4": str(interp_mp4),
            "final_mp4": str(final_mp4),
            "checkpoint_json": str(checkpoint_json),
        },
        "timeline_digest": _json_digest(_timeline_render_fingerprint(timeline)),
        "scene_digest": _json_digest(scenes or []),
    }
    try:
        meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    emit_checkpoint(stage="complete", status="complete", force=True, final=True, message=f"Internal render complete: {final_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists(), "final_exists": final_mp4.exists()})
    if log_fn:
        log_fn(f"Internal render complete: {final_mp4.name}")

    return final_mp4


def _disabled_hosted_video_variant(
    *,
    ffmpeg_path: str,
    project_dir: Path,
    variant: dict[str, Any],
    scenes: list[dict[str, Any]],
    audio_path: Path | None,
    settings: InternalVideoSettings,
    _api_key: str,
    hosted_settings: dict[str, Any],
    timeline: dict[str, Any] | None = None,
    log_fn=None,
    progress_fn=None,
    cancel_check_fn=None,
    chunk_plan: dict[str, Any] | None = None,
    checkpoint_fn=None,
) -> Path:
    _require_pillow()
    raise RuntimeError("Hosted rendering is disabled by the NVIDIA-only profile.")

    service = str(hosted_settings.get("service") or "sd3").strip().lower()
    model = str(hosted_settings.get("model") or "sd3.5-large-turbo").strip().lower()
    style_preset = str(hosted_settings.get("style_preset") or "none").strip().lower()
    output_format = str(hosted_settings.get("output_format") or "png").strip().lower()
    hosted_strength = float(hosted_settings.get("strength") or settings.temporal_strength or 0.55)
    hosted_cfg_scale = float(hosted_settings.get("cfg_scale") or settings.cfg or 6.5)

    out_w, out_h = settings.width, settings.height
    fps_r = max(1, int(settings.fps_render))
    fps_schedule = max(1, int(settings.fps_output))
    duration_s = float(variant.get("duration_s") or _infer_duration(scenes))
    total_frames = int(math.ceil(duration_s * fps_r))
    deforum_context = _build_unified_deforum_context(
        scenes=scenes,
        timeline=timeline,
        variant=variant,
        settings=settings,
        fps=fps_schedule,
    )

    provider_marker = Path(f"stability_platform/{service}/{model or 'default'}")
    work_tag = _build_work_tag(
        variant_index=int(variant.get("index", 0)),
        variant=variant,
        scenes=scenes,
        timeline=timeline,
        model_dir=provider_marker,
        settings=settings,
    )
    out_frames = project_dir / "outputs" / "frames_internal" / work_tag
    out_frames.mkdir(parents=True, exist_ok=True)

    key_times = _scene_keyframe_times(scenes, settings.keyframe_interval_s)
    total_units = max(1, len(key_times) + total_frames + 3)
    cache_info = describe_internal_render_cache(
        project_dir=project_dir,
        variant_index=int(variant.get("index", 0)),
        variant=variant,
        scenes=scenes,
        timeline=timeline,
        model_dir=provider_marker,
        settings=settings,
        total_frames=total_frames,
    )
    raw_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_raw.mp4"
    interp_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_interp.mp4"
    final_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}.mp4"
    meta_json = project_dir / "outputs" / "videos" / f"{work_tag}.render.json"
    checkpoint_json = project_dir / "outputs" / "videos" / f"{work_tag}.checkpoint.json"
    emit_checkpoint = _build_checkpoint_emitter(
        checkpoint_json=checkpoint_json,
        project_dir=project_dir,
        work_tag=work_tag,
        render_mode="hosted",
        variant_index=int(variant.get("index", 0)),
        total_frames=total_frames,
        fps_render=fps_r,
        chunk_plan=chunk_plan,
        checkpoint_fn=checkpoint_fn,
    )
    if progress_fn:
        progress_fn("preparing", 0, total_units, f"Preparing hosted Stability render via {service}")
    emit_checkpoint(stage="preparing", status="running", force=True, message=f"Preparing hosted Stability render via {service}")

    if log_fn:
        log_fn(
            f"Hosted Stability render cache tag={work_tag} service={service} model={model or 'default'} "
            f"resume_existing_frames={'yes' if settings.resume_existing_frames else 'no'}"
        )
        log_fn(
            f"Cache status frames={cache_info['frames_present']}/{cache_info['frames_expected']} "
            f"raw={'yes' if cache_info['raw_exists'] else 'no'} "
            f"interp={'yes' if cache_info['interp_exists'] else 'no'} "
            f"final={'yes' if cache_info['final_exists'] else 'no'}"
        )

    if settings.resume_existing_frames and final_mp4.exists():
        final_mtime = final_mp4.stat().st_mtime
        audio_ok = (audio_path is None) or (not audio_path.exists()) or (final_mtime >= audio_path.stat().st_mtime)
        if audio_ok:
            emit_checkpoint(stage="complete", status="complete", force=True, final=True, message=f"Reusing completed hosted render {final_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists(), "final_exists": True})
            if progress_fn:
                progress_fn("complete", total_units, total_units, f"Reusing completed hosted render {final_mp4.name}")
            if log_fn:
                log_fn(f"Reusing completed hosted render {final_mp4.name}")
            return final_mp4

    key_imgs: dict[float, Image.Image] = {}
    prev_key_img: Image.Image | None = None
    supports_init = service in {"sd3", "ultra"}
    for i, t in enumerate(key_times):
        if cancel_check_fn:
            cancel_check_fn()
        schedule_frame = int(round(float(t) * float(fps_schedule)))
        prompt = _prompt_text_for_frame(
            frame_idx=schedule_frame,
            scenes=scenes,
            timeline=timeline,
            deforum_context=deforum_context,
            fps=fps_schedule,
        ) or "cinematic"
        negative_prompt = _negative_prompt_for_frame(frame_idx=schedule_frame, settings=settings, deforum_context=deforum_context)
        seed = int(hash(f"hosted-key:{t}:{prompt}") & 0x7FFFFFFF)
        if progress_fn:
            progress_fn("keyframes", i, total_units, f"Generating hosted keyframe {i+1}/{len(key_times)}")
        emit_checkpoint(stage="keyframes", status="running", message=f"Generating hosted keyframe {i+1}/{len(key_times)}")
        if log_fn:
            log_fn(f"Hosted keyframe {i+1}/{len(key_times)} t={t:.2f}s seed={seed} service={service} model={model or 'default'}")

        key_result = client.generate_image(
            prompt=prompt,
            width=out_w,
            height=out_h,
            service=service,
            model=model,
            style_preset=style_preset,
            negative_prompt=negative_prompt,
            seed=seed,
            init_image=(prev_key_img if supports_init and prev_key_img is not None and settings.temporal_mode != "off" else None),
            strength=hosted_strength,
            cfg_scale=hosted_cfg_scale,
            output_format=output_format,
        )
        img = key_result.image
        key_imgs[t] = img
        prev_key_img = img
        if progress_fn:
            progress_fn("keyframes", i + 1, total_units, f"Ready hosted keyframe {i+1}/{len(key_times)}")
        emit_checkpoint(stage="keyframes", status="running", message=f"Ready hosted keyframe {i+1}/{len(key_times)}")

    def _save_frame(img: Image.Image, fi: int, t: float) -> Path:
        if timeline:
            img = apply_timeline_layers(img, project_dir=project_dir, timeline=timeline, t=t)
        p = _frame_path(out_frames, fi)
        img.save(p)
        return p

    for fi in range(total_frames):
        if cancel_check_fn:
            cancel_check_fn()
        t = fi / fps_r
        existing = _frame_path(out_frames, fi)
        if settings.resume_existing_frames and existing.exists():
            if progress_fn:
                progress_fn("frames", len(key_times) + fi + 1, total_units, f"Reusing hosted frame {fi+1}/{total_frames}")
            emit_checkpoint(stage="frames", status="running", message=f"Reusing hosted frame {fi+1}/{total_frames}", frame_event="reused", reused_delta=1)
            continue

        a, _b, _w = _key_times_bracket(key_times, t)
        src = key_imgs[a]
        zoom, pan_x, pan_y, rot = _camera_at_time(
            t,
            timeline=timeline,
            fallback_interval_s=settings.keyframe_interval_s,
            deforum_motion=deforum_context.motion,
            fps=fps_schedule,
        )
        fr = _ken_burns_frame(src, out_w, out_h, zoom=zoom, pan_x=pan_x, pan_y=pan_y, rotation_deg=rot)
        _save_frame(fr, fi, t)
        if progress_fn:
            progress_fn("frames", len(key_times) + fi + 1, total_units, f"Rendered hosted frame {fi+1}/{total_frames}")
        emit_checkpoint(stage="frames", status="running", message=f"Rendered hosted frame {fi+1}/{total_frames}", frame_event="rendered", rendered_delta=1)
        if log_fn and fi % max(1, fps_r * 3) == 0:
            log_fn(f"Rendered hosted frame {fi+1}/{total_frames}")

    if cancel_check_fn:
        cancel_check_fn()

    raw_mp4.parent.mkdir(parents=True, exist_ok=True)
    if settings.resume_existing_frames and cache_info["frames_complete"] and raw_mp4.exists():
        if progress_fn:
            progress_fn("assembling", total_units - 2, total_units, f"Reusing hosted raw MP4 {raw_mp4.name}")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Reusing hosted raw MP4 {raw_mp4.name}", extra_outputs={"raw_exists": True})
        if log_fn:
            log_fn(f"Reusing hosted raw MP4 {raw_mp4.name}")
    else:
        if progress_fn:
            progress_fn("assembling", total_units - 2, total_units, "Assembling hosted raw MP4")
        emit_checkpoint(stage="assembling", status="running", force=True, message="Assembling hosted raw MP4")
        if log_fn:
            log_fn("Assembling hosted raw MP4 from rendered frames")
        assemble_image_sequence(
            ffmpeg_path=ffmpeg_path,
            frames_dir=out_frames,
            out_mp4=raw_mp4,
            fps=fps_r,
            glob_pattern="frame_*.png",
            audio_path=None,
        )

    if cancel_check_fn:
        cancel_check_fn()

    if int(settings.fps_output) == int(fps_r):
        if not interp_mp4.exists() or interp_mp4.stat().st_mtime < raw_mp4.stat().st_mtime:
            interp_mp4.write_bytes(raw_mp4.read_bytes())
        if progress_fn:
            progress_fn("assembling", total_units - 1, total_units, f"Keeping FPS at {int(settings.fps_output)}")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Keeping FPS at {int(settings.fps_output)}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": True})
    elif settings.resume_existing_frames and interp_mp4.exists() and raw_mp4.exists() and interp_mp4.stat().st_mtime >= raw_mp4.stat().st_mtime:
        if progress_fn:
            progress_fn("assembling", total_units - 1, total_units, f"Reusing hosted interpolated MP4 {interp_mp4.name}")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Reusing hosted interpolated MP4 {interp_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": True})
    else:
        if progress_fn:
            progress_fn("assembling", total_units - 1, total_units, f"Interpolating to {int(settings.fps_output)} fps")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Interpolating to {int(settings.fps_output)} fps", extra_outputs={"raw_exists": raw_mp4.exists()})
        interpolate_video_fps(
            ffmpeg_path=ffmpeg_path,
            in_mp4=raw_mp4,
            out_mp4=interp_mp4,
            fps_out=int(settings.fps_output),
            engine=settings.interpolation_engine,
        )

    if cancel_check_fn:
        cancel_check_fn()

    if settings.resume_existing_frames and final_mp4.exists():
        final_mtime = final_mp4.stat().st_mtime
        audio_ok = (audio_path is None) or (not audio_path.exists()) or (final_mtime >= audio_path.stat().st_mtime)
        interp_ok = interp_mp4.exists() and final_mtime >= interp_mp4.stat().st_mtime
    else:
        audio_ok = False
        interp_ok = False

    if audio_ok and interp_ok:
        if progress_fn:
            progress_fn("muxing", total_units, total_units, f"Reusing hosted final video {final_mp4.name}")
        emit_checkpoint(stage="muxing", status="running", force=True, message=f"Reusing hosted final video {final_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists(), "final_exists": True})
    else:
        if progress_fn:
            progress_fn("muxing", total_units, total_units, "Muxing audio and finalizing hosted video")
        emit_checkpoint(stage="muxing", status="running", force=True, message="Muxing audio and finalizing hosted video", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists()})
        if audio_path and audio_path.exists():
            mux_audio(ffmpeg_path=ffmpeg_path, video_mp4=interp_mp4, audio_path=audio_path, out_mp4=final_mp4)
        else:
            final_mp4.write_bytes(interp_mp4.read_bytes())

    meta = {
        "work_tag": work_tag,
        "completed_at": __import__("time").time(),
        "variant_index": int(variant.get("index", 0)),
        "render_mode": "hosted",
        "hosted_provider": {
            "service": service,
            "model": model,
            "style_preset": style_preset,
            "output_format": output_format,
            "strength": hosted_strength,
            "cfg_scale": hosted_cfg_scale,
        },
        "settings": {
            "fps_render": int(settings.fps_render),
            "fps_output": int(settings.fps_output),
            "width": int(settings.width),
            "height": int(settings.height),
            "keyframe_interval_s": float(settings.keyframe_interval_s),
            "interpolation_engine": str(settings.interpolation_engine),
            "temporal_mode": "keyframes",
            "resume_existing_frames": bool(settings.resume_existing_frames),
            "model_id": str(settings.model_id),
        },
        "frames": {
            "expected": int(total_frames),
            "present": len(list(out_frames.glob("frame_*.png"))),
            "dir": str(out_frames),
        },
        "outputs": {
            "raw_mp4": str(raw_mp4),
            "interp_mp4": str(interp_mp4),
            "final_mp4": str(final_mp4),
            "checkpoint_json": str(checkpoint_json),
        },
        "timeline_digest": _json_digest(_timeline_render_fingerprint(timeline)),
        "scene_digest": _json_digest(scenes or []),
    }
    try:
        meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    emit_checkpoint(stage="complete", status="complete", force=True, final=True, message=f"Hosted render complete: {final_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists(), "final_exists": final_mp4.exists()})
    if log_fn:
        log_fn(f"Hosted render complete: {final_mp4.name}")
    return final_mp4


def _proxy_scene_at_time(scenes: list[dict[str, Any]], t: float) -> dict[str, Any] | None:
    for sc in scenes or []:
        try:
            start = float(sc.get("start_s", 0.0) or 0.0)
            end = float(sc.get("end_s", start) or start)
        except Exception:
            continue
        if start <= t < max(start, end):
            return sc
    return (scenes or [None])[-1]


def _proxy_palette(prompt: str) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    raw = hashlib.sha1((prompt or "proxy").encode("utf-8", errors="ignore")).digest()
    a = (40 + raw[0] % 80, 40 + raw[1] % 80, 60 + raw[2] % 80)
    b = (100 + raw[3] % 100, 80 + raw[4] % 100, 100 + raw[5] % 100)
    c = (180 + raw[6] % 60, 160 + raw[7] % 60, 180 + raw[8] % 60)
    return a, b, c


def _wrap_text(text: str, width: int = 28) -> list[str]:
    words = [w for w in re.split(r"\s+", (text or "").strip()) if w]
    if not words:
        return []
    lines: list[str] = []
    line = words[0]
    for w in words[1:]:
        if len(line) + 1 + len(w) <= width:
            line += " " + w
        else:
            lines.append(line)
            line = w
    lines.append(line)
    return lines[:4]


def _build_proxy_base_frame(
    *,
    width: int,
    height: int,
    t: float,
    duration_s: float,
    scene: dict[str, Any] | None,
) -> Image.Image:
    _require_pillow()
    scene = scene or {}
    prompt = str(scene.get("prompt") or scene.get("name") or "EDMG Studio draft proxy")
    a, b, c = _proxy_palette(prompt)
    img = Image.new("RGB", (int(width), int(height)), color=a)
    px = img.load()
    for y in range(int(height)):
        mix = y / max(1, int(height) - 1)
        row = tuple(int(a[i] * (1.0 - mix) + b[i] * mix) for i in range(3))
        for x in range(int(width)):
            px[x, y] = row
    draw = ImageDraw.Draw(img) if ImageDraw is not None else None
    font = ImageFont.load_default() if ImageFont is not None else None
    if draw is not None:
        band_h = max(44, int(height * 0.16))
        draw.rectangle([(0, height - band_h), (width, height)], fill=(10, 10, 14))
        prog = 0.0 if duration_s <= 0 else max(0.0, min(1.0, t / duration_s))
        draw.rectangle([(0, height - 8), (int(width * prog), height)], fill=c)
        scene_label = str(scene.get("name") or scene.get("emotion") or "Draft proxy")
        draw.text((18, 18), scene_label[:48], fill=(245, 245, 245), font=font)
        draw.text((18, height - band_h + 10), f"t={t:05.2f}s / {duration_s:05.2f}s", fill=(240, 240, 240), font=font)
        for idx, line in enumerate(_wrap_text(prompt, width=30)):
            draw.text((18, 50 + idx * 18), line, fill=(255, 255, 255), font=font)
    return img


def render_internal_proxy_video_variant(
    *,
    ffmpeg_path: str,
    project_dir: Path,
    variant: dict[str, Any],
    scenes: list[dict[str, Any]],
    audio_path: Path | None,
    settings: InternalVideoSettings,
    timeline: dict[str, Any] | None = None,
    log_fn=None,
    progress_fn=None,
    cancel_check_fn=None,
    chunk_plan: dict[str, Any] | None = None,
    checkpoint_fn=None,
) -> Path:
    """Render a local draft/proxy video with no diffusion dependency.

    This keeps the EDMG Studio loop productive even when ComfyUI or internal SD
    models are not installed yet. The proxy video visualizes pacing, scene prompt
    changes, and timeline overlays/text/masks using only Pillow + FFmpeg.
    """
    _require_pillow()

    out_w, out_h = settings.width, settings.height
    fps_r = max(1, int(settings.fps_render))
    fps_out = max(1, int(settings.fps_output))
    duration_s = float(variant.get("duration_s") or _infer_duration(scenes))
    total_frames = int(math.ceil(duration_s * fps_r))

    work_tag = _build_proxy_work_tag(
        variant_index=int(variant.get("index", 0)),
        scenes=scenes,
        timeline=timeline,
        settings=settings,
    )
    out_frames = project_dir / "outputs" / "frames_proxy" / work_tag
    out_frames.mkdir(parents=True, exist_ok=True)

    cache_info = describe_proxy_render_cache(
        project_dir=project_dir,
        variant_index=int(variant.get("index", 0)),
        scenes=scenes,
        timeline=timeline,
        settings=settings,
        total_frames=total_frames,
    )
    total_units = max(1, total_frames + 3)
    raw_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_raw.mp4"
    interp_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_interp.mp4"
    final_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}.mp4"
    meta_json = project_dir / "outputs" / "videos" / f"{work_tag}.render.json"
    checkpoint_json = project_dir / "outputs" / "videos" / f"{work_tag}.checkpoint.json"
    emit_checkpoint = _build_checkpoint_emitter(
        checkpoint_json=checkpoint_json,
        project_dir=project_dir,
        work_tag=work_tag,
        render_mode="proxy",
        variant_index=int(variant.get("index", 0)),
        total_frames=total_frames,
        fps_render=fps_r,
        chunk_plan=chunk_plan,
        checkpoint_fn=checkpoint_fn,
    )

    emit_checkpoint(stage="preparing", status="running", force=True, message="Preparing proxy render")

    if settings.resume_existing_frames and final_mp4.exists():
        final_mtime = final_mp4.stat().st_mtime
        audio_ok = (audio_path is None) or (not audio_path.exists()) or (final_mtime >= audio_path.stat().st_mtime)
        if audio_ok:
            emit_checkpoint(stage="complete", status="complete", force=True, final=True, message=f"Reusing completed proxy render {final_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists(), "final_exists": True})
            if progress_fn:
                progress_fn("complete", total_units, total_units, f"Reusing completed proxy render {final_mp4.name}")
            if log_fn:
                log_fn(f"Reusing completed proxy render {final_mp4.name}")
            return final_mp4

    if log_fn:
        log_fn(
            f"Proxy render cache tag={work_tag} resume_existing_frames={'yes' if settings.resume_existing_frames else 'no'}"
        )
        log_fn(
            f"Cache status frames={cache_info['frames_present']}/{cache_info['frames_expected']} "
            f"raw={'yes' if cache_info['raw_exists'] else 'no'} "
            f"interp={'yes' if cache_info['interp_exists'] else 'no'} "
            f"final={'yes' if cache_info['final_exists'] else 'no'}"
        )

    for fi in range(total_frames):
        if cancel_check_fn:
            cancel_check_fn()
        t = fi / fps_r
        existing = _frame_path(out_frames, fi)
        if settings.resume_existing_frames and existing.exists():
            if progress_fn:
                progress_fn("frames", fi + 1, total_units, f"Reusing proxy frame {fi+1}/{total_frames}")
            emit_checkpoint(stage="frames", status="running", message=f"Reusing proxy frame {fi+1}/{total_frames}", frame_event="reused", reused_delta=1)
            continue
        scene = _proxy_scene_at_time(scenes, t)
        img = _build_proxy_base_frame(width=out_w, height=out_h, t=t, duration_s=duration_s, scene=scene)
        try:
            img = apply_timeline_layers(img, project_dir=project_dir, timeline=(timeline or {}), t=float(t))
        except Exception:
            pass
        img.save(existing)
        if progress_fn:
            progress_fn("frames", fi + 1, total_units, f"Rendered proxy frame {fi+1}/{total_frames}")
        emit_checkpoint(stage="frames", status="running", message=f"Rendered proxy frame {fi+1}/{total_frames}", frame_event="rendered", rendered_delta=1)
        if log_fn and fi % max(1, fps_r * 4) == 0:
            log_fn(f"Rendered proxy frame {fi+1}/{total_frames}")

    raw_mp4.parent.mkdir(parents=True, exist_ok=True)
    if settings.resume_existing_frames and cache_info["frames_complete"] and raw_mp4.exists():
        if progress_fn:
            progress_fn("assembling", total_units - 2, total_units, f"Reusing proxy raw MP4 {raw_mp4.name}")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Reusing proxy raw MP4 {raw_mp4.name}", extra_outputs={"raw_exists": True})
    else:
        if progress_fn:
            progress_fn("assembling", total_units - 2, total_units, "Assembling proxy raw MP4")
        emit_checkpoint(stage="assembling", status="running", force=True, message="Assembling proxy raw MP4")
        assemble_image_sequence(
            ffmpeg_path=ffmpeg_path,
            frames_dir=out_frames,
            out_mp4=raw_mp4,
            fps=fps_r,
            glob_pattern="frame_*.png",
            audio_path=None,
        )

    if fps_out == fps_r:
        if not interp_mp4.exists() or interp_mp4.stat().st_mtime < raw_mp4.stat().st_mtime:
            interp_mp4.write_bytes(raw_mp4.read_bytes())
        if progress_fn:
            progress_fn("assembling", total_units - 1, total_units, f"Keeping proxy FPS at {fps_out}")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Keeping proxy FPS at {fps_out}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": True})
    elif settings.resume_existing_frames and interp_mp4.exists() and interp_mp4.stat().st_mtime >= raw_mp4.stat().st_mtime:
        if progress_fn:
            progress_fn("assembling", total_units - 1, total_units, f"Reusing interpolated proxy MP4 {interp_mp4.name}")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Reusing interpolated proxy MP4 {interp_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": True})
    else:
        if progress_fn:
            progress_fn("assembling", total_units - 1, total_units, f"Interpolating proxy render to {fps_out} fps")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Interpolating proxy render to {fps_out} fps", extra_outputs={"raw_exists": raw_mp4.exists()})
        interpolate_video_fps(
            ffmpeg_path=ffmpeg_path,
            in_mp4=raw_mp4,
            out_mp4=interp_mp4,
            fps_out=fps_out,
            engine=settings.interpolation_engine,
        )

    if audio_path and audio_path.exists():
        if progress_fn:
            progress_fn("muxing", total_units, total_units, "Muxing proxy render audio")
        emit_checkpoint(stage="muxing", status="running", force=True, message="Muxing proxy render audio", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists()})
        mux_audio(ffmpeg_path=ffmpeg_path, video_mp4=interp_mp4, audio_path=audio_path, out_mp4=final_mp4)
    else:
        final_mp4.write_bytes(interp_mp4.read_bytes())
        if progress_fn:
            progress_fn("muxing", total_units, total_units, f"Saved proxy render {final_mp4.name}")
        emit_checkpoint(stage="muxing", status="running", force=True, message=f"Saved proxy render {final_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists()})

    meta = {
        "work_tag": work_tag,
        "completed_at": __import__("time").time(),
        "variant_index": int(variant.get("index", 0)),
        "render_mode": "proxy",
        "settings": {
            "fps_render": int(settings.fps_render),
            "fps_output": int(settings.fps_output),
            "width": int(settings.width),
            "height": int(settings.height),
            "interpolation_engine": str(settings.interpolation_engine),
            "resume_existing_frames": bool(settings.resume_existing_frames),
            "model_id": str(settings.model_id or "proxy_draft"),
        },
        "frames": {
            "expected": int(total_frames),
            "present": len(list(out_frames.glob("frame_*.png"))),
            "dir": str(out_frames),
        },
        "outputs": {
            "raw_mp4": str(raw_mp4),
            "interp_mp4": str(interp_mp4),
            "final_mp4": str(final_mp4),
            "checkpoint_json": str(checkpoint_json),
        },
        "timeline_digest": _json_digest(_timeline_render_fingerprint(timeline)),
        "scene_digest": _json_digest(scenes or []),
    }
    try:
        meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    emit_checkpoint(stage="complete", status="complete", force=True, final=True, message=f"Proxy render complete: {final_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists(), "final_exists": final_mp4.exists()})
    if log_fn:
        log_fn(f"Proxy render complete: {final_mp4.name}")

    return final_mp4



def render_internal_diffusion_preview_segment(
    *,
    ffmpeg_path: str,
    project_dir: Path,
    scenes: list[dict[str, Any]],
    model_dir: Path,
    settings: InternalVideoSettings,
    timeline: dict[str, Any] | None,
    start_s: float,
    end_s: float,
    fps: int,
    out_mp4: Path,
    prompt_override: str | None = None,
    seed: int | None = None,
    force: bool = False,
    log_fn=None,
) -> Path:
    """Render a short cached diffusion preview clip (low-res, low steps).

    Intended for quick "look" checks inside the Timeline page. This is NOT a full render:
      - capped duration
      - no audio mux
      - low FPS and low steps by default (caller should set settings.steps/settings.cfg)

    Cache keys and directories are managed by the caller (backend endpoint).
    """
    _require_pillow()

    start = max(0.0, float(start_s))
    end = max(start + 0.05, float(end_s))
    # protect the machine: keep previews short
    end = min(end, start + 10.0)
    fps_i = max(1, min(12, int(fps)))

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    if out_mp4.exists() and not force:
        return out_mp4

    # tmp frames directory
    frames_dir = out_mp4.parent / f"_tmp_{out_mp4.stem}"
    if frames_dir.exists():
        try:
            for f in frames_dir.glob("*.png"):
                f.unlink(missing_ok=True)
        except Exception:
            pass
    frames_dir.mkdir(parents=True, exist_ok=True)

    device = _device_auto(settings.device_preference)
    pipes = _try_load_pipelines(model_dir, device=device)

    try:
        import torch  # type: ignore
    except Exception:
        torch = None  # type: ignore

    # Stable seed for repeatable previews
    base_seed = int(seed) if seed is not None else int(settings.seed or 1337)
    gen = None
    try:
        if torch is not None:
            gen = torch.Generator(device=device).manual_seed(base_seed)
    except Exception:
        gen = None

    # Render frames
    n = int(math.ceil((end - start) * fps_i))
    prev_img = None
    fps_schedule = max(1, int(settings.fps_output))
    deforum_context = _build_unified_deforum_context(
        scenes=scenes,
        timeline=timeline,
        variant=None,
        settings=settings,
        fps=fps_schedule,
    )

    # Limit preview cost even if user set aggressive settings
    steps = max(1, min(int(settings.steps), 30))
    cfg = float(settings.cfg)

    for i in range(n):
        t = start + (i / fps_i)
        schedule_frame = int(round(float(t) * float(fps_schedule)))

        prompt = (prompt_override or "").strip()
        if not prompt:
            prompt = _prompt_text_for_frame(
                frame_idx=schedule_frame,
                scenes=scenes,
                timeline=timeline,
                deforum_context=deforum_context,
                fps=fps_schedule,
            )
        neg = _negative_prompt_for_frame(frame_idx=schedule_frame, settings=settings, deforum_context=deforum_context)

        # camera motion (camera keyframes -> motion track -> fallback)
        zoom, pan_x, pan_y, rot = _camera_at_time(
            t,
            timeline=timeline,
            fallback_interval_s=settings.keyframe_interval_s,
            deforum_motion=deforum_context.motion,
            fps=fps_schedule,
        )

        # low-cost temporal continuity
        use_img2img = (settings.temporal_mode or "").lower() == "frame_img2img" and prev_img is not None
        strength = float(settings.temporal_strength if use_img2img else 1.0)

        try:
            if use_img2img:
                # img2img path
                img = pipes.img2img(
                    prompt=prompt,
                    negative_prompt=neg,
                    image=prev_img,
                    strength=strength,
                    guidance_scale=cfg,
                    num_inference_steps=steps,
                    generator=gen,
                ).images[0]
            else:
                img = pipes.txt2img(
                    prompt=prompt,
                    negative_prompt=neg,
                    width=int(settings.width),
                    height=int(settings.height),
                    guidance_scale=cfg,
                    num_inference_steps=steps,
                    generator=gen,
                ).images[0]
        except Exception as e:
            raise UserFacingError(
                "Diffusion preview failed",
                hint=f"Try lower resolution/steps, or switch internal model. Error: {e}",
                code="DIFF_PREVIEW",
                status_code=500,
            ) from e

        # Apply camera transform and overlays at absolute time t
        try:
            fr = _ken_burns_frame(img, int(settings.width), int(settings.height), zoom=zoom, pan_x=pan_x, pan_y=pan_y, rotation_deg=rot)
        except Exception:
            fr = img

        try:
            fr = apply_timeline_layers(fr, project_dir=project_dir, timeline=(timeline or {}), t=float(t))
        except Exception:
            pass

        fr.save(frames_dir / f"frame_{i:06d}.png")
        prev_img = img

        if log_fn and i % max(1, fps_i * 2) == 0:
            log_fn(f"Diffusion preview frame {i+1}/{n}")

    assemble_image_sequence(
        ffmpeg_path=ffmpeg_path,
        frames_dir=frames_dir,
        out_mp4=out_mp4,
        fps=fps_i,
        glob_pattern="frame_*.png",
        audio_path=None,
    )

    # cleanup
    try:
        for f in frames_dir.glob("*.png"):
            f.unlink(missing_ok=True)
        frames_dir.rmdir()
    except Exception:
        pass

    return out_mp4

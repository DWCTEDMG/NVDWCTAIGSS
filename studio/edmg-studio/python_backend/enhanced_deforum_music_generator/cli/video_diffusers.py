"""enhanced_deforum_music_generator.cli.video_diffusers

Unified CLI for running multiple Hugging Face Diffusers *video* pipelines.

Supported families:
- wan: `WanPipeline` (text-to-video, image-to-video depending on model and args)
- hunyuan_video15: `HunyuanVideo15Pipeline` / `HunyuanVideo15ImageToVideoPipeline`
- cogvideox: `CogVideoXPipeline` / `CogVideoXImageToVideoPipeline`
- svd: `StableVideoDiffusionPipeline` (image-to-video)

This module is intentionally self-contained so it can be used as:
    python -m enhanced_deforum_music_generator.cli.video_diffusers ...

References:
- Wan Diffusers docs show `WanPipeline` usage and `export_to_video`. 
- Diffusers exposes CogVideoX pipeline docs. 
- Stable Video Diffusion model card references `StableVideoDiffusionPipeline`. 
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path
from typing import Optional


def _parse_dtype(dtype: str):
    import torch

    m = {
        "float16": torch.float16,
        "fp16": torch.float16,
        "bfloat16": torch.bfloat16,
        "bf16": torch.bfloat16,
        "float32": torch.float32,
        "fp32": torch.float32,
    }
    key = dtype.lower()
    if key not in m:
        raise ValueError(f"Unsupported dtype: {dtype}. Use one of: {', '.join(sorted(m))}")
    return m[key]


def _load_image(path: Optional[str]):
    if not path:
        return None
    from diffusers.utils import load_image

    return load_image(path)


def _export_video(frames, out_path: Path, fps: int) -> None:
    from diffusers.utils import export_to_video

    out_path.parent.mkdir(parents=True, exist_ok=True)
    export_to_video(frames, str(out_path), fps=fps)


def _seeded_generator(seed: Optional[int], device: str):
    import torch

    if seed is None:
        seed = random.randint(0, 2**31 - 1)
    g = torch.Generator(device=device)
    g.manual_seed(int(seed))
    return g, int(seed)


def _infer_family(model_id: str) -> str:
    mid = model_id.lower()
    if "wan" in mid:
        return "wan"
    if "hunyuan" in mid:
        return "hunyuan_video15"
    if "cogvideox" in mid:
        return "cogvideox"
    if "ltx" in mid or "lightricks" in mid:
        return "ltx"
    if "stable-video-diffusion" in mid or "svd" in mid:
        return "svd"
    return "auto"


def run_cli(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(_run(args))


def _run(args: argparse.Namespace) -> int:
    dtype = _parse_dtype(args.dtype)
    device = args.device
    if device != "cuda":
        raise RuntimeError("The NVIDIA-only profile requires --device cuda.")
    if args.cpu_offload:
        raise RuntimeError("CPU offload is disabled by the NVIDIA-only profile.")

    family = args.family if args.family != "auto" else _infer_family(args.model_id)
    prompt = args.prompt
    negative_prompt = args.negative_prompt
    generator, used_seed = _seeded_generator(args.seed, device)
    image = _load_image(args.image)
    out = Path(args.output)

    if family == "wan":
        from diffusers import AutoencoderKLWan, WanPipeline

        import torch
        vae_dtype = _parse_dtype(args.vae_dtype)
        vae = AutoencoderKLWan.from_pretrained(args.model_id, subfolder="vae", torch_dtype=vae_dtype)
        pipe = WanPipeline.from_pretrained(args.model_id, vae=vae, torch_dtype=dtype)
        if args.cpu_offload:
            pipe.enable_model_cpu_offload()
        else:
            pipe.to(device)

        result = pipe(
            prompt=prompt,
            negative_prompt=negative_prompt,
            height=args.height,
            width=args.width,
            num_frames=args.num_frames,
            num_inference_steps=args.steps,
            guidance_scale=args.guidance_scale,
            generator=generator,
        )
        _export_video(result.frames[0], out, fps=args.fps)
        print(f"✅ Wrote: {out} (seed={used_seed})")
        return 0

    if family == "hunyuan_video15":
        if image is None:
            try:
                from diffusers import HunyuanVideo15Pipeline as PipeCls
            except Exception as e:
                raise RuntimeError(
                    "Missing HunyuanVideo15Pipeline in your diffusers. "
                    "Upgrade diffusers to a recent version."
                ) from e
            pipe = PipeCls.from_pretrained(args.model_id, torch_dtype=dtype)
        else:
            try:
                from diffusers import HunyuanVideo15ImageToVideoPipeline as PipeCls
            except Exception as e:
                raise RuntimeError(
                    "Missing HunyuanVideo15ImageToVideoPipeline in your diffusers. "
                    "Upgrade diffusers to a recent version."
                ) from e
            pipe = PipeCls.from_pretrained(args.model_id, torch_dtype=dtype)

        if args.cpu_offload:
            pipe.enable_model_cpu_offload()
        else:
            pipe.to(device)

        kwargs = dict(
            prompt=prompt,
            negative_prompt=negative_prompt,
            num_frames=args.num_frames,
            num_inference_steps=args.steps,
            guidance_scale=args.guidance_scale,
            generator=generator,
        )
        if image is not None:
            kwargs["image"] = image

        result = pipe(**kwargs)
        _export_video(result.frames[0], out, fps=args.fps)
        print(f"✅ Wrote: {out} (seed={used_seed})")
        return 0

    if family == "cogvideox":
        if image is None:
            from diffusers import CogVideoXPipeline as PipeCls

            pipe = PipeCls.from_pretrained(args.model_id, torch_dtype=dtype)
        else:
            from diffusers import CogVideoXImageToVideoPipeline as PipeCls

            pipe = PipeCls.from_pretrained(args.model_id, torch_dtype=dtype)

        if args.cpu_offload:
            pipe.enable_model_cpu_offload()
        else:
            pipe.to(device)

        kwargs = dict(
            prompt=prompt,
            negative_prompt=negative_prompt,
            num_frames=args.num_frames,
            num_inference_steps=args.steps,
            guidance_scale=args.guidance_scale,
            generator=generator,
        )
        if image is None:
            kwargs["height"] = args.height
            kwargs["width"] = args.width
        else:
            kwargs["image"] = image

        result = pipe(**kwargs)
        _export_video(result.frames[0], out, fps=args.fps)
        print(f"✅ Wrote: {out} (seed={used_seed})")
        return 0

    if family == "svd":
        if image is None:
            raise ValueError("SVD is image-to-video. Provide --image.")
        from diffusers import StableVideoDiffusionPipeline

        pipe = StableVideoDiffusionPipeline.from_pretrained(args.model_id, torch_dtype=dtype)
        if args.cpu_offload:
            pipe.enable_model_cpu_offload()
        else:
            pipe.to(device)

        result = pipe(
            image=image,
            num_frames=args.num_frames,
            num_inference_steps=args.steps,
            guidance_scale=args.guidance_scale,
            generator=generator,
        )
        _export_video(result.frames[0], out, fps=args.fps)
        print(f"✅ Wrote: {out} (seed={used_seed})")
        return 0

    
    if family == "ltx":
        if image is None:
            raise ValueError("LTX is typically image-to-video. Provide --image.")
        # Based on LTX-Video Diffusers usage guidance:
        # - LTXConditionPipeline + optional LTXLatentUpsamplePipeline
        from diffusers import LTXConditionPipeline, LTXLatentUpsamplePipeline
        from diffusers.pipelines.ltx.pipeline_ltx_condition import LTXVideoCondition
        from diffusers.utils import export_to_video, load_video

        pipe = LTXConditionPipeline.from_pretrained(args.model_id, torch_dtype=dtype)
        pipe_upsample = None
        if args.upsampler_id:
            pipe_upsample = LTXLatentUpsamplePipeline.from_pretrained(
                args.upsampler_id, vae=pipe.vae, torch_dtype=dtype
            )

        if args.cpu_offload:
            pipe.enable_model_cpu_offload()
            if pipe_upsample is not None:
                pipe_upsample.enable_model_cpu_offload()
        else:
            pipe.to(device)
            if pipe_upsample is not None:
                pipe_upsample.to(device)

        # LTX expects video-like compression for conditions; compress the single image into a 1-frame video.
        cond_video = load_video(export_to_video([image]))
        condition = LTXVideoCondition(video=cond_video, frame_index=0)

        result = pipe(
            prompt=prompt,
            negative_prompt=negative_prompt,
            height=args.height,
            width=args.width,
            num_frames=args.num_frames,
            num_inference_steps=args.steps,
            guidance_scale=args.guidance_scale,
            generator=generator,
            conditions=[condition],
        )
        frames = result.frames[0]
        if pipe_upsample is not None:
            frames = pipe_upsample(frames=frames).frames[0]

        _export_video(frames, out, fps=args.fps)
        print(f"✅ Wrote: {out} (seed={used_seed})")
        return 0
    raise ValueError(f"Unknown family: {family}")


def build_parser() -> argparse.ArgumentParser:
    import torch

    p = argparse.ArgumentParser()
    p.add_argument("--model-id", required=True, help="Hugging Face repo id (Diffusers-format).")
    p.add_argument(
        "--family",
        default="auto",
        choices=["auto", "wan", "hunyuan_video15", "cogvideox", "svd", "ltx"],
        help="Force a family; default auto-detect by model id.",
    )
    p.add_argument("--prompt", required=True)
    p.add_argument("--negative-prompt", default=None)
    p.add_argument("--image", default=None, help="Input image path/URL for I2V pipelines.")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=704)
    p.add_argument("--num-frames", type=int, default=121)
    p.add_argument("--fps", type=int, default=24)
    p.add_argument("--steps", type=int, default=50)
    p.add_argument("--guidance-scale", type=float, default=5.0)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--dtype", default="bfloat16", help="float16|bfloat16|float32")
    p.add_argument("--device", default="cuda", choices=["cuda"], help="CUDA device for the NVIDIA-only profile.")
    p.add_argument("--cpu-offload", action="store_true", help="Disabled by the NVIDIA-only profile.")
    p.add_argument(
        "--vae-dtype",
        default="float32",
        choices=["float32", "float16", "bfloat16"],
        help="Wan VAE often loaded as float32 for stability; leave default unless you know what you're doing.",
    )
    p.add_argument("--upsampler-id", default="", help="Optional upsampler repo id (LTX).")
    p.add_argument("--output", required=True, help="Output mp4 path.")
    return p


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""scripts/video_model_bench.py

Run a prompt across multiple Diffusers video model families, save results, and write a bench report.

Design:
- Spawns a *new* process per model to reduce VRAM fragmentation and keep failures isolated.
- Produces:
    - outputs/<bench_name>/<model_id_sanitized>.mp4
    - outputs/<bench_name>/bench_report.json
    - outputs/<bench_name>/bench_grid.png (first-frame grid)

Usage:
    python scripts/video_model_bench.py --prompt "..." --bench-name jan07
    python scripts/video_model_bench.py --prompt "..." --quick
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import imageio.v3 as iio
except Exception:
    iio = None


@dataclass
class ModelRun:
    family: str
    model_id: str
    output: str
    ok: bool
    seconds: float
    seed: Optional[int]
    error: str = ""


DEFAULT_MODELS: List[Tuple[str, str]] = [
    ("wan", "Wan-AI/Wan2.2-TI2V-5B-Diffusers"),
    ("hunyuan_video15", "tencent/HunyuanVideo-1.5"),
    ("cogvideox", "THUDM/CogVideoX-5b"),
    ("svd", "stabilityai/stable-video-diffusion-img2vid-xt"),
    ("ltx", "Lightricks/LTX-Video"),
]

SCRIPT_DIR = Path(__file__).resolve().parent
RUNNER_PATH = SCRIPT_DIR / "run_video_diffusers.py"


def _sanitize(s: str) -> str:
    s = s.replace("/", "__")
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s)
    return s[:180]


def build_run_command(
    *,
    runner: str,
    model_id: str,
    prompt: str,
    out_path: str,
    num_frames: int,
    fps: int,
    steps: int,
    guidance_scale: float,
    dtype: str,
    device: str,
    vae_dtype: str,
    family: str | None = None,
    image: str | None = None,
    negative_prompt: str = "",
    width: int = 512,
    height: int = 512,
    seed: int | None = None,
    python_exe: str | None = None,
) -> list[str]:
    """Build the subprocess command used to render a single model in the bench.

    This is primarily for testing and debugging; the bench uses a similar command internally.
    """
    exe = python_exe or sys.executable
    cmd: list[str] = [
        exe,
        runner,
        "--model-id",
        model_id,
        "--prompt",
        prompt,
        "--negative-prompt",
        negative_prompt,
        "--width",
        str(width),
        "--height",
        str(height),
        "--num-frames",
        str(num_frames),
        "--fps",
        str(fps),
        "--steps",
        str(steps),
        "--guidance-scale",
        str(guidance_scale),
        "--dtype",
        dtype,
        "--device",
        device,
        "--vae-dtype",
        vae_dtype,
        "--output",
        out_path,
    ]
    if family:
        cmd.extend(["--family", family])
    if seed is not None:
        cmd.extend(["--seed", str(seed)])
    if image:
        cmd.extend(["--image", image])
    return cmd



def _run_one(
    *,
    python_exe: str,
    family: str,
    model_id: str,
    prompt: str,
    negative_prompt: str,
    out_path: Path,
    width: int,
    height: int,
    num_frames: int,
    fps: int,
    steps: int,
    guidance_scale: float,
    seed: Optional[int],
    dtype: str,
    device: str,
    image: str,
    vae_dtype: str,
) -> ModelRun:
    if device != "cuda":
        raise RuntimeError("The NVIDIA-only profile requires --device cuda.")
    cmd = [
        python_exe,
        str(RUNNER_PATH),
        "--family",
        family,
        "--model-id",
        model_id,
        "--prompt",
        prompt,
        "--negative-prompt",
        negative_prompt,
        "--width",
        str(width),
        "--height",
        str(height),
        "--num-frames",
        str(num_frames),
        "--fps",
        str(fps),
        "--steps",
        str(steps),
        "--guidance-scale",
        str(guidance_scale),
        "--dtype",
        dtype,
        "--device",
        device,
        "--vae-dtype",
        vae_dtype,
        "--output",
        str(out_path),
    ]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    if image:
        cmd += ["--image", image]

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        ok = True
        err = ""
        used_seed = seed
        # try to parse "seed=..." if printed
        m = re.search(r"seed\s*=\s*(\d+)", proc.stdout, flags=re.IGNORECASE)
        if m:
            used_seed = int(m.group(1))
    except subprocess.CalledProcessError as e:
        ok = False
        err = e.stdout[-6000:] if isinstance(e.stdout, str) else str(e)
        used_seed = seed
    dt = time.time() - t0
    return ModelRun(
        family=family,
        model_id=model_id,
        output=str(out_path),
        ok=ok,
        seconds=dt,
        seed=used_seed,
        error=err,
    )


def _first_frame(path: Path):
    if iio is None:
        return None
    try:
        # Works for mp4 if imageio-ffmpeg is installed.
        frame = iio.imread(str(path), index=0)
        return frame
    except Exception:
        return None


def _make_grid(frames: List, out_path: Path, cols: int = 3) -> bool:
    if iio is None:
        return False
    import numpy as np

    frames = [f for f in frames if f is not None]
    if not frames:
        return False

    cols = max(1, cols)
    rows = (len(frames) + cols - 1) // cols

    h = max(int(f.shape[0]) for f in frames)
    w = max(int(f.shape[1]) for f in frames)

    grid = np.zeros((rows * h, cols * w, 3), dtype=np.uint8)
    for idx, f in enumerate(frames):
        r = idx // cols
        c = idx % cols
        fh, fw = f.shape[0], f.shape[1]
        grid[r * h : r * h + fh, c * w : c * w + fw, :3] = f[..., :3]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    iio.imwrite(str(out_path), grid)
    return True


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Benchmark multiple Diffusers video pipelines (EDMG).")
    p.add_argument("--prompt", required=True)
    p.add_argument("--negative-prompt", default="")
    p.add_argument("--bench-name", default="bench")
    p.add_argument("--models", action="append", default=[], help="Repeatable: family=model_id")
    p.add_argument("--quick", action="store_true", help="Small settings for sanity checks.")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=704)
    p.add_argument("--num-frames", type=int, default=121)
    p.add_argument("--fps", type=int, default=24)
    p.add_argument("--steps", type=int, default=50)
    p.add_argument("--guidance-scale", type=float, default=5.0)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--dtype", default="bfloat16")
    p.add_argument("--vae-dtype", default="float32")
    p.add_argument("--device", default="cuda", choices=["cuda"], help="CUDA device for the NVIDIA-only profile.")
    p.add_argument("--image", default="", help="Optional image path for I2V models.")
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--dry-run", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.device != "cuda":
        print("ERROR: the NVIDIA-only profile requires --device cuda.", file=sys.stderr)
        return 2

    out_dir = Path("outputs") / args.bench_name
    out_dir.mkdir(parents=True, exist_ok=True)

    models: List[Tuple[str, str]] = []
    if args.models:
        for m in args.models:
            if "=" not in m:
                print(f"ERROR: --models expects family=model_id, got: {m}", file=sys.stderr)
                return 2
            fam, mid = m.split("=", 1)
            models.append((fam.strip(), mid.strip()))
    else:
        models = list(DEFAULT_MODELS)

    if args.quick:
        # Keep user-visible behavior deterministic: only reduce if user explicitly asks for quick.
        args.width = min(args.width, 768)
        args.height = min(args.height, 432)
        args.num_frames = min(args.num_frames, 49)
        args.steps = min(args.steps, 20)

    runs: List[ModelRun] = []
    frames_for_grid = []

    for family, model_id in models:
        out_path = out_dir / f"{_sanitize(model_id)}.mp4"
        cmd_preview = f"{args.python} {shlex.quote(str(RUNNER_PATH))} --family {family} --model-id {shlex.quote(model_id)} --output {out_path}"
        print(f"\n=== {family} :: {model_id}")
        if args.dry_run:
            print(cmd_preview)
            runs.append(
                ModelRun(
                    family=family,
                    model_id=model_id,
                    output=str(out_path),
                    ok=False,
                    seconds=0.0,
                    seed=args.seed,
                    error="dry-run",
                )
            )
            continue

        run = _run_one(
            python_exe=args.python,
            family=family,
            model_id=model_id,
            prompt=args.prompt,
            negative_prompt=args.negative_prompt,
            out_path=out_path,
            width=args.width,
            height=args.height,
            num_frames=args.num_frames,
            fps=args.fps,
            steps=args.steps,
            guidance_scale=args.guidance_scale,
            seed=args.seed,
            dtype=args.dtype,
            device=args.device,
            image=args.image,
            vae_dtype=args.vae_dtype,
        )
        runs.append(run)
        print(f"ok={run.ok} seconds={run.seconds:.1f} out={run.output}")
        if not run.ok:
            print(run.error)

        if run.ok and out_path.exists():
            frames_for_grid.append(_first_frame(out_path))
        else:
            frames_for_grid.append(None)

    report = {
        "bench_name": args.bench_name,
        "prompt": args.prompt,
        "negative_prompt": args.negative_prompt,
        "settings": {
            "width": args.width,
            "height": args.height,
            "num_frames": args.num_frames,
            "fps": args.fps,
            "steps": args.steps,
            "guidance_scale": args.guidance_scale,
            "seed": args.seed,
            "dtype": args.dtype,
            "vae_dtype": args.vae_dtype,
            "device": args.device,
            "image": args.image,
        },
        "runs": [asdict(r) for r in runs],
    }

    (out_dir / "bench_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    grid_ok = _make_grid(frames_for_grid, out_dir / "bench_grid.png", cols=3)
    if grid_ok:
        print(f"\nWrote grid: {out_dir/'bench_grid.png'}")
    else:
        print("\nGrid not written (missing imageio/ffmpeg or no outputs).")

    print(f"Report: {out_dir/'bench_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

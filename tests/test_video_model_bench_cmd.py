from __future__ import annotations

from pathlib import Path
import sys
import importlib.util


def _load_script_module(script_path: Path):
    name = "video_model_bench"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # required for dataclasses/type resolution during exec
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def test_build_run_command_smoke(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "studio" / "edmg-studio" / "scripts" / "video_model_bench.py"
    mod = _load_script_module(script)

    cmd = mod.build_run_command(
        runner=str(repo_root / "studio" / "edmg-studio" / "scripts" / "run_video_diffusers.py"),
        model_id="Org/Model",
        prompt="hello",
        out_path=str(tmp_path / "out.mp4"),
        num_frames=24,
        fps=12,
        steps=4,
        guidance_scale=5.0,
        dtype="float16",
        device="cuda",
        vae_dtype="float16",
        family="wan",
        image=None,
    )
    assert isinstance(cmd, list)
    assert cmd[0] == sys.executable
    assert "--model-id" in cmd
    assert "Org/Model" in cmd
    assert "--prompt" in cmd
    assert "--cpu-offload" not in cmd

from __future__ import annotations

import json
from pathlib import Path

import pytest
from edmg_studio_backend import app as studio_app
from edmg_studio_backend.integrations import comfyui as comfy
from edmg_studio_backend.services.model_manager import ModelManager
from edmg_studio_backend.store.jobs import JobStore
from edmg_studio_backend.store.projects import ProjectStore
from PIL import Image


def _make_project(tmp_path: Path):
    store = ProjectStore(tmp_path / "data")
    jobs = JobStore(store.projects_dir)
    proj = store.create("Stable Diffusion Slice")
    store.save(proj)
    return store, jobs, proj


def test_default_workflow_chains_loras_into_sampler():
    wf = comfy.default_workflow(
        checkpoint="base.safetensors",
        prompt="neon city",
        negative_prompt="low quality",
        seed=123,
        width=1024,
        height=576,
        steps=28,
        cfg=7.0,
        sampler="euler",
        loras=[
            {"filename": "style-a.safetensors", "weight": 0.8},
            {"filename": "style-b.safetensors", "weight": 1.15},
        ],
    )

    lora_nodes = {node_id: node for node_id, node in wf.items() if node.get("class_type") == "LoraLoader"}
    assert len(lora_nodes) == 2

    final_lora_node = sorted(lora_nodes.keys(), key=int)[-1]
    sampler = next(node for node in wf.values() if node.get("class_type") == "KSampler")
    positive = next(
        node for node in wf.values()
        if node.get("class_type") == "CLIPTextEncode" and node.get("inputs", {}).get("text") == "neon city"
    )

    assert sampler["inputs"]["model"] == [final_lora_node, 0]
    assert positive["inputs"]["clip"] == [final_lora_node, 1]


def test_img2img_workflow_appends_hires_fix_and_refiner_nodes():
    wf = comfy.img2img_workflow(
        checkpoint="base.safetensors",
        prompt="neon skyline",
        negative_prompt="low quality",
        seed=321,
        width=768,
        height=432,
        steps=24,
        cfg=6.5,
        sampler="euler",
        source_image="source.png",
        hires_fix={"enabled": True, "scale": 1.75, "steps": 12, "denoise": 0.3, "upscaler": "latent_bicubic"},
        refiner={"model": "sdxl_refiner.safetensors", "switch_at": 0.8, "steps": 8, "checkpoint": "sdxl_refiner.safetensors"},
        upscaler="latent_bicubic",
    )

    sampler_nodes = [node for node in wf.values() if node.get("class_type") == "KSampler"]
    checkpoint_nodes = [node for node in wf.values() if node.get("class_type") == "CheckpointLoaderSimple"]
    latent_upscales = [node for node in wf.values() if node.get("class_type") == "LatentUpscaleBy"]

    assert len(sampler_nodes) == 3
    assert len(checkpoint_nodes) == 2
    assert latent_upscales
    assert latent_upscales[0]["inputs"]["upscale_method"] == "bicubic"
    assert any(node.get("inputs", {}).get("ckpt_name") == "sdxl_refiner.safetensors" for node in checkpoint_nodes)


def test_run_comfyui_scene_writes_metadata_sidecar(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)
    monkeypatch.setattr(
        studio_app,
        "_resolve_comfy_still_selection",
        lambda **kwargs: {
            "checkpoint": "base.safetensors",
            "workflow_family": "txt2img",
            "controlnet_name": None,
            "conditioning_mode": "raw",
        },
    )
    monkeypatch.setattr(
        studio_app.models,
        "resolve_loras",
        lambda items: [
            {
                "name": "Cinematic Detail LoRA",
                "filename": "cinematic-detail.safetensors",
                "path": str(tmp_path / "cinematic-detail.safetensors"),
                "weight": 0.9,
                "clip_weight": 0.9,
            }
        ],
    )
    monkeypatch.setattr(studio_app.comfy_pool, "acquire", lambda req: "http://comfy.local")
    monkeypatch.setattr(studio_app.comfy_pool, "release", lambda url: None)
    monkeypatch.setattr(studio_app.comfy, "default_workflow", lambda **kwargs: {"ok": True, "workflow": kwargs})
    monkeypatch.setattr(studio_app.comfy, "submit_prompt", lambda url, wf: {"prompt_id": "prompt-123"})
    monkeypatch.setattr(studio_app.comfy, "get_history", lambda url, prompt_id: {"prompt-123": {"outputs": {}}})
    monkeypatch.setattr(
        studio_app.comfy,
        "extract_output_images",
        lambda history: [{"filename": "frame.png", "subfolder": "", "type": "output"}],
    )
    monkeypatch.setattr(studio_app.comfy, "extract_execution_error", lambda history: None)
    monkeypatch.setattr(studio_app.comfy, "download_image_bytes", lambda *args, **kwargs: b"fake-png")

    out_path = store.project_dir(proj.id) / "outputs" / "images" / "scene.png"
    result = studio_app._run_comfyui_scene(
        proj.id,
        "job12345",
        {
            "variant_index": 0,
            "scene_index": 0,
            "model_id": "hf_sd35_large_turbo_ckpt",
            "prompt": "A luminous skyline over water",
            "negative_prompt": "blurry, watermark",
            "seed": 77,
            "width": 1024,
            "height": 576,
            "steps": 30,
            "cfg": 7.5,
            "sampler": "euler",
            "workflow_family": "txt2img",
            "loras": [{"name": "cinematic-detail", "weight": 0.9}],
            "out_path": str(out_path),
        },
    )

    metadata_path = out_path.with_name(f"{out_path.name}.json")
    assert out_path.exists()
    assert metadata_path.exists()
    assert result["metadata_path"] == str(metadata_path)

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["prompt"] == "A luminous skyline over water"
    assert metadata["base_model"]["checkpoint"] == "base.safetensors"
    assert metadata["loras"][0]["filename"] == "cinematic-detail.safetensors"
    assert metadata["output"]["image"].replace("\\", "/") == "outputs/images/scene.png"


def test_run_internal_still_scene_uses_final_image_size_in_metadata(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)

    model_dir = tmp_path / "internal-model"
    model_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(studio_app.models, "installed_path", lambda model_id: model_dir)
    monkeypatch.setattr(
        studio_app,
        "render_internal_still_image",
        lambda **kwargs: {
            "image": Image.new("RGB", (160, 96), (32, 64, 96)),
            "device": "cpu",
            "requested_device": "cpu",
            "family": "sdxl",
            "backend": "diffusers",
            "seed": 55,
        },
    )

    out_path = store.project_dir(proj.id) / "outputs" / "images" / "internal_scene.png"
    result = studio_app._run_internal_still_scene(
        proj.id,
        "job-internal",
        {
            "variant_index": 0,
            "scene_index": 0,
            "model_id": "hf_sdxl_internal",
            "family": "sdxl",
            "prompt": "A widescreen skyline",
            "negative_prompt": "blurry",
            "seed": 55,
            "width": 96,
            "height": 64,
            "steps": 20,
            "cfg": 7.0,
            "sampler": "euler",
            "workflow_family": "txt2img",
            "hires_fix": {"enabled": True, "scale": 1.5, "denoise": 0.3},
            "out_path": str(out_path),
        },
    )

    metadata = json.loads(Path(result["metadata_path"]).read_text(encoding="utf-8"))
    assert metadata["width"] == 160
    assert metadata["height"] == 96
    assert metadata["hires_fix"]["scale"] == 1.5


def test_run_internal_still_scene_uses_installed_model_path_and_resolves_internal_controlnet_units(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)

    refs_dir = store.project_dir(proj.id) / "assets" / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    source_path = refs_dir / "source.png"
    Image.new("RGB", (64, 64), (24, 48, 72)).save(source_path)

    model_dir = tmp_path / "internal-model"
    model_dir.mkdir(parents=True, exist_ok=True)
    controlnet_dir = tmp_path / "internal-controlnet"
    controlnet_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(studio_app.models, "installed_path", lambda model_id: model_dir if model_id == "hf_sdxl_internal" else None)
    monkeypatch.setattr(
        studio_app.models,
        "resolve_internal_asset",
        lambda ref, **kwargs: {
            "id": ref,
            "name": "SDXL Canny Internal",
            "path": str(controlnet_dir),
            "family": "sdxl",
        },
    )
    monkeypatch.setattr(studio_app, "_prepare_condition_image", lambda project_id, path, mode: path)

    captured: dict[str, object] = {}

    def _fake_render_internal_still_image(**kwargs):
        captured.update(kwargs)
        return {
            "image": Image.new("RGB", (96, 64), (12, 24, 36)),
            "device": "cpu",
            "requested_device": "cpu",
            "family": "sdxl",
            "backend": "diffusers",
            "seed": 303,
        }

    monkeypatch.setattr(studio_app, "render_internal_still_image", _fake_render_internal_still_image)

    out_path = store.project_dir(proj.id) / "outputs" / "images" / "internal_controlnet.png"
    studio_app._run_internal_still_scene(
        proj.id,
        "job-controlnet",
        {
            "variant_index": 0,
            "scene_index": 0,
            "model_id": "hf_sdxl_internal",
            "family": "sdxl",
            "workflow_family": "controlnet",
            "prompt": "Structured architecture study",
            "negative_prompt": "blurry",
            "seed": 303,
            "width": 96,
            "height": 64,
            "steps": 12,
            "cfg": 6.0,
            "sampler": "euler",
            "controlnet_units": [
                {
                    "controlnet_name": "hf_sdxl_controlnet_canny_internal",
                    "reference_asset": "assets/refs/source.png",
                    "conditioning_mode": "edge",
                    "strength": 0.7,
                    "start_percent": 0.0,
                    "end_percent": 1.0,
                }
            ],
            "out_path": str(out_path),
        },
    )

    assert captured["model_dir"] == model_dir
    assert captured["workflow_family"] == "controlnet"
    units = captured["controlnet_units"]
    assert isinstance(units, list) and len(units) == 1
    unit = units[0]
    assert unit["path"] == str(controlnet_dir)
    assert unit["family"] == "sdxl"
    assert unit["reference_path"] == str(source_path)


def test_prepare_outpaint_assets_generates_canvas_and_mask(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)

    refs_dir = store.project_dir(proj.id) / "assets" / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    src = refs_dir / "source.png"
    Image.new("RGB", (64, 32), (20, 40, 60)).save(src)

    prepared = studio_app._prepare_still_scene_assets(
        proj.id,
        {
            "source_asset": "assets/refs/source.png",
            "outpaint": {"top_px": 10, "right_px": 5, "bottom_px": 0, "left_px": 3},
            "width": 64,
            "height": 32,
        },
        "outpaint",
    )

    assert prepared["mask_source"] == "generated_outpaint"
    assert prepared["source_path"] is not None
    assert prepared["mask_path"] is not None

    with Image.open(prepared["source_path"]) as expanded:
        assert expanded.size == (72, 42)
    with Image.open(prepared["mask_path"]) as mask:
        assert mask.size == (72, 42)


def test_prepare_outpaint_assets_prefers_explicit_mask_override(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)

    refs_dir = store.project_dir(proj.id) / "assets" / "refs"
    masks_dir = store.project_dir(proj.id) / "assets" / "masks"
    refs_dir.mkdir(parents=True, exist_ok=True)
    masks_dir.mkdir(parents=True, exist_ok=True)

    Image.new("RGB", (64, 32), (20, 40, 60)).save(refs_dir / "source.png")
    Image.new("L", (64, 32), 255).save(masks_dir / "override.png")

    prepared = studio_app._prepare_still_scene_assets(
        proj.id,
        {
            "source_asset": "assets/refs/source.png",
            "inpaint_mask": "assets/masks/override.png",
            "outpaint": {"top_px": 10, "right_px": 5, "bottom_px": 0, "left_px": 3},
            "width": 64,
            "height": 32,
        },
        "outpaint",
    )

    assert prepared["mask_source"] == "explicit_mask_with_margins"
    with Image.open(prepared["source_path"]) as expanded:
        assert expanded.size == (72, 42)
    with Image.open(prepared["mask_path"]) as mask:
        assert mask.size == (72, 42)


def test_export_comfyui_workflows_writes_multi_controlnet_refs(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)

    proj.meta["last_plan"] = {
        "variants": [
            {
                "name": "Variant 1",
                "scenes": [{"start_s": 0, "end_s": 8, "prompt": "neon skyline"}],
            }
        ]
    }
    store.save(proj)

    refs_dir = store.project_dir(proj.id) / "assets" / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), (255, 0, 0)).save(refs_dir / "source.png")
    Image.new("RGB", (64, 64), (0, 255, 0)).save(refs_dir / "depth.png")

    monkeypatch.setattr(
        studio_app,
        "_resolve_still_scene_selection",
        lambda **kwargs: {
            "checkpoint": "base.safetensors",
            "workflow_family": "controlnet",
            "controlnet_name": None,
            "conditioning_mode": "raw",
            "engine": "comfyui",
            "family": "sdxl",
        },
    )
    monkeypatch.setattr(
        studio_app,
        "_normalize_controlnet_units",
        lambda raw_units, **kwargs: [
            {
                "controlnet_name": "controlnet-canny.safetensors",
                "reference_asset": "assets/refs/source.png",
                "conditioning_mode": "edge",
                "strength": 0.8,
                "start_percent": 0.0,
                "end_percent": 1.0,
            },
            {
                "controlnet_name": "controlnet-depth.safetensors",
                "reference_asset": "assets/refs/depth.png",
                "conditioning_mode": "raw",
                "strength": 0.65,
                "start_percent": 0.1,
                "end_percent": 0.9,
            },
        ],
    )
    monkeypatch.setattr(studio_app, "_prepare_condition_image", lambda project_id, path, mode: path)
    monkeypatch.setattr(studio_app.comfy, "controlnet_workflow", lambda **kwargs: {"workflow": kwargs})

    result = studio_app.export_comfyui_workflows(
        proj.id,
        variant_index=0,
        model_id="hf_sdxl_base_1_0",
        workflow_family="controlnet",
        controlnet_units_json=json.dumps(
            [
                {
                    "model": "hf_sdxl_controlnet_canny",
                    "reference_asset": "assets/refs/source.png",
                    "conditioning_mode": "edge",
                },
                {
                    "model": "hf_sdxl_controlnet_depth",
                    "reference_asset": "assets/refs/depth.png",
                    "conditioning_mode": "raw",
                },
            ]
        ),
    )

    assert result["ok"] is True
    exported = store.project_dir(proj.id) / result["files"][0]
    payload = json.loads(exported.read_text(encoding="utf-8"))
    units = payload["workflow"]["controlnet_units"]
    assert len(units) == 2
    assert units[0]["reference_image"].startswith("refs/")
    assert units[1]["reference_image"].startswith("refs/")


def test_export_comfyui_workflows_rejects_internal_still_models(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)

    proj.meta["last_plan"] = {
        "variants": [
            {
                "name": "Variant 1",
                "scenes": [{"start_s": 0, "end_s": 8, "prompt": "neon skyline"}],
            }
        ]
    }
    store.save(proj)

    monkeypatch.setattr(
        studio_app,
        "_resolve_still_scene_selection",
        lambda **kwargs: {
            "checkpoint": None,
            "workflow_family": "txt2img",
            "controlnet_name": None,
            "conditioning_mode": "raw",
            "engine": "internal",
            "family": "sdxl",
            "model_path": Path(tmp_path / "internal-model"),
        },
    )

    with pytest.raises(studio_app.UserFacingError) as exc:
        studio_app.export_comfyui_workflows(
            proj.id,
            variant_index=0,
            model_id="hf_sdxl_internal",
            workflow_family="txt2img",
        )

    assert exc.value.code == "EXPORT_ENGINE_UNSUPPORTED"


def test_model_manager_requires_complete_internal_snapshots(tmp_path, monkeypatch):
    manager = ModelManager(
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "models",
        external_dir=tmp_path / "external",
        comfyui_url="http://127.0.0.1:8188",
        _legacy_planner_url="",
    )
    entries = {
        "hf_sdxl_internal": {
            "id": "hf_sdxl_internal",
            "name": "SDXL Internal",
            "kind": "diffusers",
            "target": {"engine": "internal", "folder": "diffusers"},
            "family": "sdxl",
        },
        "hf_sdxl_controlnet_canny_internal": {
            "id": "hf_sdxl_controlnet_canny_internal",
            "name": "SDXL ControlNet Canny Internal",
            "kind": "controlnet",
            "target": {"engine": "internal", "folder": "controlnet"},
            "family": "sdxl",
        },
    }
    monkeypatch.setattr(manager, "_find_entry", lambda model_id: entries.get(model_id))

    model_dir = manager._internal_models_dir("diffusers") / "hf_sdxl_internal"
    model_dir.mkdir(parents=True, exist_ok=True)
    assert manager.installed_path("hf_sdxl_internal") is None
    (model_dir / "model_index.json").write_text(
        json.dumps(
            {
                "_class_name": "StableDiffusionXLPipeline",
                "scheduler": ["diffusers", "EulerDiscreteScheduler"],
                "tokenizer": ["transformers", "CLIPTokenizer"],
                "tokenizer_2": ["transformers", "CLIPTokenizer"],
                "text_encoder": ["transformers", "CLIPTextModel"],
                "text_encoder_2": ["transformers", "CLIPTextModelWithProjection"],
                "unet": ["diffusers", "UNet2DConditionModel"],
                "vae": ["diffusers", "AutoencoderKL"],
            }
        ),
        encoding="utf-8",
    )
    assert manager.installed_path("hf_sdxl_internal") is None
    assert manager.internal_asset_issue("hf_sdxl_internal") == "incomplete"
    for component, filename in (
        ("text_encoder", "model.safetensors"),
        ("text_encoder_2", "model.safetensors"),
        ("unet", "model.onnx"),
        ("vae", "diffusion_pytorch_model.safetensors"),
    ):
        component_dir = model_dir / component
        component_dir.mkdir(parents=True, exist_ok=True)
        (component_dir / filename).write_text("weights", encoding="utf-8")
    assert manager.installed_path("hf_sdxl_internal") == model_dir
    assert manager.internal_asset_issue("hf_sdxl_internal") is None

    controlnet_dir = manager._internal_models_dir("controlnet") / "hf_sdxl_controlnet_canny_internal"
    controlnet_dir.mkdir(parents=True, exist_ok=True)
    assert manager.installed_path("hf_sdxl_controlnet_canny_internal") is None

    (controlnet_dir / "config.json").write_text("{}", encoding="utf-8")
    assert manager.installed_path("hf_sdxl_controlnet_canny_internal") is None
    with pytest.raises(studio_app.UserFacingError) as exc:
        manager.resolve_internal_asset(
            "hf_sdxl_controlnet_canny_internal",
            folder="controlnet",
            allowed_kinds={"controlnet"},
        )
    assert "not installed" in str(exc.value).lower()

    (controlnet_dir / "diffusion_pytorch_model.safetensors").write_text("weights", encoding="utf-8")
    assert manager.installed_path("hf_sdxl_controlnet_canny_internal") == controlnet_dir
    resolved = manager.resolve_internal_asset(
        "hf_sdxl_controlnet_canny_internal",
        folder="controlnet",
        allowed_kinds={"controlnet"},
    )
    assert resolved["path"] == str(controlnet_dir)
    assert resolved["family"] == "sdxl"

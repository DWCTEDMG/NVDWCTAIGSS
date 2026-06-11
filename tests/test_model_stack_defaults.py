from __future__ import annotations

import asyncio
from pathlib import Path

from edmg_studio_backend.services.model_catalog import built_in_catalog, built_in_packs
from enhanced_deforum_music_generator.api import models as hf_video_api


def test_model_catalog_defaults_and_pack_references() -> None:
    catalog = {entry["id"]: entry for entry in built_in_catalog()}

    assert catalog["nvidia_nim_nemotron_super"]["source"] == "nvidia_nim"
    assert catalog["nvidia_nim_nemotron_super"]["recommended"] == "default"
    assert catalog["nvidia_nim_nemotron_super"]["installable"] is False
    assert catalog["hf_sdxl_base_1_0"]["recommended"] == "default"
    assert catalog["hf_sd35_large_turbo_ckpt"]["recommended"] == "advanced"

    packs = {pack["id"]: pack for pack in built_in_packs()}
    for pack in packs.values():
        for model_id in pack["models"]:
            assert model_id in catalog

    assert packs["basic"]["models"] == []
    assert packs["creator"]["models"] == []
    assert "hf_sd35_controlnet_depth" in packs["nvidia_sd35"]["models"]


def test_hf_video_catalog_api_preserves_primary_and_fallback_order(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(hf_video_api, "_models_root", lambda: tmp_path)

    payload = asyncio.run(hf_video_api.catalog())
    names = [item["name"] for item in payload["models"]]

    assert names[:2] == ["wan2.2-ti2v-5b", "svd-xt-img2vid"]
    assert payload["models"][0]["repo_id"] == "Wan-AI/Wan2.2-TI2V-5B-Diffusers"
    assert payload["models"][1]["repo_id"] == "stabilityai/stable-video-diffusion-img2vid-xt"

from __future__ import annotations

from edmg_studio_backend import app as backend_app


def test_resolve_comfy_still_selection_handles_missing_catalog_entry(monkeypatch):
    monkeypatch.setattr(
        backend_app,
        "_resolve_comfy_checkpoint_name",
        lambda preferred, allow_auto_selection: ("v1-5-pruned-emaonly.safetensors", "sd_xl_base_1.0.safetensors"),
    )

    selection = backend_app._resolve_comfy_still_selection(
        model_id=None,
        checkpoint=None,
        workflow_family="auto",
        controlnet_model=None,
        reference_asset=None,
        conditioning_mode="raw",
    )

    assert selection["checkpoint"] == "v1-5-pruned-emaonly.safetensors"
    assert selection["workflow_family"] == "txt2img"
    assert selection["controlnet_name"] is None


def test_resolve_comfy_motion_selection_handles_missing_catalog_entry(monkeypatch):
    monkeypatch.setattr(
        backend_app,
        "_resolve_comfy_checkpoint_name",
        lambda preferred, allow_auto_selection: ("v1-5-pruned-emaonly.safetensors", "sd_xl_base_1.0.safetensors"),
    )

    selection = backend_app._resolve_comfy_motion_selection(
        model_id=None,
        checkpoint=None,
        svd_model_id=None,
        svd_checkpoint=None,
    )

    assert selection["checkpoint"] == "v1-5-pruned-emaonly.safetensors"
    assert selection["svd_checkpoint"] == "svd_xt.safetensors"

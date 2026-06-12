# Studio SD Review Slices

This note separates the mixed review surface introduced in commit `5dffa28` so follow-up PRs can stay narrow without rewriting the already-pushed branch history.

## Slice A: Studio-native diffusion work

These files belong together as the Stable Diffusion feature track:

- `studio/edmg-studio/python_backend/edmg_studio_backend/app.py`
- `studio/edmg-studio/python_backend/edmg_studio_backend/schemas.py`
- `studio/edmg-studio/python_backend/edmg_studio_backend/services/internal_video.py`
- `studio/edmg-studio/python_backend/edmg_studio_backend/services/model_manager.py`
- `studio/edmg-studio/python_backend/edmg_studio_backend/services/model_catalog.py`
- `studio/edmg-studio/src/pages/Render.tsx`
- `studio/edmg-studio/src/test/Render.test.tsx`
- `tests/test_studio_sd_feature_slice.py`
- `tests/test_model_stack_defaults.py`
- `tests/test_studio_live_still_smoke.py`

If a narrower SD-only branch is needed later, replay the diffusion hunks from `5dffa28` plus `bbf9124` on top of `51ac012`.

## Slice B: Model stack, provider, and setup refresh

These files are not required for the still-image diffusion feature path and should review separately:

- `README.md`
- `docs/AI_PROVIDERS.md`
- `docs/HF_VIDEO_MODELS.md`
- `run_me.sh`
- `studio/edmg-studio/.env.template`
- `studio/edmg-studio/README.md`
- `studio/edmg-studio/main.mjs`
- `studio/edmg-studio/python_backend/README.md`
- `studio/edmg-studio/python_backend/edmg_ai_service/config.py`
- `studio/edmg-studio/python_backend/enhanced_deforum_music_generator/api/models.py`
- `studio/edmg-studio/python_backend/enhanced_deforum_music_generator/cli/hf_models.py`
- `studio/edmg-studio/python_backend/enhanced_deforum_music_generator/presets/hf_video_model_catalog.json`
- `studio/edmg-studio/scripts/packaged-nvidia-setup-proof.mjs`
- `studio/edmg-studio/src/pages/Settings.tsx`
- `studio/edmg-studio/tools/edmgctl/internal/support/support_test.go`

## Safe branch strategy

Do not rewrite `codex/Unified` if it is already under review.

Use this instead:

1. Create a clean diffusion review branch from `51ac012`.
2. Replay only Slice A changes.
3. Create a separate setup/model-stack branch from the same base.
4. Replay only Slice B changes.

That preserves the shipped branch while giving reviewers two focused surfaces.

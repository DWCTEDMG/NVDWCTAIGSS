# Workshop Forge

Workshop Forge is an experimental, opt-in AI builder workbench for EDMG NVIDIA Workshop.

It is currently read-only. It does not replace:
- Electron shell
- React frontend
- FastAPI backend
- Setup Wizard
- internal renderer
- ComfyUI integration
- model manager
- render queue
- packaging scripts

Enable in development with:

```bash
VITE_EDMG_ENABLE_STUDIO_FORGE=1
```

Current features:
- runtime status overview
- runtime-aware recommendations
- template preview
- workflow recipe preview
- Unreal bridge preview cards
- backend Unreal bridge contract preview via `/v1/projects/{project_id}/unreal/preview`
- backend Unreal bridge export bundle via `POST /v1/projects/{project_id}/export/unreal`
- backend Unreal bridge import-plan generation via `POST /v1/projects/{project_id}/unreal/import-plan`
- backend Unreal bridge return import via `POST /v1/projects/{project_id}/import/unreal`
- one-click Unreal bundle export/import-plan/import actions in Outputs, with returned media registered back into canonical project outputs
- Unreal-side importer script at `tools/unreal/import_unreal_bridge_bundle.py` for creating a Level Sequence from the exported bundle inside Unreal Editor
- validation checklist

## Current Unreal bridge status

Finished now:

- Studio can preview, export, build an Unreal import plan, and import returned renders back into canonical project outputs.
- The Unreal-side importer script exists at `tools/unreal/import_unreal_bridge_bundle.py`.
- The backend/service contract exists under `python_backend/edmg_studio_backend/services/unreal_bridge_consumer.py`.
- The Outputs page can drive the controlled bundle export/import-plan/import flow.

Not finished:

- No verified in-editor Unreal smoke test on this machine.
- No packaged Unreal plugin or module yet.
- No live OSC, WebSocket, or Remote Control execution path yet.
- No one-click "launch Unreal render job" path from Studio yet.
- No deeper Sequencer scene build beyond the first importer pass with cameras, cuts, markers, and plan metadata.

Honest state:

- Studio-side bridge: usable
- Unreal-side runtime integration: partial
- Full Unreal support: not finished

Workshop Forge v1 does not generate code, run workflows, install runtimes, pull models, mutate project data, or execute shell commands from the frontend.

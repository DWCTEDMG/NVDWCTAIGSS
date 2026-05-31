# Audio Reactive Stage Sample

This sample defines the first project contract for the Omniverse Kit app.
It is intentionally small:

- `stage.usda` is the durable OpenUSD scene shell.
- `scene_plan.json` is the normalized AI Director output that the Kit app should
  write into USD metadata or shot prims.

The Kit app can load this stage, show the camera/light layout, read the EDMG
custom metadata, and render RTX preview frames or movie output after applying a
scene plan.

## Intended mapping

- `World.customData.edmg.audio` describes the audio source and tempo.
- `World.customData.edmg.timeline.sections` describes song sections.
- `World/Looks` contains materials that can be animated from beat/energy data.
- `World/PerformanceRig` contains reusable visual-control prims.
- `scene_plan.json` mirrors the existing EDMG planner shape and can be converted
  into USD shot prims or variant sets.


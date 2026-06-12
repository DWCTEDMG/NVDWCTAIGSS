from __future__ import annotations

import argparse
import sys
from pathlib import Path


KIT_ROOT = Path(__file__).resolve().parents[1]
AI_DIRECTOR_EXTENSION = KIT_ROOT / "extensions" / "edmg.ai_director"
if str(AI_DIRECTOR_EXTENSION) not in sys.path:
    sys.path.insert(0, str(AI_DIRECTOR_EXTENSION))

from edmg.ai_director import EdmgBackendClient, load_scene_plan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke the EDMG AI Director backend contract from the Kit workspace.")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--scene-plan",
        type=Path,
        default=KIT_ROOT / "sample_projects" / "audio_reactive_stage" / "scene_plan.json",
    )
    parser.add_argument("--output-usda", type=Path)
    parser.add_argument("--generate", action="store_true", help="Call /v1/nvidia/scene-plan before exporting USDA.")
    parser.add_argument("--title", default="EDMG NVIDIA Workshop Smoke")
    parser.add_argument("--duration-s", type=float, default=60.0)
    parser.add_argument("--bpm", type=float, default=128.0)
    parser.add_argument("--style-prefs", default="RTX stage lighting, OpenUSD-ready camera moves, audio-reactive motion")
    parser.add_argument("--user-notes", default="Build a short NVIDIA Studio scene plan that can be previewed in Kit.")
    parser.add_argument("--max-scenes", type=int, default=4)
    parser.add_argument("--timeout-s", type=float, default=180.0, help="Backend request timeout for local NIM generation.")
    parser.add_argument("--project-id", default="", help="Existing Studio project id for generate-and-persist or fetch-persisted checks.")
    parser.add_argument("--fetch-persisted", action="store_true", help="Fetch the persisted NVIDIA scene plan for --project-id.")
    args = parser.parse_args()

    client = EdmgBackendClient(base_url=args.backend_url, timeout_s=args.timeout_s)
    status = client.nvidia_status()
    nvidia = status.get("nvidia") if isinstance(status.get("nvidia"), dict) else {}
    print(f"[kit-ai-director] Backend: {args.backend_url.rstrip('/')}")
    print(f"[kit-ai-director] NVIDIA mode: {nvidia.get('enabled')}")
    print(f"[kit-ai-director] NVIDIA profile: {nvidia.get('profile')}")

    if args.fetch_persisted:
        if not args.project_id:
            raise RuntimeError("--fetch-persisted requires --project-id")
        response = client.project_scene_plan(args.project_id)
        planner = response.get("planner") if isinstance(response.get("planner"), dict) else {}
        print(f"[kit-ai-director] Persisted project: {response.get('project_id')}")
        print(f"[kit-ai-director] Saved at: {response.get('saved_at') or 'unknown'}")
        print(f"[kit-ai-director] Planner: {planner.get('provider')} {planner.get('model') or ''}".rstrip())
        stage = response.get("usd_stage") if isinstance(response.get("usd_stage"), dict) else {}
        usda_text = str(stage.get("text") or "")
    elif args.generate:
        request_payload = {
            "project_id": args.project_id or "kit-smoke-generated",
            "title": args.title,
            "duration_s": args.duration_s,
            "bpm": args.bpm,
            "style_prefs": args.style_prefs,
            "user_notes": args.user_notes,
            "num_variants": 1,
            "max_scenes": args.max_scenes,
        }
        if args.project_id:
            response = client.generate_project_scene_plan(args.project_id, request_payload)
        else:
            response = client.generate_scene_plan(request_payload)
        planner = response.get("planner") if isinstance(response.get("planner"), dict) else {}
        print(f"[kit-ai-director] Planner: {planner.get('provider')} {planner.get('model') or ''}".rstrip())
        project_status = response.get("project") if isinstance(response.get("project"), dict) else {}
        if project_status:
            print(f"[kit-ai-director] Persisted: {project_status.get('persisted')} {project_status.get('project_id') or project_status.get('reason') or ''}".rstrip())
        stage = response.get("usd_stage") if isinstance(response.get("usd_stage"), dict) else {}
        usda_text = str(stage.get("text") or "")
    else:
        scene_plan = load_scene_plan(args.scene_plan)
        usda_text = client.scene_plan_usda(scene_plan)

    if not usda_text.strip():
        raise RuntimeError("backend returned an empty USDA preview")

    print(f"[kit-ai-director] USDA preview bytes: {len(usda_text.encode('utf-8'))}")

    if args.output_usda:
        args.output_usda.parent.mkdir(parents=True, exist_ok=True)
        args.output_usda.write_text(usda_text, encoding="utf-8", newline="\n")
        print(f"[kit-ai-director] Wrote: {args.output_usda}")

    print("[kit-ai-director] Smoke complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

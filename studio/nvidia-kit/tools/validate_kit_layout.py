from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 backend venv compatibility
    import tomli as tomllib  # type: ignore[no-redef]
from typing import Any


EXPECTED_EXTENSIONS = (
    "edmg.timeline",
    "edmg.ai_director",
    "edmg.usd_schema",
    "edmg.render_queue",
)


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def validate_kit_layout(root: Path) -> list[str]:
    errors: list[str] = []
    app_path = root / "apps" / "edmg.nvidia.studio.kit"
    if not app_path.exists():
        errors.append(f"missing {app_path.relative_to(root)}")
        return errors

    try:
        app_config = _load_toml(app_path)
    except Exception as exc:
        errors.append(f"invalid app kit file: {exc}")
        return errors

    package = app_config.get("package") if isinstance(app_config.get("package"), dict) else {}
    if package.get("title") != "EDMG NVIDIA Workshop":
        errors.append("app package.title must be EDMG NVIDIA Workshop")

    dependencies = app_config.get("dependencies") if isinstance(app_config.get("dependencies"), dict) else {}
    for ext_name in EXPECTED_EXTENSIONS:
        if ext_name not in dependencies:
            errors.append(f"app is missing dependency {ext_name}")

    for ext_name in EXPECTED_EXTENSIONS:
        ext_root = root / "extensions" / ext_name
        config_path = ext_root / "config" / "extension.toml"
        if not config_path.exists():
            errors.append(f"missing extension config for {ext_name}")
            continue
        try:
            ext_config = _load_toml(config_path)
        except Exception as exc:
            errors.append(f"invalid extension config for {ext_name}: {exc}")
            continue
        modules = ext_config.get("python", {}).get("module", [])
        module_names = [item.get("name") for item in modules if isinstance(item, dict)]
        if ext_name not in module_names:
            errors.append(f"extension {ext_name} must expose python module {ext_name}")

        module_root = ext_root / Path(*ext_name.split("."))
        module_path = module_root / "extension.py"
        if not module_path.exists():
            errors.append(f"missing extension module for {ext_name}")
            continue
        for python_path in sorted(module_root.rglob("*.py")):
            try:
                compile(python_path.read_text(encoding="utf-8"), str(python_path), "exec")
            except SyntaxError as exc:
                errors.append(f"invalid extension module for {ext_name}: {exc}")

    sample_stage = root / "sample_projects" / "audio_reactive_stage" / "stage.usda"
    sample_plan = root / "sample_projects" / "audio_reactive_stage" / "scene_plan.json"
    if not sample_stage.exists():
        errors.append("missing sample OpenUSD stage")
    if not sample_plan.exists():
        errors.append("missing sample scene plan")

    tools_root = root / "tools"
    for tool_path in sorted(tools_root.glob("*.py")):
        try:
            compile(tool_path.read_text(encoding="utf-8"), str(tool_path), "exec")
        except SyntaxError as exc:
            errors.append(f"invalid tool script {tool_path.name}: {exc}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the EDMG NVIDIA Workshop Kit layout.")
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    errors = validate_kit_layout(args.root.resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print(f"OK: {args.root.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

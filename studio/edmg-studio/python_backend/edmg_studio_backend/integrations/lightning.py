from __future__ import annotations
import os
from pathlib import Path
from typing import Any

def generate_lightning_bundle(output_dir: str, host: str = "0.0.0.0", port: int = 7863) -> dict[str, Any]:
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    (out / "startup.sh").write_text(
        f"""#!/usr/bin/env bash
set -e
echo "Starting EDMG NVIDIA Workshop backend on {host}:{port}"
python -m edmg_studio_backend serve --host {host} --port {port}
""".strip() + "\n",
        encoding="utf-8",
    )
    (out / "requirements.txt").write_text("edmg-studio-backend\n", encoding="utf-8")
    (out / "README.md").write_text(
        f"""# Lightning bundle

Upload this folder to Lightning (or copy it into a Lightning workspace) and run:
- bash startup.sh

It binds to {host}:{port}.
""".strip() + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(out / "startup.sh", 0o755)
    except Exception:
        pass
    return {"ok": True, "output_dir": str(out)}

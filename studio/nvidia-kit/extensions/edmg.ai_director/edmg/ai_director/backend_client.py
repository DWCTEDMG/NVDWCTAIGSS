from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib import error, request


class BackendClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class EdmgBackendClient:
    base_url: str = "http://127.0.0.1:8000"
    timeout_s: float = 15.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    def _json_request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method.upper(),
        )
        try:
            with request.urlopen(req, timeout=self.timeout_s) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise BackendClientError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise BackendClientError(f"{method} {path} failed: {exc.reason}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BackendClientError(f"{method} {path} returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise BackendClientError(f"{method} {path} returned non-object JSON")
        return data

    def nvidia_status(self) -> dict[str, Any]:
        return self._json_request("GET", "/v1/nvidia/status")

    def nvidia_diagnostics(self) -> dict[str, Any]:
        return self._json_request("GET", "/v1/nvidia/diagnostics")

    def projects(self) -> dict[str, Any]:
        return self._json_request("GET", "/v1/projects")

    def generate_scene_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json_request("POST", "/v1/nvidia/scene-plan", payload)

    def generate_project_scene_plan(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        encoded_project_id = quote(str(project_id), safe="")
        return self._json_request("POST", f"/v1/projects/{encoded_project_id}/nvidia/scene-plan", payload)

    def project_scene_plan(self, project_id: str) -> dict[str, Any]:
        encoded_project_id = quote(str(project_id), safe="")
        return self._json_request("GET", f"/v1/projects/{encoded_project_id}/nvidia/scene-plan")

    def export_project_scene_plan(self, project_id: str) -> dict[str, Any]:
        encoded_project_id = quote(str(project_id), safe="")
        return self._json_request("POST", f"/v1/projects/{encoded_project_id}/nvidia/scene-plan/export", {})

    def scene_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json_request("POST", "/v1/usd/scene-plan", payload)

    def generated_scene_plan_usda(self, payload: dict[str, Any]) -> str:
        response = self.generate_scene_plan(payload)
        stage = response.get("usd_stage")
        if not isinstance(stage, dict) or stage.get("format") != "usda":
            raise BackendClientError("generated scene-plan response did not include a USDA stage")
        text = stage.get("text")
        if not isinstance(text, str) or not text.strip():
            raise BackendClientError("generated scene-plan response included an empty USDA stage")
        return text

    def scene_plan_usda(self, payload: dict[str, Any]) -> str:
        response = self.scene_plan(payload)
        stage = response.get("usd_stage")
        if not isinstance(stage, dict) or stage.get("format") != "usda":
            raise BackendClientError("scene-plan response did not include a USDA stage")
        text = stage.get("text")
        if not isinstance(text, str) or not text.strip():
            raise BackendClientError("scene-plan response included an empty USDA stage")
        return text

    def project_scene_plan_usda(self, project_id: str) -> str:
        response = self.project_scene_plan(project_id)
        stage = response.get("usd_stage")
        if not isinstance(stage, dict) or stage.get("format") != "usda":
            raise BackendClientError("project scene-plan response did not include a USDA stage")
        text = stage.get("text")
        if not isinstance(text, str) or not text.strip():
            raise BackendClientError("project scene-plan response included an empty USDA stage")
        return text


def load_scene_plan(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BackendClientError("scene plan root must be an object")
    return payload

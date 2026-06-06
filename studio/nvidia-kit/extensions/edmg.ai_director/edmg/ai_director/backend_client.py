from __future__ import annotations

import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib import error, request
from uuid import uuid4


class BackendClientError(RuntimeError):
    pass


def _audio_search_roots() -> list[Path]:
    roots: list[Path] = []
    configured = os.getenv("EDMG_KIT_AUDIO_SEARCH_DIRS", "")
    for item in configured.split(os.pathsep):
        if item.strip():
            roots.append(Path(item.strip()).expanduser())

    user_profile = os.getenv("USERPROFILE", "")
    if user_profile:
        roots.append(Path(user_profile) / "Downloads")
    roots.extend(
        [
            Path.home() / "Downloads",
            Path("D:/Downloads"),
            Path("C:/Downloads"),
            Path.cwd(),
        ]
    )

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).casefold()
        if key not in seen:
            unique.append(root)
            seen.add(key)
    return unique


def resolve_audio_upload_path(audio_path: str | Path) -> Path:
    raw = Path(audio_path).expanduser()
    if raw.exists() and raw.is_file():
        return raw
    if raw.is_absolute():
        return raw

    for root in _audio_search_roots():
        candidate = root / raw
        if candidate.exists() and candidate.is_file():
            return candidate
    return raw


@dataclass(frozen=True)
class EdmgBackendClient:
    base_url: str = "http://127.0.0.1:8000"
    timeout_s: float = 180.0

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
        except TimeoutError as exc:
            raise BackendClientError(f"{method} {path} timed out after {self.timeout_s:g}s") from exc
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

    def nvidia_services(self) -> dict[str, Any]:
        return self._json_request("GET", "/v1/nvidia/services")

    def nvidia_service_status(self, service_id: str) -> dict[str, Any]:
        encoded_service_id = quote(str(service_id), safe="")
        return self._json_request("GET", f"/v1/nvidia/services/{encoded_service_id}/status")

    def projects(self) -> dict[str, Any]:
        return self._json_request("GET", "/v1/projects")

    def create_project(self, name: str) -> dict[str, Any]:
        return self._json_request("POST", "/v1/projects", {"name": str(name or "").strip()})

    def project(self, project_id: str) -> dict[str, Any]:
        encoded_project_id = quote(str(project_id), safe="")
        return self._json_request("GET", f"/v1/projects/{encoded_project_id}")

    def upload_project_audio(self, project_id: str, audio_path: str | Path) -> dict[str, Any]:
        path = resolve_audio_upload_path(audio_path)
        if not path.exists() or not path.is_file():
            raise BackendClientError(f"audio file does not exist: {path}")

        encoded_project_id = quote(str(project_id), safe="")
        boundary = f"----edmg-kit-{uuid4().hex}"
        filename = path.name.replace('"', "_")
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        prefix = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
        suffix = f"\r\n--{boundary}--\r\n".encode("utf-8")
        body = prefix + path.read_bytes() + suffix
        req = request.Request(
            f"{self.base_url}/v1/projects/{encoded_project_id}/assets/audio",
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_s) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise BackendClientError(f"POST /v1/projects/{encoded_project_id}/assets/audio failed with HTTP {exc.code}: {detail}") from exc
        except TimeoutError as exc:
            raise BackendClientError(
                f"POST /v1/projects/{encoded_project_id}/assets/audio timed out after {self.timeout_s:g}s"
            ) from exc
        except error.URLError as exc:
            raise BackendClientError(f"POST /v1/projects/{encoded_project_id}/assets/audio failed: {exc.reason}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BackendClientError("POST /v1/projects/{project_id}/assets/audio returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise BackendClientError("POST /v1/projects/{project_id}/assets/audio returned non-object JSON")
        return data

    def analyze_project_audio(self, project_id: str) -> dict[str, Any]:
        encoded_project_id = quote(str(project_id), safe="")
        return self._json_request("POST", f"/v1/projects/{encoded_project_id}/analyze_audio", {})

    def transcribe_project_audio_riva(self, project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        encoded_project_id = quote(str(project_id), safe="")
        return self._json_request("POST", f"/v1/projects/{encoded_project_id}/nvidia/riva/transcribe", payload or {})

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

    def create_project_render_job(self, project_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        encoded_project_id = quote(str(project_id), safe="")
        return self._json_request("POST", f"/v1/projects/{encoded_project_id}/nvidia/render-job", payload or {})

    def audio2face_generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json_request("POST", "/v1/nvidia/audio2face/generate", payload)

    def ace_generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json_request("POST", "/v1/nvidia/ace/generate", payload)

    def cosmos_infer(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json_request("POST", "/v1/nvidia/cosmos/infer", payload)

    def triton_infer(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json_request("POST", "/v1/nvidia/triton/infer", payload)

    def invoke_typed_nvidia_service(self, service_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = str(service_id or "").strip().lower().replace("_", "").replace("-", "")
        if normalized in {"audio2face", "a2f"}:
            return self.audio2face_generate(payload)
        if normalized == "ace":
            return self.ace_generate(payload)
        if normalized == "cosmos":
            return self.cosmos_infer(payload)
        if normalized == "triton":
            return self.triton_infer(payload)
        raise BackendClientError(f"unsupported typed NVIDIA service `{service_id}`")

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

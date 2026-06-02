from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from .nvidia_profile import nvidia_profile_status


HTTP_SERVICE_IDS = {"nim", "nemo", "triton", "audio2face", "ace", "cosmos", "omniverse"}
SENSITIVE_QUERY_MARKERS = ("key", "token", "secret", "auth", "password")
DEFAULT_TRITON_SHOT_FEATURES_MODEL = "edmg_shot_features"


class NvidiaOptionalServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def normalize_nvidia_service_id(service_id: str) -> str:
    normalized = str(service_id or "").strip().lower().replace("-", "_")
    aliases = {
        "nim_llm": "nim",
        "nvidia_nim": "nim",
        "nvidia_nemo": "nemo",
        "nvidia_triton": "triton",
        "audio_2_face": "audio2face",
        "a2f": "audio2face",
        "nvidia_audio2face": "audio2face",
        "nvidia_a2f": "audio2face",
        "nvidia_ace": "ace",
        "nvidia_cosmos": "cosmos",
        "nvidia_omniverse": "omniverse",
    }
    return aliases.get(normalized, normalized)


def _env(name: str) -> str:
    return str(os.getenv(name, "") or "").strip()


def _reserved_payload_keys() -> set[str]:
    return {"path", "endpoint", "method", "timeout_s"}


def _invoke_payload(body: dict[str, Any]) -> Any:
    if "payload" in body:
        return body.get("payload")
    if "body" in body:
        return body.get("body")
    return {key: value for key, value in body.items() if key not in _reserved_payload_keys()}


def _configured_path(body: dict[str, Any], env_name: str, fallback: str = "") -> str:
    return str(body.get("path") or body.get("endpoint") or _env(env_name) or fallback).strip()


def _service_profile(service_id: str) -> dict[str, Any]:
    normalized = normalize_nvidia_service_id(service_id)
    if normalized not in HTTP_SERVICE_IDS:
        raise NvidiaOptionalServiceError(f"NVIDIA service '{service_id}' is not an HTTP invoke service.", status_code=404)
    profile = nvidia_profile_status()
    services = profile.get("services", {}) if isinstance(profile.get("services"), dict) else {}
    service = services.get(normalized)
    if not isinstance(service, dict):
        raise NvidiaOptionalServiceError(f"NVIDIA service '{service_id}' was not found.", status_code=404)
    if not service.get("configured"):
        raise NvidiaOptionalServiceError(f"NVIDIA service '{normalized}' is not configured.", status_code=400)
    base_url = str(service.get("base_url") or "").strip()
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise NvidiaOptionalServiceError(f"NVIDIA service '{normalized}' must use an HTTP(S) base URL.", status_code=400)
    return {"service_id": normalized, "base_url": base_url, "model": service.get("model") or ""}


def _service_api_key(service_id: str) -> str:
    env_names = [f"EDMG_NVIDIA_{service_id.upper()}_API_KEY"]
    if service_id == "nim":
        env_names.extend(["EDMG_AI_OPENAI_COMPAT_API_KEY", "NVIDIA_API_KEY"])
    if service_id == "cosmos":
        env_names.append("NGC_API_KEY")
    for name in env_names:
        value = _env(name)
        if value:
            return value
    return ""


def _join_service_url(base_url: str, relative_path: str) -> str:
    raw_path = str(relative_path or "").strip()
    if not raw_path:
        raise NvidiaOptionalServiceError("NVIDIA service invoke path is required.", status_code=400)
    split_path = urlsplit(raw_path)
    if split_path.scheme or split_path.netloc:
        raise NvidiaOptionalServiceError("NVIDIA service invoke path must be relative, not an absolute URL.", status_code=400)
    if not split_path.path.startswith("/"):
        raise NvidiaOptionalServiceError("NVIDIA service invoke path must start with '/'.", status_code=400)
    if any(part == ".." for part in split_path.path.split("/")):
        raise NvidiaOptionalServiceError("NVIDIA service invoke path must not contain '..'.", status_code=400)

    base = base_url.rstrip("/")
    path = split_path.path
    url = f"{base}{path}"
    if split_path.query:
        url = f"{url}?{split_path.query}"
    return url


def _redacted_url(url: str) -> str:
    split_url = urlsplit(url)
    query_pairs = []
    for key, value in parse_qsl(split_url.query, keep_blank_values=True):
        lowered = key.lower()
        if any(marker in lowered for marker in SENSITIVE_QUERY_MARKERS):
            query_pairs.append((key, "***"))
        else:
            query_pairs.append((key, value))
    query = urlencode(query_pairs, doseq=True)
    return urlunsplit((split_url.scheme, split_url.netloc, split_url.path, query, ""))


def _decode_response(raw: bytes, content_type: str = "") -> tuple[Any | None, str]:
    text = raw.decode("utf-8", errors="replace")
    if "json" in content_type.lower() or text.strip().startswith(("{", "[")):
        try:
            return json.loads(text) if text else None, text
        except json.JSONDecodeError:
            return None, text
    return None, text


def invoke_nvidia_service(
    service_id: str,
    path: str,
    *,
    payload: Any | None = None,
    method: str = "POST",
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Invoke a configured optional NVIDIA HTTP service without exposing secrets."""

    service = _service_profile(service_id)
    normalized = str(service["service_id"])
    target_url = _join_service_url(str(service["base_url"]), path)
    http_method = str(method or "POST").strip().upper()
    if http_method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise NvidiaOptionalServiceError("NVIDIA service method must be GET, POST, PUT, PATCH, or DELETE.", status_code=400)

    headers = {"Accept": "application/json"}
    body: bytes | None = None
    if payload is not None and http_method not in {"GET", "DELETE"}:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    api_key = _service_api_key(normalized)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = request.Request(target_url, data=body, headers=headers, method=http_method)
    response_url = _redacted_url(target_url)
    try:
        with request.urlopen(req, timeout=max(1.0, float(timeout_s))) as response:
            raw = response.read(5_000_000)
            parsed, text = _decode_response(raw, response.headers.get("Content-Type", ""))
            result: dict[str, Any] = {
                "ok": 200 <= int(response.status) < 300,
                "service_id": normalized,
                "method": http_method,
                "url": response_url,
                "status_code": int(response.status),
                "model": service.get("model") or "",
            }
            if parsed is not None:
                result["response"] = parsed
            else:
                result["raw_text"] = text[:20000]
            return result
    except error.HTTPError as exc:
        raw = exc.read(1_000_000)
        parsed, text = _decode_response(raw, exc.headers.get("Content-Type", "") if exc.headers else "")
        result = {
            "ok": False,
            "service_id": normalized,
            "method": http_method,
            "url": response_url,
            "status_code": int(exc.code),
            "error": str(exc.reason or exc),
            "model": service.get("model") or "",
        }
        if parsed is not None:
            result["response"] = parsed
        elif text:
            result["raw_text"] = text[:20000]
        return result
    except error.URLError as exc:
        raise NvidiaOptionalServiceError(
            f"NVIDIA service '{normalized}' request failed: {exc.reason}",
            status_code=502,
        ) from exc
    except TimeoutError as exc:
        raise NvidiaOptionalServiceError(f"NVIDIA service '{normalized}' request timed out.", status_code=504) from exc
    except Exception as exc:
        raise NvidiaOptionalServiceError(f"NVIDIA service '{normalized}' request failed: {exc}", status_code=502) from exc


def audio2face_generate(body: dict[str, Any], *, timeout_s: float = 30.0) -> dict[str, Any]:
    """Invoke a configured Audio2Face/Audio2Face gateway generation endpoint."""

    if not isinstance(body, dict):
        raise NvidiaOptionalServiceError("Audio2Face request root must be an object.", status_code=400)
    path = _configured_path(body, "EDMG_NVIDIA_AUDIO2FACE_GENERATE_PATH")
    if not path:
        raise NvidiaOptionalServiceError(
            "Audio2Face gateway path is required; pass `path` or set EDMG_NVIDIA_AUDIO2FACE_GENERATE_PATH.",
            status_code=400,
        )
    return invoke_nvidia_service("audio2face", path, payload=_invoke_payload(body), method="POST", timeout_s=timeout_s)


def ace_generate(body: dict[str, Any], *, timeout_s: float = 30.0) -> dict[str, Any]:
    """Invoke a configured ACE gateway generation endpoint."""

    if not isinstance(body, dict):
        raise NvidiaOptionalServiceError("ACE request root must be an object.", status_code=400)
    path = _configured_path(body, "EDMG_NVIDIA_ACE_GENERATE_PATH")
    if not path:
        raise NvidiaOptionalServiceError(
            "ACE gateway path is required; pass `path` or set EDMG_NVIDIA_ACE_GENERATE_PATH.",
            status_code=400,
        )
    return invoke_nvidia_service("ace", path, payload=_invoke_payload(body), method="POST", timeout_s=timeout_s)


def cosmos_infer(body: dict[str, Any], *, timeout_s: float = 120.0) -> dict[str, Any]:
    """Invoke Cosmos NIM /v1/infer with a normalized text/image/video-world payload."""

    if not isinstance(body, dict):
        raise NvidiaOptionalServiceError("Cosmos inference request root must be an object.", status_code=400)
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise NvidiaOptionalServiceError("Cosmos `prompt` is required.", status_code=400)

    payload: dict[str, Any] = {"prompt": prompt}
    for key in ("negative_prompt", "image", "video", "prompt_upsampling", "seed", "guidance_scale", "steps", "resolution"):
        if key in body and body.get(key) not in (None, ""):
            payload[key] = body.get(key)
    if isinstance(body.get("video_params"), dict):
        payload["video_params"] = body["video_params"]
    for key in ("edge", "depth", "vis", "seg"):
        if isinstance(body.get(key), dict):
            payload[key] = body[key]

    path = _configured_path(body, "EDMG_NVIDIA_COSMOS_INFER_PATH", "/v1/infer")
    return invoke_nvidia_service("cosmos", path, payload=payload, method="POST", timeout_s=timeout_s)


def triton_infer(
    *,
    model: str = "",
    inputs: list[Any] | None = None,
    outputs: list[Any] | None = None,
    parameters: dict[str, Any] | None = None,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    service = _service_profile("triton")
    model_name = str(model or service.get("model") or "").strip()
    if not model_name:
        raise NvidiaOptionalServiceError(
            "Triton model is required; pass `model` or set EDMG_NVIDIA_TRITON_MODEL.",
            status_code=400,
        )
    if not isinstance(inputs, list):
        raise NvidiaOptionalServiceError("Triton `inputs` must be a list.", status_code=400)

    body: dict[str, Any] = {"inputs": inputs}
    if outputs is not None:
        if not isinstance(outputs, list):
            raise NvidiaOptionalServiceError("Triton `outputs` must be a list when provided.", status_code=400)
        body["outputs"] = outputs
    if parameters is not None:
        if not isinstance(parameters, dict):
            raise NvidiaOptionalServiceError("Triton `parameters` must be an object when provided.", status_code=400)
        body["parameters"] = parameters

    safe_model = quote(model_name, safe="")
    return invoke_nvidia_service(
        "triton",
        f"/v2/models/{safe_model}/infer",
        payload=body,
        method="POST",
        timeout_s=timeout_s,
    )


def triton_shot_features(body: dict[str, Any], *, timeout_s: float = 30.0) -> dict[str, Any]:
    """Invoke the EDMG shot-feature Triton model with a scene-plan JSON payload."""

    if not isinstance(body, dict):
        raise NvidiaOptionalServiceError("Triton shot-feature request root must be an object.", status_code=400)
    if "scene_plan" in body:
        scene_plan = body.get("scene_plan")
        model = str(body.get("model") or _env("EDMG_NVIDIA_TRITON_SHOT_MODEL") or DEFAULT_TRITON_SHOT_FEATURES_MODEL).strip()
        parameters = body.get("parameters")
    else:
        scene_plan = body
        model = str(_env("EDMG_NVIDIA_TRITON_SHOT_MODEL") or DEFAULT_TRITON_SHOT_FEATURES_MODEL).strip()
        parameters = None
    if not isinstance(scene_plan, dict):
        raise NvidiaOptionalServiceError("Triton `scene_plan` must be an object.", status_code=400)
    if parameters is not None and not isinstance(parameters, dict):
        raise NvidiaOptionalServiceError("Triton `parameters` must be an object when provided.", status_code=400)

    scene_plan_json = json.dumps(scene_plan, ensure_ascii=False, separators=(",", ":"))
    return triton_infer(
        model=model,
        inputs=[
            {
                "name": "scene_plan",
                "shape": [1],
                "datatype": "BYTES",
                "data": [scene_plan_json],
            }
        ],
        outputs=[
            {"name": "features_json"},
            {"name": "energy_score"},
            {"name": "scene_count"},
        ],
        parameters=parameters,
        timeout_s=timeout_s,
    )

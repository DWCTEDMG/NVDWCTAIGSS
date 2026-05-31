from __future__ import annotations

import json
import os
import socket
import subprocess
from typing import Any
from urllib import error, request
from urllib.parse import urlparse

from .nvidia_profile import nvidia_profile_status


PLACEHOLDER_MARKERS = (
    "your-",
    "set-edmg-",
    "example.com",
    "set-",
)


def _run_command(args: list[str], timeout_s: float = 3.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        return {"available": False, "ok": False, "error": f"{args[0]} not found"}
    except subprocess.TimeoutExpired:
        return {"available": True, "ok": False, "error": f"{args[0]} timed out"}
    except Exception as exc:
        return {"available": True, "ok": False, "error": str(exc)}

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    return {
        "available": True,
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": stdout[:2000],
        "stderr": stderr[:1000],
    }


def _gpu_status() -> dict[str, Any]:
    result = _run_command(
        ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
        timeout_s=5.0,
    )
    gpus: list[dict[str, str]] = []
    if result.get("ok"):
        for line in str(result.get("stdout") or "").splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) >= 3:
                gpus.append({"name": parts[0], "driver_version": parts[1], "memory_total": parts[2]})
    return {
        "available": result.get("available", False),
        "ok": result.get("ok", False),
        "gpus": gpus,
        "error": result.get("error") or result.get("stderr") or "",
    }


def _docker_status() -> dict[str, Any]:
    version = _run_command(["docker", "--version"], timeout_s=5.0)
    runtime_probe = _run_command(
        ["docker", "info", "--format", '{{if index .Runtimes "nvidia"}}true{{else}}false{{end}}'],
        timeout_s=8.0,
    )
    has_nvidia_runtime = str(runtime_probe.get("stdout") or "").strip().lower() == "true"
    return {
        "available": version.get("available", False),
        "ok": bool(version.get("ok") and runtime_probe.get("ok")),
        "version": version.get("stdout") or "",
        "nvidia_runtime": has_nvidia_runtime,
        "error": version.get("error") or runtime_probe.get("error") or version.get("stderr") or runtime_probe.get("stderr") or "",
    }


def _nim_models_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/models"
    return f"{base}/v1/models"


def _looks_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _http_probe(url: str, *, api_key: str | None = None, timeout_s: float = 3.0) -> dict[str, Any]:
    if not url:
        return {"configured": False, "reachable": False, "skipped": True}
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"configured": True, "reachable": False, "error": "URL is not HTTP(S)"}

    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=timeout_s) as response:
            raw = response.read(200000).decode("utf-8", errors="replace")
            payload: Any = None
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = None
            out: dict[str, Any] = {
                "configured": True,
                "reachable": True,
                "status_code": response.status,
            }
            if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                out["model_count"] = len(payload["data"])
            return out
    except error.HTTPError as exc:
        return {"configured": True, "reachable": False, "status_code": exc.code, "error": exc.reason}
    except error.URLError as exc:
        return {"configured": True, "reachable": False, "error": str(exc.reason)}
    except Exception as exc:
        return {"configured": True, "reachable": False, "error": str(exc)}


def _tcp_probe(endpoint: str, *, timeout_s: float = 3.0) -> dict[str, Any]:
    if not endpoint:
        return {"configured": False, "reachable": False, "skipped": True}
    raw = endpoint.strip()
    if "://" in raw:
        parsed = urlparse(raw)
        host = parsed.hostname or ""
        port = parsed.port
    else:
        host, sep, port_text = raw.rpartition(":")
        port = int(port_text) if sep and port_text.isdigit() else None
    if not host or port is None:
        return {"configured": True, "reachable": False, "error": "Endpoint must include host:port"}
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_s):
            return {"configured": True, "reachable": True, "host": host, "port": int(port)}
    except Exception as exc:
        return {"configured": True, "reachable": False, "host": host, "port": int(port), "error": str(exc)}


def _check(
    check_id: str,
    label: str,
    ok: bool,
    *,
    severity: str = "required",
    detail: str = "",
    fix: str = "",
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "ok": bool(ok),
        "severity": severity,
        "detail": detail,
        "fix": fix,
    }


def _readiness(profile: dict[str, Any], gpu: dict[str, Any], docker: dict[str, Any], services: dict[str, Any]) -> dict[str, Any]:
    nim = services.get("nim", {})
    nim_probe = nim.get("probe", {})
    nim_base_url = str(nim.get("base_url") or "")
    nim_model = str(nim.get("model") or "")
    ngc_ready = bool(profile.get("credentials", {}).get("ngc_api_key_configured"))
    profile_enabled = bool(profile.get("enabled"))
    nim_configured = bool(nim.get("configured")) and not _looks_placeholder(nim_base_url) and not _looks_placeholder(nim_model)

    checks = [
        _check(
            "nvidia_mode",
            "NVIDIA profile enabled",
            profile_enabled,
            detail=f"profile {profile.get('profile') or 'omniverse'}",
            fix="Set EDMG_NVIDIA_MODE=1 in deployment/nvidia/.env.local or the backend environment.",
        ),
        _check(
            "gpu_visible",
            "NVIDIA GPU visible to host",
            bool(gpu.get("ok")),
            detail=", ".join(g.get("name", "GPU") for g in gpu.get("gpus", [])[:2]) or str(gpu.get("error") or ""),
            fix="Install or update the NVIDIA driver, then confirm `nvidia-smi` works in PowerShell.",
        ),
        _check(
            "docker_available",
            "Docker engine available",
            bool(docker.get("ok")),
            detail=str(docker.get("version") or docker.get("error") or ""),
            fix="Start Docker Desktop and wait until `docker info` succeeds.",
        ),
        _check(
            "docker_nvidia_runtime",
            "Docker NVIDIA runtime available",
            bool(docker.get("nvidia_runtime")),
            severity="required_for_local_services",
            fix=(
                "Enable Docker Desktop WSL2 integration and install NVIDIA Container Toolkit in the Linux engine, "
                "then run the NVIDIA preflight again."
            ),
        ),
        _check(
            "ngc_api_key",
            "NGC API key configured",
            ngc_ready,
            severity="required_for_ngc_images",
            fix="Create an NGC API key and save it only in deployment/nvidia/.env.local or your shell environment.",
        ),
        _check(
            "nim_endpoint_configured",
            "NIM/OpenAI-compatible endpoint configured",
            nim_configured,
            detail=f"{nim_base_url} {nim_model}".strip(),
            fix="Replace the placeholder NIM base URL and model in deployment/nvidia/.env.local.",
        ),
        _check(
            "nim_endpoint_reachable",
            "NIM models endpoint reachable",
            bool(nim_probe.get("reachable")),
            detail=str(nim_probe.get("models_url") or nim_probe.get("error") or ""),
            fix="Start the local NIM service or point EDMG_AI_OPENAI_COMPAT_BASE_URL at a reachable hosted NVIDIA endpoint.",
        ),
    ]

    required_failed = [check for check in checks if check["severity"] == "required" and not check["ok"]]
    local_failed = [check for check in checks if check["severity"] == "required_for_local_services" and not check["ok"]]
    ngc_failed = [check for check in checks if check["severity"] == "required_for_ngc_images" and not check["ok"]]
    planner_ready = profile_enabled and nim_configured and bool(nim_probe.get("reachable"))
    official_local_ready = profile_enabled and bool(gpu.get("ok")) and bool(docker.get("ok")) and bool(docker.get("nvidia_runtime")) and ngc_ready

    if not profile_enabled:
        level = "disabled"
        summary = "NVIDIA profile is disabled."
    elif planner_ready and official_local_ready:
        level = "ready"
        summary = "NVIDIA profile, local GPU runtime, NGC access, and NIM planner endpoint are ready."
    elif planner_ready:
        level = "partial"
        summary = "NIM planner endpoint is reachable, but the local official NVIDIA service stack is not fully ready."
    else:
        level = "blocked"
        summary = (
            "NVIDIA host/runtime prerequisites are ready, but the planner endpoint is not ready."
            if official_local_ready
            else "NVIDIA mode is enabled, but the planner endpoint or local GPU service prerequisites are not ready."
        )

    next_actions = [
        {
            "id": check["id"],
            "title": check["label"],
            "detail": check["detail"],
            "fix": check["fix"],
            "severity": check["severity"],
        }
        for check in checks
        if not check["ok"]
    ][:5]

    return {
        "level": level,
        "ready": level == "ready",
        "planner_ready": planner_ready,
        "official_local_ready": official_local_ready,
        "summary": summary,
        "checks": checks,
        "next_actions": next_actions,
        "blocked_required_count": len(required_failed),
        "blocked_local_service_count": len(local_failed),
        "blocked_ngc_count": len(ngc_failed),
    }


def nvidia_diagnostics() -> dict[str, Any]:
    profile = nvidia_profile_status()
    profile_services = profile.get("services", {})
    nim = profile_services.get("nim", {})
    riva = profile_services.get("riva", {})
    omniverse = profile_services.get("omniverse", {})
    nim_base_url = str(nim.get("base_url") or "").strip()
    omniverse_url = str(omniverse.get("base_url") or "").strip()
    riva_url = str(riva.get("base_url") or "").strip()

    nim_probe = _http_probe(
        _nim_models_url(nim_base_url) if nim_base_url else "",
        api_key=os.getenv("EDMG_AI_OPENAI_COMPAT_API_KEY") or None,
        timeout_s=4.0,
    )
    if nim_base_url:
        nim_probe["models_url"] = _nim_models_url(nim_base_url)

    gpu_status = _gpu_status()
    docker_status = _docker_status()
    services = {
        "nim": {
            "configured": bool(nim.get("configured")),
            "base_url": nim_base_url,
            "model": nim.get("model") or "",
            "probe": nim_probe,
        },
        "riva": {
            "configured": bool(riva.get("configured")),
            "base_url": riva_url,
            "asr_enabled": bool(riva.get("asr_enabled")),
            "language_code": riva.get("language_code") or "en-US",
            "model": riva.get("model") or "",
            "fallback_enabled": bool(riva.get("fallback_enabled")),
            "probe": {
                **(_tcp_probe(riva_url, timeout_s=3.0) if riva_url else {"configured": False, "skipped": True}),
                "protocol": "grpc",
            },
        },
        "omniverse": {
            "configured": bool(omniverse.get("configured")),
            "base_url": omniverse_url,
            "probe": _http_probe(omniverse_url, timeout_s=3.0) if omniverse_url else {"configured": False, "skipped": True},
        },
    }
    for service_id in ("nemo", "triton", "audio2face", "ace", "cosmos"):
        service = profile_services.get(service_id, {})
        base_url = str(service.get("base_url") or "").strip()
        services[service_id] = {
            "configured": bool(service.get("configured")),
            "base_url": base_url,
            "model": service.get("model") or "",
            "probe": _http_probe(base_url, timeout_s=3.0) if base_url else {"configured": False, "skipped": True},
        }

    return {
        "profile": profile,
        "host": {
            "gpu": gpu_status,
            "docker": docker_status,
        },
        "services": services,
        "readiness": _readiness(profile, gpu_status, docker_status, services),
    }

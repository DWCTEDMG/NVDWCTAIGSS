from __future__ import annotations

from typing import Any

from edmg_ai_service.providers.openai_compat import OpenAICompatPlanner
from edmg_ai_service.schemas import PlanRequest


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


def _chat_payload(content: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": content,
                }
            }
        ]
    }


def test_openai_compat_planner_requests_json_schema(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_post(_url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float):
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse(
            _chat_payload(
                """
                {
                  "variants": [{
                    "name": "RTX Pulse",
                    "logline": "A compact GPU smoke scene.",
                    "mood": "neon, precise",
                    "visual_motifs": ["light columns"],
                    "color_palette": ["cyan", "magenta"],
                    "scenes": [{
                      "start_s": 0,
                      "end_s": 6,
                      "prompt": "RTX-lit stage with reactive floor lights",
                      "negative_prompt": "flat lighting",
                      "camera": "wide push",
                      "motion": "beat pulse",
                      "notes": ["NIM generated", "schema coercion"]
                    }]
                  }]
                }
                """
            )
        )

    monkeypatch.setattr("edmg_ai_service.providers.openai_compat.requests.post", fake_post)

    planner = OpenAICompatPlanner(
        base_url="http://127.0.0.1:8001/v1",
        api_key=None,
        model="nvidia/llama-3.3-nemotron-super-49b-v1.5",
        timeout_s=123,
    )
    plan = planner.plan(PlanRequest(title="Smoke", duration_s=6, num_variants=1, max_scenes=1))

    assert plan.provider == "openai_compat"
    assert plan.model == "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    assert plan.variants[0].scenes[0].camera == "wide push"
    assert plan.variants[0].scenes[0].notes == "NIM generated; schema coercion"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["json"]["temperature"] <= 0.25
    assert captured["json"]["max_tokens"] >= 700
    assert captured["timeout"] == 123


def test_openai_compat_planner_wraps_top_level_scenes(monkeypatch):
    def fake_post(_url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float):
        return _FakeResponse(
            _chat_payload(
                """
                {
                  "name": "Flat Scene List",
                  "color_palette": ["white", "blue"],
                  "scenes": [{
                    "start_s": 0,
                    "end_s": 4,
                    "prompt": "single top-level scene",
                    "camera": "orbit",
                    "motion": "slow pulse"
                  }]
                }
                """
            )
        )

    monkeypatch.setattr("edmg_ai_service.providers.openai_compat.requests.post", fake_post)

    planner = OpenAICompatPlanner(base_url="http://127.0.0.1:8001", api_key=None, model="nim")
    plan = planner.plan(PlanRequest(title="Smoke", duration_s=4, num_variants=1, max_scenes=1))

    assert plan.provider == "openai_compat"
    assert plan.variants[0].name == "Flat Scene List"
    assert plan.variants[0].scenes[0].prompt == "single top-level scene"


def test_openai_compat_planner_retries_without_response_format(monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake_post(_url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: float):
        calls.append(json)
        if len(calls) == 1:
            return _FakeResponse({"error": "unsupported response_format"}, status_code=400)
        return _FakeResponse(
            _chat_payload(
                """
                {
                  "variants": [{
                    "name": "Retry",
                    "logline": "JSON mode fallback retry.",
                    "mood": "clean",
                    "visual_motifs": [],
                    "color_palette": [],
                    "scenes": [{"start_s": 0, "end_s": 3, "prompt": "retry scene"}]
                  }]
                }
                """
            )
        )

    monkeypatch.setattr("edmg_ai_service.providers.openai_compat.requests.post", fake_post)

    planner = OpenAICompatPlanner(base_url="http://127.0.0.1:8001", api_key=None, model="nim")
    plan = planner.plan(PlanRequest(title="Smoke", duration_s=3, num_variants=1, max_scenes=1))

    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
    assert plan.provider == "openai_compat"

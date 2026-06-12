"""
AI Provider abstraction layer for EDMG.

This module provides a lightweight, optional integration layer for
NVIDIA NIM / OpenAI-compatible LLM providers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import json
import os
import re

try:
    import requests  # type: ignore
    _REQ_OK = True
except Exception:
    requests = None
    _REQ_OK = False

@dataclass
class PromptSegment:
    start: float
    end: float
    prompt: str


@dataclass
class SemanticAnalysis:
    themes: List[str] = field(default_factory=list)
    emotions: Dict[str, float] = field(default_factory=dict)
    visual_elements: List[str] = field(default_factory=list)
    timeline: List[PromptSegment] = field(default_factory=list)
    style_guidance: str = ""
    camera_motion_hints: List[str] = field(default_factory=list)


@dataclass
class AIProviderConfig:
    provider: str = "nvidia_nim"
    model: str = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    base_url: str = "http://127.0.0.1:8001"
    api_key_env: str = "EDMG_AI_OPENAI_COMPAT_API_KEY"
    timeout: int = 60
    temperature: float = 0.4
    max_tokens: int = 800


class AIProvider:
    """Abstract provider interface."""

    def analyze_lyrics(self, text: str, segments: Optional[List[Dict[str, Any]]] = None) -> SemanticAnalysis:
        raise NotImplementedError

    def generate_prompts(self, prompt: str, num_prompts: int) -> List[str]:
        raise NotImplementedError


class BaseJSONProvider(AIProvider):
    """Helper base class for providers that return JSON text."""

    def _extract_json_list(self, text: str) -> List[str]:
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [str(item) for item in data]
        except Exception:
            pass
        match = re.search(r"\[(?:.|\n)*\]", text)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, list):
                    return [str(item) for item in data]
            except Exception:
                pass
        return []

    def analyze_lyrics(self, text: str, segments: Optional[List[Dict[str, Any]]] = None) -> SemanticAnalysis:
        return SemanticAnalysis()


class OpenAIProvider(BaseJSONProvider):
    """OpenAI-compatible chat completion provider."""

    def __init__(self, config: AIProviderConfig):
        self.config = config
        self.api_key = os.getenv(config.api_key_env)

    def generate_prompts(self, prompt: str, num_prompts: int) -> List[str]:
        if not (_REQ_OK and requests):
            return []
        payload = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "messages": [
                {"role": "system", "content": "Respond ONLY with a JSON array of prompt strings."},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        base_url = self.config.base_url.rstrip("/")
        url = f"{base_url}/chat/completions" if base_url.endswith("/v1") else f"{base_url}/v1/chat/completions"
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.config.timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return self._extract_json_list(content)[:num_prompts]
        except Exception:
            return []


def build_ai_provider(config: AIProviderConfig) -> AIProvider:
    provider = config.provider.lower()
    if provider in {"openai_compat", "nvidia", "nvidia_nim", "nim"}:
        return OpenAIProvider(config)
    raise ValueError(f"Unsupported AI provider for NVIDIA-only profile: {config.provider}")


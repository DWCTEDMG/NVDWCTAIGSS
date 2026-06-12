# AI Integration

The older pluggable AI-provider design has been retired. EDMG NVIDIA Workshop
uses one planning contract:

```text
React/Kit control surface
  -> FastAPI backend
  -> NVIDIA NIM or NVIDIA-compatible OpenAI `/v1`
  -> normalized scene plan / storyboard / OpenUSD handoff
```

Provider abstractions for Ollama, llama.cpp, Hugging Face local text
generation, and heuristic fallback are no longer active runtime paths. Keep new
AI features behind the NVIDIA profile and return a setup error when the required
NVIDIA service is not configured.

## Required Behavior

- `EDMG_AI_PROVIDER=openai_compat`
- `EDMG_NVIDIA_MODE=1`
- `EDMG_NVIDIA_PROFILE=omniverse`
- Riva ASR only when explicitly enabled and reachable
- no CPU fallback, proxy fallback, Ollama fallback, or rule-based fallback

## Output Contract

The planner must still produce normalized project decisions that can drive:

- scene plans
- storyboard variants
- audio-reactive cue schedules
- OpenUSD/RTX render job handoffs
- Deforum export references when EDMG Core is installed

"""Local-LLM (Ollama) integration: config resolution, VRAM/context management, the
standalone critic-script CLI driver, and per-gate dispatch (local LLM, falling back
to Claude) for the *-auto.py orchestrators."""

import argparse
import asyncio
import contextlib
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

from agent_common import console, critic_reconcile, files, git, openai_compatible, resume_state

_FULL_GPU_OFFLOAD = 999  # sentinel > any real model's layer count; llama.cpp clamps to actual max
_ALLOWED_URL_SCHEMES = ("http://", "https://")
_DEFAULT_PREDICT_RESERVE = 1024  # ctx headroom for generated tokens when num_predict is unset
_tokenize_unavailable: set[str] = set()  # ollama_urls where /api/tokenize 404'd this process


def _resolve_provider_fields(provider: str, resolved: dict, raw: dict) -> dict:
    """Resolve the transport-specific fields for one provider: 'ollama_url' for
    provider "ollama" (the default), or 'base_url'/'api_key_env' for provider
    "openai-compatible". Raises ValueError for a missing/invalid field or an
    unrecognized provider."""
    if provider == "ollama":
        ollama_url = raw.get("ollama_url", "http://host.docker.internal:11434").rstrip("/")
        if not ollama_url.startswith(_ALLOWED_URL_SCHEMES):
            raise ValueError(
                f"local-llm.json: ollama_url must be http:// or https://, got: {ollama_url}"
            )
        return {"ollama_url": ollama_url}

    if provider == "openai-compatible":
        base_url = resolved.get("base_url") if "base_url" in resolved else raw.get("base_url")
        api_key_env = (
            resolved.get("api_key_env") if "api_key_env" in resolved else raw.get("api_key_env")
        )
        if not base_url or not base_url.rstrip("/").startswith(_ALLOWED_URL_SCHEMES):
            raise ValueError(
                f"local-llm.json: base_url must be http:// or https://, got: {base_url!r}"
            )
        if not api_key_env:
            raise ValueError(
                "local-llm.json: api_key_env is required when provider is 'openai-compatible'"
            )
        return {"base_url": base_url.rstrip("/"), "api_key_env": api_key_env}

    raise ValueError(f"local-llm.json: unknown provider {provider!r}")


def _resolve_entry(raw: dict, critic_override: dict) -> dict | None:
    """
    Merge critic_override with raw['default'] and resolve the full per-critic
    config surface (provider/model/transport fields/num_ctx/max_ctx/keep_alive/
    num_gpu/num_predict/temperature). Returns None if disabled or model unset.

    Shared by load_local_llm_config (critic_override = the single dict at
    critics[critic_type]) and load_local_llm_configs (critic_override = one
    element of a list at critics[critic_type]) — the merge/validation logic
    is identical either way; only where the override dict comes from differs.
    """
    default = raw.get("default", {})
    resolved = {**default, **critic_override}

    if not resolved.get("enabled") or not resolved.get("model", "").strip():
        return None

    provider = resolved.get("provider") if "provider" in resolved else raw.get("provider", "ollama")

    result: dict = {
        "provider": provider,
        "model": resolved["model"],
    }
    result.update(_resolve_provider_fields(provider, resolved, raw))

    # Without num_ctx, Ollama defaults to the model's native window (often 32k-128k),
    # which can overflow VRAM and spill to system RAM.
    num_ctx = resolved["num_ctx"] if "num_ctx" in resolved else raw.get("num_ctx")
    if num_ctx is not None:
        result["num_ctx"] = int(num_ctx)
    # VRAM ceiling for auto-sizing num_ctx from the actual prompt at call time (see
    # estimate_num_ctx). Only consulted when num_ctx above is absent — an explicit
    # num_ctx always wins.
    max_ctx = resolved["max_ctx"] if "max_ctx" in resolved else raw.get("max_ctx")
    if max_ctx is not None:
        result["max_ctx"] = int(max_ctx)
    # -1 pins the model in VRAM indefinitely, avoiding cold-load latency between calls.
    keep_alive = resolved.get("keep_alive") if "keep_alive" in resolved else raw.get("keep_alive")
    if keep_alive is not None:
        result["keep_alive"] = keep_alive
    # Forces this many GPU layers instead of Ollama's own auto-split, which testing
    # showed can leave usable VRAM headroom unused. Defaults to a sentinel above any
    # real model's layer count ("all layers"); _ensure_model_context() falls back to
    # auto-split if the forced value doesn't fit.
    num_gpu = resolved["num_gpu"] if "num_gpu" in resolved else raw.get("num_gpu")
    result["num_gpu"] = int(num_gpu) if num_gpu is not None else _FULL_GPU_OFFLOAD
    # Caps total generated tokens; reasoning models can otherwise think unboundedly.
    num_predict = (
        resolved.get("num_predict") if "num_predict" in resolved else raw.get("num_predict")
    )
    if num_predict is not None:
        result["num_predict"] = int(num_predict)
    # 0.0 = fully deterministic (greedy) — used for reproducible eval runs.
    temperature = (
        resolved.get("temperature") if "temperature" in resolved else raw.get("temperature")
    )
    if temperature is not None:
        result["temperature"] = float(temperature)
    return result


def load_local_llm_config(critic_type: str) -> dict | None:
    """
    Read .specify/local-llm.json and resolve config for the given critic_type.
    Merges the 'default' block with the per-critic override.
    Returns a dict with 'provider', 'model', and either 'ollama_url' (provider
    "ollama", the default) or 'base_url'/'api_key_env' (provider
    "openai-compatible") if the critic is active, or None if disabled, not
    configured, or critics[critic_type] is list-shaped (multi-critic — use
    load_local_llm_configs for that case).
    """
    config_path = Path(".specify/local-llm.json")
    if not config_path.exists():
        return None
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    critic_override = raw.get("critics", {}).get(critic_type, {})
    if isinstance(critic_override, list):
        return None
    return _resolve_entry(raw, critic_override)


def load_local_llm_configs(critic_type: str) -> list[dict]:
    """
    Resolve ALL critic configs configured for critic_type — the plural
    counterpart to load_local_llm_config, powering multi-critic fan-out.

    critics[critic_type] absent or a dict (today's single-critic shape):
    delegates to load_local_llm_config for byte-identical resolution, so
    every config shape that exists in production today produces exactly the
    same output as before, just wrapped in a list. Returns [] if
    disabled/unconfigured, else [{**single, "id": "default"}].

    critics[critic_type] a list (multi-critic shape): resolves each element
    through the same default/top-level merge _resolve_entry applies to the
    dict case, keeping only entries that resolve (enabled + model set). Each
    element requires a unique non-empty string "id" — raises ValueError
    (checked before enabled-filtering, so a typo is caught immediately, not
    mid-fanout) on a missing or duplicate id.
    """
    config_path = Path(".specify/local-llm.json")
    if not config_path.exists():
        return []
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    critic_entry = raw.get("critics", {}).get(critic_type, {})
    if not isinstance(critic_entry, list):
        single = load_local_llm_config(critic_type)
        return [{**single, "id": "default"}] if single else []

    resolved: list[dict] = []
    seen_ids: set[str] = set()
    for i, entry in enumerate(critic_entry):
        critic_id = entry.get("id")
        if not critic_id or not isinstance(critic_id, str):
            raise ValueError(
                f"local-llm.json: critics.{critic_type}[{i}] is missing a required 'id' string"
            )
        if critic_id in seen_ids:
            raise ValueError(
                f"local-llm.json: critics.{critic_type} has duplicate id {critic_id!r}"
            )
        seen_ids.add(critic_id)
        one = _resolve_entry(raw, entry)
        if one is not None:
            resolved.append({**one, "id": critic_id})
    return resolved


def load_critic_execution_mode(critic_type: str) -> str:
    """
    Read the execution mode ("sequential" or "parallel") for a multi-critic
    fan-out from top-level critic_execution[critic_type] in
    .specify/local-llm.json. Defaults to "sequential" when absent, the
    config file is missing, or unparseable — critics commonly share one
    local Ollama GPU host, where different models mid-fanout would thrash
    VRAM/keep_alive pinning, so sequential is the safe default. "parallel"
    is opt-in per phase for critics that target independent endpoints (e.g.
    one local Ollama + one openai-compatible API).
    """
    config_path = Path(".specify/local-llm.json")
    if not config_path.exists():
        return "sequential"
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "sequential"
    mode = raw.get("critic_execution", {}).get(critic_type, "sequential")
    return mode if mode in ("sequential", "parallel") else "sequential"


_DEFAULT_MAX_TURNS = 40


def _resolve_generation_entry(raw: dict, stage_override: dict) -> dict | None:
    """
    Generation counterpart to _resolve_entry: reuses it for the shared
    provider/model/transport/num_ctx/max_ctx/keep_alive/num_gpu/num_predict/
    temperature surface (merging stage_override with raw['default'] exactly
    as critics do — a project can share one 'default' block across both),
    then layers on the generation-only agentic-loop fields, each resolved
    from stage_override -> raw['default'] -> a hardcoded default:

    - max_turns: tool-call round budget before the loop raises
      local_agent_loop.LocalAgentError (default 40).
    - command_timeout_s / output_max_bytes / deny_patterns: passed straight
      through to local_tools.BashSandboxConfig by the caller; absent here
      unless explicitly set, so BashSandboxConfig's own defaults apply.
    """
    resolved = _resolve_entry(raw, stage_override)
    if resolved is None:
        return None

    default = raw.get("default", {})
    merged = {**default, **stage_override}

    resolved["max_turns"] = int(merged.get("max_turns", _DEFAULT_MAX_TURNS))
    if "command_timeout_s" in merged:
        resolved["command_timeout_s"] = int(merged["command_timeout_s"])
    if "output_max_bytes" in merged:
        resolved["output_max_bytes"] = int(merged["output_max_bytes"])
    if "deny_patterns" in merged:
        resolved["deny_patterns"] = list(merged["deny_patterns"])
    return resolved


def load_local_llm_generation_config(stage: str) -> dict | None:
    """
    Read .specify/local-llm.json and resolve the local-agentic-loop config
    for the given generation stage ("plan"/"tasks"/"test"/"implement"), from
    the top-level "generation" object — a sibling to "critics", not a
    variant of it (see agent_common/local_agent_loop.py's module docstring
    for why generation needs its own top-level section). One entry covers
    every agent role for that stage: initial generation, revision, fix, and
    CI-fix all resolve through the same config.

    Returns None if disabled, not configured, or the config file is
    missing/unparseable/malformed — callers (run_generation) treat that
    identically to "no local LLM configured for this stage" and fall back
    to the existing Claude Agent SDK path, exactly like critics do when
    load_local_llm_configs returns [].
    """
    config_path = Path(".specify/local-llm.json")
    if not config_path.exists():
        return None
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    stage_override = raw.get("generation", {}).get(stage, {})
    if not isinstance(stage_override, dict):
        return None
    return _resolve_generation_entry(raw, stage_override)


def _build_request(url: str, **kwargs) -> urllib.request.Request:
    """Thin wrapper so the S310 (audit URL scheme) note lives in one place:
    ollama_url's scheme is validated once, at config load time, in
    load_local_llm_config()."""
    return urllib.request.Request(url, **kwargs)  # noqa: S310


def _urlopen(url_or_request: str | urllib.request.Request, timeout: float):
    """See _build_request — same audit note."""
    return urllib.request.urlopen(url_or_request, timeout=timeout)  # noqa: S310


def _fmt_bytes(b: int) -> str:
    if b >= 1024**3:
        return f"{b / 1024**3:.1f} GB"
    if b >= 1024**2:
        return f"{b // 1024**2} MB"
    return f"{b} B"


def _get_ps_entry(ollama_url: str, model: str) -> dict | None:
    """Return the /api/ps entry for model, or None if not loaded / unreachable."""
    try:
        with _urlopen(f"{ollama_url}/api/ps", timeout=3) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None
    for entry in data.get("models", []):
        name = entry.get("name", "")
        if name == model or name.startswith(model + ":"):
            return entry
    return None


def _estimate_prompt_tokens(ollama_url: str, model: str, prompt: str) -> int:
    """
    Return an estimated token count for prompt under model's tokenizer.

    Tries Ollama's (experimental, not yet in the stable API as of writing) native
    POST /api/tokenize endpoint for an exact, model-aligned count. Falls back to a
    character-based heuristic — deliberately biased to overestimate, since
    Ollama silently truncates a prompt that exceeds the loaded num_ctx (no error),
    so undercounting is far more costly than a bit of wasted VRAM.

    A 404 (route doesn't exist in this Ollama version) is cached per ollama_url for
    the life of the process, so it isn't retried on every call. Any other failure
    (timeout, malformed body) falls back for just that call, since it may be
    transient on a reachable endpoint.
    """
    if ollama_url not in _tokenize_unavailable:
        try:
            payload = json.dumps({"model": model, "content": prompt}).encode("utf-8")
            req = _build_request(
                f"{ollama_url}/api/tokenize",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            tokens = data.get("tokens")
            if isinstance(tokens, list):
                return len(tokens)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                _tokenize_unavailable.add(ollama_url)
        except Exception:  # noqa: S110 — best-effort probe, heuristic fallback below covers it
            pass  # transient failure on a reachable endpoint — retry next call

    return math.ceil(len(prompt) / 3.3)


def estimate_num_ctx(
    ollama_url: str,
    model: str,
    prompt: str,
    num_predict: int | None = None,
    max_ctx: int | None = None,
    min_ctx: int = 2048,
    round_to: int = 256,
    margin: float = 1.15,
) -> int:
    """
    Compute a right-sized num_ctx for prompt: estimated prompt tokens plus headroom
    for generated tokens (num_predict, or a default reserve if unset — Ollama's own
    default is "generate until natural stop," which can be large for reasoning
    models), padded by margin, rounded up to the nearest round_to, floored at
    min_ctx.

    round_to is a small linear increment, not a power-of-two bucket: num_ctx has no
    power-of-two requirement (KV-cache allocation is linear in it), so a large
    bucket would waste VRAM for nothing. The only reason to round at all is to
    avoid reloading the model (expensive) over trivial prompt-size drift between
    calls — a small increment already achieves that.

    If the computed size exceeds max_ctx, clamps to it and logs a warning
    (best-effort, not a hard failure — the caller may see truncated results, same
    as the existing VRAM-spillage warning in _log_vram_state is informational
    rather than fatal).
    """
    tokens = _estimate_prompt_tokens(ollama_url, model, prompt)
    reserve = num_predict if num_predict is not None else _DEFAULT_PREDICT_RESERVE
    raw = math.ceil((tokens + reserve) * margin)
    result = max(min_ctx, math.ceil(raw / round_to) * round_to)

    if max_ctx is not None and result > max_ctx:
        print(
            f"[ollama] estimated ~{tokens} prompt tokens need ctx={result}, "
            f"but max_ctx={max_ctx} — clamping (results may be truncated; consider raising max_ctx)",
            flush=True,
        )
        result = max_ctx

    return result


def _log_vram_state(ollama_url: str, model: str) -> None:
    """
    Query Ollama's /api/ps and log how much of the model is in VRAM vs system RAM.
    Best-effort: silent no-op if unreachable or model not yet listed.
    """
    entry = _get_ps_entry(ollama_url, model)
    if entry is None:
        return
    size_vram = entry.get("size_vram", 0)
    size_total = entry.get("size", size_vram)
    size_ram = max(0, size_total - size_vram)
    ctx = entry.get("context_length", "?")
    spillage = " (spillage — reduce num_ctx in local-llm.json)" if size_ram > 0 else " ✓"
    print(
        f"[ollama] {entry['name']} — ctx: {ctx} — VRAM: {_fmt_bytes(size_vram)}, RAM: {_fmt_bytes(size_ram)}{spillage}",
        flush=True,
    )


def _ensure_model_context(
    ollama_url: str, model: str, num_ctx: int | None = None, keep_alive=None, num_gpu=None
) -> None:
    """
    Ensure the model is loaded with at least the requested num_ctx, and the
    requested num_gpu.

    Ollama won't shrink an already-loaded model's context on its own, and the
    OpenAI-compatible endpoint ignores options.num_ctx at load time — so when the
    loaded context is smaller than requested, this unloads the model and reloads it
    via the native /api/generate endpoint (which does respect num_ctx at load
    time), pinned with keep_alive=-1. When num_ctx is None, it only preloads if
    nothing is loaded yet (to apply num_gpu); an already-loaded model is left
    alone. A loaded context that's already >= requested is also left alone — this
    is a high-water mark, not an exact match, so num_ctx values that fluctuate
    slightly across calls (e.g. auto-sized from varying prompt lengths) don't
    trigger a reload on every call, only when growth genuinely requires it.

    If a forced num_gpu doesn't fit in VRAM, retries once without it so Ollama falls
    back to its own auto-split rather than leaving the model unloaded.
    """
    entry = _get_ps_entry(ollama_url, model)
    current_ctx = entry.get("context_length") if entry else None
    if entry is not None and (
        num_ctx is None or (current_ctx is not None and current_ctx >= num_ctx)
    ):
        return  # already loaded, and either we don't care about its context or it's big enough

    if current_ctx is not None:
        print(
            f"[ollama] model loaded at ctx={current_ctx}, want ctx={num_ctx} — reloading at correct size",
            flush=True,
        )
        try:
            unload_payload = json.dumps({"model": model, "keep_alive": 0}).encode("utf-8")
            req = _build_request(
                f"{ollama_url}/api/generate",
                data=unload_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _urlopen(req, timeout=30):
                pass
        except Exception as e:
            print(f"[ollama] unload request failed (best-effort): {e}", flush=True)
    else:
        print(
            f"[ollama] preloading {model}" + (f" at ctx={num_ctx}" if num_ctx is not None else ""),
            flush=True,
        )

    # Preload via the native endpoint, which respects options.num_ctx at model-load
    # time (unlike /v1/chat/completions).
    def _preload(options: dict) -> None:
        preload_body = {
            "model": model,
            "options": options,
            "keep_alive": keep_alive if keep_alive is not None else -1,
        }
        req = _build_request(
            f"{ollama_url}/api/generate",
            data=json.dumps(preload_body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _urlopen(req, timeout=120):
            pass

    options: dict = {}
    if num_ctx is not None:
        options["num_ctx"] = num_ctx
    if num_gpu is not None:
        options["num_gpu"] = num_gpu
    try:
        _preload(options)
    except Exception:
        if num_gpu is not None:
            print(
                f"[ollama] num_gpu={num_gpu} failed to load — falling back to auto GPU split",
                flush=True,
            )
            with contextlib.suppress(Exception):
                _preload({"num_ctx": num_ctx} if num_ctx is not None else {})
        # else: best-effort; inference will still proceed


def _build_chat_request(prompt: str, config: dict) -> urllib.request.Request:
    """Build the /api/chat streaming request. Uses the native endpoint rather than
    /v1/chat/completions because the OpenAI-compatible one ignores options.num_ctx
    at load time, defeating VRAM optimisation."""
    url = f"{config['ollama_url']}/api/chat"
    options: dict = {"temperature": config.get("temperature", 0.1)}
    if config.get("num_ctx"):
        options["num_ctx"] = config["num_ctx"]
    if config.get("num_predict"):
        options["num_predict"] = config["num_predict"]
    body: dict = {
        "model": config["model"],
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "think": False,
        "format": "json",
        "options": options,
    }
    if "keep_alive" in config:
        body["keep_alive"] = config["keep_alive"]
    payload = json.dumps(body).encode("utf-8")

    return _build_request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )


def _stream_chat_response(
    req: urllib.request.Request, progress_fn=None, progress_interval: int = 250
) -> str:
    """Consume a streaming /api/chat response, invoking progress_fn(token_count,
    elapsed_s) every progress_interval content tokens for logging heartbeats."""
    content_parts = []
    token_count = 0
    thinking_count = 0
    start = time.monotonic()

    with _urlopen(req, timeout=300) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
                if chunk.get("done"):
                    break
                msg = chunk.get("message", {})
                thinking = msg.get("thinking", "")
                if thinking:
                    thinking_count += len(thinking.split())
                    if thinking_count % progress_interval == 0:
                        print(
                            f"[ollama] thinking... {thinking_count} tokens ({time.monotonic() - start:.0f}s elapsed)",
                            flush=True,
                        )
                token = msg.get("content", "")
                if token:
                    content_parts.append(token)
                    token_count += 1
                    if progress_fn and token_count % progress_interval == 0:
                        progress_fn(token_count, time.monotonic() - start)
            except (KeyError, json.JSONDecodeError):
                continue

    if progress_fn and token_count > 0:
        progress_fn(token_count, time.monotonic() - start, done=True)

    return "".join(content_parts)


def call_local_llm(
    prompt: str, config: dict, progress_fn=None, progress_interval: int = 250
) -> str:
    """
    Send prompt to Ollama via the native /api/chat endpoint (streaming, to keep the
    socket alive during generation). Thinking mode disabled for lower latency;
    format="json" grammar-constrains decoding to valid JSON.

    progress_fn: optional callable(token_count, elapsed_s) invoked every
                 progress_interval content tokens, for logging heartbeats.

    If config has no explicit num_ctx but does have max_ctx, num_ctx is computed
    per-call from prompt via estimate_num_ctx (see there for the sizing/clamping
    rules), on a local copy of config — the caller's dict is never mutated.
    """
    if config.get("num_ctx") is None and config.get("max_ctx") is not None:
        config = {
            **config,
            "num_ctx": estimate_num_ctx(
                config["ollama_url"],
                config["model"],
                prompt,
                num_predict=config.get("num_predict"),
                max_ctx=config["max_ctx"],
            ),
        }

    req = _build_chat_request(prompt, config)

    # num_gpu is a model-load-time decision already applied (with fallback) by
    # _ensure_model_context(); repeating it here on every call would trigger an
    # unguarded reload whenever the fallback took a different value.
    if config.get("num_ctx") or config.get("num_gpu") is not None:
        _ensure_model_context(
            config["ollama_url"],
            config["model"],
            config.get("num_ctx"),
            config.get("keep_alive"),
            config.get("num_gpu"),
        )

    result = _stream_chat_response(req, progress_fn, progress_interval)

    _log_vram_state(config["ollama_url"], config["model"])

    return result


def _call_configured_llm(prompt: str, config: dict, progress_fn=None) -> str:
    """Dispatch to the transport matching config["provider"] (default "ollama")."""
    if config.get("provider", "ollama") == "openai-compatible":
        return openai_compatible.call_openai_compatible_llm(prompt, config, progress_fn=progress_fn)
    return call_local_llm(prompt, config, progress_fn=progress_fn)


# --- Tool-calling transport for the local agentic generation loop ---
#
# The functions above (call_local_llm, _build_chat_request) are the critic
# transport: one prompt in, one JSON string out, format="json" grammar-
# constrained. The functions below are the sibling transport for
# local_agent_loop.py's multi-turn tool-calling loop: a canonical
# provider-agnostic message list in, one turn's {"content", "tool_calls"}
# out, no forced JSON mode (a turn's final content is free text, not
# necessarily JSON). See local_agent_loop.py's module docstring for the
# canonical message shape. Kept as new, separate functions rather than
# branching the existing ones on `tools`, so the well-tested critic path is
# untouched.


def _to_ollama_wire_messages(messages: list[dict]) -> list[dict]:
    """Translate the canonical message list into Ollama's /api/chat wire
    format. Ollama's tool_calls entries carry only {"function": {"name",
    "arguments"}} — no id — and its "tool" role messages are matched back to
    the call positionally within the conversation, not by id, so the
    canonical tool_call_id is simply dropped on the way out."""
    wire = []
    for m in messages:
        role = m["role"]
        if role == "assistant" and m.get("tool_calls"):
            wire.append(
                {
                    "role": "assistant",
                    "content": m.get("content") or "",
                    "tool_calls": [
                        {"function": {"name": tc["name"], "arguments": tc["arguments"]}}
                        for tc in m["tool_calls"]
                    ],
                }
            )
        elif role == "tool":
            wire.append({"role": "tool", "content": m["content"]})
        else:
            wire.append({"role": role, "content": m.get("content") or ""})
    return wire


def _parse_ollama_chat_message(message: dict) -> dict:
    """Parse one Ollama /api/chat response 'message' object into the
    canonical turn shape {"content": str|None, "tool_calls": [{"id","name",
    "arguments"}]}. Ollama's tool_calls carry no id, so one is synthesized
    per call — used only to pair a later tool-result message back to this
    call within our own in-process loop state; never sent back to Ollama
    (see _to_ollama_wire_messages, which drops it again)."""
    raw_tool_calls = message.get("tool_calls") or []
    tool_calls = [
        {
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "name": tc.get("function", {}).get("name", ""),
            "arguments": tc.get("function", {}).get("arguments") or {},
        }
        for tc in raw_tool_calls
    ]
    return {"content": message.get("content") or None, "tool_calls": tool_calls}


def _build_chat_turn_request(
    messages: list[dict], config: dict, tools: list[dict]
) -> urllib.request.Request:
    """Build a non-streaming /api/chat request carrying `tools`, for the
    generation agentic loop. Unlike _build_chat_request (critics), this
    never forces format="json"."""
    url = f"{config['ollama_url']}/api/chat"
    options: dict = {"temperature": config.get("temperature", 0.1)}
    if config.get("num_ctx"):
        options["num_ctx"] = config["num_ctx"]
    if config.get("num_predict"):
        options["num_predict"] = config["num_predict"]
    body: dict = {
        "model": config["model"],
        "messages": _to_ollama_wire_messages(messages),
        "tools": tools,
        "stream": False,
        "think": False,
        "options": options,
    }
    if "keep_alive" in config:
        body["keep_alive"] = config["keep_alive"]
    payload = json.dumps(body).encode("utf-8")

    return _build_request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )


def call_local_llm_turn(messages: list[dict], config: dict, tools: list[dict]) -> dict:
    """
    Non-streaming single-turn call to Ollama's native /api/chat with `tools`,
    for the local agentic generation loop (local_agent_loop.py). Returns the
    canonical turn shape {"content": str|None, "tool_calls": [...]}.

    Reuses the same num_ctx auto-sizing and model-context management as
    call_local_llm (critics): num_ctx is estimated from the serialized
    request when only max_ctx is set, and _ensure_model_context preloads/
    reloads the model as needed. Non-streaming (stream: false) trades away
    live token progress for simpler, more robust tool_call parsing — there
    is no incremental tool-call-argument-delta assembly to get right.
    """
    if config.get("num_ctx") is None and config.get("max_ctx") is not None:
        serialized = json.dumps(_to_ollama_wire_messages(messages))
        config = {
            **config,
            "num_ctx": estimate_num_ctx(
                config["ollama_url"],
                config["model"],
                serialized,
                num_predict=config.get("num_predict"),
                max_ctx=config["max_ctx"],
            ),
        }

    req = _build_chat_turn_request(messages, config, tools)

    if config.get("num_ctx") or config.get("num_gpu") is not None:
        _ensure_model_context(
            config["ollama_url"],
            config["model"],
            config.get("num_ctx"),
            config.get("keep_alive"),
            config.get("num_gpu"),
        )

    with _urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())

    _log_vram_state(config["ollama_url"], config["model"])

    return _parse_ollama_chat_message(data.get("message", {}))


def call_configured_llm_turn(messages: list[dict], config: dict, tools: list[dict]) -> dict:
    """Dispatch to the transport matching config["provider"] (default
    "ollama") — the tool-calling counterpart to _call_configured_llm."""
    if config.get("provider", "ollama") == "openai-compatible":
        return openai_compatible.call_openai_compatible_turn(messages, config, tools)
    return call_local_llm_turn(messages, config, tools)


def strip_fences(text: str) -> str:
    """Strip markdown code fences from an LLM response that was supposed to be raw JSON."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _resolve_cli_config(critic_type: str, critic_id: str | None) -> dict | None:
    """Resolve the config a standalone critic-script CLI invocation should use:
    a specific list entry when critic_id is given (multi-critic fan-out), else
    the single-critic config — unchanged lookup from before multi-critic
    support existed."""
    if critic_id:
        return next((c for c in load_local_llm_configs(critic_type) if c["id"] == critic_id), None)
    return load_local_llm_config(critic_type)


def _log_cli_result(
    label: str, iteration: int, result: dict, result_path: Path, summary_style: str
) -> None:
    status = result.get("status", "FAIL")
    if summary_style == "confidence":
        confidence = result.get("confidence", 0)
        blocking = len(result.get("blocking_issues", []))
        if status == "PASS":
            print(
                f"[{label}] iteration {iteration} → PASS (confidence {confidence}/10) → {result_path}",
                flush=True,
            )
        else:
            print(
                f"[{label}] iteration {iteration} → FAIL ({blocking} blocking issue(s), confidence {confidence}/10) → {result_path}",
                flush=True,
            )
    else:
        violations = result.get("violations", [])
        blocking = sum(1 for v in violations if v.get("severity") == "BLOCKING")
        warnings = sum(1 for v in violations if v.get("severity") == "WARNING")
        if status == "PASS":
            print(f"[{label}] iteration {iteration} → PASS → {result_path}", flush=True)
        else:
            print(
                f"[{label}] iteration {iteration} → FAIL ({blocking} blocking, {warnings} warning) → {result_path}",
                flush=True,
            )


def run_local_critic_cli(
    critic_type: str,
    result_prefix: str,
    build_prompt: Callable[[Path, int], str],
    summary_style: str = "violations",
) -> None:
    """
    Shared CLI driver for a standalone local-LLM critic script (ch_1_plan_critic.py,
    ch_1_plan_architecture_critic.py, etc). Callers only supply build_prompt(spec_dir, iteration)
    -> str; this handles arg parsing, config loading, the model call, and writing
    the result.

    critic_type doubles as the local-llm.json config key and the log-line label —
    both were previously separate params, but they diverged only cosmetically
    (e.g. "plan-critic" vs "plan"), so a single value now serves both purposes.

    summary_style: "violations" counts BLOCKING/WARNING entries in
    result["violations"] (plan/tasks/test/implement critics); "confidence" reports
    result["confidence"] and len(result["blocking_issues"]) (architecture/quality
    reviews).

    --critic-id (multi-critic fan-out only): when passed, resolves config via
    load_local_llm_configs(critic_type) filtered to that id instead of
    load_local_llm_config, and writes to {result_prefix}-raw/{critic_id}-
    {iteration}.json instead of the canonical {result_prefix}-{iteration}.json.
    Absent, this function behaves exactly as it did before multi-critic support
    existed — orchestrator.run_gate only passes --critic-id when >1 critic is
    configured for critic_type.

    Exit codes: 0 success, 1 runtime error, 2 local LLM not configured.
    """
    parser = argparse.ArgumentParser(description=f"{critic_type} using local LLM")
    parser.add_argument(
        "--feature", help="Feature folder name (derived from git branch if omitted)"
    )
    parser.add_argument("--iteration", type=int, help="Iteration number (auto-detected if omitted)")
    parser.add_argument(
        "--critic-id",
        help="Run only the critic config with this id from a list-shaped "
        "critics[critic_type] entry (multi-critic fan-out).",
    )
    args = parser.parse_args()

    config = _resolve_cli_config(critic_type, args.critic_id)
    if config is None:
        sys.exit(2)

    label = f"{critic_type}:{args.critic_id}" if args.critic_id else critic_type

    feature = args.feature or git.get_feature_from_branch(critic_type)
    spec_dir = Path(f"specs/{feature}")
    iteration = (
        args.iteration
        if args.iteration is not None
        else resume_state.next_iteration(spec_dir, result_prefix)
    )

    prompt = build_prompt(spec_dir, iteration)

    print(
        f"[{label}] Running iteration {iteration} via local LLM ({config['model']})...", flush=True
    )

    def _progress(token_count: int, elapsed_s: float, done: bool = False) -> None:
        if done:
            print(f"[{label}]   done — {token_count} tokens in {elapsed_s:.0f}s", flush=True)
        else:
            print(f"[{label}]   ... {token_count} tokens ({elapsed_s:.0f}s elapsed)", flush=True)

    try:
        raw = _call_configured_llm(prompt, config, progress_fn=_progress)
    except Exception as e:
        print(f"[{label}] ERROR: local LLM call failed: {e}", flush=True)
        sys.exit(1)

    cleaned = strip_fences(raw)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[{label}] ERROR: could not parse LLM response as JSON: {e}", flush=True)
        print(f"[{label}] Raw response (first 500 chars): {cleaned[:500]}", flush=True)
        sys.exit(1)

    result["iteration"] = iteration

    result_path = (
        spec_dir / f"{result_prefix}-raw" / f"{args.critic_id}-{iteration}.json"
        if args.critic_id
        else spec_dir / f"{result_prefix}-{iteration}.json"
    )
    files.write_file(result_path, json.dumps(result, indent=2))

    _log_cli_result(label, iteration, result, result_path, summary_style)


def _run_critic_subprocess(cmd: list, prefix: str = "") -> int:
    """
    Run a critic subprocess, streaming its output through sys.stdout in real time
    (so the *-auto.log file, teed by setup_log_file, shows critic progress — e.g.
    the "[ollama] thinking..." heartbeats — as it happens rather than only after
    the subprocess exits, which could otherwise be tens of minutes for a slow
    local model). Returns the process exit code.

    prefix: see console.stream_subprocess — used only for parallel multi-critic
    fan-out, where several subprocesses' output would otherwise interleave
    unattributed.
    """
    return console.stream_subprocess(cmd, prefix=prefix)


def _run_one_critic(
    log,
    label: str,
    script: Path,
    feature: str,
    iteration: int,
    raw_dir: Path,
    config: dict,
    prefix: str = "",
) -> dict:
    """
    Run a single critic (from a multi-critic fan-out) as a subprocess with
    --critic-id, reusing its raw result file if one already exists for this
    (critic_id, iteration) — the resume-idempotency check for the fan-out,
    kept local to run_gate's multi-critic branch so resume_state.py needs no
    changes. Returns {"id", "model", "result"} for build_reconcile_prompt.
    """
    raw_path = raw_dir / f"{config['id']}-{iteration}.json"
    if raw_path.exists():
        log(
            f"  {label} critic '{config['id']}' result for iteration {iteration} "
            f"already exists — reusing."
        )
    else:
        log(f"  running {label} critic '{config['id']}' ({config['model']})...")
        cmd = [
            sys.executable,
            str(script),
            "--feature",
            feature,
            "--iteration",
            str(iteration),
            "--critic-id",
            config["id"],
        ]
        returncode = _run_critic_subprocess(cmd, prefix=prefix)
        if returncode != 0:
            log(
                f"ERROR: critic '{config['id']}' failed for {label} iteration {iteration}. Aborting."
            )
            sys.exit(1)
        if not raw_path.exists():
            log(f"ERROR: critic '{config['id']}' did not write {raw_path}. Aborting.")
            sys.exit(1)
    return {
        "id": config["id"],
        "model": config["model"],
        "result": json.loads(raw_path.read_text(encoding="utf-8")),
    }


async def _gather_raw_critic_results(
    log,
    label: str,
    script: Path,
    feature: str,
    iteration: int,
    raw_dir: Path,
    configs: list[dict],
    mode: str,
) -> list[dict]:
    """Run every critic in configs (sequentially, or concurrently via
    asyncio.to_thread when mode == 'parallel') and return their raw results."""
    if mode == "parallel":
        return await asyncio.gather(
            *(
                asyncio.to_thread(
                    _run_one_critic,
                    log,
                    label,
                    script,
                    feature,
                    iteration,
                    raw_dir,
                    config,
                    f"[{config['id']}] ",
                )
                for config in configs
            )
        )
    return [
        _run_one_critic(log, label, script, feature, iteration, raw_dir, config)
        for config in configs
    ]


async def _run_multi_critic_gate(
    log,
    critic_type: str,
    script: Path,
    feature: str,
    iteration: int,
    label: str,
    configs: list[dict],
    result_prefix: str | None,
    summary_style: str,
    build_reconcile_query: Callable[[int, list[dict]], object] | None,
) -> None:
    """The >1-configured-critics branch of run_gate: fan out to every critic,
    then either synthesize a trivial PASS (all critics clean) or hand the
    combined raw findings to build_reconcile_query for the main harness (a
    Claude subagent, never a local LLM) to adjudicate into the canonical
    result. Split out of run_gate to keep its cyclomatic complexity in check."""
    if result_prefix is None:
        log(
            f"ERROR: {label} has {len(configs)} critics configured but run_gate was not "
            f"given a result_prefix for multi-critic fan-out. Aborting."
        )
        sys.exit(1)

    spec_dir = Path(f"specs/{feature}")
    raw_dir = spec_dir / f"{result_prefix}-raw"
    mode = load_critic_execution_mode(critic_type)
    ids = ", ".join(f"{c['id']} ({c['model']})" for c in configs)
    log(f"Using {len(configs)} local LLM critics for {label} ({mode}): {ids}...")

    raw_results = await _gather_raw_critic_results(
        log, label, script, feature, iteration, raw_dir, configs, mode
    )

    canonical_path = spec_dir / f"{result_prefix}-{iteration}.json"
    if critic_reconcile.all_clean_pass(raw_results):
        canonical = critic_reconcile.synthesize_trivial_pass(raw_results, iteration, summary_style)
        files.write_file(canonical_path, json.dumps(canonical, indent=2))
        log(
            f"All {len(configs)} critics returned a clean PASS for {label} — skipping reconciliation."
        )
        return

    if build_reconcile_query is None:
        log(
            f"ERROR: {label} has {len(configs)} critics configured but no reconciliation "
            f"query builder wired up. Aborting."
        )
        sys.exit(1)

    log(f"Critics reported findings for {label} — running reconciliation...")
    await console.stream_query(build_reconcile_query(iteration, raw_results))
    if not canonical_path.exists():
        log(f"ERROR: reconciliation did not write {canonical_path}. Aborting.")
        sys.exit(1)


async def run_gate(
    log,
    critic_type: str,
    script_name: str,
    feature: str,
    iteration: int,
    label: str,
    claude_fallback: Callable,
    result_prefix: str | None = None,
    summary_style: str = "violations",
    build_reconcile_query: Callable[[int, list[dict]], object] | None = None,
) -> None:
    """
    Run one review gate for the *-auto.py orchestrators.

    - 0 configured critics: fall back to Claude (claude_fallback) — unchanged
      from before multi-critic support existed.
    - 1 configured critic: try its local-LLM subprocess, falling back to Claude
      if it isn't configured (exit code 2). Aborts (sys.exit(1)) on any other
      subprocess failure. Bit-for-bit the same code path as before multi-critic
      support existed — this is what keeps single-critic behaviour unchanged.
    - >1 configured critics: see _run_multi_critic_gate.

    claude_fallback: zero-arg callable returning the async iterator of SDK
    messages, e.g. `lambda: query(prompt=..., options=...)`. Only invoked when
    zero critics resolve.

    result_prefix/build_reconcile_query are only required when >1 critic can
    resolve for critic_type — gates that will only ever have 0-or-1 critics can
    omit them.
    """
    configs = load_local_llm_configs(critic_type)
    script = Path(__file__).parent.parent / script_name

    if len(configs) == 1:
        config = configs[0]
        log(f"Using local LLM ({config['model']}) for {label}...")
        returncode = _run_critic_subprocess(
            [sys.executable, str(script), "--feature", feature, "--iteration", str(iteration)],
        )
        if returncode == 2:
            configs = []  # not configured; fall through to Claude below
        elif returncode != 0:
            log(f"ERROR: local LLM {label} failed for iteration {iteration}. Aborting.")
            sys.exit(1)
        else:
            return

    elif len(configs) > 1:
        await _run_multi_critic_gate(
            log,
            critic_type,
            script,
            feature,
            iteration,
            label,
            configs,
            result_prefix,
            summary_style,
            build_reconcile_query,
        )
        return

    if not configs:
        await console.stream_query(claude_fallback())

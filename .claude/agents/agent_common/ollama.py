"""Local-LLM (Ollama) integration: config resolution, VRAM/context management, and the
standalone critic-script CLI driver."""

import argparse
import contextlib
import json
import re
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from agent_common import files, git, resume_state

_FULL_GPU_OFFLOAD = 999  # sentinel > any real model's layer count; llama.cpp clamps to actual max


def load_local_llm_config(critic_type: str) -> dict | None:
    """
    Read .specify/local-llm.json and resolve config for the given critic_type.
    Merges the 'default' block with the per-critic override.
    Returns a dict with 'ollama_url' and 'model' if the critic is active,
    or None if disabled or not configured.
    """
    config_path = Path(".specify/local-llm.json")
    if not config_path.exists():
        return None
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    default = raw.get("default", {})
    critic_override = raw.get("critics", {}).get(critic_type, {})
    resolved = {**default, **critic_override}

    if not resolved.get("enabled") or not resolved.get("model", "").strip():
        return None

    result: dict = {
        "ollama_url": raw.get("ollama_url", "http://host.docker.internal:11434").rstrip("/"),
        "model": resolved["model"],
    }
    # num_ctx caps the KV-cache context window. Without it Ollama uses the model's
    # default (often 32k–128k), which overflows VRAM and spills to system RAM.
    # 16384 is a good default for an 8 GB GPU: critic prompts fit comfortably in VRAM.
    num_ctx = resolved["num_ctx"] if "num_ctx" in resolved else raw.get("num_ctx")
    if num_ctx is not None:
        result["num_ctx"] = int(num_ctx)
    # keep_alive controls how long Ollama keeps the model in VRAM after a request.
    # Set to -1 to pin the model indefinitely — avoids cold-load latency between
    # critic iterations and between pipeline stages.
    keep_alive = resolved.get("keep_alive") if "keep_alive" in resolved else raw.get("keep_alive")
    if keep_alive is not None:
        result["keep_alive"] = keep_alive
    # num_gpu forces this many transformer layers onto the GPU instead of Ollama's own
    # conservative auto-split, which testing showed leaves usable VRAM headroom unused
    # (e.g. it picked 34/37 layers when its own math showed 35 would still fit). Defaults
    # to a sentinel above any real model's layer count so "all layers" is the default
    # behavior with no config needed; llama.cpp clamps to the model's actual max. If the
    # forced value doesn't fit on a smaller GPU, _ensure_model_context() falls back to
    # Ollama's normal auto-split automatically.
    num_gpu = resolved["num_gpu"] if "num_gpu" in resolved else raw.get("num_gpu")
    result["num_gpu"] = int(num_gpu) if num_gpu is not None else _FULL_GPU_OFFLOAD
    # num_predict caps total generated tokens (thinking + response). For reasoning models
    # like deepseek-r1, runaway thinking causes hallucinations; this is the safety cap.
    num_predict = (
        resolved.get("num_predict") if "num_predict" in resolved else raw.get("num_predict")
    )
    if num_predict is not None:
        result["num_predict"] = int(num_predict)
    # temperature controls generation randomness. Default 0.1; use 0.0 for fully
    # deterministic output (greedy decoding) — useful for reproducible eval runs.
    temperature = (
        resolved.get("temperature") if "temperature" in resolved else raw.get("temperature")
    )
    if temperature is not None:
        result["temperature"] = float(temperature)
    return result


def _fmt_bytes(b: int) -> str:
    if b >= 1024**3:
        return f"{b / 1024**3:.1f} GB"
    if b >= 1024**2:
        return f"{b // 1024**2} MB"
    return f"{b} B"


def _get_ps_entry(ollama_url: str, model: str) -> dict | None:
    """Return the /api/ps entry for model, or None if not loaded / unreachable."""
    try:
        with urllib.request.urlopen(f"{ollama_url}/api/ps", timeout=3) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None
    for entry in data.get("models", []):
        name = entry.get("name", "")
        if name == model or name.startswith(model + ":"):
            return entry
    return None


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
    Ensure the model is loaded with the requested num_ctx (if any) and num_gpu (if any).

    Ollama reuses a loaded model for any request that fits within its current context
    window — it will NOT shrink context on its own, and the OpenAI-compatible endpoint
    does not apply options.num_ctx at load time. When num_ctx is given, this function:
      1. Unloads the model if it is currently loaded at the wrong context size.
      2. Preloads it at num_ctx via the native /api/generate endpoint (which does
         respect options.num_ctx at load time), using keep_alive=-1 so it stays pinned.

    When num_ctx is None (the project hasn't opted into pinning it), this function only
    acts if the model isn't loaded at all yet — it preloads once (to apply num_gpu) and
    otherwise leaves an already-loaded model alone, since we have no opinion on what
    context size it should be loaded at and don't want to force one.

    num_gpu, if given, is passed through to force a specific GPU-layer split. If that
    preload fails (e.g. it doesn't fit in VRAM on a smaller GPU), retries once without
    num_gpu so Ollama falls back to its own conservative auto-split rather than leaving
    the model unloaded.
    """
    entry = _get_ps_entry(ollama_url, model)
    current_ctx = entry.get("context_length") if entry else None
    if entry is not None and (num_ctx is None or current_ctx == num_ctx):
        return  # already loaded, and either we don't care about its context or it matches

    if current_ctx is not None:
        print(
            f"[ollama] model loaded at ctx={current_ctx}, want ctx={num_ctx} — reloading at correct size",
            flush=True,
        )
        try:
            unload_payload = json.dumps({"model": model, "keep_alive": 0}).encode("utf-8")
            req = urllib.request.Request(
                f"{ollama_url}/api/generate",
                data=unload_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30):
                pass
        except Exception:
            pass
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
        req = urllib.request.Request(
            f"{ollama_url}/api/generate",
            data=json.dumps(preload_body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120):
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


def call_local_llm(
    prompt: str, config: dict, progress_fn=None, progress_interval: int = 250
) -> str:
    """
    Send prompt to Ollama via the native /api/chat endpoint.
    Uses streaming so the socket stays alive during generation (avoids read timeout).
    Thinking mode disabled — reduces latency for rule-checking tasks.
    format="json" grammar-constrains decoding to syntactically valid JSON, preventing
    the malformed output (e.g. a dropped comma) that smaller models occasionally produce.
    Per-chunk read timeout: 300s.

    Uses the native endpoint (not /v1/chat/completions) because the OpenAI-compatible
    endpoint ignores options.num_ctx at model-load time and always loads at the model's
    default context size — defeating VRAM optimisation.

    progress_fn: optional callable(token_count: int, elapsed_s: float) invoked every
                 progress_interval content tokens. Useful for logging heartbeats to a
                 log file when the caller cannot otherwise observe generation progress.
    progress_interval: how often (in tokens) to fire progress_fn (default: 250).
    """
    url = f"{config['ollama_url']}/api/chat"
    # num_gpu is deliberately NOT included here: it's a model-load-time decision that
    # _ensure_model_context() already applies (with a fallback if the forced value
    # doesn't fit). Re-requesting it on every chat call would tell Ollama to reload
    # whenever the fallback took a different value than the raw config asked for,
    # forcing a second, unguarded load attempt outside that fallback's protection.
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

    if config.get("num_ctx") or config.get("num_gpu") is not None:
        _ensure_model_context(
            config["ollama_url"],
            config["model"],
            config.get("num_ctx"),
            config.get("keep_alive"),
            config.get("num_gpu"),
        )

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    content_parts = []
    token_count = 0
    thinking_count = 0
    start = time.monotonic()

    with urllib.request.urlopen(req, timeout=300) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
                if chunk.get("done"):
                    break
                msg = chunk.get("message", {})
                # Thinking tokens (native API returns them in message.thinking)
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

    _log_vram_state(config["ollama_url"], config["model"])

    if progress_fn and token_count > 0:
        progress_fn(token_count, time.monotonic() - start, done=True)

    return "".join(content_parts)


def strip_fences(text: str) -> str:
    """Strip markdown code fences from an LLM response that was supposed to be raw JSON."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def run_local_critic_cli(
    name: str,
    critic_type: str,
    result_prefix: str,
    build_prompt: Callable[[Path, int], str],
    summary_style: str = "violations",
) -> None:
    """
    Shared CLI driver for a standalone local-LLM critic script (plan_critic.py,
    architecture_critic.py, etc). Handles argument parsing, config loading, calling
    the model, parsing/writing the result, and the PASS/FAIL summary line — callers
    only need to supply build_prompt(spec_dir, iteration) -> str, which should read
    whatever files it needs (via require_files/read_optional) and return the finished
    prompt.

    summary_style:
      "violations" — count BLOCKING/WARNING entries in result["violations"] (default;
                     used by plan/tasks/test/implement critics)
      "confidence" — report result["confidence"] and len(result["blocking_issues"])
                     (used by architecture/quality reviews)

    Exit codes: 0 success, 1 runtime error, 2 local LLM not configured.
    """
    parser = argparse.ArgumentParser(description=f"{name} using local LLM")
    parser.add_argument(
        "--feature", help="Feature folder name (derived from git branch if omitted)"
    )
    parser.add_argument("--iteration", type=int, help="Iteration number (auto-detected if omitted)")
    args = parser.parse_args()

    config = load_local_llm_config(critic_type)
    if config is None:
        sys.exit(2)

    feature = args.feature or git.get_feature_from_branch(name)
    spec_dir = Path(f"specs/{feature}")
    iteration = (
        args.iteration
        if args.iteration is not None
        else resume_state.next_iteration(spec_dir, result_prefix)
    )

    prompt = build_prompt(spec_dir, iteration)

    print(
        f"[{name}] Running iteration {iteration} via local LLM ({config['model']})...", flush=True
    )

    def _progress(token_count: int, elapsed_s: float, done: bool = False) -> None:
        if done:
            print(f"[{name}]   done — {token_count} tokens in {elapsed_s:.0f}s", flush=True)
        else:
            print(f"[{name}]   ... {token_count} tokens ({elapsed_s:.0f}s elapsed)", flush=True)

    try:
        raw = call_local_llm(prompt, config, progress_fn=_progress)
    except Exception as e:
        print(f"[{name}] ERROR: local LLM call failed: {e}", flush=True)
        sys.exit(1)

    cleaned = strip_fences(raw)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[{name}] ERROR: could not parse LLM response as JSON: {e}", flush=True)
        print(f"[{name}] Raw response (first 500 chars): {cleaned[:500]}", flush=True)
        sys.exit(1)

    result["iteration"] = iteration

    result_path = spec_dir / f"{result_prefix}-{iteration}.json"
    files.write_file(result_path, json.dumps(result, indent=2))

    status = result.get("status", "FAIL")
    if summary_style == "confidence":
        confidence = result.get("confidence", 0)
        blocking = len(result.get("blocking_issues", []))
        if status == "PASS":
            print(
                f"[{name}] iteration {iteration} → PASS (confidence {confidence}/10) → {result_path}",
                flush=True,
            )
        else:
            print(
                f"[{name}] iteration {iteration} → FAIL ({blocking} blocking issue(s), confidence {confidence}/10) → {result_path}",
                flush=True,
            )
    else:
        violations = result.get("violations", [])
        blocking = sum(1 for v in violations if v.get("severity") == "BLOCKING")
        warnings = sum(1 for v in violations if v.get("severity") == "WARNING")
        if status == "PASS":
            print(f"[{name}] iteration {iteration} → PASS → {result_path}", flush=True)
        else:
            print(
                f"[{name}] iteration {iteration} → FAIL ({blocking} blocking, {warnings} warning) → {result_path}",
                flush=True,
            )

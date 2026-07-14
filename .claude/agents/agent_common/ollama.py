"""Local-LLM (Ollama) integration: config resolution, VRAM/context management, the
standalone critic-script CLI driver, and per-gate dispatch (local LLM, falling back
to Claude) for the *-auto.py orchestrators."""

import argparse
import contextlib
import json
import re
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from agent_common import console, files, git, resume_state

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
    # Without num_ctx, Ollama defaults to the model's native window (often 32k-128k),
    # which can overflow VRAM and spill to system RAM.
    num_ctx = resolved["num_ctx"] if "num_ctx" in resolved else raw.get("num_ctx")
    if num_ctx is not None:
        result["num_ctx"] = int(num_ctx)
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
    Ensure the model is loaded with the requested num_ctx and num_gpu.

    Ollama won't shrink an already-loaded model's context on its own, and the
    OpenAI-compatible endpoint ignores options.num_ctx at load time — so when num_ctx
    is given, this unloads a wrongly-sized model and reloads it via the native
    /api/generate endpoint (which does respect num_ctx at load time), pinned with
    keep_alive=-1. When num_ctx is None, it only preloads if nothing is loaded yet
    (to apply num_gpu); an already-loaded model is left alone.

    If a forced num_gpu doesn't fit in VRAM, retries once without it so Ollama falls
    back to its own auto-split rather than leaving the model unloaded.
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
    Send prompt to Ollama via the native /api/chat endpoint (streaming, to keep the
    socket alive during generation). Thinking mode disabled for lower latency;
    format="json" grammar-constrains decoding to valid JSON. Uses the native endpoint
    rather than /v1/chat/completions because the OpenAI-compatible one ignores
    options.num_ctx at load time, defeating VRAM optimisation.

    progress_fn: optional callable(token_count, elapsed_s) invoked every
                 progress_interval content tokens, for logging heartbeats.
    """
    url = f"{config['ollama_url']}/api/chat"
    # num_gpu is a model-load-time decision already applied (with fallback) by
    # _ensure_model_context(); repeating it here on every call would trigger an
    # unguarded reload whenever the fallback took a different value.
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

    Exit codes: 0 success, 1 runtime error, 2 local LLM not configured.
    """
    parser = argparse.ArgumentParser(description=f"{critic_type} using local LLM")
    parser.add_argument(
        "--feature", help="Feature folder name (derived from git branch if omitted)"
    )
    parser.add_argument("--iteration", type=int, help="Iteration number (auto-detected if omitted)")
    args = parser.parse_args()

    config = load_local_llm_config(critic_type)
    if config is None:
        sys.exit(2)

    feature = args.feature or git.get_feature_from_branch(critic_type)
    spec_dir = Path(f"specs/{feature}")
    iteration = (
        args.iteration
        if args.iteration is not None
        else resume_state.next_iteration(spec_dir, result_prefix)
    )

    prompt = build_prompt(spec_dir, iteration)

    print(
        f"[{critic_type}] Running iteration {iteration} via local LLM ({config['model']})...",
        flush=True,
    )

    def _progress(token_count: int, elapsed_s: float, done: bool = False) -> None:
        if done:
            print(f"[{critic_type}]   done — {token_count} tokens in {elapsed_s:.0f}s", flush=True)
        else:
            print(
                f"[{critic_type}]   ... {token_count} tokens ({elapsed_s:.0f}s elapsed)", flush=True
            )

    try:
        raw = call_local_llm(prompt, config, progress_fn=_progress)
    except Exception as e:
        print(f"[{critic_type}] ERROR: local LLM call failed: {e}", flush=True)
        sys.exit(1)

    cleaned = strip_fences(raw)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"[{critic_type}] ERROR: could not parse LLM response as JSON: {e}", flush=True)
        print(f"[{critic_type}] Raw response (first 500 chars): {cleaned[:500]}", flush=True)
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
                f"[{critic_type}] iteration {iteration} → PASS (confidence {confidence}/10) → {result_path}",
                flush=True,
            )
        else:
            print(
                f"[{critic_type}] iteration {iteration} → FAIL ({blocking} blocking issue(s), confidence {confidence}/10) → {result_path}",
                flush=True,
            )
    else:
        violations = result.get("violations", [])
        blocking = sum(1 for v in violations if v.get("severity") == "BLOCKING")
        warnings = sum(1 for v in violations if v.get("severity") == "WARNING")
        if status == "PASS":
            print(f"[{critic_type}] iteration {iteration} → PASS → {result_path}", flush=True)
        else:
            print(
                f"[{critic_type}] iteration {iteration} → FAIL ({blocking} blocking, {warnings} warning) → {result_path}",
                flush=True,
            )


def _run_critic_subprocess(cmd: list) -> int:
    """
    Run a critic subprocess and tee its stdout/stderr through sys.stdout/sys.stderr
    so output reaches the log file (via the _Tee set up by setup_log_file).
    Returns the process exit code.
    """
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr, flush=True)
    return result.returncode


async def run_gate(
    log,
    critic_type: str,
    script_name: str,
    feature: str,
    iteration: int,
    label: str,
    claude_fallback: Callable,
) -> None:
    """
    Run one review gate for the *-auto.py orchestrators: try the local-LLM subprocess
    first (if critic_type is configured), falling back to Claude when it isn't
    configured (exit code 2) or absent. Aborts (sys.exit(1)) on any other subprocess
    failure. The run_local_critic_cli counterpart for orchestrator gates, which — unlike
    standalone critic scripts — have a Claude fallback.

    claude_fallback: zero-arg callable returning the async iterator of SDK messages,
    e.g. `lambda: query(prompt=..., options=...)`. Only invoked on fallback.
    """
    llm_config = load_local_llm_config(critic_type)
    if llm_config:
        log(f"Using local LLM ({llm_config['model']}) for {label}...")
        script = Path(__file__).parent.parent / script_name
        returncode = _run_critic_subprocess(
            [sys.executable, str(script), "--feature", feature, "--iteration", str(iteration)],
        )
        if returncode == 2:
            llm_config = None  # not configured; fall through to Claude
        elif returncode != 0:
            log(f"ERROR: local LLM {label} failed for iteration {iteration}. Aborting.")
            sys.exit(1)

    if not llm_config:
        async for message in claude_fallback():
            console.log_sdk_message(message, prefix="  ")

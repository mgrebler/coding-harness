"""
.claude/agents/agent_common.py

Shared utilities for all spec-kit auto-orchestrator agents.
Imported by plan-auto.py, tasks-auto.py, and implement-auto.py.
"""

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Optional

from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock


class _Tee:
    """Write to multiple file-like objects simultaneously."""
    def __init__(self, *files: IO):
        self._files = files

    def write(self, text: str):
        for f in self._files:
            f.write(text)
            f.flush()

    def flush(self):
        for f in self._files:
            f.flush()

    def fileno(self):
        return self._files[0].fileno()


def run_critic_subprocess(cmd: list) -> int:
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


def setup_log_file(path: Path):
    """
    Open *path* in append mode and tee all stdout/stderr to it.
    Call once per script after spec_dir is known.
    A run-separator line is written so successive runs are easy to distinguish.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(path, "a", encoding="utf-8")  # noqa: SIM115 (kept open for process lifetime)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_fh.write(f"\n{'='*60}\n[run started {ts}]\n{'='*60}\n")
    log_fh.flush()
    sys.stdout = _Tee(sys.__stdout__, log_fh)  # type: ignore[assignment]
    sys.stderr = _Tee(sys.__stderr__, log_fh)  # type: ignore[assignment]


def get_feature_from_branch(agent_name: str) -> str:
    """Derive the feature folder name from the current git branch."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True
    )
    branch = result.stdout.strip()
    if branch == "main":
        print(f"[{agent_name}] ERROR: Must be on a feature branch. Currently on main.")
        sys.exit(1)
    return branch


def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def next_iteration(spec_dir: Path, result_prefix: str) -> int:
    """Return the next critic iteration number based on existing result files."""
    existing = list(spec_dir.glob(f"{result_prefix}-*.json"))
    return len(existing) + 1


def read_result(spec_dir: Path, result_prefix: str, iteration: int) -> dict:
    path = spec_dir / f"{result_prefix}-{iteration}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def run_auto_commit(event: str, agent_name: str):
    """Delegate commit to the speckit-git-commit script for the given event."""
    script = Path(".specify/extensions/git/scripts/bash/auto-commit.sh")
    if script.exists():
        subprocess.run(["bash", str(script), event], check=False)
    else:
        print(f"[{agent_name}] Warning: auto-commit.sh not found; skipping commit.", flush=True)


def make_logger(agent_name: str):
    """Return a log function prefixed with the agent name."""
    def log(msg: str):
        print(f"[{agent_name}] {msg}", flush=True)
    return log


def log_sdk_message(message, prefix: str = ""):
    """Print a Claude Agent SDK message in a readable format."""
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                for line in block.text.strip().splitlines():
                    print(f"{prefix}{line}", flush=True)
            elif isinstance(block, ToolUseBlock):
                args = ", ".join(f"{k}={str(v)[:80]!r}" for k, v in block.input.items())
                print(f"{prefix}→ {block.name}({args})", flush=True)
    elif isinstance(message, ResultMessage) and message.result:
        print(f"{prefix}[done] {message.result[:200]}", flush=True)


# ---------------------------------------------------------------------------
# Resume helpers
# ---------------------------------------------------------------------------

def find_passing_iteration(
    spec_dir: Path,
    result_prefix: str,
    max_iterations: int = 3,
) -> Optional[int]:
    """Return the first iteration number whose result has status PASS, or None."""
    for i in range(1, max_iterations + 1):
        rp = spec_dir / f"{result_prefix}-{i}.json"
        if rp.exists():
            try:
                result = json.loads(rp.read_text(encoding="utf-8"))
                if result.get("status") == "PASS":
                    return i
            except (json.JSONDecodeError, ValueError):
                pass
    return None


def extend_iterations_if_reviewed(
    spec_dir: Path,
    review_filename: str,
    primary_result_prefix: str,
    max_iterations: int,
    log_fn=None,
) -> tuple[int, bool]:
    """
    Check for a human escalation review file. If it exists and the primary
    critic loop has already exhausted max_iterations, extend the limit by
    max_iterations more and return (new_max, True). Otherwise return
    (max_iterations, False).

    The boolean signals that violations were resolved externally — callers
    should skip the fix agent for the first new iteration.

    Call this BEFORE the resume guard so find_passing_iteration covers any
    extended iteration range.
    """
    review_path = spec_dir / review_filename
    if not review_path.exists():
        return max_iterations, False
    if next_iteration(spec_dir, primary_result_prefix) <= max_iterations:
        return max_iterations, False
    _log = log_fn or print
    review_text = review_path.read_text(encoding="utf-8")
    _log(f"Human escalation review found ({review_filename}) — extending iteration limit by {max_iterations}.")
    _log(f"Review:\n{review_text.strip()}")
    return max_iterations + max_iterations, True


def load_prior_violations(
    spec_dir: Path,
    result_prefix: str,
    iteration: int,
) -> Optional[list]:
    """
    Single-gate resume helper: if the result at (iteration - 1) was FAIL,
    return its violations list so the revision/fix runs before the next critic.
    Returns None if iteration == 1 or the previous result was PASS.
    Reads violations from the 'violations' key.
    """
    if iteration <= 1:
        return None
    try:
        result = read_result(spec_dir, result_prefix, iteration - 1)
        if result.get("status") == "FAIL":
            return result.get("violations", [])
    except (json.JSONDecodeError, ValueError, OSError):
        pass
    return None


def find_two_gate_resume_state(
    spec_dir: Path,
    gate1_prefix: str,
    gate2_prefix: str,
    iteration: int,
) -> tuple[int, Optional[list], Optional[list]]:
    """
    Two-gate resume helper: inspect existing result files and return the state
    needed to continue correctly after an interruption.

    Returns (adjusted_iteration, gate1_violations, gate2_violations) where:
    - adjusted_iteration may be decremented by 1 if gate1 passed but gate2
      was never run (so the loop re-enters at the same iteration and skips gate1)
    - gate1_violations: violations from the last gate1 FAIL ('violations' key)
    - gate2_violations: violations from the last gate2 FAIL ('blocking_issues' key)

    At most one of gate1_violations / gate2_violations will be non-None.
    """
    if iteration <= 1:
        return iteration, None, None

    prev = iteration - 1
    try:
        prev_gate1 = read_result(spec_dir, gate1_prefix, prev)
    except (json.JSONDecodeError, ValueError, OSError):
        return iteration, None, None

    if prev_gate1.get("status") == "FAIL":
        return iteration, prev_gate1.get("violations", []), None

    gate2_prev_path = spec_dir / f"{gate2_prefix}-{prev}.json"
    if not gate2_prev_path.exists():
        # Gate1 passed but gate2 never ran — step back so the loop reuses the result.
        return prev, None, None

    try:
        prev_gate2 = json.loads(gate2_prev_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return prev, None, None

    if prev_gate2.get("status") == "FAIL":
        return iteration, None, prev_gate2.get("blocking_issues", [])

    # Both gates passed — the resume guard should have caught this already.
    return iteration, None, None


# ---------------------------------------------------------------------------
# Local LLM support
# ---------------------------------------------------------------------------

def format_violations_block(
    violations: list | None,
    iteration: int,
    context: str = "violations (already addressed by the fix agent)",
) -> str:
    """Return a formatted violations context block for a critic prompt, or '' if no violations."""
    if not violations:
        return ""
    return (
        f"\n\nFor context, the previous iteration ({iteration - 1}) found these "
        f"{context}:\n\n{json.dumps(violations, indent=2)}\n\n"
    )


def write_escalation(
    spec_dir: Path,
    feature: str,
    escalation_filename: str,
    log_description: str,
    review_history_prefixes: list[tuple[str, str]],
    max_iterations: int,
    title: str,
    summary: str,
    required_action: str,
    log_fn=None,
) -> None:
    """Write an escalation document and exit non-zero. Called when the critic loop exhausts MAX_ITERATIONS."""
    _log = log_fn or print
    _log(f"ESCALATION: {log_description} after {max_iterations} iterations.")
    escalation_path = spec_dir / escalation_filename
    history = build_review_history(spec_dir, review_history_prefixes, max_iterations)
    content = (
        f"# {title}\n\n"
        f"Feature: {feature}\n"
        f"Date: {datetime.now(timezone.utc).isoformat()}\n"
        f"Status: FAILED after {max_iterations} iterations\n\n"
        f"## Summary\n\n{summary}\n\n"
        f"## Review History\n\n{history}\n\n"
        f"## Required Action\n\n{required_action}\n"
    )
    review_filename = escalation_filename.replace(".md", "-review.md")
    content += (
        f"\n## Resuming After Review\n\n"
        f"Once you have addressed the violations (by fixing code, updating the constitution,\n"
        f"or waiving a violation with justification), create:\n\n"
        f"    specs/{feature}/{review_filename}\n\n"
        f"Use this template:\n\n"
        f"    # Escalation Review\n"
        f"    \n"
        f"    Date: YYYY-MM-DD\n"
        f"    Reviewed by: <name>\n"
        f"    \n"
        f"    ## Action taken\n"
        f"    \n"
        f"    <Describe what you changed: code fixes, constitution updates, waived violations, etc.>\n"
        f"    \n"
        f"    ## Violations waived\n"
        f"    \n"
        f"    - <rule> — <justification>  (omit section if none)\n\n"
        f"Re-running the pipeline will detect this file and grant {max_iterations} additional\n"
        f"iterations automatically — without deleting any existing result files.\n"
    )
    write_file(escalation_path, content)
    _log(f"Human review required → {escalation_path}")
    sys.exit(1)


def write_stage_complete(spec_dir: Path, stage: str) -> None:
    """Write a completion marker file for the given pipeline stage."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    marker = spec_dir / f"{stage}-auto-complete"
    marker.write_text(f"completed: true\nstage: {stage}\ntimestamp: {ts}\n", encoding="utf-8")


def stage_is_complete(spec_dir: Path, stage: str) -> bool:
    """Return True if the given pipeline stage has a completion marker."""
    return (spec_dir / f"{stage}-auto-complete").exists()


def load_local_llm_config(critic_type: str) -> Optional[dict]:
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
    # Critic prompts are typically 3–6k tokens; 8192 keeps everything in VRAM.
    num_ctx = resolved.get("num_ctx") or raw.get("num_ctx")
    if num_ctx:
        result["num_ctx"] = int(num_ctx)
    # keep_alive controls how long Ollama keeps the model in VRAM after a request.
    # Set to -1 to pin the model indefinitely — avoids cold-load latency between
    # critic iterations and between pipeline stages.
    keep_alive = resolved.get("keep_alive") if "keep_alive" in resolved else raw.get("keep_alive")
    if keep_alive is not None:
        result["keep_alive"] = keep_alive
    return result


def _fmt_bytes(b: int) -> str:
    if b >= 1024**3:
        return f"{b / 1024**3:.1f} GB"
    if b >= 1024**2:
        return f"{b // 1024**2} MB"
    return f"{b} B"


def _get_ps_entry(ollama_url: str, model: str) -> Optional[dict]:
    """Return the /api/ps entry for model, or None if not loaded / unreachable."""
    try:
        with urllib.request.urlopen(f"{ollama_url}/api/ps", timeout=3) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None
    base = model.split(":")[0]
    for entry in data.get("models", []):
        name = entry.get("name", "")
        if name == model or name.startswith(base + ":") or name.startswith(base):
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


def _ensure_model_context(ollama_url: str, model: str, num_ctx: int, keep_alive=None) -> None:
    """
    Ensure the model is loaded at exactly num_ctx tokens of context.

    Ollama reuses a loaded model for any request that fits within its current context
    window — it will NOT shrink context on its own, and the OpenAI-compatible endpoint
    does not apply options.num_ctx at load time. This function:
      1. Unloads the model if it is currently loaded at the wrong context size.
      2. Preloads it at num_ctx via the native /api/generate endpoint (which does
         respect options.num_ctx at load time), using keep_alive=-1 so it stays pinned.
    """
    entry = _get_ps_entry(ollama_url, model)
    current_ctx = entry.get("context_length") if entry else None
    if current_ctx == num_ctx:
        return  # already loaded at the right size

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
        print(f"[ollama] preloading {model} at ctx={num_ctx}", flush=True)

    # Preload at the correct context via the native endpoint, which respects
    # options.num_ctx at model-load time (unlike /v1/chat/completions).
    try:
        preload_body: dict = {
            "model": model,
            "options": {"num_ctx": num_ctx},
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
    except Exception:
        pass  # best-effort; inference will still proceed


def call_local_llm(prompt: str, config: dict, progress_fn=None, progress_interval: int = 250) -> str:
    """
    Send prompt to Ollama via the native /api/chat endpoint.
    Uses streaming so the socket stays alive during generation (avoids read timeout).
    Thinking mode disabled — reduces latency for rule-checking tasks.
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
    options: dict = {"temperature": 0.1}
    if config.get("num_ctx"):
        options["num_ctx"] = config["num_ctx"]
    body: dict = {
        "model": config["model"],
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "think": False,
        "options": options,
    }
    if "keep_alive" in config:
        body["keep_alive"] = config["keep_alive"]
    payload = json.dumps(body).encode("utf-8")

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
                        print(f"[ollama] thinking... {thinking_count} tokens ({time.monotonic() - start:.0f}s elapsed)", flush=True)
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


def get_changed_files() -> list[str]:
    """Return list of files changed on this branch relative to main."""
    result = subprocess.run(
        ["git", "diff", "main...HEAD", "--name-only"],
        capture_output=True, text=True,
    )
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]


def read_changed_files(changed_files: list[str], dirs: tuple[str, ...]) -> str:
    """Read files in changed_files that start with any prefix in dirs; return formatted sections."""
    sections = []
    for path_str in changed_files:
        if not any(path_str.startswith(d) for d in dirs):
            continue
        p = Path(path_str)
        if not p.exists():
            continue
        try:
            content = p.read_text(encoding="utf-8")
            sections.append(f"--- {path_str} ---\n{content}")
        except Exception:
            sections.append(f"--- {path_str} --- (could not read)")
    return "\n\n".join(sections) if sections else "(no changed files found in specified directories)"


def build_review_history(
    spec_dir: Path,
    result_prefixes: list[tuple[str, str]],
    max_iterations: int = 3,
) -> str:
    """
    Build a markdown history block for escalation documents.

    result_prefixes is a list of (file_prefix, display_label) pairs, e.g.:
        [("plan-critic-result", "Plan Critic"),
         ("architecture-review-result", "Architecture Review")]

    Returns a string of fenced JSON blocks, one per result file found.
    """
    blocks = []
    for i in range(1, max_iterations + 1):
        for prefix, label in result_prefixes:
            rp = spec_dir / f"{prefix}-{i}.json"
            if rp.exists():
                heading = f"### Iteration {i} — {label}" if label else f"### Iteration {i}"
                blocks.append(f"{heading}\n```json\n{rp.read_text(encoding='utf-8')}\n```")
    return "\n\n".join(blocks)

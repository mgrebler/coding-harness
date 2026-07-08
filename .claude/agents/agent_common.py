"""
.claude/agents/agent_common.py

Shared utilities for all spec-kit auto-orchestrator agents.
Imported by plan-auto.py, tasks-auto.py, and implement-auto.py.
"""

import argparse
import asyncio
import contextlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, NamedTuple

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
    Run one review gate: try the local-LLM subprocess first (if critic_type is
    configured in .specify/local-llm.json), falling back to Claude when it isn't
    configured (subprocess exit code 2) or absent entirely. Aborts the whole
    process (sys.exit(1)) if the local LLM subprocess fails for any other reason.

    claude_fallback: zero-arg callable returning the async iterator of SDK
    messages to consume on the Claude path, e.g. `lambda: query(prompt=..., options=...)`.
    Invoked only when falling back — never called if the local LLM path succeeds.
    """
    llm_config = load_local_llm_config(critic_type)
    if llm_config:
        log(f"Using local LLM ({llm_config['model']}) for {label}...")
        script = Path(__file__).parent / script_name
        returncode = run_critic_subprocess(
            [sys.executable, str(script), "--feature", feature, "--iteration", str(iteration)],
        )
        if returncode == 2:
            llm_config = None  # not configured; fall through to Claude
        elif returncode != 0:
            log(f"ERROR: local LLM {label} failed for iteration {iteration}. Aborting.")
            sys.exit(1)

    if not llm_config:
        async for message in claude_fallback():
            log_sdk_message(message, prefix="  ")


def setup_log_file(path: Path):
    """
    Open *path* in append mode and tee all stdout/stderr to it.
    Call once per script after spec_dir is known.
    A run-separator line is written so successive runs are easy to distinguish.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(path, "a", encoding="utf-8")  # noqa: SIM115 (kept open for process lifetime)
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_fh.write(f"\n{'=' * 60}\n[run started {ts}]\n{'=' * 60}\n")
    log_fh.flush()
    sys.stdout = _Tee(sys.__stdout__, log_fh)  # type: ignore[assignment]
    sys.stderr = _Tee(sys.__stderr__, log_fh)  # type: ignore[assignment]


def get_feature_from_branch(agent_name: str) -> str:
    """Derive the feature folder name from the current git branch."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, check=True
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


def read_optional(path: Path, fallback: str) -> str:
    """Read path as UTF-8 text if it exists, else return fallback."""
    return path.read_text(encoding="utf-8") if path.exists() else fallback


def require_files(name: str, *paths: Path) -> None:
    """Exit(1) with a standard error message if any of paths is missing. Used by standalone critic scripts."""
    for p in paths:
        if not p.exists():
            print(f"[{name}] ERROR: required file not found: {p}", flush=True)
            sys.exit(1)


def require_spec_files(log_fn, spec_dir: Path, *filenames: str) -> None:
    """Exit(1) via log_fn if any of filenames is missing from spec_dir. Used by *-auto.py preflight checks."""
    for f in filenames:
        if not (spec_dir / f).exists():
            log_fn(f"ERROR: {spec_dir}/{f} not found. Cannot proceed.")
            sys.exit(1)


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
) -> int | None:
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
    _log(
        f"Human escalation review found ({review_filename}) — extending iteration limit by {max_iterations}."
    )
    _log(f"Review:\n{review_text.strip()}")
    return max_iterations + max_iterations, True


def load_prior_violations(
    spec_dir: Path,
    result_prefix: str,
    iteration: int,
) -> list | None:
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
) -> tuple[int, list | None, list | None]:
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
# Two-gate critic loop
# ---------------------------------------------------------------------------


class GateSpec(NamedTuple):
    """
    Describes one gate of a two-gate critic loop (e.g. the plan critic, or the
    architecture review that follows it).

    result_prefix: result file prefix, e.g. "plan-critic-result"
    script_name: standalone critic script passed to run_gate, e.g. "plan_critic.py"
    critic_type: local-LLM critic_type key in .specify/local-llm.json, e.g. "plan"
    label: display label used in log lines and passed to run_gate, e.g. "plan critic"
    build_query: (iteration, prior_violations) -> the query(...) call to run via run_gate
    """

    result_prefix: str
    script_name: str
    critic_type: str
    label: str
    build_query: Callable[[int, list | None], object]


async def run_two_gate_loop(
    log,
    spec_dir: Path,
    feature: str,
    max_iterations: int,
    gate1: GateSpec,
    gate2: GateSpec,
    resume_state: tuple[int, list | None, list | None],
    skip_fix_agent: bool,
    run_revision: Callable[[int, list, str], Awaitable],
    on_both_pass: Callable[[dict], Awaitable],
    escalation_kwargs: dict,
) -> None:
    """
    Shared driver for a two-gate critic loop: gate1 (e.g. plan critic / implement
    critic) must PASS before gate2 (e.g. architecture review / code quality review)
    runs; both must PASS in the same iteration before on_both_pass fires and the loop
    returns. Mirrors the per-gate result-file idempotency and violation-carrying
    behaviour of find_two_gate_resume_state.

    resume_state: (iteration, gate1_violations, gate2_violations) as returned by
    find_two_gate_resume_state.

    run_revision(pending_iteration, pending_violations, pending_label): awaited before
    re-running gate1 whenever violations are pending from either gate's previous FAIL.
    pending_label is gate1.label or gate2.label, whichever gate produced the violations.

    on_both_pass(gate2_result): awaited once gate2 PASSes, before the loop returns.
    Callers do their own commit / stage-complete / CI-check work here.

    escalation_kwargs: forwarded to write_escalation() if the loop exhausts
    max_iterations (spec_dir, feature, max_iterations, and log_fn are supplied here).
    """
    iteration, violations1, violations2 = resume_state
    if skip_fix_agent and (violations1 or violations2):
        log(
            "Escalation review present — skipping revision agent; violations were resolved externally."
        )
        violations1 = None
        violations2 = None
    elif violations1:
        log(
            f"Resuming after {gate1.label} FAIL at iteration {iteration - 1} — revision will run before {gate1.label} {iteration}."
        )
    elif violations2:
        log(
            f"Resuming after {gate2.label} FAIL at iteration {iteration - 1} — revision will run before {gate1.label} {iteration}."
        )
    elif iteration < next_iteration(spec_dir, gate1.result_prefix):
        log(
            f"Resuming: {gate1.label} {iteration} already PASS — {gate2.label} will run for iteration {iteration}."
        )

    while iteration <= max_iterations:
        path1 = spec_dir / f"{gate1.result_prefix}-{iteration}.json"
        path2 = spec_dir / f"{gate2.result_prefix}-{iteration}.json"

        # --- Gate 1 ---
        if not path1.exists():
            if violations1 or violations2:
                pending_label = gate1.label if violations1 else gate2.label
                pending_violations = violations1 if violations1 else violations2
                await run_revision(iteration - 1, pending_violations, pending_label)

            prev_violations1 = violations1
            violations1 = None
            violations2 = None

            log(f"Running {gate1.label} (iteration {iteration})...")
            await run_gate(
                log,
                gate1.critic_type,
                gate1.script_name,
                feature,
                iteration,
                gate1.label,
                lambda iteration=iteration, prev_violations1=prev_violations1: gate1.build_query(
                    iteration, prev_violations1
                ),
            )

            if not path1.exists():
                log(
                    f"ERROR: {gate1.label} did not write result file for iteration {iteration}. Aborting."
                )
                sys.exit(1)
        else:
            log(f"{gate1.label} result for iteration {iteration} already exists — reading status.")

        result1 = read_result(spec_dir, gate1.result_prefix, iteration)
        status1 = result1.get("status", "FAIL")
        blocking1 = sum(1 for v in result1.get("violations", []) if v.get("severity") == "BLOCKING")
        warnings1 = sum(1 for v in result1.get("violations", []) if v.get("severity") == "WARNING")

        if status1 == "FAIL":
            log(
                f"{gate1.label} FAIL (iteration {iteration}) — {blocking1} blocking, {warnings1} warning(s)."
            )
            violations1 = result1.get("violations", [])
            iteration += 1
            continue

        log(f"{gate1.label} PASS (iteration {iteration}) — {warnings1} warning(s).")

        # --- Gate 2 ---
        if not path2.exists():
            log(f"Running {gate2.label} (iteration {iteration})...")
            await run_gate(
                log,
                gate2.critic_type,
                gate2.script_name,
                feature,
                iteration,
                gate2.label,
                lambda iteration=iteration, violations2=violations2: gate2.build_query(
                    iteration, violations2
                ),
            )
            if not path2.exists():
                log(
                    f"ERROR: {gate2.label} did not write result file for iteration {iteration}. Aborting."
                )
                sys.exit(1)
        else:
            log(f"{gate2.label} result for iteration {iteration} already exists — reading status.")

        result2 = read_result(spec_dir, gate2.result_prefix, iteration)
        status2 = result2.get("status", "FAIL")
        confidence = result2.get("confidence", 0)

        if status2 == "PASS":
            log(f"{gate2.label} PASS (iteration {iteration}, confidence {confidence}/10).")
            await on_both_pass(result2)
            return

        blocking2 = len(result2.get("blocking_issues", []))
        log(
            f"{gate2.label} FAIL (iteration {iteration}) — {blocking2} blocking issue(s), confidence {confidence}/10."
        )
        violations2 = result2.get("blocking_issues", [])
        iteration += 1

    write_escalation(
        spec_dir=spec_dir,
        feature=feature,
        max_iterations=max_iterations,
        log_fn=log,
        **escalation_kwargs,
    )


async def run_single_gate_loop(
    log,
    spec_dir: Path,
    feature: str,
    max_iterations: int,
    gate: GateSpec,
    resume_state: tuple[int, list | None],
    skip_fix_agent: bool,
    run_fix: Callable[[int, list], Awaitable],
    on_pass: Callable[[dict], Awaitable],
    escalation_kwargs: dict,
) -> None:
    """
    Shared driver for a single-gate critic loop (e.g. the tasks critic, or the test
    critic): one gate must PASS before on_pass fires and the loop returns. The
    single-gate counterpart to run_two_gate_loop — see its docstring for the shared
    resume/violation-carrying/escalation semantics.

    resume_state: (iteration, violations) — e.g. (next_iteration(...), load_prior_violations(...)).

    run_fix(pending_iteration, pending_violations): awaited before re-running the gate
    whenever violations are pending from the previous FAIL.

    on_pass(result): awaited once the gate PASSes, before the loop returns.
    """
    iteration, violations = resume_state
    if skip_fix_agent and violations:
        log("Escalation review present — skipping fix agent; violations were resolved externally.")
        violations = None
    elif violations:
        log(
            f"Resuming after FAIL at iteration {iteration - 1} "
            f"({len(violations)} violation(s)) — fix agent will run before {gate.label} {iteration}."
        )

    while iteration <= max_iterations:
        path = spec_dir / f"{gate.result_prefix}-{iteration}.json"

        if not path.exists():
            if violations:
                await run_fix(iteration - 1, violations)

            prev_violations = violations
            violations = None

            log(f"Running {gate.label} (iteration {iteration})...")
            await run_gate(
                log,
                gate.critic_type,
                gate.script_name,
                feature,
                iteration,
                gate.label,
                lambda iteration=iteration, prev_violations=prev_violations: gate.build_query(
                    iteration, prev_violations
                ),
            )

            if not path.exists():
                log(
                    f"ERROR: {gate.label} did not write result file for iteration {iteration}. Aborting."
                )
                sys.exit(1)
        else:
            log(f"{gate.label} result for iteration {iteration} already exists — reading status.")

        result = read_result(spec_dir, gate.result_prefix, iteration)
        status = result.get("status", "FAIL")
        blocking = sum(1 for v in result.get("violations", []) if v.get("severity") == "BLOCKING")
        warnings = sum(1 for v in result.get("violations", []) if v.get("severity") == "WARNING")

        if status == "FAIL":
            log(
                f"{gate.label} FAIL (iteration {iteration}) — {blocking} blocking, {warnings} warning(s)."
            )
            violations = result.get("violations", [])
            iteration += 1
            continue

        log(f"{gate.label} PASS (iteration {iteration}) — {warnings} warning(s).")
        await on_pass(result)
        return

    write_escalation(
        spec_dir=spec_dir,
        feature=feature,
        max_iterations=max_iterations,
        log_fn=log,
        **escalation_kwargs,
    )


def finish_stage(
    log, spec_dir: Path, agent_name: str, commit_event: str, stage: str, ready_message: str
) -> None:
    """Log ready_message, commit, and mark stage complete. The common tail of every *-auto.py success path."""
    log(ready_message)
    run_auto_commit(commit_event, agent_name)
    write_stage_complete(spec_dir, stage)


def finish_if_already_passing(
    log,
    spec_dir: Path,
    agent_name: str,
    result_prefix: str,
    max_iterations: int,
    label: str,
    ready_message: str,
    commit_event: str,
    stage: str,
) -> bool:
    """
    If a passing result already exists for result_prefix, log it, commit, mark the
    stage complete, and return True (caller should return immediately). Otherwise
    return False. Shared by the trivial "already PASS -> finish" resume guards in
    plan-auto.py, tasks-auto.py, and test-auto.py (implement-auto.py's guard also
    runs CI checks, so it stays bespoke).
    """
    passing = find_passing_iteration(spec_dir, result_prefix, max_iterations)
    if passing is None:
        return False
    log(f"Already PASS from {label} iteration {passing}.")
    finish_stage(log, spec_dir, agent_name, commit_event, stage, ready_message)
    return True


def run_cli(agent_name: str, description: str, run_coro: Callable[[str], Awaitable]) -> None:
    """
    Shared entrypoint for the async *-auto.py orchestrators: parses --feature
    (falling back to the current git branch), then runs run_coro(feature) via
    asyncio.run. Callers still need `if __name__ == "__main__": run_cli(...)`.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--feature", help="Feature folder name (derived from git branch if omitted)"
    )
    args = parser.parse_args()

    feature = args.feature or get_feature_from_branch(agent_name)
    asyncio.run(run_coro(feature))


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
        f"Date: {datetime.now(UTC).isoformat()}\n"
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
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    marker = spec_dir / f"{stage}-auto-complete"
    marker.write_text(f"completed: true\nstage: {stage}\ntimestamp: {ts}\n", encoding="utf-8")


def stage_is_complete(spec_dir: Path, stage: str) -> bool:
    """Return True if the given pipeline stage has a completion marker."""
    return (spec_dir / f"{stage}-auto-complete").exists()


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

    feature = args.feature or get_feature_from_branch(name)
    spec_dir = Path(f"specs/{feature}")
    iteration = (
        args.iteration if args.iteration is not None else next_iteration(spec_dir, result_prefix)
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
    write_file(result_path, json.dumps(result, indent=2))

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


def get_changed_files() -> list[str]:
    """Return list of files changed on this branch relative to main."""
    result = subprocess.run(
        ["git", "diff", "main...HEAD", "--name-only"],
        capture_output=True,
        text=True,
    )
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]


def read_changed_source_files(changed_files: list[str]) -> str:
    """Read the contents of changed_files, skipping specs/ paths and critic result files."""
    content_parts = []
    for path_str in changed_files:
        if path_str.startswith("specs/") or "-result-" in path_str:
            continue
        p = Path(path_str)
        if not p.exists():
            continue
        try:
            content_parts.append(f"--- {path_str} ---\n{p.read_text(encoding='utf-8')}")
        except Exception:
            content_parts.append(f"--- {path_str} --- (could not read)")
    return "\n\n".join(content_parts) if content_parts else "(no changed files found)"


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
    return (
        "\n\n".join(sections) if sections else "(no changed files found in specified directories)"
    )


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

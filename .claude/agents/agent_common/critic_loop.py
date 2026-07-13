"""Two-gate/single-gate critic-loop orchestration engine shared by the *-auto.py agents."""

import argparse
import asyncio
import subprocess
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from agent_common import console, files, git, ollama
from agent_common import resume_state as rstate


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
    Run one review gate: try the local-LLM subprocess first (if critic_type is
    configured in .specify/local-llm.json), falling back to Claude when it isn't
    configured (subprocess exit code 2) or absent entirely. Aborts the whole
    process (sys.exit(1)) if the local LLM subprocess fails for any other reason.

    claude_fallback: zero-arg callable returning the async iterator of SDK
    messages to consume on the Claude path, e.g. `lambda: query(prompt=..., options=...)`.
    Invoked only when falling back — never called if the local LLM path succeeds.
    """
    llm_config = ollama.load_local_llm_config(critic_type)
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
    elif iteration < rstate.next_iteration(spec_dir, gate1.result_prefix):
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

        result1 = rstate.read_result(spec_dir, gate1.result_prefix, iteration)
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

        result2 = rstate.read_result(spec_dir, gate2.result_prefix, iteration)
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

        result = rstate.read_result(spec_dir, gate.result_prefix, iteration)
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
    git.run_auto_commit(commit_event, agent_name)
    rstate.write_stage_complete(spec_dir, stage)


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
    passing = rstate.find_passing_iteration(spec_dir, result_prefix, max_iterations)
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

    feature = args.feature or git.get_feature_from_branch(agent_name)
    asyncio.run(run_coro(feature))


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
    history = rstate.build_review_history(spec_dir, review_history_prefixes, max_iterations)
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
    files.write_file(escalation_path, content)
    _log(f"Human review required → {escalation_path}")
    sys.exit(1)

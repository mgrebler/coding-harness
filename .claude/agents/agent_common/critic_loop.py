"""Two-gate/single-gate critic-loop orchestration engine shared by the *-auto.py agents.

Backend-oblivious: gate execution (local LLM vs. Claude dispatch) lives in
ollama.run_gate — this module only sequences gates and tracks resume state."""

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from agent_common import console, files, git, ollama
from agent_common import resume_state as rstate


# A critic loop that keeps re-running against the exact same BLOCKING findings
# is not making progress — most often a human decision (a missing constitution
# decision record, an unresolved amendment) is blocking it, not something the
# revision/fix agent can address by editing code. Detecting this after
# _NO_PROGRESS_THRESHOLD identical consecutive iterations and escalating
# immediately, instead of burning the rest of the iteration budget, is what
# closed specs/032's 6-iteration test-phase escalation (4 of which were spent
# stuck on the same missing decision record before a human noticed).
_NO_PROGRESS_THRESHOLD = 2


def _blocking_signature_from_violations(violations: list) -> frozenset:
    """Signature for gate1-style critics, whose 'violations' list mixes
    BLOCKING and WARNING severities — only BLOCKING entries count toward
    'no progress', since a critic legitimately re-reporting the same
    WARNING isn't a stuck loop."""
    return frozenset(
        (v.get("rule"), v.get("location")) for v in violations if v.get("severity") == "BLOCKING"
    )


def _blocking_signature_from_issues(blocking_issues: list) -> frozenset:
    """Signature for gate2-style critics (architecture/quality review),
    whose 'blocking_issues' list is already all-blocking by construction."""
    return frozenset(
        (item.get("title") or item.get("rule") or item.get("principle"), item.get("location"))
        for item in blocking_issues
    )


def _escalate_no_progress(
    log, spec_dir: Path, feature: str, iteration: int, gate_label: str, escalation_kwargs: dict
) -> None:
    log(
        f"ESCALATION: {gate_label} reported an unchanged set of BLOCKING finding(s) across "
        f"iterations {iteration - 1} and {iteration} — no forward progress; escalating early "
        f"rather than continuing to the iteration limit."
    )
    kwargs = dict(escalation_kwargs)
    original_summary = kwargs.pop("summary", "")
    no_progress_summary = (
        f"{gate_label} reported an unchanged set of BLOCKING findings across two consecutive "
        f"iterations ({iteration - 1} and {iteration}) — the revision/fix agent's attempt did not "
        f"change anything the critic flagged, so continuing would not make progress. This most often "
        f"means the loop is blocked on something only a human can resolve (e.g. a missing constitution "
        f"decision record, an unresolved amendment, a genuine disagreement with the rule). Escalating "
        f"now instead of running to the full iteration limit.\n\n{original_summary}"
    )
    write_escalation(
        spec_dir=spec_dir,
        feature=feature,
        max_iterations=iteration,
        log_fn=log,
        summary=no_progress_summary,
        **kwargs,
    )


class GateSpec(NamedTuple):
    """
    Describes one gate of a critic loop (e.g. the plan critic, or the architecture
    review that follows it).

    result_prefix: result file prefix, e.g. "ch-1-plan-critic-result"
    script_name: standalone critic script passed to run_gate, e.g. "ch_1_plan_critic.py"
    critic_type: local-LLM critic_type key in .specify/local-llm.json, e.g. "plan"
    label: display label used in log lines and passed to run_gate, e.g. "plan critic"
    build_query: (iteration, prior_violations) -> the query(...) call to run via run_gate
    """

    result_prefix: str
    script_name: str
    critic_type: str
    label: str
    build_query: Callable[[int, list | None], object]


async def _run_gate_for_iteration(
    log,
    spec_dir: Path,
    feature: str,
    iteration: int,
    gate: GateSpec,
    prev_violations: list | None,
    summary_style: str,
) -> tuple[str, dict]:
    """
    Idempotently run one gate for one iteration — the unit shared by both the
    single-gate and two-gate loops. Reuses the result file if it already
    exists (resume case); otherwise runs the gate and verifies it wrote one.
    Logs the PASS/FAIL summary line and returns (status, result).

    prev_violations is only consulted when the gate actually runs (passed
    through to gate.build_query); it's ignored when reusing an existing
    result, mirroring run_gate's own idempotency check.

    summary_style: "violations" counts BLOCKING/WARNING entries in
    result["violations"] (gate1-style critics); "confidence" reports
    result["confidence"] and len(result["blocking_issues"]) (architecture/
    quality-review-style gates) — same convention as run_local_critic_cli.
    """
    path = spec_dir / f"{gate.result_prefix}-{iteration}.json"
    if not path.exists():
        log(f"Running {gate.label} (iteration {iteration})...")
        await ollama.run_gate(
            log,
            gate.critic_type,
            gate.script_name,
            feature,
            iteration,
            gate.label,
            lambda: gate.build_query(iteration, prev_violations),
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

    if summary_style == "confidence":
        confidence = result.get("confidence", 0)
        if status == "PASS":
            log(f"{gate.label} PASS (iteration {iteration}, confidence {confidence}/10).")
        else:
            blocking = len(result.get("blocking_issues", []))
            log(
                f"{gate.label} FAIL (iteration {iteration}) — {blocking} blocking issue(s), confidence {confidence}/10."
            )
    else:
        blocking = sum(1 for v in result.get("violations", []) if v.get("severity") == "BLOCKING")
        warnings = sum(1 for v in result.get("violations", []) if v.get("severity") == "WARNING")
        if status == "PASS":
            log(f"{gate.label} PASS (iteration {iteration}) — {warnings} warning(s).")
        else:
            log(
                f"{gate.label} FAIL (iteration {iteration}) — {blocking} blocking, {warnings} warning(s)."
            )

    return status, result


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
    Shared driver for a two-gate critic loop: gate1 must PASS before gate2 runs; both
    must PASS in the same iteration before on_both_pass fires and the loop returns.

    resume_state: (iteration, gate1_violations, gate2_violations), as returned by
    find_two_gate_resume_state.
    run_revision(pending_iteration, pending_violations, pending_label): awaited before
    re-running gate1 whenever either gate has pending violations from its last FAIL.
    on_both_pass(gate2_result): awaited once gate2 PASSes; callers do their own
    commit/stage-complete/CI-check work here.
    escalation_kwargs: forwarded to write_escalation() (with spec_dir, feature,
    max_iterations, log_fn) if the loop exhausts max_iterations.
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

    stuck_sig1 = _blocking_signature_from_violations(violations1) if violations1 else None
    stuck_sig2 = _blocking_signature_from_issues(violations2) if violations2 else None

    while iteration <= max_iterations:
        path1 = spec_dir / f"{gate1.result_prefix}-{iteration}.json"

        # --- Gate 1 ---
        prev_violations1 = violations1
        if not path1.exists():
            if violations1 or violations2:
                pending_label = gate1.label if violations1 else gate2.label
                pending_violations = violations1 if violations1 else violations2
                await run_revision(iteration - 1, pending_violations, pending_label)
            violations1 = None
            violations2 = None

        status1, result1 = await _run_gate_for_iteration(
            log, spec_dir, feature, iteration, gate1, prev_violations1, "violations"
        )

        if status1 == "FAIL":
            violations1 = result1.get("violations", [])
            sig1 = _blocking_signature_from_violations(violations1)
            if sig1 and sig1 == stuck_sig1 and iteration >= _NO_PROGRESS_THRESHOLD:
                _escalate_no_progress(log, spec_dir, feature, iteration, gate1.label, escalation_kwargs)
                return
            stuck_sig1 = sig1
            iteration += 1
            continue

        stuck_sig1 = None  # gate1 passed — reset

        # --- Gate 2 ---
        status2, result2 = await _run_gate_for_iteration(
            log, spec_dir, feature, iteration, gate2, violations2, "confidence"
        )

        if status2 == "PASS":
            await on_both_pass(result2)
            return

        violations2 = result2.get("blocking_issues", [])
        sig2 = _blocking_signature_from_issues(violations2)
        if sig2 and sig2 == stuck_sig2 and iteration >= _NO_PROGRESS_THRESHOLD:
            _escalate_no_progress(log, spec_dir, feature, iteration, gate2.label, escalation_kwargs)
            return
        stuck_sig2 = sig2
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

    stuck_sig = _blocking_signature_from_violations(violations) if violations else None

    while iteration <= max_iterations:
        path = spec_dir / f"{gate.result_prefix}-{iteration}.json"

        prev_violations = violations
        if not path.exists():
            if violations:
                await run_fix(iteration - 1, violations)
            violations = None

        status, result = await _run_gate_for_iteration(
            log, spec_dir, feature, iteration, gate, prev_violations, "violations"
        )

        if status == "FAIL":
            violations = result.get("violations", [])
            sig = _blocking_signature_from_violations(violations)
            if sig and sig == stuck_sig and iteration >= _NO_PROGRESS_THRESHOLD:
                _escalate_no_progress(log, spec_dir, feature, iteration, gate.label, escalation_kwargs)
                return
            stuck_sig = sig
            iteration += 1
            continue

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
    ch_1_plan_auto.py, ch_2_tasks_auto.py, and ch_3_test_auto.py (ch_4_implement_auto.py's guard also
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
    try:
        asyncio.run(run_coro(feature))
    except console.SessionLimitError as e:
        log = console.make_logger(agent_name)
        log(f"PAUSED — hit a Claude usage/session limit: {e}")
        log(
            "This is not a critic or code failure — re-run this command once the "
            "limit resets. Progress made so far (commits, result files) is preserved."
        )
        sys.exit(console.USAGE_LIMIT_EXIT_CODE)


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
    """Write an escalation document and exit non-zero. Called when the critic loop exhausts the iteration limit."""
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

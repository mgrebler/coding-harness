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

from claude_agent_sdk import AgentDefinition, query

from agent_common import console, critic_reconcile, files, git, ollama
from agent_common import resume_state as rstate
from agent_common.driving_agent import NO_RECURSION_NOTICE, driving_agent_options
from agent_common.followup import record_pass_followups
from agent_common.preflight_checks import revision_regressions

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
        (v.get("rule"), v.get("location"))
        for v in violations
        if isinstance(v, dict) and v.get("severity") == "BLOCKING"
    )


def _blocking_signature_from_issues(blocking_issues: list) -> frozenset:
    """Signature for gate2-style critics (architecture/quality review),
    whose 'blocking_issues' list is already all-blocking by construction."""
    return frozenset(
        (item.get("title") or item.get("rule") or item.get("principle"), item.get("location"))
        for item in blocking_issues
        if isinstance(item, dict)
    )


def _escalate_no_progress(
    log,
    spec_dir: Path,
    feature: str,
    iteration: int,
    max_iterations: int,
    gate_label: str,
    escalation_kwargs: dict,
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
        max_iterations=max_iterations,
        log_fn=log,
        summary=no_progress_summary,
        last_iteration=iteration,
        **kwargs,
    )


async def _run_fix_guarded(
    log, fix: Callable[[list], Awaitable], violations: list, artifact: Path | None
) -> None:
    """Run a fix/revision agent. When it revises a single document (artifact),
    check it actually changed it without destroying structure; if not,
    restore the previous version and retry once with the problem added to the
    violations, before spending a critic iteration on it. A second bad
    revision is kept for the critic to judge."""
    if artifact is None or not artifact.exists():
        await fix(violations)
        return
    before = artifact.read_text(encoding="utf-8")
    await fix(violations)
    after = artifact.read_text(encoding="utf-8") if artifact.exists() else ""
    problems = revision_regressions(before, after)
    if not problems:
        return
    log(f"Revision of {artifact.name} rejected ({'; '.join(problems)}) — retrying once.")
    artifact.write_text(before, encoding="utf-8")
    guard = {
        "rule": "Revision guard",
        "severity": "BLOCKING",
        "location": artifact.name,
        "finding": "Your previous revision was rejected and reverted: " + "; ".join(problems),
    }
    await fix([guard, *violations])


class GateSpec(NamedTuple):
    """
    Describes one gate of a critic loop (e.g. the plan critic, or the architecture
    review that follows it).

    result_prefix: result file prefix, e.g. "ch-1-plan-critic-result"
    script_name: standalone critic script passed to run_gate, e.g. "ch_1_plan_critic.py"
    critic_type: local-LLM critic_type key in .specify/pipeline.json, e.g. "plan"
    label: display label used in log lines and passed to run_gate, e.g. "plan critic"
    build_query: (iteration, prior_violations, claude_model) -> the query(...) call
        to run via run_gate. claude_model is the resolved Claude model (or None) —
        only meaningful when this gate falls back to Claude; build_query itself
        decides how to use it (pass to driving_agent_options/AgentDefinition).
    build_reconcile_query: (iteration, raw_results, claude_model) -> the query(...)
        call that reconciles >1 critics' raw findings into the canonical result,
        when critic_type resolves to more than one configured critic. None (the
        default) for gates that will only ever have 0-or-1 critics configured.
    """

    result_prefix: str
    script_name: str
    critic_type: str
    label: str
    build_query: Callable[[int, list | None, str | None], object]
    build_reconcile_query: Callable[[int, list[dict], str | None], object] | None = None


CRITIC_TOOLS = ["Read", "Write", "Bash", "Glob", "Grep"]


def claude_gate(
    *,
    feature: str,
    result_prefix: str,
    script_name: str,
    critic_type: str,
    label: str,
    summary_style: str,
    agent_name: str,
    description: str,
    task: str,
    prompt: Callable[[int, str, str], str],
    context: Callable[[], list[tuple[str, str]]],
    violations_context: str | None = None,
) -> GateSpec:
    """Build a GateSpec whose Claude critic and Claude reconciler are wired the
    same way for every gate, so a stage only states what differs:

    agent_name / description: the critic subagent (its reconciler is
        "<agent_name>-reconcile").
    task: the driving agent's instruction, e.g. "Validate plan.md for feature X."
    prompt(iteration, violations_block, output_instructions): the critic prompt,
        normally the gate's build_*_prompt shared with its local critic script.
    context(): (heading, text) sections the reconciler re-verifies findings
        against — read at call time, so revised artifacts are current.
    violations_context: how format_violations_block describes the previous
        iteration's findings.
    """
    tag = result_prefix.removesuffix("-result")
    critics = agent_name.removesuffix("-critic")

    def result_path(iteration: int) -> str:
        return f"specs/{feature}/{result_prefix}-{iteration}.json"

    def output_instructions(iteration: int, suffix: str = "") -> str:
        return (
            f"- After producing JSON, write it to {result_path(iteration)} using Bash\n"
            f"- Print one line: [{tag}{suffix}] iteration {iteration} → PASS or FAIL → path"
        )

    def drive(user_prompt: str, name: str, agent: AgentDefinition, model: str | None):
        return query(
            prompt=user_prompt + NO_RECURSION_NOTICE,
            options=driving_agent_options(
                allowed_tools=[*CRITIC_TOOLS, "Agent"], agents={name: agent}, model=model
            ),
        )

    def build_query(iteration: int, prev_violations, model: str | None):
        violations_block = rstate.format_violations_block(
            prev_violations, iteration, violations_context
        )
        agent = AgentDefinition(
            description=description,
            model=model,
            prompt=prompt(iteration, violations_block, output_instructions(iteration)),
            tools=CRITIC_TOOLS,
        )
        return drive(f"{task} Write result to {result_path(iteration)}.", agent_name, agent, model)

    def build_reconcile_query(iteration: int, raw_results: list[dict], model: str | None):
        context_block = "\n\n".join(f"--- {heading} ---\n{text}" for heading, text in context())
        agent = AgentDefinition(
            description=f"Reconciles findings from multiple independent {critics} critics "
            "into one verified result.",
            model=model,
            prompt=critic_reconcile.build_reconcile_prompt(
                context_block,
                raw_results,
                iteration,
                summary_style,
                output_instructions(iteration, "-reconcile"),
            ),
            tools=CRITIC_TOOLS,
        )
        return drive(
            f"Reconcile the raw {agent_name} findings for feature {feature} into one verified "
            f"result. Write result to {result_path(iteration)}.",
            f"{agent_name}-reconcile",
            agent,
            model,
        )

    return GateSpec(
        result_prefix, script_name, critic_type, label, build_query, build_reconcile_query
    )


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
    # An off-schema result file is a harness/subagent bug, not a verdict: move
    # it aside and run the gate once more instead of failing on it forever on
    # every resume (specs/006).
    for attempt in (1, 2):
        if not path.exists():
            log(f"Running {gate.label} (iteration {iteration})...")
            await ollama.run_gate(
                log,
                gate.critic_type,
                gate.script_name,
                feature,
                iteration,
                gate.label,
                lambda model: gate.build_query(iteration, prev_violations, model),
                result_prefix=gate.result_prefix,
                summary_style=summary_style,
                build_reconcile_query=gate.build_reconcile_query,
            )
            if not path.exists():
                log(
                    f"ERROR: {gate.label} did not write result file for iteration {iteration}. Aborting."
                )
                sys.exit(1)
        else:
            log(f"{gate.label} result for iteration {iteration} already exists — reading status.")
        try:
            result = rstate.read_result(spec_dir, gate.result_prefix, iteration, summary_style)
            break
        except rstate.GateSchemaError as e:
            invalid = path.with_suffix(".invalid")
            path.rename(invalid)
            log(
                f"ERROR: {gate.label} (iteration {iteration}) result file {e} — moved to {invalid}."
            )
            if attempt == 2:
                sys.exit(1)
    status = result.get("status", "FAIL")
    record_pass_followups(spec_dir, gate.label.title(), iteration, result)

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
        blocking = sum(
            1
            for v in result.get("violations", [])
            if isinstance(v, dict) and v.get("severity") == "BLOCKING"
        )
        warnings = sum(
            1
            for v in result.get("violations", [])
            if isinstance(v, dict) and v.get("severity") == "WARNING"
        )
        if status == "PASS":
            log(f"{gate.label} PASS (iteration {iteration}) — {warnings} warning(s).")
        else:
            log(
                f"{gate.label} FAIL (iteration {iteration}) — {blocking} blocking, {warnings} warning(s)."
            )

    return status, result


def _log_two_gate_resume(
    log,
    spec_dir: Path,
    gate1: GateSpec,
    gate2: GateSpec,
    iteration: int,
    violations1: list | None,
    violations2: list | None,
    budget: rstate.IterationBudget,
) -> tuple[list | None, list | None]:
    """Log the resume situation for run_two_gate_loop and return the
    (violations1, violations2) to actually start the loop with — cleared to
    (None, None) when the escalation review says the human already fixed them,
    or with the review's guidance prepended for the revision agent."""
    if budget.resolved_externally:
        log("Escalation review says the human made the fix — skipping revision agent.")
        return None, None
    if budget.human_guidance:
        log("Escalation review guidance will be passed to the revision agent.")
        guidance = rstate.human_guidance_violation(budget.human_guidance)
        if violations2 and not violations1:
            return None, [guidance, *violations2]
        return [guidance, *(violations1 or [])], None
    if violations1:
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
    return violations1, violations2


async def run_two_gate_loop(
    log,
    spec_dir: Path,
    feature: str,
    budget: rstate.IterationBudget,
    gate1: GateSpec,
    gate2: GateSpec,
    resume_state: tuple[int, list | None, list | None],
    run_revision: Callable[[int, list, str], Awaitable],
    on_both_pass: Callable[[dict], Awaitable],
    escalation_kwargs: dict,
    revised_artifact: Path | None = None,
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
    max_iterations, log_fn) if the loop exhausts budget.max_iterations.
    revised_artifact: the single document run_revision edits (e.g. plan.md), if
    any — see _run_fix_guarded.
    """
    iteration, violations1, violations2 = resume_state
    max_iterations = budget.max_iterations
    escalation_kwargs = {**escalation_kwargs, "grant_size": budget.grant_size}
    violations1, violations2 = _log_two_gate_resume(
        log, spec_dir, gate1, gate2, iteration, violations1, violations2, budget
    )
    # A consumed escalation review starts a fresh attempt, so the review-wrapped
    # violations never count toward "no progress".
    reviewed = budget.human_guidance or budget.resolved_externally
    stuck_sig1 = (
        _blocking_signature_from_violations(violations1) if violations1 and not reviewed else None
    )
    stuck_sig2 = (
        _blocking_signature_from_issues(violations2) if violations2 and not reviewed else None
    )

    while iteration <= max_iterations:
        path1 = spec_dir / f"{gate1.result_prefix}-{iteration}.json"

        # --- Gate 1 ---
        prev_violations1 = violations1
        if not path1.exists():
            if violations1 or violations2:
                pending_label = gate1.label if violations1 else gate2.label
                pending_violations = violations1 if violations1 else violations2
                await _run_fix_guarded(
                    log,
                    lambda v, i=iteration - 1, label=pending_label: run_revision(i, v, label),
                    pending_violations,
                    revised_artifact,
                )
            violations1 = None
            violations2 = None

        status1, result1 = await _run_gate_for_iteration(
            log, spec_dir, feature, iteration, gate1, prev_violations1, "violations"
        )

        if status1 == "FAIL":
            violations1 = result1.get("violations", [])
            sig1 = _blocking_signature_from_violations(violations1)
            if sig1 and sig1 == stuck_sig1 and iteration >= _NO_PROGRESS_THRESHOLD:
                _escalate_no_progress(
                    log,
                    spec_dir,
                    feature,
                    iteration,
                    max_iterations,
                    gate1.label,
                    escalation_kwargs,
                )
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
            _escalate_no_progress(
                log,
                spec_dir,
                feature,
                iteration,
                max_iterations,
                gate2.label,
                escalation_kwargs,
            )
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
    budget: rstate.IterationBudget,
    gate: GateSpec,
    resume_state: tuple[int, list | None],
    run_fix: Callable[[int, list], Awaitable],
    on_pass: Callable[[dict], Awaitable],
    escalation_kwargs: dict,
    revised_artifact: Path | None = None,
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
    max_iterations = budget.max_iterations
    escalation_kwargs = {**escalation_kwargs, "grant_size": budget.grant_size}
    stuck_sig = (
        _blocking_signature_from_violations(violations)
        if violations and not (budget.human_guidance or budget.resolved_externally)
        else None
    )
    if budget.resolved_externally:
        log("Escalation review says the human made the fix — skipping fix agent.")
        violations = None
    elif budget.human_guidance:
        log("Escalation review guidance will be passed to the fix agent.")
        violations = [rstate.human_guidance_violation(budget.human_guidance), *(violations or [])]
    elif violations:
        log(
            f"Resuming after FAIL at iteration {iteration - 1} "
            f"({len(violations)} violation(s)) — fix agent will run before {gate.label} {iteration}."
        )

    while iteration <= max_iterations:
        path = spec_dir / f"{gate.result_prefix}-{iteration}.json"

        prev_violations = violations
        if not path.exists():
            if violations:
                await _run_fix_guarded(
                    log, lambda v, i=iteration - 1: run_fix(i, v), violations, revised_artifact
                )
            violations = None

        status, result = await _run_gate_for_iteration(
            log, spec_dir, feature, iteration, gate, prev_violations, "violations"
        )

        if status == "FAIL":
            violations = result.get("violations", [])
            sig = _blocking_signature_from_violations(violations)
            if sig and sig == stuck_sig and iteration >= _NO_PROGRESS_THRESHOLD:
                _escalate_no_progress(
                    log,
                    spec_dir,
                    feature,
                    iteration,
                    max_iterations,
                    gate.label,
                    escalation_kwargs,
                )
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
    log,
    spec_dir: Path,
    agent_name: str,
    commit_event: str,
    stage: str,
    ready_message: str,
    exclude_from_commit: list[str] | None = None,
) -> None:
    """Log ready_message, commit, and mark stage complete. The common tail of every *-auto.py success path.

    exclude_from_commit: forwarded to git.run_auto_commit — paths (typically a
    pre-stage dirty-file baseline) to leave out of this commit. See
    FOLLOWUP_HARNESS.md Bug 4."""
    log(ready_message)
    if exclude_from_commit:
        git.run_auto_commit(commit_event, agent_name, exclude=exclude_from_commit)
    else:
        git.run_auto_commit(commit_event, agent_name)
    rstate.write_stage_complete(spec_dir, stage)


def finish_if_already_passing(
    log,
    spec_dir: Path,
    agent_name: str,
    result_prefix: str,
    label: str,
    ready_message: str,
    commit_event: str,
    stage: str,
    exclude_from_commit: list[str] | None = None,
) -> bool:
    """
    If a passing result already exists for result_prefix, log it, commit, mark the
    stage complete, and return True (caller should return immediately). Otherwise
    return False. Shared by the trivial "already PASS -> finish" resume guards in
    ch_1_plan_auto.py, ch_2_tasks_auto.py, and ch_3_test_auto.py (ch_4_implement_auto.py's guard also
    runs CI checks, so it stays bespoke).
    """
    passing = rstate.find_passing_iteration(spec_dir, result_prefix)
    if passing is None:
        return False
    log(f"Already PASS from {label} iteration {passing}.")
    record_pass_followups(
        spec_dir, label.title(), passing, rstate.read_result(spec_dir, result_prefix, passing)
    )
    finish_stage(
        log,
        spec_dir,
        agent_name,
        commit_event,
        stage,
        ready_message,
        exclude_from_commit=exclude_from_commit,
    )
    return True


def _ask(log, agent_name: str, question: str, default: str) -> str:
    """Ask question on a terminal (an empty answer takes default); take default
    without asking when running unattended."""
    if not sys.stdin.isatty():
        log(f"Non-interactive mode: defaulting to '{default}'.")
        return default
    return input(f"[{agent_name}] {question} ").strip().lower() or default


def confirm_regenerate(
    log, agent_name: str, spec_dir: Path, artifact: str, result_prefix: str
) -> bool:
    """When artifact exists but no critic has reviewed it yet (e.g. an
    interrupted first run), ask whether to review it as-is (the default),
    regenerate it, or abort. Returns True to regenerate."""
    if not (spec_dir / artifact).exists() or any(spec_dir.glob(f"{result_prefix}-*.json")):
        return False
    choice = _ask(
        log,
        agent_name,
        f"WARNING: {spec_dir}/{artifact} exists with no critic results.\n"
        f"  resume — run critic on existing {artifact} (default)\n"
        f"  regen  — regenerate {artifact} from scratch\n"
        f"  abort  — exit without changes\n"
        f"Choice [resume]:",
        "resume",
    )
    if choice == "abort":
        log("Aborted. No changes made.")
        sys.exit(0)
    return choice == "regen"


def confirm_review_existing(
    log, agent_name: str, spec_dir: Path, result_prefix: str, work: str
) -> None:
    """When an agent's work already looks complete but no critic has reviewed
    it, confirm running the critic on it as-is (the default) or abort."""
    if any(spec_dir.glob(f"{result_prefix}-*.json")):
        return
    answer = _ask(
        log,
        agent_name,
        f"WARNING: {work} appear complete but no critic result exists. "
        f"Run critic on them as-is? (yes/no) [yes]:",
        "yes",
    )
    if answer != "yes":
        log("Aborted. No changes made.")
        sys.exit(0)


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
    ollama.check_providers(console.make_logger(agent_name))
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
    grant_size: int = 3,
    last_iteration: int | None = None,
) -> None:
    """Write an escalation document and exit non-zero. Called when the critic loop
    exhausts the iteration limit, or escalates early at last_iteration."""
    _log = log_fn or print
    ran = last_iteration or max_iterations
    _log(f"ESCALATION: {log_description} after {ran} iterations.")
    escalation_path = spec_dir / escalation_filename
    history = rstate.build_review_history(spec_dir, review_history_prefixes, max_iterations)
    content = (
        f"# {title}\n\n"
        f"Feature: {feature}\n"
        f"Date: {datetime.now(UTC).isoformat()}\n"
        f"Status: FAILED after {ran} iterations\n\n"
        f"## Summary\n\n{summary}\n\n"
        f"## Review History\n\n{history}\n\n"
        f"## Required Action\n\n{required_action}\n"
    )
    review_filename = escalation_filename.replace(".md", "-review.md")
    content += (
        f"\n## Resuming After Review\n\n"
        f"Create the file below either with guidance for the fix agent (it will run next\n"
        f"with your review as an authoritative instruction), or after making the fix\n"
        f"yourself — in that case include the line `{rstate.FIXED_BY_HUMAN_MARKER}` so\n"
        f"the fix agent is skipped:\n\n"
        f"    specs/{feature}/{review_filename}\n\n"
        f"Use this template:\n\n"
        f"    # Escalation Review\n"
        f"    \n"
        f"    Date: YYYY-MM-DD\n"
        f"    Reviewed by: <name>\n"
        f"    \n"
        f"    ## Action taken / guidance for the fix agent\n"
        f"    \n"
        f"    <What you changed, or what the fix agent should do. Add\n"
        f"    `{rstate.FIXED_BY_HUMAN_MARKER}` only if no agent fix is needed.>\n"
        f"    \n"
        f"    ## Violations waived\n"
        f"    \n"
        f"    - <rule> — <justification>  (omit section if none)\n\n"
        f"Re-running the pipeline will detect this file and grant {grant_size} additional\n"
        f"iterations automatically — without deleting any existing result files. The\n"
        f"file is renamed to *-consumed-to-<N>.md once applied, so it is used only once.\n"
    )
    files.write_file(escalation_path, content)
    _log(f"Human review required → {escalation_path}")
    sys.exit(1)

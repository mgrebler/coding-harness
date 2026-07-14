"""Critic-loop resume-state and result-file bookkeeping."""

import json
from datetime import UTC, datetime
from pathlib import Path


def next_iteration(spec_dir: Path, result_prefix: str) -> int:
    """Return the next critic iteration number based on existing result files."""
    existing = list(spec_dir.glob(f"{result_prefix}-*.json"))
    return len(existing) + 1


def read_result(spec_dir: Path, result_prefix: str, iteration: int) -> dict:
    path = spec_dir / f"{result_prefix}-{iteration}.json"
    return json.loads(path.read_text(encoding="utf-8"))


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


def write_stage_complete(spec_dir: Path, stage: str) -> None:
    """Write a completion marker file for the given pipeline stage."""
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    marker = spec_dir / f"{stage}-auto-complete"
    marker.write_text(f"completed: true\nstage: {stage}\ntimestamp: {ts}\n", encoding="utf-8")


def stage_is_complete(spec_dir: Path, stage: str) -> bool:
    """Return True if the given pipeline stage has a completion marker."""
    return (spec_dir / f"{stage}-auto-complete").exists()


def build_review_history(
    spec_dir: Path,
    result_prefixes: list[tuple[str, str]],
    max_iterations: int = 3,
) -> str:
    """
    Build a markdown history block for escalation documents.

    result_prefixes is a list of (file_prefix, display_label) pairs, e.g.:
        [("ch-1-plan-critic-result", "Plan Critic"),
         ("ch-1-plan-architecture-review-result", "Architecture Review")]

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

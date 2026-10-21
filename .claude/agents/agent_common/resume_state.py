"""Critic-loop resume-state and result-file bookkeeping."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

# Required top-level keys per summary_style, mirroring the confidence/violations
# convention shared with critic_loop.py and ollama.py's _log_cli_result. A gate
# result missing these must never be silently read as a worst-case FAIL — that's
# indistinguishable from a real failing review and makes the gate permanently
# unable to PASS. See FOLLOWUP_HARNESS.md.
_REQUIRED_KEYS = {
    "confidence": {"status", "confidence", "blocking_issues"},
    "violations": {"status", "violations"},
}


class GateSchemaError(Exception):
    """A gate result file is missing key(s) required for its summary_style."""


# Claude-written results drifted to these keys (specs/002, 003), which left
# FOLLOWUP entries reading "(untitled concern) (unspecified severity)".
_ITEM_KEY_ALIASES = {
    "title": ("summary", "issue"),
    "finding": ("detail", "issue", "summary"),
}
_FINDING_LISTS = ("violations", "blocking_issues", "non_blocking_concerns")


def _normalize_item(item: dict) -> None:
    for key, aliases in _ITEM_KEY_ALIASES.items():
        alias = next((a for a in aliases if item.get(a)), None)
        if not item.get(key) and alias:
            item[key] = item[alias]
    if not item.get("location") and item.get("file"):
        item["location"] = f"{item['file']}:{item['line']}" if item.get("line") else item["file"]


def _status_rule_breach(result: dict, summary_style: str) -> str | None:
    """Why a PASS contradicts its own findings under the documented rule, or None."""
    if summary_style == "violations":
        if any(
            isinstance(v, dict) and v.get("severity") == "BLOCKING" for v in result["violations"]
        ):
            return "PASS with BLOCKING violation(s)"
        return None
    severities = [i.get("severity") for i in result["blocking_issues"] if isinstance(i, dict)]
    if "Critical" in severities:
        return "PASS with a Critical blocking issue"
    if severities.count("High") > 2:
        return "PASS with more than 2 High blocking issues"
    if (result.get("confidence") or 0) < 7:
        return "PASS with confidence below 7"
    return None


def normalize_result(result: dict, summary_style: str) -> bool:
    """Validate a gate result in place and make it self-consistent, for local
    and Claude critics alike. Raises GateSchemaError if required keys are
    missing. Maps drifted finding keys onto title/finding/location, and turns a
    PASS that breaks the documented status rule (any BLOCKING violation; any
    Critical, more than 2 High, or confidence < 7) into FAIL — specs/083 had a
    critic PASS its own High finding. Returns True if anything changed."""
    missing = sorted(_REQUIRED_KEYS[summary_style] - result.keys())
    if missing:
        raise GateSchemaError(f"missing required key(s) {missing}")
    before = json.dumps(result, sort_keys=True)
    for key in _FINDING_LISTS:
        for item in result.get(key) or []:
            if isinstance(item, dict):
                _normalize_item(item)
    if result.get("status") == "PASS" and (breach := _status_rule_breach(result, summary_style)):
        result["status"] = "FAIL"
        result["harness_note"] = f"status changed from PASS to FAIL by the harness: {breach}"
    return json.dumps(result, sort_keys=True) != before


def next_iteration(spec_dir: Path, result_prefix: str) -> int:
    """Return the next critic iteration number based on existing result files."""
    existing = list(spec_dir.glob(f"{result_prefix}-*.json"))
    return len(existing) + 1


def max_existing_iteration(spec_dir: Path, result_prefix: str, default: int = 3) -> int:
    """
    Return the highest iteration number among existing result files for result_prefix,
    or default if none exist. Use this (rather than a hardcoded 3) when checking a
    stage that may have escalated past the usual iteration limit via
    extend_iterations_if_reviewed — otherwise find_passing_iteration's own default
    of 3 would miss a passing result at iteration 4+.
    """
    existing_iterations = [
        int(stem)
        for p in spec_dir.glob(f"{result_prefix}-*.json")
        if (stem := p.stem.rsplit("-", 1)[-1]).isdigit()
    ]
    return max(existing_iterations, default=default)


def read_result(
    spec_dir: Path, result_prefix: str, iteration: int, summary_style: str | None = None
) -> dict:
    """
    Read and parse a gate's result file. If summary_style is given, also
    normalize it (see normalize_result), writing any correction back so every
    later reader sees the same verdict — a missing required key raises
    GateSchemaError: it is a harness/subagent bug, not a review verdict, and
    must never be indistinguishable from one. summary_style=None (the default)
    skips this, for callers that don't know/need the shape.
    """
    path = spec_dir / f"{result_prefix}-{iteration}.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    if summary_style is not None and normalize_result(result, summary_style):
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def find_passing_iteration(
    spec_dir: Path,
    result_prefix: str,
    max_iterations: int | None = None,
) -> int | None:
    """Return the first iteration number whose result has status PASS, or None.
    max_iterations=None scans every result file on disk."""
    if max_iterations is None:
        max_iterations = max_existing_iteration(spec_dir, result_prefix, default=0)
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


class IterationBudget(NamedTuple):
    """How many critic iterations a stage may run, and what a human escalation
    review (if one was just consumed) says to do with the first new one.

    grant_size: iterations a future escalation review will add.
    human_guidance: the review's text, to be handed to the fix agent as an
        authoritative instruction (None when no fresh review was consumed).
    resolved_externally: the review declared the human already made the fix
        (FIXED_BY_HUMAN_MARKER), so the fix agent is skipped for one iteration.
    """

    max_iterations: int
    grant_size: int
    human_guidance: str | None = None
    resolved_externally: bool = False


FIXED_BY_HUMAN_MARKER = "Resolution: fixed-by-human"

# A consumed review is renamed so a later resume (e.g. after a session-limit
# pause inside the extended range) can't re-apply it as a fresh grant — the
# ceiling it granted is recorded in the name instead.
_CONSUMED_RE = re.compile(r"-consumed-to-(\d+)\.md$")


def _granted_ceiling(spec_dir: Path, review_filename: str) -> int:
    stem = review_filename.removesuffix(".md")
    return max(
        (
            int(m.group(1))
            for p in spec_dir.glob(f"{stem}-consumed-to-*.md")
            if (m := _CONSUMED_RE.search(p.name))
        ),
        default=0,
    )


def apply_escalation_review(
    spec_dir: Path,
    review_filename: str,
    primary_result_prefix: str,
    max_iterations: int,
    log_fn=None,
) -> IterationBudget:
    """
    Resolve a stage's iteration budget, consuming a fresh human escalation
    review if the loop has exhausted its current ceiling.

    The ceiling is the highest one any previously consumed review granted (or
    max_iterations if none). A review file present while the loop still has
    budget left is left in place untouched. Consuming a review extends the
    ceiling by max_iterations, renames the file to
    <review>-consumed-to-<ceiling>.md, and returns its text as human_guidance
    for the fix agent — unless the review contains FIXED_BY_HUMAN_MARKER, in
    which case the fix agent is skipped instead.

    Call this BEFORE the resume guard so find_passing_iteration covers any
    extended iteration range.
    """
    ceiling = max(max_iterations, _granted_ceiling(spec_dir, review_filename))
    review_path = spec_dir / review_filename
    if not review_path.exists() or next_iteration(spec_dir, primary_result_prefix) <= ceiling:
        return IterationBudget(ceiling, max_iterations)
    _log = log_fn or print
    review_text = review_path.read_text(encoding="utf-8").strip()
    new_ceiling = max(ceiling, max_existing_iteration(spec_dir, primary_result_prefix)) + (
        max_iterations
    )
    stem = review_filename.removesuffix(".md")
    review_path.rename(spec_dir / f"{stem}-consumed-to-{new_ceiling}.md")
    _log(
        f"Human escalation review found ({review_filename}) — extending iteration limit to {new_ceiling}."
    )
    _log(f"Review:\n{review_text}")
    if FIXED_BY_HUMAN_MARKER in review_text:
        return IterationBudget(new_ceiling, max_iterations, resolved_externally=True)
    return IterationBudget(new_ceiling, max_iterations, human_guidance=review_text)


def human_guidance_violation(review_text: str) -> dict:
    """Wrap a human escalation review as a pending violation, so it reaches the
    fix agent through the same violations list every stage already passes it."""
    return {
        "rule": "Human escalation review",
        "title": "Human escalation review",
        "severity": "BLOCKING",
        "location": "escalation review",
        "finding": review_text,
        "fix": "Apply the human's guidance above. It is authoritative and overrides any "
        "conflicting critic finding.",
    }


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
        result = read_result(spec_dir, result_prefix, iteration - 1, summary_style="violations")
        if result.get("status") == "FAIL":
            return result.get("violations", [])
    except (json.JSONDecodeError, ValueError, OSError, GateSchemaError):
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
        prev_gate1 = read_result(spec_dir, gate1_prefix, prev, summary_style="violations")
    except (json.JSONDecodeError, ValueError, OSError, GateSchemaError):
        return iteration, None, None

    if prev_gate1.get("status") == "FAIL":
        return iteration, prev_gate1.get("violations", []), None

    gate2_prev_path = spec_dir / f"{gate2_prefix}-{prev}.json"
    if not gate2_prev_path.exists():
        # Gate1 passed but gate2 never ran — step back so the loop reuses the result.
        return prev, None, None

    try:
        prev_gate2 = read_result(spec_dir, gate2_prefix, prev, summary_style="confidence")
    except (json.JSONDecodeError, ValueError, OSError, GateSchemaError):
        return prev, None, None

    if prev_gate2.get("status") == "FAIL":
        return iteration, None, prev_gate2.get("blocking_issues", [])

    # Both gates passed — the resume guard should have caught this already.
    return iteration, None, None


def format_violations_block(
    violations: list | None,
    iteration: int,
    context: str | None = None,
) -> str:
    """Return a formatted violations context block for a critic prompt, or '' if no violations."""
    if not violations:
        return ""
    context = context or "violations (already addressed by the fix agent)"
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

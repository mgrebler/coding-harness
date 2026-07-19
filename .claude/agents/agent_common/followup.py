"""
Writes non-blocking review concerns to a tracked, greppable follow-up file
instead of leaving them only inside a *-result-*.json that nobody re-reads
after merge.

A gate that returns PASS with non-empty non_blocking_concerns has still
described a real defect — see specs/033-job-timeout's
ch-4-implement-code-quality-review-result-2.json, whose three non-blocking
concerns all shipped to main and needed a separate human-requested
follow-up PR (commit 25b13fb) after a human happened to notice. This module
makes that backlog visible in the feature's own spec directory instead of
depending on someone re-reading a passing JSON result after merge.
"""

import json
from pathlib import Path

FOLLOWUP_FILENAME = "FOLLOWUP-non-blocking-concerns.md"


def _format_concern(concern: dict) -> str:
    title = concern.get("title") or concern.get("finding") or "(untitled concern)"
    severity = concern.get("severity", "unspecified severity")
    location = concern.get("location", "location unspecified")
    finding = concern.get("finding", "")

    lines = [f"- **{title}** ({severity}) — `{location}`"]
    if finding and finding.strip() != title.strip():
        lines.append(f"  {finding}")
    return "\n".join(lines)


def record_non_blocking_concerns(
    spec_dir: Path,
    feature: str,
    source_label: str,
    iteration: int,
    concerns: list,
) -> None:
    """Append any non_blocking_concerns from a PASS result to
    specs/$FEATURE/FOLLOWUP-non-blocking-concerns.md. No-op if concerns is
    empty. Idempotent per (source_label, iteration) — calling this more than
    once for the same gate/iteration (e.g. across a resumed run) does not
    duplicate the entry."""
    if not concerns:
        return

    followup_path = spec_dir / FOLLOWUP_FILENAME
    heading = f"## {source_label} — iteration {iteration}"

    if followup_path.exists():
        existing = followup_path.read_text(encoding="utf-8")
        if heading in existing:
            return
    else:
        existing = ""

    if not existing:
        existing = (
            f"# Non-blocking concerns — {feature}\n\n"
            "Concerns raised by an automated review that PASSED (nothing here was\n"
            "blocking) but were flagged as real, worth-tracking maintenance risk.\n"
            "A PASS on the originating gate does not mean these are handled — resolve\n"
            "or explicitly close each entry, the same as any other tracked defect.\n\n"
        )

    body = [heading, ""]
    body.extend(_format_concern(concern) for concern in concerns)
    entry = "\n".join(body) + "\n\n"

    followup_path.write_text(existing + entry, encoding="utf-8")


def record_from_result_file(
    spec_dir: Path,
    feature: str,
    source_label: str,
    result_path: Path,
) -> None:
    """Convenience wrapper: read a *-result-N.json file and record its
    non_blocking_concerns (only meaningful when status is PASS — a FAIL
    result's non_blocking_concerns, if any, are addressed by the normal
    fix-agent loop instead, since the gate hasn't cleared yet)."""
    if not result_path.exists():
        return
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return
    if result.get("status") != "PASS":
        return
    record_non_blocking_concerns(
        spec_dir,
        feature,
        source_label,
        result.get("iteration", 0),
        result.get("non_blocking_concerns", []),
    )

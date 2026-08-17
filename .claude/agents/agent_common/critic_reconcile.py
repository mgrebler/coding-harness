"""Reconciliation of multiple independent critics' raw findings into one
canonical result, for the multi-critic fan-out path in ollama.run_gate.

Schema-aware across the two result shapes used throughout the harness:
"violations" style (plan/tasks/test/implement critics — a flat 'violations'
list) and "confidence" style (architecture/quality-review gates —
'blocking_issues'/'non_blocking_concerns'). Kept out of ollama.py (transport/
dispatch-focused) and the per-stage *_auto.py files (context-focused)."""

import json

_VIOLATIONS_SCHEMA = """{
  "iteration": %(iteration)d,
  "status": "PASS or FAIL",
  "violations": [
    {
      "rule": "<section and label>",
      "severity": "BLOCKING or WARNING",
      "location": "<section/task ID/location in the artifact>",
      "finding": "<specific citable description>"
    }
  ],
  "not_applicable": [
    {
      "rule": "<rule label>",
      "reason": "<why not applicable>"
    }
  ],
  "summary": "<one paragraph>"
}"""

_VIOLATIONS_TAIL = "- status is FAIL if any violation is BLOCKING\n- status is PASS only if zero BLOCKING violations"

_CONFIDENCE_SCHEMA = """{
  "iteration": %(iteration)d,
  "status": "PASS or FAIL",
  "confidence": <number 1-10>,
  "blocking_issues": [
    {
      "title": "<short title>",
      "severity": "Critical or High",
      "principle": "<principle name>",
      "location": "<section/location in the artifact>",
      "finding": "<specific, citable description>"
    }
  ],
  "non_blocking_concerns": [
    {
      "title": "<short title>",
      "severity": "Medium or Low",
      "principle": "<principle name>",
      "location": "<section/location in the artifact>",
      "finding": "<specific, citable description>"
    }
  ],
  "required_remediations": ["<concrete required change>"],
  "summary": "<one paragraph>"
}"""

_CONFIDENCE_TAIL = (
    "- status is FAIL if any blocking_issue exists with severity Critical or High "
    "(more than 2 High = FAIL)\n"
    "- status is PASS if no Critical issues and at most 2 High issues and confidence >= 7"
)

_FINDING_KEYS = ("violations", "blocking_issues", "non_blocking_concerns")


def is_clean_pass(raw: dict) -> bool:
    """True if a single critic's raw result is status PASS with nothing to
    reconcile — schema-agnostic: keys absent for the other summary_style are
    simply not present, so checking all three finding-list keys is safe for
    either shape."""
    if raw.get("status") != "PASS":
        return False
    return not any(raw.get(key) for key in _FINDING_KEYS)


def all_clean_pass(raw_results: list[dict]) -> bool:
    """True if every critic in raw_results (each {'id', 'model', 'result'})
    independently reported a clean PASS — nothing for the harness to
    adjudicate, so reconciliation can be skipped."""
    return all(is_clean_pass(entry["result"]) for entry in raw_results)


def synthesize_trivial_pass(raw_results: list[dict], iteration: int, summary_style: str) -> dict:
    """Canonical-schema PASS result for the all-clean-pass case — no LLM
    reconciliation call needed since there is nothing to adjudicate."""
    ids = ", ".join(entry["id"] for entry in raw_results)
    summary = (
        f"{len(raw_results)} independent critics ({ids}) each reviewed independently "
        f"and reported a clean PASS with no findings — reconciliation skipped."
    )
    if summary_style == "confidence":
        return {
            "iteration": iteration,
            "status": "PASS",
            "confidence": 10,
            "blocking_issues": [],
            "non_blocking_concerns": [],
            "required_remediations": [],
            "summary": summary,
        }
    return {
        "iteration": iteration,
        "status": "PASS",
        "violations": [],
        "not_applicable": [],
        "summary": summary,
    }


def build_reconcile_prompt(
    context_block: str,
    raw_results: list[dict],
    iteration: int,
    summary_style: str,
    output_instructions: str = "",
) -> str:
    """
    Build the reconciliation subagent's prompt: N independent critics
    reviewed the same artifact against the same objective using different
    models; the reconciler (the main harness's own judgment, not another
    critic) must independently re-verify every finding, reject what it
    can't confirm, merge duplicates, and resolve disagreements itself —
    never trusting a finding merely because one or more critics reported it.

    context_block: the same constitution/architecture/spec/artifact
    assembly each gate's own critic prompt already builds.
    raw_results: list of {"id": str, "model": str, "result": dict} — each
    critic's raw, unverified JSON output.
    summary_style: selects the schema/status-rules block ("violations" or
    "confidence") that this gate's own critics use, so reconciliation output
    can never drift from what a single critic for this gate would produce.
    """
    schema, tail = (
        (_CONFIDENCE_SCHEMA, _CONFIDENCE_TAIL)
        if summary_style == "confidence"
        else (_VIOLATIONS_SCHEMA, _VIOLATIONS_TAIL)
    )
    schema = schema % {"iteration": iteration}
    if output_instructions:
        tail += f"\n{output_instructions}"

    findings_block = "\n\n".join(
        f"--- CRITIC '{entry['id']}' (model: {entry['model']}) ---\n"
        f"{json.dumps(entry['result'], indent=2)}"
        for entry in raw_results
    )

    return f"""You are the Reconciliation Agent for a spec-kit project.

{len(raw_results)} independent critics reviewed the SAME artifact against the SAME review
objective below, using different models for independent perspectives. Their raw,
unverified findings are attached, labeled by critic id and model.

Your job — and only yours, not any critic's — is to decide what is actually valid and
what change (if any) is required. A critic's job was to report findings only; it is
now your job to adjudicate them.

{context_block}

--- RAW CRITIC FINDINGS (iteration {iteration}) ---
{findings_block}

Adjudication rules:
1. For EVERY finding reported by ANY critic, independently re-verify it against the
   artifact/context above yourself. Do not treat a critic's own confidence, or the
   number of critics reporting it, as evidence it is correct.
2. REJECT any finding you cannot personally confirm by reading the cited location. A
   finding one critic alone reported is not automatically wrong; a finding every
   critic reported is not automatically right.
3. MERGE duplicate/overlapping findings (same underlying issue, different wording or
   cited location) into one entry.
4. Where critics disagree, resolve it yourself and record your own independent
   judgment — never keep a finding solely because "a critic said so."
5. Do not introduce a finding no critic reported.
6. You alone decide the final status/severity of each surviving finding.

Output ONLY valid JSON, no preamble, no markdown fences, in EXACTLY the same schema a
single critic for this gate would produce:
{schema}

Rules:
{tail}"""

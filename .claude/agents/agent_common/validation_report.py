"""Scenario extraction, report checking, and report rendering for ch_6_validate_auto.py.

Everything here is deterministic: the orchestrator decides which scenarios
must be covered and whether the validation agent's report.json actually
covers them with evidence on disk, rather than trusting the agent's own
claim that it validated everything."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

PASS = "PASS"  # noqa: S105 — a verdict, not a password
FAIL = "FAIL"
BLOCKED = "BLOCKED"
NOT_APPLICABLE = "NOT_APPLICABLE"
VERDICTS = (PASS, FAIL, BLOCKED, NOT_APPLICABLE)
NEEDS_ATTENTION = (FAIL, BLOCKED)
# A verdict reached through the project's probe commands instead of the
# browser; its evidence is the transcripts the harness wrote for each probe.
PROBE_METHOD = "probe"
PROBE_TRANSCRIPT_GLOB = "probe-*.txt"

COMMENT_MARKER_PREFIX = "<!-- ch-6-validate sha="

_USER_STORY_RE = re.compile(r"(?m)^#{2,4}\s*User Story\s+(\d+)\b.*$")
_ACCEPTANCE_HEADING_RE = re.compile(r"(?im)^\*\*Acceptance Scenarios\*\*:?\s*$")
_NUMBERED_RE = re.compile(r"^\s*(\d+)\.\s+(.+)$")
_SUCCESS_CRITERION_RE = re.compile(r"(?m)^\s*[-*]\s+\*\*(SC-\d+)\*\*:?\s*(.+)$")
_SECTION_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
_SOURCE_ISSUE_RE = re.compile(
    r"(?im)^\s*(?:\*\*Source Issue\*\*:\s*#(\d+)|Source:\s*GitHub issue\s+#(\d+))"
)
_SOURCED_FROM_RE = re.compile(r"(?i)\bSourced from GitHub issue\s+#(\d+)")


@dataclass(frozen=True)
class Scenario:
    id: str
    text: str

    @property
    def from_quickstart(self) -> bool:
        return self.id.startswith("QS-")


def _acceptance_scenarios(spec: str) -> list[Scenario]:
    scenarios = []
    stories = list(_USER_STORY_RE.finditer(spec))
    for i, story in enumerate(stories):
        end = stories[i + 1].start() if i + 1 < len(stories) else len(spec)
        body = spec[story.end() : end]
        heading = _ACCEPTANCE_HEADING_RE.search(body)
        if not heading:
            continue
        numbered: list[tuple[str, str]] = []
        for line in body[heading.end() :].splitlines():
            if not line.strip():
                continue
            match = _NUMBERED_RE.match(line)
            if match:
                numbered.append((match[1], match[2].strip()))
            elif numbered and line[0].isspace():
                number, text = numbered[-1]
                numbered[-1] = (number, f"{text} {line.strip()}")
            else:
                break
        scenarios += [Scenario(f"US{story[1]}-AS{n}", text) for n, text in numbered]
    return scenarios


def extract_scenarios(spec: str, quickstart: str | None) -> list[Scenario]:
    """Every scenario the validation agent must report on: each user story's
    numbered acceptance scenarios (US<n>-AS<m>), each success criterion
    (SC-NNN), and each top-level section of quickstart.md (QS-<n>)."""
    scenarios = _acceptance_scenarios(spec)
    scenarios += [Scenario(m[1], m[2].strip()) for m in _SUCCESS_CRITERION_RE.finditer(spec)]
    if quickstart:
        scenarios += [
            Scenario(f"QS-{n}", m[1]) for n, m in enumerate(_SECTION_RE.finditer(quickstart), 1)
        ]
    return list({s.id: s for s in scenarios}.values())


def source_issue(spec: str) -> int | None:
    """The GitHub issue a spec was created from, per the `**Source Issue**: #N`
    header line ch-specify-from-issue writes, or the older `Source: GitHub
    issue #N` trailer or mid-line `(Sourced from GitHub issue #N)` that specs
    written before that line existed still carry."""
    if match := _SOURCE_ISSUE_RE.search(spec):
        return int(match[1] or match[2])
    match = _SOURCED_FROM_RE.search(spec)
    return int(match[1]) if match else None


def recording_name(entry: dict) -> str:
    """The video an entry's evidence is in: its own `recording`, which several
    scenarios checking the same screen in the same data state may share, or
    `<id>.webm` by default."""
    return entry.get("recording") or f"{entry.get('id')}.webm"


def _recording_problem(label: str, entry: dict, run_dir: Path) -> str | None:
    name = recording_name(entry)
    if not isinstance(name, str) or Path(name).name != name or not name.endswith(".webm"):
        return f"{label}: recording {name!r} must be a .webm file name in the run directory"
    if not (run_dir / name).exists():
        return f"{label}: no recording at {run_dir / name}"
    return None


def _evidence_problems(label: str, entry: dict) -> list[str]:
    evidence = entry.get("evidence", [])
    if not isinstance(evidence, list):
        return [f"{label}: 'evidence' must be a list of file paths"]
    return [f"{label}: evidence file {e} does not exist" for e in evidence if not Path(e).exists()]


def load_report(path: Path) -> tuple[dict | None, str | None]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"{path} was not written"
    except (OSError, json.JSONDecodeError) as e:
        return None, f"{path} is not valid JSON: {e}"
    if isinstance(report, dict) and setup_problem(report):
        return report, None
    if not isinstance(report, dict) or not isinstance(report.get("scenarios"), list):
        return None, f'{path} must be an object with a "scenarios" list'
    return report, None


def setup_problem(report: dict) -> str:
    """Why the agent stopped before any scenario: the environment wasn't fit to
    validate on (blocked origins, a broken login, seeded data missing)."""
    return str(report.get("setup_problem") or "").strip()


def check_report(report: dict, scenarios: list[Scenario], run_dir: Path) -> list[str]:
    """Problems that make report unusable as evidence — empty when every
    scenario has exactly one well-formed entry, every setup step is recorded,
    and all of their evidence exists."""
    expected = {s.id: s for s in scenarios}
    problems = _setup_problems(report.get("setup", []), run_dir)
    seen = set()
    for entry in report["scenarios"]:
        if not isinstance(entry, dict):
            problems.append(f"non-object scenario entry: {entry!r}")
            continue
        sid = entry.get("id")
        if sid not in expected:
            problems.append(f"unknown scenario id {sid!r}")
            continue
        if sid in seen:
            problems.append(f"{sid}: reported more than once")
            continue
        seen.add(sid)
        problems += entry_problems(entry, expected[sid], run_dir)
    problems += [f"{sid}: missing from report" for sid in expected if sid not in seen]
    return problems


def _setup_problems(setup, run_dir: Path) -> list[str]:
    if not isinstance(setup, list):
        return ["'setup' must be a list of setup steps"]
    problems = []
    for n, step in enumerate(setup, 1):
        label = f"setup step {n}"
        if not isinstance(step, dict) or not step.get("recording"):
            problems.append(f"{label}: needs a 'recording'")
            continue
        if not str(step.get("created", "")).strip():
            problems.append(f"{label}: needs a 'created' description of the data")
        if problem := _recording_problem(label, step, run_dir):
            problems.append(problem)
        problems += _evidence_problems(label, step)
    return problems


def entry_problems(entry: dict, scenario: Scenario, run_dir: Path) -> list[str]:
    sid = scenario.id
    verdict = entry.get("verdict")
    if verdict not in VERDICTS:
        return [f"{sid}: verdict {verdict!r} is not one of {', '.join(VERDICTS)}"]
    if verdict == NOT_APPLICABLE and not scenario.from_quickstart:
        return [f"{sid}: only quickstart (QS-*) scenarios may be {NOT_APPLICABLE}"]
    if verdict in (NOT_APPLICABLE, BLOCKED) and not str(entry.get("reason", "")).strip():
        return [f"{sid}: a {verdict} verdict needs a non-empty reason"]
    if not str(entry.get("observed", "")).strip() and verdict in (PASS, FAIL):
        return [f"{sid}: a {verdict} verdict needs a non-empty 'observed' description"]
    problems = []
    if verdict in (PASS, FAIL) and entry.get("method") == PROBE_METHOD:
        if not probe_transcripts(entry, run_dir):
            problems.append(f"{sid}: a probe verdict needs a probe transcript as evidence")
    elif verdict in (PASS, FAIL) and (problem := _recording_problem(sid, entry, run_dir)):
        problems.append(problem)
    return problems + _evidence_problems(sid, entry)


def probe_transcripts(entry: dict, run_dir: Path) -> list[Path]:
    """The harness-written probe transcripts an entry cites as evidence."""
    evidence = entry.get("evidence")
    if not isinstance(evidence, list):
        return []
    root = run_dir.resolve()
    paths = [Path(e).resolve() for e in evidence if isinstance(e, str)]
    return [p for p in paths if p.parent == root and p.match(PROBE_TRANSCRIPT_GLOB) and p.exists()]


def needs_attention(report: dict) -> list[dict]:
    return [e for e in report["scenarios"] if e.get("verdict") in NEEDS_ATTENTION]


def blocked(report: dict) -> list[dict]:
    return [e for e in report["scenarios"] if e.get("verdict") == BLOCKED]


def comment_marker(sha: str) -> str:
    return f"{COMMENT_MARKER_PREFIX}{sha} -->"


def render_markdown(
    report: dict, scenarios: list[Scenario], sha: str, base_url: str, run_dir: Path
) -> str:
    entries = {e["id"]: e for e in report["scenarios"]}
    counts = {v: sum(1 for e in entries.values() if e["verdict"] == v) for v in VERDICTS}
    summary = ", ".join(f"{n} {v}" for v, n in counts.items() if n)
    rows = []
    for scenario in scenarios:
        entry = entries[scenario.id]
        detail = entry.get("reason") or entry.get("observed", "")
        rows.append(
            f"| {scenario.id} | {entry['verdict']} | {_cell(scenario.text)} | {_cell(detail)} "
            f"| {_evidence_cell(entry, run_dir)} |"
        )
    setup = "".join(
        f"- `{run_dir / step['recording']}`: {_cell(step['created'])}\n"
        for step in report.get("setup", [])
    )
    if setup:
        setup = f"Test data the agent created, recorded before the checks:\n\n{setup}\n"
    return (
        f"{comment_marker(sha)}\n"
        f"## Deployed validation — `{sha[:12]}`\n\n"
        f"Validated against {base_url} by ch-6-validate-auto: **{summary}**.\n\n"
        f"{setup}"
        "| Scenario | Verdict | Expected | Observed / reason | Recording or probe |\n"
        "|---|---|---|---|---|\n" + "\n".join(rows) + "\n\n"
        f"Recordings and traces are local to the machine that ran the stage, under "
        f"`{run_dir}/`. Review them before marking this PR ready for review.\n"
    )


def _evidence_cell(entry: dict, run_dir: Path) -> str:
    if entry.get("method") == PROBE_METHOD:
        transcripts = probe_transcripts(entry, run_dir)
        return "probe: " + ", ".join(f"`{p}`" for p in transcripts) if transcripts else "—"
    recording = run_dir / recording_name(entry)
    return f"`{recording}`" if recording.exists() else "—"


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")

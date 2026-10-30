"""Live pipeline progress for the Claude Code status line.

Each *-auto.py stage appends a JSON line to specs/<feature>/pipeline-status.log
whenever it moves to a new step (generating, a critic iteration, a revision, CI).
Run as a script, this module is the status line: it renders those entries plus
the stage completion markers as one line, e.g.

    012-foo  plan ✓ │ tasks ✓ │ test ▶ test quality review 2/3 · 14m │ implement · │ docs ⊘

    pipeline_status.py                         — print the status line
    pipeline_status.py install <settings.json> — set it as the project's statusLine

Stdlib only: the status line runs under the system python3, which may not have
the harness's dependencies installed."""

import contextlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

LOG_NAME = "pipeline-status.log"
STAGES = ("plan", "tasks", "test", "implement", "docs")
ESCALATED = "escalated"
PAUSED = "paused: usage limit"

_bound: tuple[Path, str] | None = None


def bind(spec_dir: Path, stage: str) -> None:
    """Direct this process's step() entries to spec_dir, tagged with stage."""
    global _bound
    _bound = (spec_dir, stage)
    step("starting")


def step(label: str, iteration: int | None = None, max_iterations: int | None = None) -> None:
    """Record that the bound stage is now on step label. A no-op when unbound,
    and never raises: a missed status update must not break a pipeline run."""
    if _bound is None:
        return
    spec_dir, stage = _bound
    entry = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pid": os.getpid(),
        "stage": stage,
        "step": label,
        "iteration": iteration,
        "max_iterations": max_iterations,
    }
    with contextlib.suppress(OSError):
        spec_dir.mkdir(parents=True, exist_ok=True)
        with (spec_dir / LOG_NAME).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def _last_entries(spec_dir: Path) -> dict[str, dict]:
    try:
        lines = (spec_dir / LOG_NAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    last = {}
    for line in lines:
        with contextlib.suppress(json.JSONDecodeError):
            entry = json.loads(line)
            if isinstance(entry, dict) and entry.get("stage") in STAGES:
                last[entry["stage"]] = entry
    return last


def _disabled_stages(project_dir: Path) -> set[str]:
    # Mirrors ollama.is_stage_enabled, which can't be imported here (not stdlib-only).
    try:
        stages = json.loads((project_dir / ".specify/pipeline.json").read_text()).get("stages")
    except (OSError, json.JSONDecodeError, AttributeError):
        return set()
    if not isinstance(stages, dict):
        return set()
    return {
        name
        for name, entry in stages.items()
        if isinstance(entry, dict) and not entry.get("enabled", True)
    }


def _alive(pid) -> bool:
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _elapsed(ts: str, now: datetime) -> str:
    try:
        started = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return ""
    seconds = max(0, int((now - started).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def _stage_cell(name: str, entry: dict | None, now: datetime) -> str:
    if entry is None:
        return f"{name} ·"
    label = entry.get("step", "")
    if label == ESCALATED:
        return f"{name} ⚠ escalated"
    if label == PAUSED:
        return f"{name} ⏸ paused (usage limit)"
    if not _alive(entry.get("pid")):
        return f"{name} ✗ stopped at {label}"
    if entry.get("iteration") is not None:
        label += f" {entry['iteration']}"
        if entry.get("max_iterations") is not None:
            label += f"/{entry['max_iterations']}"
    elapsed = _elapsed(entry.get("ts"), now)
    return f"{name} ▶ {label}" + (f" · {elapsed}" if elapsed else "")


def render(project_dir: Path, feature: str, now: datetime | None = None) -> str:
    """The status line for feature, or "" when it has no pipeline state yet."""
    spec_dir = project_dir / "specs" / feature
    if not spec_dir.is_dir():
        return ""
    entries = _last_entries(spec_dir)
    done = {
        name
        for number, name in enumerate(STAGES, 1)
        if (spec_dir / f"ch-{number}-{name}-auto-complete").exists()
    }
    if not entries and not done:
        return ""
    disabled = _disabled_stages(project_dir)
    now = now or datetime.now(UTC)
    cells = []
    for name in STAGES:
        if name in done:
            cells.append(f"{name} ✓")
        elif name in disabled:
            cells.append(f"{name} ⊘")
        else:
            cells.append(_stage_cell(name, entries.get(name), now))
    return f"{feature}  " + " │ ".join(cells)


def _current_branch(project_dir: Path) -> str | None:
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        return subprocess.run(
            ["git", "-C", str(project_dir), "symbolic-ref", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        ).stdout.strip()
    return None


_SCRIPT = ".claude/agents/agent_common/pipeline_status.py"
# notify.py's guard, for the same reason: the script is gitignored in installed
# projects, so a fresh clone that hasn't run install.sh doesn't have it.
STATUS_LINE = {
    "type": "command",
    "command": f'd="${{CLAUDE_PROJECT_DIR:-.}}"; [ ! -f "$d/{_SCRIPT}" ] || python3 "$d/{_SCRIPT}"',
    # Claude Code otherwise re-runs the command only on session events (e.g. a
    # new assistant message), which go quiet while it waits on a pipeline run.
    "refreshInterval": 5,
}


def install(path: Path) -> str:
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError as e:
        return f"SKIP {path.name}: not valid JSON ({e}); add the pipeline status line by hand"
    if not isinstance(settings, dict):
        return f"SKIP {path.name}: not a JSON object; add the pipeline status line by hand"
    current = settings.get("statusLine")
    if current == STATUS_LINE:
        return f"UNCHANGED {path.name}: pipeline status line"
    if current and not (isinstance(current, dict) and _SCRIPT in str(current.get("command"))):
        return f"SKIP {path.name}: project already has its own statusLine; pipeline status line not set"
    settings["statusLine"] = STATUS_LINE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return f"MERGE {path.name}: pipeline status line"


def main() -> None:
    if sys.argv[1:2] == ["install"]:
        print(install(Path(sys.argv[2])))
        return
    project_dir = Path(__file__).resolve().parents[3]
    if branch := _current_branch(project_dir):
        print(render(project_dir, branch))


if __name__ == "__main__":
    main()

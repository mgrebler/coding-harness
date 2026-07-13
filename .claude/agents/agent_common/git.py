"""Git helpers: branch/feature resolution, changed-file listing, and auto-commit delegation."""

import subprocess
import sys
from pathlib import Path


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


def run_auto_commit(event: str, agent_name: str):
    """Delegate commit to the speckit-git-commit script for the given event."""
    script = Path(".specify/extensions/git/scripts/bash/auto-commit.sh")
    if script.exists():
        subprocess.run(["bash", str(script), event], check=False)
    else:
        print(f"[{agent_name}] Warning: auto-commit.sh not found; skipping commit.", flush=True)


def get_changed_files() -> list[str]:
    """Return list of files changed on this branch relative to main."""
    result = subprocess.run(
        ["git", "diff", "main...HEAD", "--name-only"],
        capture_output=True,
        text=True,
    )
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]

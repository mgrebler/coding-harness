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


def resolve_base_ref(base_branch: str = "main") -> str:
    """Best-effort fetch origin/<base_branch> and return the freshest ref
    to diff/merge-base against for "what changed on this branch" checks —
    'origin/<base_branch>' when fetchable/resolvable, else local
    <base_branch>. A stale local main (never fetched/fast-forwarded)
    otherwise silently widens a diff against it with commits merged
    upstream but not yet pulled locally. The fetch is best-effort — an
    offline environment or a repo without an 'origin' remote falls through
    to base_branch unchanged rather than raising."""
    subprocess.run(["git", "fetch", "origin", base_branch], capture_output=True, text=True)
    origin_ref = f"origin/{base_branch}"
    return origin_ref if _ref_exists(origin_ref) else base_branch


def _ref_exists(ref: str) -> bool:
    return (
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref], capture_output=True, text=True
        ).returncode
        == 0
    )


def get_changed_files() -> list[str]:
    """Return list of files changed on this branch relative to main."""
    base_ref = resolve_base_ref()
    result = subprocess.run(
        ["git", "diff", f"{base_ref}...HEAD", "--name-only"],
        capture_output=True,
        text=True,
    )
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]

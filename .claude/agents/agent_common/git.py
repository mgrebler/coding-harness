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


def dirty_files() -> list[str]:
    """Return every currently dirty path — staged, unstaged, or untracked —
    via `git status --porcelain`. Used to snapshot a baseline before a stage
    does any work, so that stage's final auto-commit can tell its own new
    changes apart from pre-existing drift that was already sitting dirty in
    the working tree for unrelated reasons. See FOLLOWUP_HARNESS.md Bug 4."""
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    paths = []
    for line in result.stdout.splitlines():
        path = line[3:]
        if " -> " in path:  # rename: "old -> new"
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip('"'))
    return paths


def run_auto_commit(event: str, agent_name: str, exclude: list[str] | None = None):
    """Delegate commit to the speckit-git-commit script for the given event.

    exclude (optional): paths to leave out of this commit — typically a
    baseline of files that were already dirty before the current stage
    started touching anything, so unrelated pre-existing drift doesn't get
    silently swept into this feature's commit history alongside its actual
    changes. See FOLLOWUP_HARNESS.md Bug 4."""
    script = Path(".specify/extensions/git/scripts/bash/auto-commit.sh")
    if not script.exists():
        print(f"[{agent_name}] Warning: auto-commit.sh not found; skipping commit.", flush=True)
        return
    cmd = ["bash", str(script), event]
    if exclude:
        for path in exclude:
            print(
                f"[{agent_name}] Note: leaving pre-existing dirty path out of this commit "
                f"(it was already dirty before this stage started): {path}",
                flush=True,
            )
        cmd.append("--exclude")
        cmd.extend(exclude)
    subprocess.run(cmd, check=False)


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

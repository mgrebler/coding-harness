"""File I/O helpers: reading/writing spec files and changed-file content."""

import sys
from collections.abc import Callable
from pathlib import Path

from agent_common import git


def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_optional(path: Path, fallback: str) -> str:
    """Read path as UTF-8 text if it exists, else return fallback."""
    return path.read_text(encoding="utf-8") if path.exists() else fallback


MEMORY_DIR = Path(".specify/memory")
CONSTITUTION_PATH = MEMORY_DIR / "constitution.md"


def read_memory(name: str) -> str:
    """An optional .specify/memory/<name>.md document, or a placeholder saying
    it wasn't found."""
    return read_optional(MEMORY_DIR / f"{name}.md", f"({name}.md not found)")


def read_required(owner: str, *paths: Path) -> list[str]:
    """The contents of paths, exiting via require_files if any is missing."""
    require_files(owner, *paths)
    return [read_file(p) for p in paths]


def require_files(name: str, *paths: Path) -> None:
    """Exit(1) with a standard error message if any of paths is missing. Used by standalone critic scripts."""
    for p in paths:
        if not p.exists():
            print(f"[{name}] ERROR: required file not found: {p}", flush=True)
            sys.exit(1)


def require_spec_files(log_fn, spec_dir: Path, *filenames: str) -> None:
    """Exit(1) via log_fn if any of filenames is missing from spec_dir. Used by *-auto.py preflight checks."""
    for f in filenames:
        if not (spec_dir / f).exists():
            log_fn(f"ERROR: {spec_dir}/{f} not found. Cannot proceed.")
            sys.exit(1)


_PLAN_SIBLINGS = ("research.md", "data-model.md", "quickstart.md")


def read_plan_bundle(spec_dir: Path) -> str:
    """plan.md followed by its sibling plan artifacts. plan.md routinely defers
    detail to them ("see research.md §4"), so a critic shown plan.md alone
    reports gaps those files already answer (specs/006's local critics did)."""
    parts = [read_file(spec_dir / "plan.md")]
    for name in _PLAN_SIBLINGS:
        path = spec_dir / name
        if path.exists():
            parts.append(f"--- {name} (sibling plan artifact) ---\n{read_file(path)}")
    return "\n\n".join(parts)


def read_changed_source_files(changed_files: list[str]) -> str:
    """Read the contents of changed_files, skipping specs/ paths and critic result files."""
    content_parts = []
    for path_str in changed_files:
        if path_str.startswith("specs/") or "-result-" in path_str:
            continue
        p = Path(path_str)
        if not p.exists():
            continue
        try:
            content_parts.append(f"--- {path_str} ---\n{p.read_text(encoding='utf-8')}")
        except Exception:
            content_parts.append(f"--- {path_str} --- (could not read)")
    return "\n\n".join(content_parts) if content_parts else "(no changed files found)"


class NoChangedFilesError(Exception):
    """read_changed_files matched nothing — a critic given this input would
    review an empty diff and PASS it (specs/082: all four test-stage critics
    passed on "no test files were changed")."""


def read_changed_files(
    changed_files: list[str],
    dirs: tuple[str, ...],
    extra_match: Callable[[str], bool] | None = None,
    diff_only: bool = False,
) -> str:
    """Read files in changed_files that start with any prefix in dirs, or
    (when extra_match is given) satisfy extra_match — a safety net for a
    changed file outside every resolved dir that still looks like a match by
    some other signal (e.g. project_conventions.looks_like_test_file), so a
    directory resolution gap doesn't silently drop it from the result. See
    FOLLOWUP_HARNESS.md Bug 2. diff_only shows a file that already existed on
    main as its branch diff rather than in full — a prompt-size reduction for
    features that touch many large existing files (specs/084, a rename).
    Raises NoChangedFilesError if nothing matches."""
    sections = []
    for path_str in changed_files:
        if not any(path_str.startswith(d) for d in dirs) and not (
            extra_match and extra_match(path_str)
        ):
            continue
        p = Path(path_str)
        if not p.exists():
            continue
        diff = git.branch_diff(path_str) if diff_only else None
        if diff is not None:
            sections.append(f"--- {path_str} (diff against main) ---\n{diff}")
            continue
        try:
            content = p.read_text(encoding="utf-8")
            sections.append(f"--- {path_str} ---\n{content}")
        except Exception:
            sections.append(f"--- {path_str} --- (could not read)")
    if not sections:
        raise NoChangedFilesError(
            f"no changed files under {', '.join(dirs) or '(no resolved dirs)'} — check the "
            "'Test file location' bullets in constitution.md"
        )
    return "\n\n".join(sections)


def read_directory_tree(root: Path) -> dict[str, str]:
    """Read all files under root recursively, keyed by path relative to root (posix-style,
    sorted). Returns {} if root doesn't exist (e.g. docs/ not yet created)."""
    if not root.exists():
        return {}
    tree: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            try:
                tree[rel] = p.read_text(encoding="utf-8")
            except Exception:
                tree[rel] = "(could not read)"
    return tree


def format_directory_tree(tree: dict[str, str], empty_message: str = "(no files)") -> str:
    """Format a {relative_path: content} mapping (from read_directory_tree) into a
    prompt-ready block of --- path --- sections."""
    if not tree:
        return empty_message
    return "\n\n".join(f"--- {path} ---\n{content}" for path, content in tree.items())

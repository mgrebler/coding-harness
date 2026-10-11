"""File I/O helpers: reading/writing spec files and changed-file content."""

import sys
from collections.abc import Callable
from pathlib import Path


def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def read_optional(path: Path, fallback: str) -> str:
    """Read path as UTF-8 text if it exists, else return fallback."""
    return path.read_text(encoding="utf-8") if path.exists() else fallback


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


def read_changed_files(
    changed_files: list[str],
    dirs: tuple[str, ...],
    extra_match: Callable[[str], bool] | None = None,
) -> str:
    """Read files in changed_files that start with any prefix in dirs, or
    (when extra_match is given) satisfy extra_match — a safety net for a
    changed file outside every resolved dir that still looks like a match by
    some other signal (e.g. project_conventions.looks_like_test_file), so a
    directory resolution gap doesn't silently drop it from the result. See
    FOLLOWUP_HARNESS.md Bug 2."""
    sections = []
    for path_str in changed_files:
        if not any(path_str.startswith(d) for d in dirs) and not (
            extra_match and extra_match(path_str)
        ):
            continue
        p = Path(path_str)
        if not p.exists():
            continue
        try:
            content = p.read_text(encoding="utf-8")
            sections.append(f"--- {path_str} ---\n{content}")
        except Exception:
            sections.append(f"--- {path_str} --- (could not read)")
    return (
        "\n\n".join(sections) if sections else "(no changed files found in specified directories)"
    )

#!/usr/bin/env bash
# install.sh — Install or update the coding-harness into a target project.
#
# Usage:
#   ./install.sh <target-dir> [--dry-run]
#
# <target-dir>  Root of the project to install into.
#
# --dry-run     Print what would be done without making any changes.
#
# The harness source directory is always wherever this script lives (a
# standalone clone, or a .coding-harness/ subtree inside a project).
#
# To update an existing project, pull the harness repo and re-run this script:
#   cd /path/to/coding-harness && git pull
#   ./install.sh /path/to/project
#
# Behaviour:
#   ALWAYS OVERWRITES (harness-managed — no project customisation):
#     .claude/agents/, .claude/skills/
#     .specify/extensions/, .specify/scripts/, .specify/templates/, .specify/workflows/
#     .devcontainer/Dockerfile, .devcontainer/entrypoint.sh
#
#   WRITES ONLY IF FILE DOES NOT EXIST (project-initialised — customise after first install):
#     .specify/memory/constitution.md
#     .specify/memory/architecture.md
#     .specify/memory/architecture-principles.md
#     .specify/memory/code-quality-principles.md
#     .specify/memory/test-principles.md
#     .specify/memory/product-context.md
#     docker-compose.yml
#     .devcontainer/devcontainer.json
#     CLAUDE.md

set -euo pipefail

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------

DRY_RUN=false
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR=""

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    -*) echo "Unknown flag: $arg" >&2; exit 1 ;;
    *)
      if [[ -z "$TARGET_DIR" ]]; then TARGET_DIR="$arg"
      else echo "Too many positional arguments." >&2; exit 1
      fi
      ;;
  esac
done

if [[ -z "$TARGET_DIR" ]]; then
  echo "Usage: $0 <target-dir> [--dry-run]" >&2
  exit 1
fi

TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[dry-run] SOURCE: $SOURCE_DIR"
  echo "[dry-run] TARGET: $TARGET_DIR"
  echo ""
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() { echo "$1"; }

# Copy a file or directory, always overwriting. Creates parent dirs.
always_copy() {
  local src="$SOURCE_DIR/$1"
  local dst="$TARGET_DIR/$1"

  if [[ ! -e "$src" ]]; then
    log "  SKIP (not in source): $1"
    return
  fi

  if [[ "$DRY_RUN" == "true" ]]; then
    log "  OVERWRITE: $1"
    return
  fi

  mkdir -p "$(dirname "$dst")"
  if [[ -d "$src" ]]; then
    rm -rf "$dst"
    cp -r "$src" "$dst"
  else
    cp "$src" "$dst"
  fi
  log "  OVERWRITE: $1"
}

# Write/replace the coding-harness-managed section in the project's .gitignore.
manage_gitignore() {
  local gitignore="$TARGET_DIR/.gitignore"
  local begin_marker="# BEGIN coding-harness-managed"

  if [[ "$DRY_RUN" == "true" ]]; then
    log "  UPDATE .gitignore: coding-harness-managed section"
    return
  fi

  touch "$gitignore"

  # Strip existing section, then remove any trailing blank lines it leaves behind
  if grep -qF "$begin_marker" "$gitignore"; then
    local tmp
    tmp=$(mktemp)
    awk '/# BEGIN coding-harness-managed/{skip=1} !skip{print} /# END coding-harness-managed/{skip=0}' "$gitignore" > "$tmp"
    # Strip trailing blank lines: only print up to the last non-empty line
    awk 'NF{found=NR} {lines[NR]=$0} END{for(i=1;i<=found;i++) print lines[i]}' "$tmp" > "$gitignore"
    rm -f "$tmp"
  fi

  # Add a single blank line separator only if the file already has content
  [[ -s "$gitignore" ]] && echo >> "$gitignore"

  # Append updated section (no leading blank line — separator is added above)
  cat >> "$gitignore" <<'GITIGNORE'
# BEGIN coding-harness-managed
# Managed by coding-harness/install.sh — do not edit this section manually.
# Re-run install.sh after updating the harness to keep this section current.

# .claude/agents — individual files so project-specific agents can coexist
# .claude/agents/agent_common/ — fully harness-owned package, no project customisation expected inside
.claude/agents/agent_common/
.claude/agents/ch-4-implement-auto.py
.claude/agents/ch_4_implement_critic.py
.claude/agents/ch-1-plan-auto.py
.claude/agents/ch_1_plan_critic.py
.claude/agents/ch-plan-to-implement-auto.py
.claude/agents/semble-search.md
.claude/agents/ch-2-tasks-auto.py
.claude/agents/ch_2_tasks_critic.py
.claude/agents/ch-3-test-auto.py
.claude/agents/ch_3_test_critic.py

# .claude/skills — individual skill dirs so project-specific skills can coexist
.claude/skills/ch-1-plan-architecture-review/
.claude/skills/ch-4-implement-code-quality-review/
.claude/skills/speckit-analyze/
.claude/skills/speckit-checklist/
.claude/skills/speckit-clarify/
.claude/skills/speckit-constitution/
.claude/skills/speckit-git-commit/
.claude/skills/speckit-git-feature/
.claude/skills/speckit-git-initialize/
.claude/skills/speckit-git-remote/
.claude/skills/speckit-git-validate/
.claude/skills/speckit-implement/
.claude/skills/ch-4-implement-auto/
.claude/skills/ch-4-implement-critic/
.claude/skills/speckit-plan/
.claude/skills/ch-1-plan-auto/
.claude/skills/ch-1-plan-critic/
.claude/skills/ch-plan-to-implement-auto/
.claude/skills/speckit-specify/
.claude/skills/speckit-tasks/
.claude/skills/ch-2-tasks-auto/
.claude/skills/ch-2-tasks-critic/
.claude/skills/speckit-taskstoissues/
.claude/skills/ch-3-test/
.claude/skills/ch-3-test-auto/
.claude/skills/ch-3-test-critic/

# .specify subdirs — fully harness-owned, no project files expected here
.specify/extensions/
.specify/scripts/
.specify/templates/
.specify/workflows/

# .devcontainer — specific files only (devcontainer.json stays tracked)
.devcontainer/Dockerfile
.devcontainer/entrypoint.sh
# END coding-harness-managed
GITIGNORE

  log "  UPDATE .gitignore: coding-harness-managed section"
}

# Copy a file from examples/, but only if it doesn't already exist in the target.
init_copy() {
  local src="$SOURCE_DIR/examples/$1"
  local dst="$TARGET_DIR/$1"

  if [[ ! -e "$src" ]]; then
    log "  SKIP (not in source examples): $1"
    return
  fi

  if [[ -e "$dst" ]]; then
    log "  EXISTS (skipping): $1"
    return
  fi

  if [[ "$DRY_RUN" == "true" ]]; then
    log "  INIT: $1"
    return
  fi

  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  log "  INIT: $1"
}

# ---------------------------------------------------------------------------
# Harness-managed files (always overwrite)
# ---------------------------------------------------------------------------

log "==> Harness-managed files (always overwritten)"

always_copy ".claude/agents"
always_copy ".claude/skills"
always_copy ".specify/extensions"
always_copy ".specify/scripts"
always_copy ".specify/templates"
always_copy ".specify/workflows"
always_copy ".devcontainer/Dockerfile"
always_copy ".devcontainer/entrypoint.sh"

manage_gitignore

# ---------------------------------------------------------------------------
# Project-initialised files (only written if absent)
# ---------------------------------------------------------------------------

log ""
log "==> Project-initialised files (written once, never overwritten)"

init_copy ".specify/memory/constitution.md"
init_copy ".specify/memory/architecture.md"
init_copy ".specify/memory/architecture-principles.md"
init_copy ".specify/memory/code-quality-principles.md"
init_copy ".specify/memory/test-principles.md"
init_copy ".specify/memory/product-context.md"
init_copy "docker-compose.yml"
init_copy ".devcontainer/devcontainer.json"
init_copy "CLAUDE.md"

# ---------------------------------------------------------------------------
# Post-install reminders
# ---------------------------------------------------------------------------

log ""
log "==> Done."
log ""
log "Reminders:"
if [[ -f "$TARGET_DIR/.specify/memory/constitution.md" && "$DRY_RUN" == "false" ]]; then
  log "  1. Customise .specify/memory/constitution.md — fill in [PROJECT: ...] placeholders"
  log "  2. Customise .specify/memory/architecture.md and product-context.md"
  log "  3. Customise docker-compose.yml — replace <project-name> with your project name"
fi

#!/usr/bin/env bash
# install.sh — Install or update the coding-harness into a target project.
#
# Usage:
#   ./install.sh <source-dir> <target-dir> [--dry-run]
#
# <source-dir>  Root of the coding-harness repo (or a .coding-harness/ subtree inside a project).
# <target-dir>  Root of the project to install into.
#
# --dry-run     Print what would be done without making any changes.
#
# To update an existing project, pull the harness repo and re-run this script:
#   cd /path/to/coding-harness && git pull
#   ./install.sh /path/to/coding-harness /path/to/project
#
# Behaviour:
#   ALWAYS OVERWRITES (harness-managed — no project customisation):
#     .claude/agents/, .claude/hooks/, .claude/skills/
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
SOURCE_DIR=""
TARGET_DIR=""

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    -*) echo "Unknown flag: $arg" >&2; exit 1 ;;
    *)
      if [[ -z "$SOURCE_DIR" ]]; then SOURCE_DIR="$arg"
      elif [[ -z "$TARGET_DIR" ]]; then TARGET_DIR="$arg"
      else echo "Too many positional arguments." >&2; exit 1
      fi
      ;;
  esac
done

if [[ -z "$SOURCE_DIR" || -z "$TARGET_DIR" ]]; then
  echo "Usage: $0 <source-dir> <target-dir> [--dry-run]" >&2
  exit 1
fi

SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
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
always_copy ".claude/hooks/post-commit"
always_copy ".claude/skills"
always_copy ".specify/extensions"
always_copy ".specify/scripts"
always_copy ".specify/templates"
always_copy ".specify/workflows"
always_copy ".devcontainer/Dockerfile"
always_copy ".devcontainer/entrypoint.sh"

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
log "  1. Copy the git hook:  cp .claude/hooks/post-commit .git/hooks/post-commit && chmod +x .git/hooks/post-commit"
if [[ -f "$TARGET_DIR/.specify/memory/constitution.md" && "$DRY_RUN" == "false" ]]; then
  log "  2. Customise .specify/memory/constitution.md — fill in [PROJECT: ...] placeholders"
  log "  3. Customise .specify/memory/architecture.md and product-context.md"
  log "  4. Customise docker-compose.yml — replace <project-name> with your project name"
fi

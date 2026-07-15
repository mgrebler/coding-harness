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
#
#   WRITES ONLY IF FILE DOES NOT EXIST (project-initialised — customise after first install):
#     .specify/memory/constitution.md
#     .specify/memory/architecture.md
#     .specify/memory/architecture-principles.md
#     .specify/memory/code-quality-principles.md
#     .specify/memory/test-principles.md
#     .specify/memory/product-context.md
#     docker-compose.yml
#     .devcontainer/Dockerfile
#     .devcontainer/devcontainer.json
#     CLAUDE.md
#
#   MANAGED SECTION REFRESHED EVERY RUN (rest of the file stays project-owned):
#     .gitignore
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

# Strip any existing managed section (delimited by begin/end marker lines)
# from a file and append a fresh one built from the given body. Creates the
# file if absent. Idempotent — safe to re-run.
write_managed_section() {
  local file="$1" begin_marker="$2" end_marker="$3" body="$4"

  touch "$file"

  # Strip existing section, then remove any trailing blank lines it leaves behind
  if grep -qF "$begin_marker" "$file"; then
    local tmp
    tmp=$(mktemp)
    awk -v b="$begin_marker" -v e="$end_marker" \
      '$0==b{skip=1} !skip{print} $0==e{skip=0}' "$file" > "$tmp"
    # Strip trailing blank lines: only print up to the last non-empty line
    awk 'NF{found=NR} {lines[NR]=$0} END{for(i=1;i<=found;i++) print lines[i]}' "$tmp" > "$file"
    rm -f "$tmp"
  fi

  # Add a single blank line separator only if the file already has content
  [[ -s "$file" ]] && echo >> "$file"

  # Append updated section (no leading blank line — separator is added above)
  { printf '%s\n' "$begin_marker"; printf '%s\n' "$body"; printf '%s\n' "$end_marker"; } >> "$file"
}

# Write/replace the coding-harness-managed section in the project's .gitignore.
manage_gitignore() {
  local gitignore="$TARGET_DIR/.gitignore"

  if [[ "$DRY_RUN" == "true" ]]; then
    log "  UPDATE .gitignore: coding-harness-managed section"
    return
  fi

  local body
  body=$(cat <<'GITIGNORE'
# Managed by coding-harness/install.sh — do not edit this section manually.
# Re-run install.sh after updating the harness to keep this section current.

# .claude/agents — matched by naming convention so new ch-N-* agent files are
# picked up automatically without editing this list; project-specific agents
# using any other filename still coexist un-ignored
# .claude/agents/agent_common/ — fully harness-owned package, no project customisation expected inside
.claude/agents/agent_common/
.claude/agents/ch_*.py
.claude/agents/semble-search.md

# .claude/skills — harness-owned skill dirs, matched by naming convention so new
# ch-N-*/speckit-* skills are picked up automatically without editing this list
.claude/skills/ch-*/
.claude/skills/speckit-*/

# .specify subdirs — fully harness-owned, no project files expected here
.specify/extensions/
.specify/scripts/
.specify/templates/
.specify/workflows/
GITIGNORE
)

  write_managed_section "$gitignore" "# BEGIN coding-harness-managed" "# END coding-harness-managed" "$body"

  log "  UPDATE .gitignore: coding-harness-managed section"
}

# Write/replace the coding-harness-managed section in the project's CLAUDE.md.
manage_claude_md() {
  local claude_md="$TARGET_DIR/CLAUDE.md"

  if [[ "$DRY_RUN" == "true" ]]; then
    log "  UPDATE CLAUDE.md: coding-harness-managed section"
    return
  fi

  local body
  body=$(cat <<'CLAUDEMD'
## Spec-Driven Development

This project follows the coding-harness spec-driven pipeline by default:
`specify → plan → tasks → test → implement`. New features and non-trivial
changes should go through this pipeline (starting with `/speckit-specify`)
rather than being implemented directly, unless the user explicitly asks to
skip it. Full governance rules are in `.specify/memory/constitution.md`.

Managed by coding-harness/install.sh — do not edit this section manually.
Re-run install.sh after updating the harness to keep this section current.
CLAUDEMD
)

  write_managed_section "$claude_md" "<!-- BEGIN coding-harness-managed -->" "<!-- END coding-harness-managed -->" "$body"

  log "  UPDATE CLAUDE.md: coding-harness-managed section"
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
init_copy ".devcontainer/Dockerfile"
init_copy ".devcontainer/devcontainer.json"
init_copy "CLAUDE.md"

manage_claude_md

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

if [[ "$DRY_RUN" == "false" && -f "$TARGET_DIR/.devcontainer/Dockerfile" ]] \
   && ! grep -q "^FROM ghcr.io/" "$TARGET_DIR/.devcontainer/Dockerfile" 2>/dev/null; then
  log "  4. .devcontainer/Dockerfile is now a project-owned template (FROM ghcr.io/.../coding-harness-base)"
  log "     instead of a harness-managed file. Your existing Dockerfile predates this change and is no"
  log "     longer gitignored — replace it with the new template or 'git add' your customised version."
fi

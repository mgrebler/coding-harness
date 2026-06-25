# coding-harness

A spec-driven coding harness built on Claude Code and SpecKit. Features go through a structured pipeline — specification → planning → tasks → tests → implementation — with AI-generated artifacts at each stage and optional human review gates.

---

## Installing into a New Project

```bash
git clone <harness-repo-url> coding-harness
coding-harness/install.sh coding-harness/ /path/to/your-project/
```

Then:

```bash
cd /path/to/your-project

# Install the git hook
cp .claude/hooks/post-commit .git/hooks/post-commit
chmod +x .git/hooks/post-commit

# Customise the project-specific files
#   .specify/memory/constitution.md   — fill in [PROJECT: ...] placeholders
#   .specify/memory/architecture.md   — describe your system architecture
#   .specify/memory/product-context.md — describe your product and users
#   docker-compose.yml                — replace <project-name> with your project name

# Add your project's Playwright browser install to .devcontainer/Dockerfile
# (see the comment block in that file)
```

### Updating an Existing Project

```bash
# From inside the project:
./scripts/update-harness.sh
```

This re-runs `install.sh` from the pinned source directory. Harness-managed files are overwritten; project-initialised files (constitution, architecture, etc.) are never touched.

### Migrating to Git Subtree (Optional)

If you later want harness updates tracked in git:

```bash
git subtree add --prefix=.coding-harness <harness-repo-url> main
```

Then update `scripts/update-harness.sh` to:

```bash
git subtree pull --prefix=.coding-harness <harness-repo-url> main
.coding-harness/install.sh .coding-harness/ .
```

`install.sh` itself needs no changes — it already accepts a source directory parameter.

---

## Workflow Overview

Every feature follows the same pipeline:

```
specify → plan → tasks → test → implement → PR
```

Two modes are supported:

| Mode | When to use |
|------|-------------|
| **Human-in-the-loop** | Review and approve each stage; the next stage starts automatically after each approval |
| **Fully automatic** | Let Claude run the entire plan → tasks → test → implement pipeline after you've approved the spec |

Before running any SpecKit command, start a Claude Code session inside Docker:

```bash
UID=$(id -u) GID=$(id -g) docker compose run --rm dev
# Inside the container:
claude
```

---

## One-Time Hook Setup

The harness uses a post-commit git hook to automatically launch the next pipeline stage after each approval commit. Install it once per clone:

```bash
cp .claude/hooks/post-commit .git/hooks/post-commit
chmod +x .git/hooks/post-commit
```

Once installed, you never need to manually invoke the `*-auto` commands — each approval commit fires the right one in the background.

---

## Human-in-the-Loop Workflow

You are the gatekeeper at each stage. After you approve, the hook automatically starts the next stage as a background process so it's ready for your next review.

### Step 1 — Specify

```
/speckit-specify <feature description>
```

- Creates a numbered feature branch (`NNN-feature-name`)
- Writes `specs/NNN-feature/spec.md` from your description
- Runs a quality checklist; asks for clarification (max 3 questions) if needed

**Review** `specs/NNN-feature/spec.md`. Edit it directly or give Claude feedback.

### Step 2 — Approve the spec

```
/speckit-spec-approved
```

Creates and commits `specs/NNN-feature/spec-approved`. The post-commit hook fires `plan-auto` in the background.

### Step 3 — Review the plan

Wait for `specs/NNN-feature/plan.md` to appear, then review it.

### Step 4 — Approve the plan

```
/speckit-plan-approved
```

Commits `specs/NNN-feature/plan-approved`. The hook fires `tasks-auto` in the background.

### Step 5 — Review the tasks

Wait for `specs/NNN-feature/tasks.md` to appear, then review the dependency-ordered list of `[TEST]` / `[IMPL]` task pairs.

### Step 6 — Approve tasks

```
/speckit-tasks-approved
```

Commits `specs/NNN-feature/tasks-approved`. The hook fires `test-auto` in the background.

### Step 7 — Review the tests

Wait for the test files and `specs/NNN-feature/test-results/` to appear, then review the failing tests.

### Step 8 — Approve tests

```
/speckit-test-approved
```

Commits `specs/NNN-feature/test-approved`. The hook fires `implement-auto` in the background.

### Step 9 — Review the implementation

When implementation is complete, review the code changes and verify CI passes locally. Then open a PR. **The merge is always a human action.**

---

## Fully Automatic Mode

After specifying the feature, run the entire plan → tasks → test → implement pipeline unattended:

```
/speckit-specify <feature description>
```

Review and edit `specs/NNN-feature/spec.md` if needed, then:

```
/speckit-plan-to-implement-auto
```

This chains four stages — plan, tasks, test, implement — with built-in critic loops at each stage.

---

## Without the Hook (Manual Mode)

```
/speckit-specify <description>
# review spec.md
/speckit-spec-approved
/speckit-plan          # or /speckit-plan-auto for automatic critic loop
# review plan.md
/speckit-plan-approved
/speckit-tasks         # or /speckit-tasks-auto
# review tasks.md
/speckit-tasks-approved
/speckit-test          # or /speckit-test-auto
# review test files
/speckit-test-approved
/speckit-implement     # or /speckit-implement-auto
```

---

## Quick Reference

| Command | What it does |
|---------|-------------|
| `/speckit-specify <desc>` | Create spec + feature branch |
| `/speckit-spec-approved` | Approve spec → hook fires `plan-auto` |
| `/speckit-plan` | Generate implementation plan (manual) |
| `/speckit-plan-auto` | Generate plan with automatic critic loop |
| `/speckit-plan-approved` | Approve plan → hook fires `tasks-auto` |
| `/speckit-tasks` | Generate task list (manual) |
| `/speckit-tasks-auto` | Generate tasks with automatic critic loop |
| `/speckit-tasks-approved` | Approve tasks → hook fires `test-auto` |
| `/speckit-test` | Write failing tests for all `[TEST]` tasks (manual) |
| `/speckit-test-auto` | Write tests with automatic critic loop |
| `/speckit-test-approved` | Approve tests → hook fires `implement-auto` |
| `/speckit-implement` | Implement all tasks (manual) |
| `/speckit-implement-auto` | Implement with automatic critic loop |
| `/speckit-plan-to-implement-auto` | Full pipeline: plan → tasks → test → implement |

---

## Local LLM Support (Optional)

Critic passes can optionally run against a local [Ollama](https://ollama.com) instance instead of Claude. Edit `.specify/local-llm.json` in your project root:

```json
{
  "ollama_url": "http://host.docker.internal:11434",
  "default": { "enabled": false, "model": "" },
  "critics": {
    "plan":      { "enabled": true,  "model": "qwen3:30b-a3b" },
    "tasks":     { "enabled": false, "model": "" },
    "implement": { "enabled": false, "model": "" },
    "test":      { "enabled": false, "model": "" }
  }
}
```

---

## Key Rules

- **No agent merges to `main`** — the merge is always a human action after PR review and CI pass.
- **TDD is mandatory** — failing tests must be written before implementation code.
- **One feature at a time** — each feature has its own branch and `specs/NNN-feature/` directory.

---

## What This Repo Contains

```
speckit/
├── .claude/
│   ├── agents/             # Python orchestrators (plan-auto, tasks-auto, etc.)
│   ├── hooks/post-commit   # Git hook that fires the next pipeline stage on approval
│   └── skills/             # All /speckit-* slash commands
├── .specify/
│   ├── extensions/         # Git integration scripts
│   ├── memory/             # Generic critic quality bars (architecture, code, test principles)
│   ├── scripts/            # Bash helpers
│   ├── templates/          # Spec/plan/tasks/constitution templates
│   └── workflows/          # Workflow registry
├── .devcontainer/
│   ├── Dockerfile          # Base dev container (Claude Code + Python SDK + system deps)
│   └── entrypoint.sh       # Docker socket + user management
├── examples/               # Templates for project-specific files (written once, never updated)
│   ├── docker-compose.yml
│   ├── CLAUDE.md
│   ├── .devcontainer/devcontainer.json
│   └── .specify/memory/
│       ├── constitution.md     # Annotated template — fill in project-specific sections
│       ├── architecture.md     # Structural template
│       └── product-context.md  # Product vision template
└── install.sh              # Install/update script
```

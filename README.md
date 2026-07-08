# coding-harness

A spec-driven coding harness built on Claude Code and SpecKit. Features go through a structured pipeline — specification → planning → tasks → tests → implementation — with AI-generated artifacts at each stage and optional human review gates.

---

## Setup

Clone the harness once and keep it somewhere on your machine:

```bash
git clone <harness-repo-url> coding-harness
```

### Installing into a New Project

```bash
coding-harness/install.sh /path/to/your-project/
```

Then customise the project-initialised files:

```bash
cd /path/to/your-project

# Fill in the project-specific placeholders
#   .specify/memory/constitution.md   — fill in [PROJECT: ...] sections
#   .specify/memory/architecture.md   — describe your system architecture
#   .specify/memory/product-context.md — describe your product and users
#   docker-compose.yml                — replace <project-name>

# Add your Playwright browser install to .devcontainer/Dockerfile
# (see the comment block in that file)
```

### Updating an Existing Project

Pull the harness, then re-run the install script against the project. Harness-managed files are overwritten; project-initialised files are never touched.

```bash
cd /path/to/coding-harness && git pull
./install.sh /path/to/your-project
```

### Migrating to Git Subtree (Optional)

If you want the harness pinned inside the project repo so updates are tracked in git:

```bash
git subtree add --prefix=.coding-harness <harness-repo-url> main
```

To update:

```bash
git subtree pull --prefix=.coding-harness <harness-repo-url> main
.coding-harness/install.sh .
```

`install.sh` needs no changes — it infers its source from its own location, so this works whether it's a standalone clone or a subtree.

---

## Workflow Overview

Every feature follows the same pipeline:

```
specify → plan → tasks → test → implement → PR
```

Two modes are supported:

| Mode | When to use |
|------|-------------|
| **Human-in-the-loop** | Review each stage's artifact, then manually run the next stage yourself |
| **Fully automatic** | Let Claude run the entire plan → tasks → test → implement pipeline after you've reviewed the spec |

Before running any SpecKit command, start a Claude Code session inside Docker:

```bash
UID=$(id -u) GID=$(id -g) docker compose run --rm dev
# Inside the container:
claude
```

---

## Human-in-the-Loop Workflow

You are the gatekeeper at each stage: review the artifact, then run the next command yourself when you're ready.

### Step 1 — Specify

```
/speckit-specify <feature description>
```

- Creates a numbered feature branch (`NNN-feature-name`)
- Writes `specs/NNN-feature/spec.md` from your description
- Runs a quality checklist; asks for clarification (max 3 questions) if needed

**Review** `specs/NNN-feature/spec.md`. Edit it directly or give Claude feedback. Then:

```
/speckit-plan-auto     # or /speckit-plan for a single generation without the critic loop
```

### Step 2 — Review the plan

Review `specs/NNN-feature/plan.md`. Then:

```
/speckit-tasks-auto     # or /speckit-tasks
```

### Step 3 — Review the tasks

Review the dependency-ordered list of `[TEST]` / `[IMPL]` task pairs in `specs/NNN-feature/tasks.md`. Then:

```
/speckit-test-auto     # or /speckit-test
```

### Step 4 — Review the tests

Review the failing test files. Then:

```
/speckit-implement-auto     # or /speckit-implement
```

### Step 5 — Review the implementation

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

## Quick Reference

| Command | What it does |
|---------|-------------|
| `/speckit-specify <desc>` | Create spec + feature branch |
| `/speckit-plan` | Generate implementation plan (manual) |
| `/speckit-plan-auto` | Generate plan with automatic critic loop |
| `/speckit-tasks` | Generate task list (manual) |
| `/speckit-tasks-auto` | Generate tasks with automatic critic loop |
| `/speckit-test` | Write failing tests for all `[TEST]` tasks (manual) |
| `/speckit-test-auto` | Write tests with automatic critic loop |
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
    "plan":         { "enabled": true,  "model": "qwen3:30b-a3b" },
    "architecture": { "enabled": true,  "model": "qwen3:30b-a3b" },
    "tasks":        { "enabled": false, "model": "" },
    "implement":    { "enabled": false, "model": "" },
    "quality":      { "enabled": false, "model": "qwen3-coder:30b-a3b" },
    "test":         { "enabled": false, "model": "" }
  }
}
```

`architecture` and `quality` are the second-gate reviews for the plan and implement pipelines respectively (run after the `plan`/`implement` critic passes). Test-principles checking has no separate key — it's folded into the `test` critic.

---

## Key Rules

- **No agent merges to `main`** — the merge is always a human action after PR review and CI pass.
- **TDD is mandatory** — failing tests must be written before implementation code.
- **One feature at a time** — each feature has its own branch and `specs/NNN-feature/` directory.

---

## Regression Tests

The harness has a two-layer test suite under `tests/`.

### Layer 1 — Unit tests (no LLM, fast)

Tests for pure functions in `agent_common.py` and the prompt-building functions exported by each critic module. No external calls.

```bash
bash tests/run_tests.sh --skip-evals
# or directly:
python3 -m unittest discover -s tests/unit -p 'test_*.py' -v
```

### Layer 2 — Critic evals (requires local Ollama)

Each critic script (`plan_critic.py`, `tasks_critic.py`, `test_critic.py`, `implement_critic.py`, `architecture_critic.py`, `quality_critic.py`) is run against known-good and known-bad fixture artifacts. The result JSON is asserted. This catches prompt degradation, rule drift, or regressions in critic logic.

Fixtures live in `tests/evals/fixtures/` — a minimal "health endpoint" feature with good and bad variants for each pipeline stage.

```bash
bash tests/run_tests.sh
# Override Ollama model (default: deepseek-r1:8b):
OLLAMA_MODEL=qwen3:30b-a3b bash tests/run_tests.sh
# Override Ollama URL (default: http://localhost:11434):
OLLAMA_URL=http://host.docker.internal:11434 bash tests/run_tests.sh
```

The eval tests configure Ollama via a `.specify/local-llm.json` written into each test's temp directory — no changes to your local config needed. If Ollama is unreachable or the model isn't pulled, tests skip with a clear message rather than erroring.

## Linting

The harness's own Python source (`.claude/agents/`, `tests/`) is linted and formatted with [Ruff](https://docs.astral.sh/ruff/), config in `pyproject.toml`. Enforced in CI (`.github/workflows/ruff.yml`).

```bash
ruff check .
ruff format .
```

---

## What This Repo Contains

```
speckit/
├── .claude/
│   ├── agents/             # Python orchestrators (plan-auto, tasks-auto, etc.)
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
├── tests/
│   ├── run_tests.sh            # Top-level runner (--skip-evals for unit only)
│   ├── unit/                   # Pure-function tests, no LLM
│   │   ├── test_agent_common.py
│   │   └── test_prompt_builders.py
│   └── evals/                  # Critic evals against fixture artifacts (requires Ollama)
│       ├── fixtures/           # Good and bad artifacts for the health-endpoint feature
│       ├── test_plan_critic_eval.py
│       ├── test_tasks_critic_eval.py
│       ├── test_test_critic_eval.py
│       └── test_implement_critic_eval.py
└── install.sh              # Install/update script
```

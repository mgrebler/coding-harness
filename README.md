# coding-harness

coding-harness builds on [GitHub's SpecKit](https://github.com/github/spec-kit) spec-driven flow — specify → plan → tasks → implement — and adds autonomous agents that drive each stage, plus a strict test-first split between writing tests and writing implementation code: `specify → ch-1-plan-auto → ch-2-tasks-auto → ch-3-test-auto → ch-4-implement-auto`.

**Why this matters:** agentic development is fast, but code that "looks right" can quietly drift from your architecture, skip tests, or break conventions. coding-harness keeps agents fast *and* maintainable by gating every stage behind critic agents grounded in documents your team owns — `constitution.md`, `architecture.md`, `test-principles.md`, and more. A plan must pass an architecture review before tasks are generated; tests and implementation must pass a quality review before they're considered done. Humans remain the only ones who can merge to `main`.

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

# .devcontainer/Dockerfile extends the published coding-harness base image
# (ghcr.io/mgrebler/coding-harness-base:latest). Add Playwright or other
# project-specific dependencies in the PROJECT-SPECIFIC blocks in that file.
# Pin the FROM tag to a specific commit SHA instead of :latest if you want
# reproducible builds.
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
docker compose run --rm dev
# Inside the container:
claude
```

This same command also works at the root of this repo (coding-harness itself has its own `docker-compose.yml`), for developing the harness's own agents and skills.

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
/ch-1-plan-auto     # or /speckit-plan for a single generation without the critic loop
```

### Step 2 — Review the plan

Review `specs/NNN-feature/plan.md`. Then:

```
/ch-2-tasks-auto     # or /speckit-tasks
```

### Step 3 — Review the tasks

Review the dependency-ordered list of `[TEST]` / `[IMPL]` task pairs in `specs/NNN-feature/tasks.md`. Then:

```
/ch-3-test-auto     # or /ch-3-test
```

### Step 4 — Review the tests

Review the failing test files. Then:

```
/ch-4-implement-auto     # or /speckit-implement
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
/ch-plan-to-implement-auto
```

This chains four stages — plan, tasks, test, implement — with built-in critic loops at each stage.

---

## Quick Reference

`/speckit-specify` starts every feature. After that, the `/ch-*` commands below are what you actually run — each wraps a SpecKit generation step in an iterative critic loop:

| Command | Generation step | Critic gate(s) |
|---------|-----------------|-----------------|
| `/ch-1-plan-auto` | `/speckit-plan` | plan critic → plan-architecture-review |
| `/ch-2-tasks-auto` | `/speckit-tasks` | tasks critic |
| `/ch-3-test-auto` | `/ch-3-test` | test critic → test-quality-review |
| `/ch-4-implement-auto` | `/speckit-implement` | implement critic → implement-quality-review |
| `/ch-plan-to-implement-auto` | all of the above | all of the above, chained |

The plain `/speckit-plan`, `/speckit-tasks`, `/speckit-implement`, and `/ch-3-test` commands generate a single artifact with **no critic loop** — use them only if you want to hand-edit before any automated critique runs, not as the default path.

---

## Local LLM Support (Optional)

Critic passes can optionally run against a local [Ollama](https://ollama.com) instance instead of Claude. Edit `.specify/local-llm.json` in your project root:

```json
{
  "ollama_url": "http://host.docker.internal:11434",
  "default": { "enabled": false, "model": "" },
  "critics": {
    "plan":                     { "enabled": true,  "model": "qwen3:30b-a3b" },
    "plan-architecture-review": { "enabled": true,  "model": "qwen3:30b-a3b" },

    "tasks":                    { "enabled": false, "model": "" },

    "test":                     { "enabled": false, "model": "" },
    "test-quality-review":      { "enabled": false, "model": "" },

    "implement":                { "enabled": false, "model": "" },
    "implement-quality-review": { "enabled": false, "model": "qwen3-coder:30b-a3b" }
  }
}
```

Each phase's secondary review key is named after the primary key it follows, so the
pairing is explicit from the name alone: `plan` → `plan-architecture-review`, `test` →
`test-quality-review`, `implement` → `implement-quality-review`. `tasks` has no
secondary gate.

---

## Key Rules

- **No agent merges to `main`** — the merge is always a human action after PR review and CI pass.
- **TDD is mandatory** — failing tests must be written before implementation code.
- **One feature at a time** — each feature has its own branch and `specs/NNN-feature/` directory.

---

## Regression Tests

The harness has a two-layer test suite under `tests/`.

### Layer 1 — Unit tests (no LLM, fast)

Tests for pure functions in the `agent_common/` package and the prompt-building functions exported by each critic module. No external calls. Enforced in CI (`.github/workflows/tests.yml`).

```bash
bash tests/run_tests.sh --skip-evals
# or directly:
python3 -m unittest discover -s tests/unit -p 'test_*.py' -v
```

### Layer 2 — Critic evals (requires local Ollama)

Each critic script (`ch_1_plan_critic.py`, `ch_1_plan_architecture_critic.py`, `ch_2_tasks_critic.py`, `ch_3_test_critic.py`, `ch_3_test_quality_critic.py`, `ch_4_implement_critic.py`, `ch_4_implement_quality_critic.py`) is run against known-good and known-bad fixture artifacts. The result JSON is asserted. This catches prompt degradation, rule drift, or regressions in critic logic. Not run in CI — no local Ollama instance available there; run locally before pushing changes that touch a critic's prompt or scoring logic.

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

The `speckit-*` skills (including the `speckit-git-*` git extension) and the five templates in `.specify/templates/` come from upstream [SpecKit](https://github.com/github/spec-kit). Everything else — the `ch-*` skills and agents, the critic loop pattern, and the principle docs beyond `constitution.md` (`architecture.md`, `architecture-principles.md`, `code-quality-principles.md`, `test-principles.md`, `product-context.md`) — is coding-harness's own addition.

```
speckit/
├── .claude/
│   ├── agents/             # Python orchestrators (ch_1_plan_auto, ch_2_tasks_auto, etc.)
│   └── skills/             # Upstream /speckit-* commands plus this harness's /ch-* commands
├── .specify/
│   ├── extensions/         # Git integration scripts
│   ├── memory/             # Generic critic quality bars (architecture, code, test principles)
│   ├── scripts/            # Bash helpers
│   ├── templates/          # Spec/plan/tasks/constitution templates
│   └── workflows/          # Workflow registry
├── .devcontainer/
│   ├── Dockerfile          # Source for the published ghcr.io/mgrebler/coding-harness-base image
│   └── entrypoint.sh       # Docker socket + user management
├── docker-compose.yml      # Builds this repo's own dev container from .devcontainer/Dockerfile
├── examples/               # Templates for project-specific files (written once, never updated)
│   ├── docker-compose.yml
│   ├── CLAUDE.md
│   ├── .devcontainer/Dockerfile      # FROM ghcr.io/mgrebler/coding-harness-base:latest
│   ├── .devcontainer/devcontainer.json
│   └── .specify/memory/
│       ├── constitution.md     # Annotated template — fill in project-specific sections
│       ├── architecture.md     # Structural template
│       └── product-context.md  # Product vision template
├── tests/
│   ├── run_tests.sh            # Top-level runner (--skip-evals for unit only)
│   ├── unit/                   # Pure-function tests, no LLM
│   │   ├── test_console.py
│   │   ├── test_git.py
│   │   ├── test_files.py
│   │   ├── test_resume_state.py
│   │   ├── test_ollama.py
│   │   ├── test_critic_loop.py
│   │   └── test_prompt_builders.py
│   └── evals/                  # Critic evals against fixture artifacts (requires Ollama)
│       ├── fixtures/                       # Good and bad artifacts for the health-endpoint feature
│       ├── common.py, _ollama.py           # Shared eval helpers
│       ├── test_plan_critic_eval.py
│       ├── test_architecture_critic_eval.py
│       ├── test_tasks_critic_eval.py
│       ├── test_test_critic_eval.py
│       ├── test_test_quality_critic_eval.py
│       ├── test_implement_critic_eval.py
│       └── test_quality_critic_eval.py
└── install.sh              # Install/update script
```

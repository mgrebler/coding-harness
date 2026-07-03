# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

**coding-harness** is a spec-driven, AI-assisted feature development harness that gets installed into other projects. It is not a runnable application — there are no build or test commands for the harness itself. The primary operation is `install.sh`.

**Install into a project:**
```bash
./install.sh /path/to/coding-harness /path/to/your-project
```

After install, manually wire up the git hook in the target project:
```bash
cp .claude/hooks/post-commit .git/hooks/post-commit
chmod +x .git/hooks/post-commit
```

**Update an installed project** (after pulling the latest harness):
```bash
git pull
./install.sh /path/to/coding-harness /path/to/your-project
```

**Alternative: pin the harness inside the project repo via git subtree:**
```bash
git subtree add --prefix=.coding-harness <harness-repo-url> main
# To update:
git subtree pull --prefix=.coding-harness <harness-repo-url> main
.coding-harness/install.sh .coding-harness/ .
```

## Pipeline Overview

The harness enforces a strict spec-driven pipeline:

```
specify → plan → tasks → test → implement → human PR review + merge
```

Each stage produces a markdown artifact in `specs/NNN-feature/` within the target project. Humans approve each stage; git hooks trigger the next stage automatically after approval commits. No agent ever merges to `main`.

**Starting a session in an installed project:**
```bash
UID=$(id -u) GID=$(id -g) docker compose run --rm dev
# Inside the container:
claude
```

**Two execution modes in installed projects:**
- **Human-in-the-loop**: approve each stage manually with `/speckit-*-approved` commands
- **Fully automatic**: `/speckit-plan-to-implement-auto` chains all stages after spec approval

## Harness Architecture

### Ownership Model

`install.sh` distinguishes between two file classes:

**Always overwrites (harness-managed):**
- `.claude/agents/` — Python orchestrators for autonomous pipelines
- `.claude/hooks/` — Git hook that triggers next stage on approval commits
- `.claude/skills/` — All `/speckit-*` slash commands
- `.specify/extensions/` — Git integration module
- `.specify/scripts/` — Shared Bash utilities
- `.specify/templates/` — Markdown templates for specs/plans/tasks
- `.specify/workflows/` — Workflow registry
- `.devcontainer/Dockerfile`, `.devcontainer/entrypoint.sh`

**Writes only if absent (project-customized by humans, never overwritten):**
- `.specify/memory/constitution.md` — Supreme governance law for that project
- `.specify/memory/architecture.md`, `architecture-principles.md`, `code-quality-principles.md`, `test-principles.md`, `product-context.md`
- `docker-compose.yml`, `.devcontainer/devcontainer.json`
- `CLAUDE.md`

### Key Directories

**`.claude/agents/`** — Python orchestrators that run autonomous multi-stage pipelines. Each stage has a `*-auto.py` (end-to-end with critic loop) and a `*_critic.py` (builds critic prompts). These are invoked by the git hook after approval commits, or can run standalone. `agent_common.py` provides shared utilities.

**`.claude/skills/`** — User-facing slash commands. Each is a directory with a `SKILL.md` prompt. Skills write artifacts to disk, approve and commit stages, or trigger agent pipelines (indirectly via git hook).

**`.specify/memory/`** — Project-specific knowledge that persists across all features in a target project. Constitution is supreme law; architecture/principles documents are long-term north stars. These files are written once by humans and never auto-regenerated.

**`.specify/templates/`** — Markdown templates copied to `specs/NNN-feature/` when a feature is created. Agents fill them in; humans review.

**`.specify/extensions/git/`** — Optional module for sequential/timestamp branch numbering and auto-commit hooks. Config: `.specify/extensions/git/git-config.yml`.

**`.devcontainer/`** — Docker-based dev environment with Node 22, Python 3, Docker-in-Docker, GitHub CLI, and Playwright browser dependencies.

**`examples/`** — Templates for project-specific files that `install.sh` copies on first install (constitution annotated with `[PROJECT: ...]` placeholders, docker-compose with `<project-name>` placeholder, etc.).

### Artifact Paths in Installed Projects

| Artifact | Path |
|---|---|
| Feature specs/plans/tasks | `specs/NNN-feature/{spec,plan,tasks}.md` |
| Approval markers | `specs/NNN-feature/{spec,plan,tasks,test}-approved` |
| Critic results | `specs/NNN-feature/*-result-N.json` |
| Constitution | `.specify/memory/constitution.md` |
| Git hook | `.git/hooks/post-commit` |

### TDD Enforcement

Tasks are split into `[TEST]` and `[IMPL]` pairs. The test phase writes only failing tests; the implement phase makes them pass. These are kept strictly separate — no implementation code during `[TEST]` tasks.

### Local LLM Support

Critic passes can run against a local [Ollama](https://ollama.com) instance instead of Claude. Configure via `.specify/local-llm.json` in the target project root:

```json
{
  "ollama_url": "http://host.docker.internal:11434",
  "num_ctx": 16384,
  "keep_alive": -1,
  "default": { "enabled": false, "model": "" },
  "critics": {
    "plan":      { "enabled": true,  "model": "qwen3:30b-a3b" },
    "tasks":     { "enabled": false, "model": "" },
    "implement": { "enabled": false, "model": "" },
    "test":      { "enabled": false, "model": "" }
  }
}
```

`num_ctx` caps the Ollama KV-cache context window. Without it, Ollama uses the model's default (often 32k–128k), which can overflow VRAM and spill to system RAM, making inference very slow. `16384` is a good default for an 8 GB GPU: critic prompts fit comfortably and the KV cache stays in VRAM. Tune down if your GPU is smaller, or up if your prompts are very large.

`keep_alive: -1` pins the model in VRAM indefinitely, eliminating cold-load latency between critic iterations and pipeline stages. Omit it to use Ollama's default (5 min).

### Critic Loop Pattern

Each `*-auto.py` agent runs an iteration loop: generate → critic review → fix → repeat until the critic passes or a max iteration count is reached. Critic prompts are built by companion `*_critic.py` modules. The constitution and quality principles documents from `.specify/memory/` are injected into every critic prompt.

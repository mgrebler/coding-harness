# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

**coding-harness** is a spec-driven, AI-assisted feature development harness that gets installed into other projects. It is not a runnable application — there is no build for the harness itself. The primary operation is `install.sh`. The exceptions are the harness's own Python source (`.claude/agents/`, `tests/`): linting/formatting via `ruff check .` / `ruff format .` (config in `pyproject.toml`), and unit tests via `bash tests/run_tests.sh --skip-evals`. Both are enforced in CI (`.github/workflows/`).

**Install into a project:**
```bash
./install.sh /path/to/your-project
```

**Update an installed project** (after pulling the latest harness):
```bash
git pull
./install.sh /path/to/your-project
```

**Alternative: pin the harness inside the project repo via git subtree:**
```bash
git subtree add --prefix=.coding-harness <harness-repo-url> main
# To update:
git subtree pull --prefix=.coding-harness <harness-repo-url> main
.coding-harness/install.sh .
```

## Pipeline Overview

The harness enforces a strict spec-driven pipeline:

```
specify → plan → tasks → test → implement → human PR review + merge
```

Each stage produces a markdown artifact in `specs/NNN-feature/` within the target project. Humans review each stage's artifact, then manually run the next stage's command. No agent ever merges to `main`.

**Starting a session in an installed project:**
```bash
UID=$(id -u) GID=$(id -g) docker compose run --rm dev
# Inside the container:
claude
```

**Two execution modes in installed projects:**
- **Human-in-the-loop**: review each stage's artifact, then manually run the next `/speckit-*-auto` (or plain `/speckit-*`) command yourself
- **Fully automatic**: `/speckit-plan-to-implement-auto` chains all stages unattended after `spec.md` is reviewed

## Harness Architecture

### Ownership Model

`install.sh` distinguishes between two file classes:

**Always overwrites (harness-managed):**
- `.claude/agents/` — Python orchestrators for autonomous pipelines
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

**`.claude/agents/`** — Python orchestrators that run autonomous multi-stage pipelines. Each stage has a `*-auto.py` (end-to-end with critic loop) and a `*_critic.py` (builds critic prompts). These are invoked manually via slash commands, or chained automatically by the fully-automatic pipeline. The `agent_common/` package provides shared utilities, split by concern (`console`, `git`, `files`, `resume_state`, `ollama`, `critic_loop`).

**`.claude/skills/`** — User-facing slash commands. Each is a directory with a `SKILL.md` prompt. Skills write artifacts to disk or invoke agent pipelines directly.

**`.specify/memory/`** — Project-specific knowledge that persists across all features in a target project. Constitution is supreme law; architecture/principles documents are long-term north stars. These files are written once by humans and never auto-regenerated.

**`.specify/templates/`** — Markdown templates copied to `specs/NNN-feature/` when a feature is created. Agents fill them in; humans review.

**`.specify/extensions/git/`** — Optional module for sequential/timestamp branch numbering and auto-commit hooks. Config: `.specify/extensions/git/git-config.yml`.

**`.devcontainer/`** — Docker-based dev environment with Node 22, Python 3, Docker-in-Docker, GitHub CLI, and Playwright browser dependencies.

**`examples/`** — Templates for project-specific files that `install.sh` copies on first install (constitution annotated with `[PROJECT: ...]` placeholders, docker-compose with `<project-name>` placeholder, etc.).

### Artifact Paths in Installed Projects

| Artifact | Path |
|---|---|
| Feature specs/plans/tasks | `specs/NNN-feature/{spec,plan,tasks}.md` |
| Critic results | `specs/NNN-feature/*-result-N.json` |
| Constitution | `.specify/memory/constitution.md` |

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
    "plan":         { "enabled": true,  "model": "qwen3:30b-a3b" },
    "architecture": { "enabled": true,  "model": "qwen3:30b-a3b" },
    "tasks":        { "enabled": false, "model": "" },
    "implement":    { "enabled": false, "model": "" },
    "quality":      { "enabled": false, "model": "qwen3-coder:30b-a3b" },
    "test":         { "enabled": false, "model": "" }
  }
}
```

`plan`/`architecture` and `implement`/`quality` are each two-gate pipelines — the first key is the spec/constitution critic, the second is the independent architecture-quality or code-quality review that runs after it. There is no separate key for test-principles checking: it's folded into the `test` critic's prompt rather than run as its own gate.

`num_ctx` caps the Ollama KV-cache context window. Without it, Ollama uses the model's default (often 32k–128k), which can overflow VRAM and spill to system RAM, making inference very slow. `16384` is a good default for an 8 GB GPU: critic prompts fit comfortably and the KV cache stays in VRAM. Tune down if your GPU is smaller, or up if your prompts are very large.

`keep_alive: -1` pins the model in VRAM indefinitely, eliminating cold-load latency between critic iterations and pipeline stages. Omit it to use Ollama's default (5 min).

`num_gpu` defaults to forcing all model layers onto the GPU, overriding Ollama's own conservative auto-split (testing showed Ollama can leave usable VRAM headroom unused — e.g. choosing 34/37 layers when its own memory-fit calculation showed 35 would still fit). No config is needed to get this: it's the default. If the forced value doesn't fit on a smaller GPU, the harness automatically falls back to Ollama's normal auto-split. Set `num_gpu` explicitly in `local-llm.json` only to override this default (e.g. to force a specific layer count).

`temperature` controls generation randomness (default `0.1`). Set to `0.0` for fully deterministic (greedy) decoding — recommended for eval runs and CI to eliminate non-deterministic hallucinations in reasoning models like deepseek-r1.

### Critic Loop Pattern

Each `*-auto.py` agent runs an iteration loop: generate → critic review → fix → repeat until the critic passes or a max iteration count is reached. Critic prompts are built by companion `*_critic.py` modules. The constitution and quality principles documents from `.specify/memory/` are injected into every critic prompt.

# coding-harness

coding-harness builds on [GitHub's SpecKit](https://github.com/github/spec-kit) spec-driven flow — specify → plan → tasks → implement — and adds autonomous agents that drive each stage, plus a strict test-first split between writing tests and writing implementation code, and a final stage that keeps a persistent user/maintainer documentation set current: `specify → ch-1-plan-auto → ch-2-tasks-auto → ch-3-test-auto → ch-4-implement-auto → ch-5-docs-auto`.

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
specify → plan → tasks → test → implement → docs → PR
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
/ch-specify <feature description>     # or /speckit-specify for the same flow without docs/ grounding
```

- Creates a numbered feature branch (`NNN-feature-name`)
- Writes `specs/NNN-feature/spec.md` from your description
- Runs a quality checklist; asks for clarification (max 3 questions) if needed
- If `docs/` exists (see "Adopting the Docs Stage into an Existing Project" below), reads it first to tell new capability apart from a change to behaviour already documented there, so the spec frames it accordingly

Already tracked as a GitHub issue? Use `/ch-specify-from-issue <issue-number-or-url>` instead — it fetches the issue's title and body via the `gh` CLI, delegates through `/ch-specify` (so it gets the same `docs/` grounding), and produces an identical `spec.md` with a source line back to the issue.

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

Review the code changes and verify CI passes locally. Then:

```
/ch-5-docs-auto
```

### Step 6 — Review the documentation

Review the changes under `docs/`. Then open a PR. **The merge is always a human action.**

---

## Adopting the Docs Stage into an Existing Project

`/ch-5-docs-auto` merges each *new* feature's shipped behaviour into `docs/` — it assumes `docs/`
already reflects everything built before that feature. A project installing the harness
mid-project (with existing code and no `docs/` yet) needs a one-time bootstrap first:

```
/ch-docs-bootstrap
```

This surveys the current codebase as a whole (not a feature delta — there's no `spec.md`/
`plan.md`/`tasks.md` to read yet) and writes the initial Diátaxis-structured `docs/` tree. Run it
once per project; review its output, then `/ch-5-docs-auto` takes over incrementally from the next
feature onward.

---

## Fully Automatic Mode

After specifying the feature, run the entire plan → tasks → test → implement → docs pipeline unattended:

```
/ch-specify <feature description>
# or: /ch-specify-from-issue <issue-number-or-url>
```

Review and edit `specs/NNN-feature/spec.md` if needed, then:

```
/ch-plan-to-implement-auto
```

This chains five stages — plan, tasks, test, implement, docs — with built-in critic loops at each stage.

The pipeline stops for you in three situations:

- **Pending governance amendments.** If plan.md lists anything under `## Pending Human Approvals`, the pipeline stops before tasks (exit code 4). Agents may never edit `.specify/memory/`. Apply or reject each amendment yourself, replace the list with `None.`, and re-run.
- **Escalation.** A stage that runs out of critic iterations writes `specs/NNN-feature/<stage>-escalation.md`. Reply by creating the `-review.md` file it names. Write guidance in it and the fix agent runs next with your review as an authoritative instruction. If you already made the fix yourself, include the line `Resolution: fixed-by-human` and the fix agent is skipped. Each review is used once: it is renamed to `*-consumed-to-<N>.md`.
- **`[MANUAL]` tasks.** These are left unchecked for you. They cover anything that needs a human or an out-of-repo action, such as approvals, live deploys or manual verification.

---

## Quick Reference

`/ch-specify` starts every feature (or `/ch-specify-from-issue <issue-number-or-url>` to source it from an existing GitHub issue instead of typing the description — it delegates through `/ch-specify` too). After that, the `/ch-*` commands below are what you actually run — each wraps a SpecKit generation step in an iterative critic loop:

| Command | Generation step | Critic gate(s) |
|---------|-----------------|-----------------|
| `/ch-specify` | `/speckit-specify` | (none — specify has no critic; adds `docs/` grounding only) |
| `/ch-1-plan-auto` | `/speckit-plan` | plan critic (now also checks `docs/` consistency) → plan-architecture-review |
| `/ch-2-tasks-auto` | `/speckit-tasks` | tasks critic |
| `/ch-3-test-auto` | `/ch-3-test` | test critic → test-quality-review |
| `/ch-4-implement-auto` | `/speckit-implement` | implement critic → implement-quality-review |
| `/ch-5-docs-auto` | (none — docs has no plain-generation counterpart) | docs critic |
| `/ch-plan-to-implement-auto` | all of the above | all of the above, chained |

The plain `/speckit-specify`, `/speckit-plan`, `/speckit-tasks`, `/speckit-implement`, and `/ch-3-test` commands generate a single artifact with **no critic loop** — use them only if you want to hand-edit before any automated critique runs, not as the default path. `/speckit-specify` also skips the `docs/`-grounding step `/ch-specify` adds.

---

## Pipeline Configuration (`.specify/pipeline.json`, Optional)

Every pipeline stage — each critic and each generation step — can independently run
against Claude (the default, zero-config), a local [Ollama](https://ollama.com) instance,
or a hosted OpenAI-compatible API (NVIDIA's hosted inference, OpenAI, Groq, Together.ai,
etc). Edit `.specify/pipeline.json` in your project root:

```json
{
  "ollama_url": "http://host.docker.internal:11434",
  "default": { "provider": "claude" },
  "critics": {
    "plan":                     { "provider": "ollama", "model": "qwen3:30b-a3b" },
    "plan-architecture-review": { "provider": "ollama", "model": "qwen3:30b-a3b" },

    "tasks":                    "default",

    "test":                     "default",
    "test-quality-review":      "default",

    "implement":                "default",
    "implement-quality-review": { "provider": "ollama", "model": "qwen3-coder:30b-a3b" },

    "docs":                     "default"
  }
}
```

Each phase's value is either the literal string `"default"` (inherit the top-level
`default` entry wholesale) or a full `{"provider": ..., "model": ..., ...}` object used
as-is — entries never merge field-by-field with `default`, so a phase you do configure
gets exactly what you wrote, nothing implicitly inherited. `"provider"` is one of
`"ollama"`, `"openai-compatible"`, or `"claude"`; omitting it on an entry defaults to
`"claude"`, and omitting `"model"` on a `"claude"` entry means "inherit whatever model
the invoking Claude Code session already runs under" — so an absent config file, or a
`default` you never customize, is a zero-behavior-change no-op.

Each phase's secondary review key is named after the primary key it follows, so the
pairing is explicit from the name alone: `plan` → `plan-architecture-review`, `test` →
`test-quality-review`, `implement` → `implement-quality-review`. `tasks` and `docs`
have no secondary gate.

### Picking a specific Claude model

`"provider": "claude"` is an explicit, first-class choice, not just "no local backend
configured" — set `"model"` to a model alias (`"sonnet"`, `"opus"`, `"haiku"`,
`"inherit"`) or a full model ID (e.g. `"claude-opus-5-5"`) to pin a phase to a specific
Claude model regardless of which model the invoking session itself runs under:

```json
{
  "default": { "provider": "claude", "model": "sonnet" },
  "critics": {
    "implement-quality-review": { "provider": "claude", "model": "opus" }
  }
}
```

Here every phase runs on Sonnet by default, except code quality review, which always
runs on Opus. `ollama`/`openai-compatible` entries ignore `"model"` as a Claude
selector — it names the local/hosted model instead, per the usual fields below.

### Pipeline stage toggles

The test and docs stages can each be turned off entirely for a project, via a
top-level `stages` object:

```json
{
  "stages": {
    "test": { "enabled": true },
    "docs": { "enabled": true }
  }
}
```

Both default to `true` when the config file is absent, has no `stages` key, or omits
a specific stage — so this is purely opt-out. `plan`, `tasks`, and `implement` are not
toggleable.

Disabling `test` has one real consequence beyond skipping `/ch-3-test-auto`: the tasks
agent stops pairing `[IMPL]` tasks with `[TEST]` tasks (plain `[IMPL]`-only tasks
instead), and `/ch-4-implement-auto`'s test-gate preflight check (constitution §19) is
skipped — implementation proceeds directly from tasks.md without a prior red-state
artifact. Set the toggle *before* generating `tasks.md` for a given feature; flipping it
mid-feature, after `[TEST]` tasks already exist, leaves them permanently unchecked.

Disabling `docs` simply skips `/ch-5-docs-auto` (and the documentation gate, §20) —
no ripple into any other stage, since docs runs last.

Two ways to control the Ollama context window (`num_ctx`), settable at the
top level or per-critic like any other field above:

- **`num_ctx`** — an exact, fixed size. Use this when you want reproducible,
  deterministic behavior (e.g. the eval suite pins one so critic output is
  comparable run-to-run).
- **`max_ctx`** — a VRAM *ceiling* instead of a fixed size. When set (and `num_ctx`
  is *not* set), the harness measures each prompt's actual size — exactly, via
  Ollama's own tokenizer, when available; otherwise a conservative estimate — and
  computes a right-sized `num_ctx` per call, clamped to `max_ctx`. This is the
  better default for critics whose prompt size varies a lot over a feature's
  lifetime (e.g. `tasks`, where `tasks.md` grows across iterations) — it removes
  the need to manually notice a critic is running out of context and bump
  `num_ctx` by hand.

If both are set, `num_ctx` wins. If a prompt genuinely needs more context than
`max_ctx` allows, the harness logs a warning and clamps to `max_ctx` rather than
failing — watch for that warning, since it means results may be degraded
(truncated) rather than wrong outright.

### Multiple critics per phase (multi-LLM review)

Any `critics[phase]` entry can be a **list** of critic configs instead of a
single object, so a phase can be reviewed by several independent models at
once:

```json
{
  "ollama_url": "http://host.docker.internal:11434",
  "critics": {
    "plan": [
      { "id": "qwen",     "provider": "ollama", "model": "qwen3:30b-a3b" },
      {
        "id": "nvidia-llama",
        "provider": "openai-compatible",
        "model": "meta/llama-3.1-70b-instruct",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_env": "NVIDIA_API_KEY"
      }
    ],
    "plan-architecture-review": { "provider": "ollama", "model": "qwen3:30b-a3b" }
  },
  "critic_execution": { "plan": "parallel" }
}
```

Each list entry supports every field a single-critic config does (`provider`,
`model`, `base_url`/`api_key_env`, `num_ctx`, `max_ctx`, `keep_alive`,
`num_gpu`, `num_predict`, `temperature`, `json_mode`), resolved exactly like a single
non-list entry (used as-is, no merge with `default`), and requires a unique
`id` string (a missing or duplicate `id` fails fast at config-load time
rather than mid-run). A list with only one entry behaves identically to the
single-object form — nothing special to configure. `"provider": "claude"` on
a list entry is never meaningful (there's nothing for a local-critic
subprocess fan-out to run) and is silently excluded from the fan-out rather
than erroring.

A single critic's verdict is used as-is. If every critic configured for a
gate fails (unreachable, timed out, or still returning unusable output after
one retry), the gate falls back to the Claude critic and logs that loudly; it
no longer aborts the stage.

When more than one critic resolves for a phase, each independently reviews
the same artifact against the same objective (an identical prompt — only the
model differs), writing its raw, unverified findings to
`specs/$FEATURE/{result-file-prefix}-raw/{id}-{iteration}.json`. Two outcomes:

- **Every critic reports a clean PASS** — the harness synthesizes the
  canonical PASS result directly. Nothing to adjudicate, no extra LLM call.
- **Any critic reports something** — the harness runs a **reconciliation
  pass**: a Claude subagent (never a local LLM — the harness's own judgment
  is always final) reads every critic's raw findings alongside the artifact
  and independently re-verifies each one. It rejects anything it can't
  personally confirm, merges duplicates, and resolves disagreements itself —
  a finding is never trusted just because one critic reported it, and never
  discarded just because critics disagree about it. The reconciled result is
  written to the normal canonical result file
  (`specs/$FEATURE/{result-file-prefix}-{iteration}.json`), so everything
  downstream (the fix/revision loop, resume behavior, escalation docs) works
  exactly as it does for a single critic.

`critic_execution` (top-level, keyed by phase name) controls whether a
phase's critics run one after another (`"sequential"`, the default when the
key is omitted) or concurrently (`"parallel"`). Sequential is the safer
default when critics share one local Ollama GPU host — different models
mid-fanout can thrash VRAM and `keep_alive` pinning against each other.
`"parallel"` is worth setting for a phase whose critics target independent
endpoints, e.g. one local Ollama model plus one hosted `openai-compatible`
API, where there's no shared GPU to contend over.

Multi-critic support applies to the automated `ch-N-*-auto` pipeline, where
`.specify/pipeline.json` is consulted. The human-triggered `/ch-N-*-review`
skills are a separate, single-shot Claude-only review path unrelated to this
config and are unaffected either way.

### Using a hosted OpenAI-compatible API instead of Ollama

Set `"provider": "openai-compatible"` and supply `base_url` and `api_key_env`
alongside `model`:

```json
{
  "base_url": "https://integrate.api.nvidia.com/v1",
  "api_key_env": "NVIDIA_API_KEY",
  "default": { "provider": "openai-compatible", "model": "moonshotai/kimi-k2.6" },
  "critics": {}
}
```

`api_key_env` names an environment variable holding the API key — **the key
itself must never be written into this file**, since `.specify/pipeline.json`
is typically committed alongside the rest of the project's specs. Set the
named variable in your shell/CI environment before running critics, or drop
it in a `.env` file (`KEY=value` per line) in your project root — it's loaded
automatically before each request and never overrides a variable already set
in the environment. `.env` is optional: if it doesn't exist, this is a no-op
and the shell/CI environment is used as-is. `.env` should never be committed
(it's gitignored by default) — only `.env.sample` with placeholder values.

`base_url`/`api_key_env` can be shared at the top level (as above) or set
per-entry to mix backends — e.g. Ollama for most critics, a hosted model for
one — but `provider` and `model` themselves are never inherited from a bare
top-level field; they always come from the entry itself (or `"default"`,
inheriting the `default` block wholesale). `num_ctx`/`max_ctx`/`keep_alive`/
`num_gpu` are Ollama-specific and are ignored when `provider` is
`openai-compatible`.

Every pipeline run first checks each configured hosted or local model. It
fails fast if a key's `api_key_env` variable isn't set, or if the provider no
longer lists the model (for example, an end-of-life model). This check runs
before any generation.

`max_prompt_tokens` (any provider; per entry or top level) caps a critic's
prompt. The default is `num_ctx`/`max_ctx` if set, otherwise 100000. When a
test-stage critic's prompt would exceed the cap, the harness drops
non-essential context first: red-state output is reduced to its tail, then
the assertion summaries go, then architecture.md. The test files under review
are never dropped. Give each multi-critic phase at least one large-context
model so a gate keeps a working critic on a very large feature. A
context-overflow response from a hosted API is reported as such and is not
retried.

`json_mode` (any provider; per entry or top level, default `true`) controls
whether critic calls switch on the provider's JSON mode (`response_format:
json_object` / Ollama `format: "json"`). Set it to `false` for a model whose
JSON mode is broken. For example, `poolside/laguna-xs-2.1` on NVIDIA answers
every critic prompt with a bare `{}` when JSON mode is on, but follows the
prompt's JSON instructions correctly when it's off.

For example, Ollama by default with the `test` critic overridden to run
against NVIDIA instead:

```json
{
  "ollama_url": "http://host.docker.internal:11434",
  "critics": {
    "plan": { "provider": "ollama", "model": "qwen3:30b-a3b" },

    "test": {
      "provider": "openai-compatible",
      "model": "meta/llama-3.3-70b-instruct",
      "base_url": "https://integrate.api.nvidia.com/v1",
      "api_key_env": "NVIDIA_API_KEY"
    }
  }
}
```

`plan`'s entry sets its own `provider`/`model` but falls through to the
top-level `ollama_url` since it doesn't override that; `test`'s entry
overrides `provider` and supplies its own `base_url`/`api_key_env`, so it
never touches `ollama_url` at all. Any critic can independently be Ollama,
an OpenAI-compatible host, or Claude.

#### Recommended models (NVIDIA hosted inference)

Validated by running the harness's own eval suite (`tests/evals/`) against
NVIDIA's hosted API (`https://integrate.api.nvidia.com/v1`) instead of the
default Ollama backend:

- **`nvidia/llama-3.3-nemotron-super-49b-v1.5`** (recommended) — 16/16 eval
  tests passed across all seven critics (plan, plan-architecture-review,
  tasks, test, test-quality-review, implement, implement-quality-review),
  correctly distinguishing PASS/FAIL cases and citing the right violations.
  NVIDIA's own agentic/instruction-tuned reasoning model; keeps its reasoning
  trace out of the JSON `content` field (see caveat below), so it complies
  cleanly with the harness's "raw JSON, no fences" contract. Latency is
  uneven — most critic calls took 25-120s, but one reasoning-heavy case took
  ~5 min, worth accounting for in iterative critic loops.
- **`openai/gpt-oss-120b`** — same clean reasoning/content separation; passed
  ad hoc JSON-mode smoke tests but hasn't been run through the full eval
  suite yet. Worth trying if nemotron proves too slow or rate-limited.
- Passed a basic JSON-mode connectivity check but not eval-tested:
  `deepseek-ai/deepseek-v4-pro`, `mistralai/mistral-medium-3.5-128b`,
  `minimaxai/minimax-m3`, `nvidia/nemotron-3-super-120b-a12b`.

**Caveat — check for reasoning leakage.** Some reasoning models put their
entire chain-of-thought directly in the `content` field instead of a
separate `reasoning_content` field (observed with
`nvidia/nemotron-3-ultra-550b-a55b`), which breaks the raw-JSON contract
`run_local_critic_cli` depends on. Before trusting a new model in a critic
loop, sanity-check it with a trivial prompt (e.g. `Reply with only this
JSON: {"result": "ok"}`, `response_format: json_object`) and confirm
`content` comes back as clean JSON.

**Access varies per account/API key.** A model appearing in NVIDIA's
`/v1/models` catalog listing does not guarantee your key can call it — some
return `404: Function ... Not found for account` until access is granted.
Confirm with a real chat-completions call, not just the models list.

### Generation (plan/tasks/test/implement/docs authoring)

Everything above configures **critics** — review passes that read a prompt
and return one JSON verdict, no filesystem access needed. The five
**generation** stages (`plan`, `tasks`, `test`, `implement`, `docs` — the
agents that actually author `plan.md`/`tasks.md`/tests/implementation/`docs/`)
can independently be pointed at a local or hosted model too, via a separate
top-level `generation` object in the same `.specify/pipeline.json`:

```json
{
  "ollama_url": "http://host.docker.internal:11434",
  "generation": {
    "plan":      { "provider": "ollama", "model": "qwen3-coder:30b-a3b", "max_turns": 40 },
    "tasks":     "default",
    "test":      "default",
    "implement": { "provider": "claude", "model": "opus", "command_timeout_s": 180 },
    "docs":      "default"
  }
}
```

`generation` is a sibling to `critics`, not a variant of it: a generation
stage needs real tool access (Read/Write/Edit/Bash/Glob/Grep — the model
authors files and runs commands itself, including `git commit`), whereas a
critic never touches the filesystem. One `generation.<stage>` entry covers
every agent role for that stage — initial generation, revision, and fix (or
CI-fix for `implement`) all resolve through the same config, mirroring how
a single `critics.<type>` entry already covers every iteration of that
critic regardless of round number.

Every field from the critic config surface — `provider`, `ollama_url` /
`base_url`+`api_key_env`, `num_ctx`, `max_ctx`, `keep_alive`, `num_gpu`,
`num_predict`, `temperature` — resolves for a generation stage exactly as
it does for a critic, including sharing the same top-level/`default` block
if a project wants one backend for everything. Three fields are
generation-only, since they configure the tool-calling loop itself rather
than the model call:

- **`max_turns`** — the round budget (one model turn + its tool calls) before
  the loop gives up. That counts as a local failure (see below). Defaults to 40.
- **`command_timeout_s`** — per-`Bash`-call timeout in seconds (default 120).
- **`output_max_bytes`** — caps how much of a single `Bash` call's output is
  fed back to the model, tail-biased since the actionable failure is
  usually at the end (default ~200KB).

If local generation fails, the stage doesn't stop. A failure is an
unreachable or erroring backend after the transport's retries, a context
overflow, or running out of `max_turns`. The harness logs a `WARNING` naming
the model and the error, then re-runs that same call on Claude (the stage's
`generation` Claude model if one is configured, otherwise the session
default). Claude picks up from whatever the local model already wrote to
disk. That stage stays on Claude for the rest of the run, so later revision
and fix calls don't wait out a dead backend again. Other stages keep their
own backends. A new run (including a resume) tries the local model again.

A local/hosted model generating a stage doesn't change anything about how
that stage is *reviewed* — the corresponding critic (local, hosted, or
Claude) still runs against whatever was produced, exactly as if Claude had
written it. Generation and critic backends are fully independent per stage:
mix and match freely (e.g. a local model drafts `plan.md`, Claude critiques
it; or both run locally with different models).

**Bash runs with guardrails, not a full sandbox.** Every filesystem tool is
confined to the project root. `Bash` additionally gets a timeout, an output
cap, and a denylist rejecting a small set of catastrophic one-liners
(`rm -rf /`, `git push --force`, `sudo`, `dd`, piping `curl`/`wget` into a
shell, etc) — configurable per stage via a `deny_patterns` list of regexes,
merged with the built-in list rather than replacing it. This is
defense-in-depth, not a jail: the real backstops are that generation
offload is opt-in per project, the devcontainer is the actual outer sandbox
boundary, and the unchanged critic gates plus mandatory human PR review are
still the last word before anything reaches `main`.

**Tool-calling support varies a lot by model.** Many smaller or
instruction-only models don't emit real tool calls — they narrate them as
text instead, which the loop can't act on. Sanity-check a candidate model's
tool-calling support before trusting it with a real feature; the same
reasoning-leakage caveat from the critic section above applies here too.

---

## Key Rules

- **No agent merges to `main`** — the merge is always a human action after PR review and CI pass.
- **TDD is mandatory** — failing tests must be written before implementation code, when the test stage is enabled for the project (see § Pipeline Configuration → stage toggles; enabled by default).
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

Each critic script (`ch_1_plan_critic.py`, `ch_1_plan_architecture_critic.py`, `ch_2_tasks_critic.py`, `ch_3_test_critic.py`, `ch_3_test_quality_critic.py`, `ch_4_implement_critic.py`, `ch_4_implement_quality_critic.py`, `ch_5_docs_critic.py`) is run against known-good and known-bad fixture artifacts. The result JSON is asserted. This catches prompt degradation, rule drift, or regressions in critic logic. Not run in CI — no local Ollama instance available there; run locally before pushing changes that touch a critic's prompt or scoring logic.

Fixtures live in `tests/evals/fixtures/` — a minimal "health endpoint" feature with good and bad variants for each pipeline stage.

```bash
bash tests/run_tests.sh
# Override Ollama model (default: deepseek-r1:8b):
OLLAMA_MODEL=qwen3:30b-a3b bash tests/run_tests.sh
# Override Ollama URL (default: http://localhost:11434):
OLLAMA_URL=http://host.docker.internal:11434 bash tests/run_tests.sh
```

The eval tests configure Ollama via a `.specify/pipeline.json` written into each test's temp directory — no changes to your local config needed. If Ollama is unreachable or the model isn't pulled, tests skip with a clear message rather than erroring.

## Linting

The harness's own Python source (`.claude/agents/`, `tests/`) is linted and formatted with [Ruff](https://docs.astral.sh/ruff/), config in `pyproject.toml`. Enforced in CI (`.github/workflows/ruff.yml`).

```bash
ruff check .
ruff format .
```

---

## Notifications When Claude Is Waiting

The hooks in `.claude/settings.json` (via `.claude/hooks/notify.py`) tell you when Claude is waiting on you. They run in this repo and in every installed project. `install.sh` copies `notify.py` as a harness-managed file and merges the hooks into the project's own `.claude/settings.json`. Re-running it replaces only those hook entries, leaving every other setting and hook alone. If the file isn't valid JSON (for example, it has comments), `install.sh` leaves it untouched and says so.

| When | What you get |
|---|---|
| Claude finishes a turn | terminal bell |
| Claude needs a permission | terminal bell |
| Claude finishes a turn, or a permission prompt has waited about 6s while you weren't typing | push notification via [ntfy](https://ntfy.sh), if configured |

**Terminal bell.** It is on by default. Claude Code emits the BEL on its own terminal, so it reaches your host terminal through the container. Enable bells (sound, tab badge, or urgency hint) in your terminal profile to notice it.

To turn the bell off for yourself, set `CLAUDE_NOTIFY_BELL` to `0` without touching the committed settings. Add it either to `.claude/settings.local.json` (this repo only, gitignored) or to `~/.claude/settings.json` (every repo):

```json
{ "env": { "CLAUDE_NOTIFY_BELL": "0" } }
```

**Phone/desktop push** needs a topic, since ntfy has nowhere to send it otherwise. Once `NTFY_TOPIC` is set, every push above is on by default. To only be pushed for permission prompts, plus a finished turn left unanswered for 60s, set `CLAUDE_NOTIFY_PUSH_ON_STOP` to `0` the same way as the bell.

To set up push:

1. Pick an unguessable topic name, for example `claude-$(openssl rand -hex 8)`. Anyone who knows the name can read and post to it.
2. Subscribe to it in the ntfy app or at `https://ntfy.sh/<topic>`.
3. Add the topic to the `env` block of your personal `~/.claude/settings.json`. Add `NTFY_SERVER` there too if you self-host ntfy:

   ```json
   { "env": { "NTFY_TOPIC": "<topic>" } }
   ```

   The dev container mounts `~/.claude` from your host, so this one setting covers every project, with nothing to add per repo and nothing committed. Restart any open Claude Code sessions to pick it up.

   Alternatively, set the topic for a single project: put `NTFY_TOPIC=<topic>` in that project's gitignored `.env` or your host shell. The project's `docker-compose.yml` passes `NTFY_TOPIC`/`NTFY_SERVER` into the container. `examples/docker-compose.yml` already has those lines, but projects installed earlier need them added by hand.

The push contains only the project name and Claude's one-line notification text, such as "coding-harness: Claude needs your permission to use Bash". The project name comes from the repo's `origin` remote, because every dev container mounts its project at `/workspace` and the folder name can't tell sessions apart. It never includes code or conversation content. The unattended `ch-*-auto` pipelines run Claude without loading project settings (`setting_sources=[]`), so they never trigger these hooks.

## What This Repo Contains

The `speckit-*` skills (including the `speckit-git-*` git extension) and the five templates in `.specify/templates/` come from upstream [SpecKit](https://github.com/github/spec-kit). Everything else — the `ch-*` skills and agents, the critic loop pattern, the principle docs beyond `constitution.md` (`architecture.md`, `architecture-principles.md`, `code-quality-principles.md`, `test-principles.md`, `documentation-principles.md`, `product-context.md`), and the `docs/` output of the documentation stage — is coding-harness's own addition.

```
speckit/
├── .claude/
│   ├── agents/             # Python orchestrators (ch_1_plan_auto, ch_2_tasks_auto, etc.)
│   └── skills/             # Upstream /speckit-* commands plus this harness's /ch-* commands
├── .specify/
│   ├── extensions/         # Git integration scripts
│   ├── extensions.yml      # Registers the git extension's hooks with the speckit-* skills
│   ├── memory/             # Generic critic quality bars (architecture, code, test, documentation principles)
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
│   │   ├── test_critic_reconcile.py
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
│       ├── test_quality_critic_eval.py
│       └── test_docs_critic_eval.py
└── install.sh              # Install/update script
```

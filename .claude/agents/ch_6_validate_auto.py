#!/usr/bin/env python3
"""
.claude/agents/ch_6_validate_auto.py

Deployed-validation orchestrator. Run manually via /ch-6-validate-auto after
the docs stage, or as the last stage of ch_plan_to_implement_auto.py when
enabled. Opt-in: does nothing unless .specify/pipeline.json sets
stages.validate.enabled=true and supplies a "validation" block.

Usage:
  python .claude/agents/ch_6_validate_auto.py
  python .claude/agents/ch_6_validate_auto.py --feature 014-rich-text-formatting

What this stage does, for the commit at HEAD:
  1. Pushes the feature branch (never forced) and finds or opens its draft PR
     against main, with `Closes #N` when the spec came from an issue. A Claude
     agent writes the PR description from spec.md, plan.md and the docs/ diff;
     it is regenerated on later commits until a human takes it over by
     removing its marker.
  2. Runs the project's validation.deploy_command, which makes sure this
     commit is deployed to a non-production environment and prints its URL.
     A project with one shared environment also sets lock_command and
     unlock_command, which hold the environment from the deploy until the
     agent finishes; unlock_command runs however that ends.
  3. Runs the project's validation.auth_command, which writes a Playwright
     storage-state file so the agent starts logged in without seeing creds,
     then the optional seed_command, which creates data the agent can't
     create through a browser (e.g. an inbound email) and describes it.
  4. Has a Claude agent drive a recorded browser (Playwright MCP) through
     every acceptance scenario, success criterion, and quickstart section,
     writing one verdict per scenario to report.json. It first checks the
     preview is fit to validate on, and stops with a setup_problem if not.
  5. When the project sets validation.probe_commands, has an operator agent
     settle BLOCKED scenarios with those commands (its only tool), citing
     the transcripts the harness saves for each probe.
  6. Checks that report deterministically, posts it as one PR comment, and
     escalates any FAIL/BLOCKED scenario for a human.

The stage is report-only: it never fixes code, never commits (everything it
writes is gitignored — a commit would move HEAD off the deploy it just
validated), and never merges or marks the PR ready for review.

Resume behaviour:
  All output lives in specs/$FEATURE/validation/<sha12>/, so each commit is
  validated once. Re-running on an already-passed commit exits immediately;
  re-running on the same commit after an interruption reuses any report.json
  already written, and the cached deploy URL unless a lock_command is set (a
  shared environment may hold another deploy by then); a new commit starts
  fresh. auth_command and seed_command may run again on a resume, so they
  should reuse whatever they recorded in $VALIDATION_RUN_DIR.
  To re-validate the same commit from scratch, delete its validation dir.
"""

import asyncio
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookMatcher,
    ResultError,
    create_sdk_mcp_server,
    query,
    tool,
)

from agent_common import github_pr, ollama, pipeline_status
from agent_common import validation_report as vr
from agent_common.console import SessionLimitError, make_logger, setup_log_file, stream_query
from agent_common.critic_loop import run_cli
from agent_common.driving_agent import HUMAN_REVIEW_ATTESTATION_GUARDRAIL
from agent_common.files import read_file, read_optional, require_spec_files, write_file
from agent_common.followup import FOLLOWUP_FILENAME
from agent_common.git import branch_changes, dirty_files
from agent_common.github_deployment_url import SUPERSEDED_EXIT_CODE
from agent_common.resume_state import clear_stage_complete, stage_is_complete

AGENT_NAME = "ch-6-validate-auto"
STAGE = "ch-6-validate"
ESCALATION_FILENAME = "ch-6-validate-escalation.md"
PLAYWRIGHT_MCP_VERSION = "0.0.84"
RUN_CODE_TOOL = "mcp__playwright__browser_run_code_unsafe"
DEFAULT_TIMEOUT_MINUTES = 20
AUTH_TIMEOUT_S = 300
SEED_TIMEOUT_S = 15 * 60
DEFAULT_LOCK_TIMEOUT_MINUTES = 120
UNLOCK_TIMEOUT_S = 300
DEFAULT_MAX_TURNS = 300
# 098's 39 upload-heavy scenarios ran out of 300 turns at 14 and finished in 600.
TURNS_PER_SCENARIO = 16
DEFAULT_PROBE_MAX_TURNS = 150
PROBE_TIMEOUT_S = 120
PROBE_OUTPUT_LIMIT = 20_000
PROBE_TOOL = "mcp__probes__run_probe"
DESCRIPTION_MAX_TURNS = 10
DOCS_DIFF_LIMIT = 60_000
log = make_logger(AGENT_NAME)


def _fail(msg: str) -> None:
    log(f"ERROR: {msg}")
    sys.exit(1)


def preflight(spec_dir: Path) -> dict:
    require_spec_files(log, spec_dir, "spec.md")
    config = ollama.load_validation_config()
    if config is None:
        _fail(
            '.specify/pipeline.json has no "validation" block — see README § Deployed Validation.'
        )
    if problem := _config_problem(config, spec_dir):
        _fail(problem)
    if not stage_is_complete(spec_dir, "ch-4-implement"):
        _fail("ch-4-implement has not completed for this feature. Run it first.")
    if ollama.is_stage_enabled("docs") and not stage_is_complete(spec_dir, "ch-5-docs"):
        _fail("ch-5-docs has not completed for this feature. Run it first.")
    if ollama.load_local_llm_generation_config("validate") is not None:
        _fail(
            'generation.validate must be "default" or provider "claude": the validation '
            "agent needs MCP tools and vision, which only the Claude path provides."
        )
    # The spec dir is pipeline bookkeeping, not app code — and ch-5-docs leaves
    # its own completion marker uncommitted, which must not block this stage.
    spec_prefix = spec_dir.as_posix().rstrip("/") + "/"
    if dirty := [p for p in dirty_files() if not (p + "/").startswith(spec_prefix)]:
        _fail(
            "working tree has uncommitted changes, which the deployed preview would not "
            f"contain — commit or stash them first: {', '.join(dirty)}"
        )
    if not github_pr.gh_authenticated():
        _fail("gh is not authenticated. Run `gh auth login` and re-run.")
    return config


def _config_problem(config: dict, spec_dir: Path) -> str | None:
    if missing := [k for k in ("deploy_command", "auth_command") if not config.get(k)]:
        return f"validation config is missing: {', '.join(missing)}."
    if bool(config.get("lock_command")) != bool(config.get("unlock_command")):
        return "validation config must set lock_command and unlock_command together."
    commands = config.get("probe_commands")
    if commands is not None and (
        not isinstance(commands, dict)
        or not all(
            isinstance(c, dict) and c.get("command") and c.get("description")
            for c in commands.values()
        )
    ):
        return (
            'validation.probe_commands must map each probe name to {"command": ..., '
            '"description": ...}.'
        )
    if (fixtures := config.get("fixtures_dir")) and not _has_files(spec_dir / fixtures):
        # 051 and 072 deployed and ran with no fixtures; nearly every scenario came back BLOCKED.
        return (
            f"{spec_dir / fixtures} is missing or empty. Add this feature's validation fixtures "
            "there, or a NONE file saying why it needs none."
        )
    return None


def _has_files(path: Path) -> bool:
    return path.is_dir() and any(path.iterdir())


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def already_validated(spec_dir: Path, sha: str) -> bool:
    marker = spec_dir / f"{STAGE}-auto-complete"
    return marker.exists() and f"sha: {sha}" in marker.read_text(encoding="utf-8")


def write_complete_marker(spec_dir: Path, sha: str) -> None:
    write_file(
        spec_dir / f"{STAGE}-auto-complete", f"completed: true\nstage: {STAGE}\nsha: {sha}\n"
    )


def pr_title(spec: str, feature: str) -> str:
    for line in spec.splitlines():
        if line.startswith("# "):
            return line[2:].removeprefix("Feature Specification:").strip() or feature
    return feature


def description_instructions(
    feature: str,
    spec: str,
    plan: str,
    test_tasks: str,
    docs_diff: str,
    followup: str | None,
    body_path: Path,
) -> str:
    if len(docs_diff) > DOCS_DIFF_LIMIT:
        docs_diff = docs_diff[:DOCS_DIFF_LIMIT] + "\n[... docs diff truncated ...]"
    followup_rule = (
        f"Link `{followup}` and name its most important concerns in a sentence or two."
        if followup
        else 'Write "None".'
    )
    return f"""You write the pull request description for feature `{feature}` of a spec-kit
project. A reviewer reads it to learn what the feature does and why, before reading the code.

--- SPEC ---
{spec}

--- PLAN ---
{plan}

--- [TEST] TASKS ---
{test_tasks}

--- FILES CHANGED ON THIS BRANCH ---
{branch_changes("--stat")}

--- DOCS DIFF (the shipped behaviour, as merged into the project's docs/) ---
{docs_diff or "(none)"}

Write the description as GitHub markdown to `{body_path.resolve()}`, with these sections:
- `## Summary`: two or three sentences on the problem and what this PR does about it.
- `## What users get`: the user-visible changes, from the user stories and the docs diff.
- `## How it works`: the approach from the plan — the main components and data flow, naming
  the important files. Not a file-by-file changelog.
- `## Decisions worth a look`: choices a reviewer may want to question — resolved
  clarifications, trade-offs and deviations recorded in the plan. Skip the section if
  there are none.
- `## Testing`: what the automated tests cover, from the [TEST] tasks. Say that deployed
  validation of this commit is reported in a PR comment; don't predict its result.
- `## Follow-ups`: {followup_rule}

Rules:
- Describe only what the spec, plan, tasks and diffs above support; don't invent behaviour.
- Be concise: a reviewer should read it in two minutes. No title heading — the PR has one.
- Write nothing but that one file.
"""


def _system_prompt_file(run_dir: Path, name: str, instructions: str) -> dict:
    """The SDK passes a string system prompt to the CLI as one argument, which Linux
    caps at 128 KiB (MAX_ARG_STRLEN); a large feature's spec, plan and diffs exceed it."""
    path = run_dir / name
    write_file(path, instructions)
    return {"type": "file", "path": str(path.resolve())}


async def run_description_agent(instructions: str, run_dir: Path) -> None:
    options = ClaudeAgentOptions(
        system_prompt=_system_prompt_file(run_dir, "pr-description-prompt.md", instructions),
        tools=["Write"],
        allowed_tools=["Write"],
        setting_sources=[],
        model=ollama.load_claude_generation_model("validate"),
        max_turns=DESCRIPTION_MAX_TURNS,
        hooks={"PreToolUse": [HookMatcher(matcher="Write", hooks=[_run_dir_scope_hook(run_dir)])]},
    )
    await stream_query(query(prompt="Write the PR description.", options=options))


async def pr_body(feature: str, spec_dir: Path, spec: str, run_dir: Path) -> str:
    """The generated description, reused on resume of the same commit. Generation
    failing is logged, not fatal: the PR gets a stub that the next run replaces."""
    body_path = run_dir / "pr-body.md"
    if not body_path.exists():
        followup = spec_dir / FOLLOWUP_FILENAME
        instructions = description_instructions(
            feature,
            spec,
            read_optional(spec_dir / "plan.md", "(none)"),
            "\n".join(
                line.strip()
                for line in read_optional(spec_dir / "tasks.md", "").splitlines()
                if "[TEST]" in line
            )
            or "(none)",
            branch_changes("--", "docs/"),
            followup.as_posix() if followup.exists() else None,
            body_path,
        )
        try:
            await run_description_agent(instructions, run_dir)
        except SessionLimitError:
            raise
        except Exception as e:
            log(f"WARNING: PR description agent failed ({e}); using a stub.")
    description = read_optional(body_path, "").strip() or (
        f"Feature `{feature}` — see `specs/{feature}/spec.md`. (The description could not "
        "be generated; the next ch-6-validate-auto run retries.)"
    )
    return (
        f"{description}\n\n---\n"
        "_Draft opened by ch-6-validate-auto for a preview deploy; the validation report for "
        "each commit is in the comments. Delete the marker below to stop this description "
        f"being regenerated on later commits._\n{github_pr.GENERATED_MARKER}"
    )


def hook_env(
    feature: str, sha: str, branch: str, pr_number: int, spec_dir: Path, run_dir: Path
) -> dict:
    """The environment every project command (deploy, lock, auth, seed) runs with."""
    return {
        **os.environ,
        "VALIDATION_FEATURE": feature,
        "VALIDATION_SHA": sha,
        "VALIDATION_BRANCH": branch,
        "VALIDATION_PR_NUMBER": str(pr_number),
        "VALIDATION_SPEC_DIR": str(spec_dir.resolve()),
        "VALIDATION_RUN_DIR": str(run_dir.resolve()),
    }


def run_hook(
    config: dict, key: str, env: dict, timeout_s: float, capture: bool = False
) -> subprocess.CompletedProcess:
    log(f"Running {key}: {config[key]}")
    try:
        return subprocess.run(  # noqa: S602 — a project-authored shell command from pipeline.json
            config[key],
            shell=True,
            env=env,
            stdout=subprocess.PIPE if capture else None,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        _fail(f"{key} did not finish within {timeout_s / 60:g} minutes.")


@contextmanager
def environment_lock(config: dict, env: dict) -> Iterator[None]:
    """Hold the project's shared environment, via lock_command/unlock_command,
    for the duration of the block. A no-op when no lock_command is set."""
    if not config.get("lock_command"):
        yield
        return
    pipeline_status.step("waiting for environment lock")
    timeout_minutes = config.get("lock_timeout_minutes", DEFAULT_LOCK_TIMEOUT_MINUTES)
    if (code := run_hook(config, "lock_command", env, timeout_minutes * 60).returncode) != 0:
        _fail(f"lock_command exited {code}; the shared environment was not locked.")
    try:
        yield
    except BaseException:
        _release_environment(config, env)
        raise
    if not _release_environment(config, env):
        _fail("unlock_command failed, so the shared environment is still locked.")


def _release_environment(config: dict, env: dict) -> bool:
    try:
        code = subprocess.run(  # noqa: S602 — a project-authored shell command from pipeline.json
            config["unlock_command"], shell=True, env=env, timeout=UNLOCK_TIMEOUT_S
        ).returncode
    except subprocess.TimeoutExpired:
        code = None
    if code != 0:
        log(
            f"ERROR: unlock_command did not succeed (exit {code}). Release the shared "
            "environment by hand, or no other branch can validate."
        )
    return code == 0


def deploy(config: dict, env: dict, run_dir: Path) -> str:
    """Base URL of this commit's deployment, via the project's deploy_command —
    cached in run_dir so a resumed run doesn't wait on it again, except for a
    locked shared environment, whose contents may have changed since."""
    cached = run_dir / "base-url"
    if cached.exists() and not config.get("lock_command"):
        return cached.read_text(encoding="utf-8").strip()
    timeout_minutes = config.get("timeout_minutes", DEFAULT_TIMEOUT_MINUTES)
    result = run_hook(config, "deploy_command", env, timeout_minutes * 60, capture=True)
    if result.returncode == SUPERSEDED_EXIT_CODE:
        _fail(
            f"the deployment of {env['VALIDATION_SHA'][:12]} was superseded by a newer commit "
            f"on {env['VALIDATION_BRANCH']}. Pull it and re-run to validate the new head."
        )
    if result.returncode != 0:
        _fail(f"deploy_command exited {result.returncode}; see its output above.")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    url = lines[-1] if lines else ""
    if urlparse(url).scheme not in ("http", "https"):
        _fail(f"deploy_command's last stdout line must be the deployment URL, got {url!r}.")
    write_file(cached, url + "\n")
    return url


def login(config: dict, env: dict, base_url: str, run_dir: Path) -> Path:
    storage_state = (run_dir / "storage-state.json").resolve()
    env = {**env, "VALIDATION_BASE_URL": base_url, "VALIDATION_STORAGE_STATE": str(storage_state)}
    result = run_hook(config, "auth_command", env, AUTH_TIMEOUT_S)
    if result.returncode != 0 or not storage_state.exists():
        _fail(f"auth_command exited {result.returncode} without writing {storage_state}.")
    return storage_state


def seed(config: dict, env: dict, base_url: str) -> str:
    """What the project's seed_command created for the logged-in test account,
    in its own words (its stdout), for the agent to validate against."""
    if not config.get("seed_command"):
        return ""
    env = {**env, "VALIDATION_BASE_URL": base_url}
    result = run_hook(config, "seed_command", env, SEED_TIMEOUT_S, capture=True)
    if result.returncode != 0:
        _fail(f"seed_command exited {result.returncode}; see its output above.")
    return result.stdout.strip()


def mcp_version(config: dict) -> str:
    return config.get("playwright_mcp_version", PLAYWRIGHT_MCP_VERSION)


def _browser_dir() -> Path:
    return Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / ".cache/ms-playwright")


def _missing_browser_libs() -> list[str]:
    shells = sorted(_browser_dir().glob("chromium_headless_shell-*/*/chrome-headless-shell"))
    if not shells:
        return []
    ldd = subprocess.run(["ldd", str(shells[-1])], capture_output=True, text=True)
    return [line.split()[0] for line in ldd.stdout.splitlines() if "not found" in line]


def ensure_browser(config: dict) -> None:
    """Download the Chromium build the pinned MCP server's own Playwright
    expects — a project's e2e browsers are built for its own Playwright
    version and rarely match. A no-op once present (cached in
    ~/.cache/ms-playwright). Chromium's OS libraries need root, so they come
    from the harness base image instead; check them here so a stale image
    fails with a clear message rather than an all-BLOCKED report."""
    version = mcp_version(config)
    log(f"Ensuring Chromium for @playwright/mcp@{version}...")
    result = subprocess.run(
        ["npx", "-y", "-p", f"@playwright/mcp@{version}", "playwright", "install", "chromium"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _fail(f"Chromium download failed: {result.stderr.strip()[-500:]}")
    if missing := _missing_browser_libs():
        _fail(
            f"Chromium can't start — missing OS libraries: {', '.join(missing)}. Rebuild the "
            "dev container from the current coding-harness base image, which installs them."
        )


MCP_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "TMPDIR",
    "PLAYWRIGHT_BROWSERS_PATH",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)


def _mcp_env() -> list[str]:
    return [
        f"{key}={value}"
        for key, value in os.environ.items()
        if key in MCP_ENV_ALLOWLIST or key.lower().startswith("npm_config_")
    ]


def mcp_server(config: dict, base_url: str, storage_state: Path, run_dir: Path) -> dict:
    """The browser server starts in the run dir with --ignore-roots, which confines
    its file tools (e.g. browser_file_upload) to that dir instead of the checkout,
    and with only the environment npx and Chromium need, not the harness's AWS,
    GitHub or database credentials."""
    parsed = urlparse(base_url)
    origins = [f"{parsed.scheme}://{parsed.netloc}", *config.get("allowed_origins", [])]
    return {
        "type": "stdio",
        "command": "env",
        "args": [
            "-i",
            "-C",
            str(run_dir.resolve()),
            *_mcp_env(),
            "npx",
            "-y",
            f"@playwright/mcp@{mcp_version(config)}",
            "--browser=chromium",
            "--headless",
            "--isolated",
            "--ignore-roots",
            "--caps=devtools",
            "--viewport-size=1280x720",
            f"--storage-state={storage_state}",
            f"--output-dir={run_dir.resolve()}",
            f"--allowed-origins={';'.join(origins)}",
        ],
    }


def _run_dir_scope_hook(run_dir: Path):
    root = run_dir.resolve()

    async def hook(input_data: dict, tool_use_id, context) -> dict:
        path = Path((input_data.get("tool_input") or {}).get("file_path", ""))
        if path.resolve().is_relative_to(root):
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"This agent may only read or write under {root}.",
            }
        }

    return hook


def _manual_tasks(tasks: str) -> str:
    lines = [line.strip() for line in tasks.splitlines() if "[MANUAL]" in line]
    return "\n".join(lines) or "(none)"


def agent_instructions(
    scenarios: list[vr.Scenario],
    spec: str,
    quickstart: str,
    manual_tasks: str,
    base_url: str,
    run_dir: Path,
    agent_notes: str = "",
    seeded: str = "",
) -> str:
    run_path = run_dir.resolve()
    scenario_list = "\n".join(f"- {s.id}: {s.text}" for s in scenarios)
    project_notes = (
        f"--- PROJECT NOTES ---\n{agent_notes.strip()}\n\n" if agent_notes.strip() else ""
    )
    seeded_data = (
        "--- DATA SEEDED FOR THIS RUN ---\n"
        "Before you started, the project created this for the test account, through means a "
        "browser doesn't have. Use it for the scenarios it fits instead of creating your own, "
        "and show it in a setup recording (step 1) before the scenarios that rely on it.\n"
        f"{seeded.strip()}\n\n"
        if seeded.strip()
        else ""
    )
    return f"""You are the Validation Agent for a spec-kit project. You validate a feature on a
deployed, non-production preview of the app by driving a real browser, the way a careful human
tester would, and you record evidence a human will review.

The preview is at: {base_url}
The browser already holds a logged-in session for a test account.

{seeded_data}--- SPEC ---
{spec}

--- QUICKSTART ---
{quickstart}

--- [MANUAL] TASKS (context only) ---
{manual_tasks}

--- SCENARIOS YOU MUST REPORT ON (exactly these ids, each exactly once) ---
{scenario_list}

First, check the preview is fit to validate on: open {base_url} and confirm the app loads
for the logged-in account, `browser_console_messages` and `browser_network_requests` show no
requests failing because their origin is outside the allow-list, and any data seeded for this
run (above) shows where the app displays it. If it isn't fit, every scenario would be BLOCKED
for the same reason, so attempt none: write `{run_path}/report.json` as
{{"setup_problem": "<what is wrong, quoting the errors or blocked origins you saw>"}} and stop.

A human watches your recordings to decide whether to trust your verdicts, so every video
must show something they need to see, and nothing they have already seen:

1. Setup. If scenarios need data that doesn't exist yet, create all of it first, on camera:
   `browser_start_video` with filename `{run_path}/setup-<n>.webm`, then
   `browser_video_show_actions`. Create the data through the UI, or with `browser_evaluate`
   in the page (save the code under {run_path} and list it as evidence). Then browse to where the app shows that data (the
   lists and detail pages the checks will be computed from) so the video shows it.
   `browser_stop_video`, then `browser_close`. Add a step to the report's "setup" list.
2. Group the scenarios. Scenarios that check the same screen in the same data state share
   one recording; a scenario that changes data, or needs a different starting point, gets
   its own. A success criterion that restates acceptance scenarios goes in their group.
3. For each group: `browser_start_video` with filename `{run_path}/<first id in the
   group>.webm`, then `browser_video_show_actions`, then `browser_start_tracing`. Then, for
   each scenario in the group, in order:
   a. `browser_video_chapter` with the scenario id as title.
   b. Perform the scenario as a user would, starting from {base_url} — through the UI, not by
      guessing URLs, unless the scenario itself is about a URL.
   c. Check each expected outcome on its own: scroll the element into view, highlight it with
      `browser_highlight`, take a screenshot (filename `{run_path}/<id>-<n>.png`), then
      `browser_hide_highlight`. Never check outcomes by reading the whole page in one
      script — the recording then shows nothing of what you checked.
   End the group with `browser_stop_tracing`, then `browser_stop_video`, then
   `browser_close`, so the next group starts from a fresh browser.

Verdicts:
- PASS: you observed every expected outcome yourself, in the browser.
- FAIL: you observed behaviour that contradicts the expected outcome.
- BLOCKED: you could not exercise it (missing data, an error page unrelated to the scenario,
  behaviour that isn't observable in a browser, a step that needs access you don't have).
  Give the reason.
- NOT_APPLICABLE: QS-* scenarios only, for quickstart sections that only make sense in a
  local checkout (installing dependencies, running migrations, starting a dev server). Give
  the reason. Never use it for a section a user could follow on the deployed app.

Write `{run_path}/report.json` (update it after every scenario, so progress survives an
interruption) in exactly this shape:
{{"setup": [{{"recording": "setup-<n>.webm", "created": "<the data you created, and how>",
  "evidence": ["<absolute path of each setup script>", ...]}}],
 "scenarios": [{{"id": "<id>", "verdict": "PASS|FAIL|BLOCKED|NOT_APPLICABLE",
  "recording": "<file name of the video this scenario is a chapter of>",
  "steps": ["<what you did>", ...], "observed": "<what you saw>",
  "reason": "<required for BLOCKED/NOT_APPLICABLE>",
  "evidence": ["<absolute path of each screenshot/trace file you produced>", ...]}}]}}
Leave "setup" empty if you created no data.

{project_notes}Rules:
- This is a preview environment, but treat its data with care: create only what a scenario
  needs, name anything you create with a "[validation]" prefix, and never delete or modify
  data you did not create. Never log out, delete the account, or change its credentials.
- Create data at the pace a single user would: one request at a time, never in parallel. A
  preview's database and functions are small and shared, and its errors may alert people. If
  a request fails, retry it after a pause rather than speeding up.
- Stay on the preview's origin; other origins are blocked.
- Never report PASS for something you did not observe. An honest FAIL or BLOCKED is useful;
  a wrong PASS hides a bug from the human reviewer.
- You do not fix anything. You only read and write files under {run_path}.
{HUMAN_REVIEW_ATTESTATION_GUARDRAIL}
"""


async def run_validation_agent(
    instructions: str, task: str, config: dict, mcp: dict, run_dir: Path
) -> None:
    options = ClaudeAgentOptions(
        system_prompt=_system_prompt_file(run_dir, "agent-prompt.md", instructions),
        tools=["Read", "Write"],
        allowed_tools=["Read", "Write", "mcp__playwright"],
        # Runs Node code in the MCP server process, outside every other limit here.
        disallowed_tools=[RUN_CODE_TOOL],
        mcp_servers={"playwright": mcp},
        setting_sources=[],
        model=ollama.load_claude_generation_model("validate"),
        max_turns=config.get("max_turns", DEFAULT_MAX_TURNS),
        # The spec and quickstart are already in the prompt, so the agent never needs the repo;
        # keeping it out stops a hostile preview page steering it into reading local secrets.
        hooks={
            "PreToolUse": [HookMatcher(matcher="Read|Write", hooks=[_run_dir_scope_hook(run_dir)])]
        },
    )
    await stream_query(query(prompt=task, options=options))


def checked_report(
    report_path: Path, scenarios: list[vr.Scenario], run_dir: Path
) -> tuple[dict | None, list[str]]:
    report, error = vr.load_report(report_path)
    if report is None:
        return None, [error]
    if problem := vr.setup_problem(report):
        return report, [f"setup problem: {problem}"]
    return report, vr.check_report(report, scenarios, run_dir)


def escalate(spec_dir: Path, feature: str, sha: str, title: str, body: str) -> None:
    pipeline_status.step(pipeline_status.ESCALATED)
    path = spec_dir / ESCALATION_FILENAME
    write_file(
        path,
        f"# {title}\n\nFeature: {feature}\nCommit: {sha}\n\n{body}\n\n"
        "## Required Action\n\n"
        "Fix the cause (code, data, or the validation config), commit, and re-run "
        "`python .claude/agents/ch_6_validate_auto.py` — a new commit is validated from "
        "scratch. If the verdict itself is wrong, delete "
        f"`specs/{feature}/validation/{sha[:12]}/` and re-run to validate this commit again.\n",
    )
    log(f"Human review required → {path}")
    sys.exit(1)


async def open_pr(spec: str, feature: str, branch: str, spec_dir: Path, run_dir: Path) -> dict:
    try:
        pipeline_status.step("pushing branch")
        github_pr.push_branch()
        existing = github_pr.find_pr(branch)
        body = ""
        if existing is None or github_pr.harness_owns_body(existing["body"]):
            pipeline_status.step("describing PR")
            body = await pr_body(feature, spec_dir, spec, run_dir)
        pipeline_status.step("opening draft PR")
        pr = github_pr.ensure_draft_pr(branch, pr_title(spec, feature), body, vr.source_issue(spec))
    except github_pr.GitHubError as e:
        _fail(str(e))
    log(f"PR: {pr['url']}")
    if pr.get("baseRefName", github_pr.BASE_BRANCH) != github_pr.BASE_BRANCH:
        log(
            f"WARNING: PR targets {pr['baseRefName']}, not {github_pr.BASE_BRANCH}. "
            "Retarget it before merging."
        )
    return pr


def finished_report(run_dir: Path, scenarios: list[vr.Scenario]) -> dict | None:
    report, problems = checked_report(run_dir / "report.json", scenarios, run_dir)
    return report if report is not None and not problems else None


async def produce_report(
    config: dict,
    env: dict,
    spec_dir: Path,
    feature: str,
    sha: str,
    spec: str,
    quickstart: str,
    scenarios: list[vr.Scenario],
    base_url: str,
    run_dir: Path,
) -> dict:
    """A report.json that passes check_report, produced by the validation agent
    with one retry for an incomplete report."""
    report_path = run_dir / "report.json"
    report, problems = checked_report(report_path, scenarios, run_dir)
    if report is not None and vr.setup_problem(report):
        report_path.unlink()
        report, problems = None, []
    pipeline_status.step("preparing browser")
    ensure_browser(config)
    pipeline_status.step("logging in")
    storage_state = login(config, env, base_url, run_dir)
    pipeline_status.step("seeding data")
    seeded = seed(config, env, base_url)
    mcp = mcp_server(config, base_url, storage_state, run_dir)
    instructions = agent_instructions(
        scenarios,
        spec,
        quickstart or "(none)",
        _manual_tasks(read_optional(spec_dir / "tasks.md", "")),
        base_url,
        run_dir,
        config.get("agent_notes", ""),
        seeded,
    )
    config = {**config, "max_turns": agent_max_turns(config, scenarios)}
    if not report_path.exists():
        pipeline_status.step("validating")
        task = f"Validate feature {feature} on {base_url}."
        await _run_until_out_of_turns(instructions, task, config, mcp, run_dir)
        report, problems = checked_report(report_path, scenarios, run_dir)
        _escalate_setup_problem(spec_dir, feature, sha, base_url, report)
    if problems:
        log(f"Report incomplete ({len(problems)} problem(s)); asking the agent to finish it.")
        pipeline_status.step("completing report")
        task = (
            "Your report.json is incomplete or invalid. Fix exactly these problems — "
            "re-running a scenario (with a fresh recording) where evidence is missing — "
            "and leave correct entries alone:\n- " + "\n- ".join(problems)
        )
        await _run_until_out_of_turns(instructions, task, config, mcp, run_dir)
        report, problems = checked_report(report_path, scenarios, run_dir)
    if problems:
        escalate(
            spec_dir,
            feature,
            sha,
            "Validation Report Invalid",
            "The validation agent's report is still incomplete after one retry, so it "
            "can't be used as evidence:\n\n" + "\n".join(f"- {p}" for p in problems),
        )
    if operator_pending(config, report):
        probe_env = {**env, "VALIDATION_BASE_URL": base_url}
        report = await run_operator_phase(
            config, probe_env, spec, seeded, scenarios, run_dir, report
        )
    return report


def agent_max_turns(config: dict, scenarios: list[vr.Scenario]) -> int:
    return config.get("max_turns") or max(DEFAULT_MAX_TURNS, TURNS_PER_SCENARIO * len(scenarios))


async def _run_until_out_of_turns(
    instructions: str, task: str, config: dict, mcp: dict, run_dir: Path
) -> None:
    """Run the validation agent; running out of turns ends the run, not the
    stage, so the verdicts it saved are kept and the next pass finishes the rest."""
    try:
        await run_validation_agent(instructions, task, config, mcp, run_dir)
    except ResultError as e:
        if "maximum number of turns" not in str(e):
            raise
        log(
            f"The validation agent used all {config['max_turns']} turns; keeping its report so far."
        )


def _escalate_setup_problem(
    spec_dir: Path, feature: str, sha: str, base_url: str, report: dict | None
) -> None:
    if report is None or not (problem := vr.setup_problem(report)):
        return
    escalate(
        spec_dir,
        feature,
        sha,
        "Validation Environment Not Ready",
        f"The validation agent checked {base_url} before any scenario and stopped:\n\n"
        f"> {problem}\n\nFix the environment or the validation config (e.g. "
        "`allowed_origins`, `seed_command`, fixtures), then re-run; this commit's run "
        "starts again from the browser check.",
    )


def operator_pending(config: dict, report: dict) -> bool:
    return bool(
        config.get("probe_commands")
        and report.get("operator_phase") != "done"
        and vr.blocked(report)
    )


def _next_transcript(run_dir: Path) -> Path:
    taken = {p.name for p in run_dir.glob(vr.PROBE_TRANSCRIPT_GLOB)}
    n = len(taken) + 1
    while f"probe-{n}.txt" in taken:
        n += 1
    return run_dir.resolve() / f"probe-{n}.txt"


def run_probe(command: str, args: list[str], env: dict, run_dir: Path) -> tuple[Path, str]:
    """Run one project probe with the agent's arguments, each shell-quoted, and
    save the transcript the agent must cite: its only evidence is what the
    harness itself recorded, not the agent's account of it."""
    line = " ".join([command, *(shlex.quote(a) for a in args)])
    try:
        result = subprocess.run(  # noqa: S602 — a project-authored command; args are quoted
            line,
            shell=True,
            env=env,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
        )
        status, output = f"exit {result.returncode}", result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        status, output = f"timed out after {PROBE_TIMEOUT_S}s", ""
    transcript = _next_transcript(run_dir)
    write_file(transcript, f"$ {line}\n{status}\n{output}")
    return transcript, f"{status}\n{output[:PROBE_OUTPUT_LIMIT]}"


def probe_server(commands: dict, env: dict, run_dir: Path) -> dict:
    @tool(
        "run_probe",
        "Run one of the project's probe commands with the given arguments. Returns its exit "
        "status and output, and the path of the transcript to cite as evidence.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "enum": list(commands)},
                "args": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name"],
        },
    )
    async def probe(params: dict) -> dict:
        name = params.get("name")
        if name not in commands:
            return {
                "content": [{"type": "text", "text": f"No probe named {name!r}."}],
                "is_error": True,
            }
        args = [str(a) for a in params.get("args") or []]
        transcript, output = await asyncio.to_thread(
            run_probe, commands[name]["command"], args, env, run_dir
        )
        log(f"Probe {name} {' '.join(args)[:120]} → {transcript.name}")
        return {"content": [{"type": "text", "text": f"Transcript: {transcript}\n{output}"}]}

    return create_sdk_mcp_server("probes", tools=[probe])


def operator_instructions(
    commands: dict, blocked_entries: list[dict], spec: str, seeded: str, run_dir: Path, notes: str
) -> str:
    run_path = run_dir.resolve()
    probes = "\n".join(f"- {name}: {c['description']}" for name, c in commands.items())
    entries = json.dumps(blocked_entries, indent=2)
    project_notes = f"--- PROJECT NOTES ---\n{notes.strip()}\n\n" if notes.strip() else ""
    seeded_data = (
        f"--- DATA SEEDED FOR THIS RUN ---\n{seeded.strip()}\n\n" if seeded.strip() else ""
    )
    return f"""You are the Operator Agent for a spec-kit project. A browser agent has validated a
feature on a deployed preview, and the scenarios below came back BLOCKED: their outcome isn't
visible in a browser (a database row, a log line, a queue, an alarm). You check them the way an
operator would, with the project's probe commands. You have no browser and no shell: your only
way to look at the system is the `run_probe` tool.

--- PROBE COMMANDS ---
{probes}

--- SPEC ---
{spec}

{seeded_data}--- BLOCKED SCENARIOS (their entries in {run_path}/report.json) ---
{entries}

For each blocked scenario:
- If probes can show its expected outcome, run them and decide. PASS only when the probe output
  itself shows every expected outcome; FAIL when it contradicts one. Rewrite the entry with
  "verdict", "method": "probe", "steps" (the probes you ran and why), "observed" (what their
  output showed), and "evidence" (the transcript path `run_probe` returned for each probe).
- Otherwise leave it BLOCKED, updating "reason" if you learned more.
Edit only these entries in report.json; leave every other entry, and "setup", unchanged.

{project_notes}Rules:
- Probes are for looking. Never use one to create, change or delete data unless its description
  says that is what it does, and then only for the test account and data this run created.
- Never report PASS for something the probe output doesn't show. An honest BLOCKED is useful;
  a wrong PASS hides a bug from the human reviewer.
- You do not fix anything. You only read and write files under {run_path}.
{HUMAN_REVIEW_ATTESTATION_GUARDRAIL}
"""


async def run_operator_phase(
    config: dict,
    env: dict,
    spec: str,
    seeded: str,
    scenarios: list[vr.Scenario],
    run_dir: Path,
    report: dict,
) -> dict:
    """Let the operator agent settle BLOCKED scenarios with the project's probe
    commands. Only its PASS/FAIL/BLOCKED rewrites of BLOCKED entries that pass
    the report checks are kept; everything else stays as the browser agent left it."""
    pipeline_status.step("probing")
    targets = vr.blocked(report)
    log(f"Operator phase: {len(targets)} blocked scenario(s) to check with probes.")
    commands = config["probe_commands"]
    options = ClaudeAgentOptions(
        system_prompt=_system_prompt_file(
            run_dir,
            "operator-prompt.md",
            operator_instructions(
                commands, targets, spec, seeded, run_dir, config.get("agent_notes", "")
            ),
        ),
        tools=["Read", "Write"],
        allowed_tools=["Read", "Write", PROBE_TOOL],
        mcp_servers={"probes": probe_server(commands, env, run_dir)},
        setting_sources=[],
        model=ollama.load_claude_generation_model("validate"),
        max_turns=config.get("probe_max_turns", DEFAULT_PROBE_MAX_TURNS),
        hooks={
            "PreToolUse": [HookMatcher(matcher="Read|Write", hooks=[_run_dir_scope_hook(run_dir)])]
        },
    )
    await stream_query(query(prompt="Check the blocked scenarios with probes.", options=options))
    rewritten, _ = vr.load_report(run_dir / "report.json")
    report = merge_operator_verdicts(report, rewritten, scenarios, run_dir)
    write_file(run_dir / "report.json", json.dumps(report, indent=2) + "\n")
    return report


def merge_operator_verdicts(
    before: dict, after: dict | None, scenarios: list[vr.Scenario], run_dir: Path
) -> dict:
    by_id = {s.id: s for s in scenarios}
    rewritten = {
        e.get("id"): e
        for e in (after or {}).get("scenarios", [])
        if isinstance(e, dict) and e.get("id") in by_id
    }
    merged, settled = [], 0
    for entry in before["scenarios"]:
        candidate = rewritten.get(entry["id"])
        if (
            entry["verdict"] == vr.BLOCKED
            and candidate is not None
            and candidate.get("verdict") in (vr.PASS, vr.FAIL, vr.BLOCKED)
            and (candidate["verdict"] == vr.BLOCKED or candidate.get("method") == vr.PROBE_METHOD)
            and not vr.entry_problems(candidate, by_id[entry["id"]], run_dir)
        ):
            settled += candidate["verdict"] != vr.BLOCKED
            merged.append(candidate)
        else:
            merged.append(entry)
    log(f"Operator phase settled {settled} of {len(vr.blocked(before))} blocked scenario(s).")
    return {**before, "scenarios": merged, "operator_phase": "done"}


def verdict_counts(report: dict) -> str:
    """The authoritative tally, logged because the agent's own closing summary can miscount."""
    counts: dict[str, int] = {}
    for entry in report["scenarios"]:
        counts[entry["verdict"]] = counts.get(entry["verdict"], 0) + 1
    return f"{len(report['scenarios'])} scenario(s): " + ", ".join(
        f"{n} {verdict}" for verdict, n in sorted(counts.items())
    )


async def run(feature: str):
    spec_dir = Path(f"specs/{feature}")
    setup_log_file(spec_dir / f"{AGENT_NAME}.log")
    log(f"Starting ch-6-validate-auto for feature: {feature}")

    if not ollama.is_stage_enabled("validate"):
        log(
            "Validate stage is disabled (it is opt-in: set stages.validate.enabled=true in "
            ".specify/pipeline.json) — nothing to do."
        )
        return

    config = preflight(spec_dir)
    sha = _git("rev-parse", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if already_validated(spec_dir, sha):
        log(f"{sha[:12]} already passed validation. No further action taken.")
        return
    clear_stage_complete(spec_dir, STAGE)
    (spec_dir / ESCALATION_FILENAME).unlink(missing_ok=True)

    run_dir = spec_dir / "validation" / sha[:12]
    run_dir.mkdir(parents=True, exist_ok=True)
    spec = read_file(spec_dir / "spec.md")
    quickstart = read_optional(spec_dir / "quickstart.md", "")
    scenarios = vr.extract_scenarios(spec, quickstart)
    if not scenarios:
        _fail("spec.md has no acceptance scenarios or success criteria to validate.")
    log(f"{len(scenarios)} scenario(s) to validate: {', '.join(s.id for s in scenarios)}")

    pr = await open_pr(spec, feature, branch, spec_dir, run_dir)
    if (report := finished_report(run_dir, scenarios)) and not operator_pending(config, report):
        base_url = (run_dir / "base-url").read_text(encoding="utf-8").strip()
    else:
        env = hook_env(feature, sha, branch, pr["number"], spec_dir, run_dir)
        with environment_lock(config, env):
            pipeline_status.step("waiting for deploy")
            base_url = deploy(config, env, run_dir)
            log(f"Deployed: {base_url}")
            report = await produce_report(
                config, env, spec_dir, feature, sha, spec, quickstart, scenarios, base_url, run_dir
            )

    log(f"Verdicts from report.json: {verdict_counts(report)}")
    markdown = vr.render_markdown(report, scenarios, sha, base_url, run_dir)
    write_file(run_dir / "report.md", markdown)
    pipeline_status.step("posting report")
    try:
        posted = github_pr.post_comment_once(pr["number"], markdown, vr.comment_marker(sha))
    except github_pr.GitHubError as e:
        _fail(str(e))
    log("Report posted to the PR." if posted else "Report already on the PR.")

    if failing := vr.needs_attention(report):
        escalate(
            spec_dir,
            feature,
            sha,
            "Validation Failed",
            f"{len(failing)} scenario(s) did not pass on {base_url}:\n\n"
            + "\n".join(
                f"- **{e['id']}** {e['verdict']}: {e.get('reason') or e.get('observed', '')}"
                for e in failing
            )
            + f"\n\nRecordings: `{run_dir}/`. Full report: `{run_dir}/report.md`.",
        )

    write_complete_marker(spec_dir, sha)
    log(
        f"All scenarios validated. Review the recordings in {run_dir}/, then mark "
        f"{pr['url']} ready for review."
    )


if __name__ == "__main__":
    run_cli(AGENT_NAME, "Deployed-validation orchestrator", run)

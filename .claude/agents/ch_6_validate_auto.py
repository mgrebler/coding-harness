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
     against main, with `Closes #N` when the spec came from an issue.
  2. Runs the project's validation.deploy_command, which makes sure this
     commit is deployed to a non-production environment and prints its URL.
  3. Runs the project's validation.auth_command, which writes a Playwright
     storage-state file so the agent starts logged in without seeing creds.
  4. Has a Claude agent drive a recorded browser (Playwright MCP) through
     every acceptance scenario, success criterion, and quickstart section,
     writing one verdict per scenario to report.json.
  5. Checks that report deterministically, posts it as one PR comment, and
     escalates any FAIL/BLOCKED scenario for a human.

The stage is report-only: it never fixes code, never commits (everything it
writes is gitignored — a commit would move HEAD off the deploy it just
validated), and never merges or marks the PR ready for review.

Resume behaviour:
  All output lives in specs/$FEATURE/validation/<sha12>/, so each commit is
  validated once. Re-running on an already-passed commit exits immediately;
  re-running on the same commit after an interruption reuses the cached
  deploy URL and any report.json already written; a new commit starts fresh.
  To re-validate the same commit from scratch, delete its validation dir.
"""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, query

from agent_common import github_pr, ollama, pipeline_status
from agent_common import validation_report as vr
from agent_common.console import make_logger, setup_log_file, stream_query
from agent_common.critic_loop import run_cli
from agent_common.driving_agent import HUMAN_REVIEW_ATTESTATION_GUARDRAIL
from agent_common.files import read_file, read_optional, require_spec_files, write_file
from agent_common.git import dirty_files
from agent_common.github_deployment_url import SUPERSEDED_EXIT_CODE
from agent_common.resume_state import clear_stage_complete, stage_is_complete

AGENT_NAME = "ch-6-validate-auto"
STAGE = "ch-6-validate"
ESCALATION_FILENAME = "ch-6-validate-escalation.md"
PLAYWRIGHT_MCP_VERSION = "0.0.84"
DEFAULT_TIMEOUT_MINUTES = 20
AUTH_TIMEOUT_S = 300
DEFAULT_MAX_TURNS = 300
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
    missing = [k for k in ("deploy_command", "auth_command") if not config.get(k)]
    if missing:
        _fail(f"validation config is missing: {', '.join(missing)}.")
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


def pr_body(feature: str) -> str:
    return (
        f"Feature `{feature}` — see `specs/{feature}/spec.md`.\n\n"
        "Opened as a draft by ch-6-validate-auto so this branch gets a preview deploy "
        "for automated validation. A human marks it ready for review after reviewing the "
        "validation recordings; no agent merges it."
    )


def deploy(config: dict, run_dir: Path, sha: str, branch: str, pr_number: int) -> str:
    """Base URL of this commit's deployment, via the project's deploy_command —
    cached in run_dir so a resumed run doesn't wait on it again."""
    cached = run_dir / "base-url"
    if cached.exists():
        return cached.read_text(encoding="utf-8").strip()
    timeout_minutes = config.get("timeout_minutes", DEFAULT_TIMEOUT_MINUTES)
    env = {
        **os.environ,
        "VALIDATION_SHA": sha,
        "VALIDATION_BRANCH": branch,
        "VALIDATION_PR_NUMBER": str(pr_number),
    }
    log(f"Running deploy_command (timeout {timeout_minutes} min): {config['deploy_command']}")
    try:
        result = subprocess.run(  # noqa: S602 — deploy_command is a project-authored shell command
            config["deploy_command"],
            shell=True,
            env=env,
            stdout=subprocess.PIPE,
            text=True,
            timeout=timeout_minutes * 60,
        )
    except subprocess.TimeoutExpired:
        _fail(f"deploy_command did not finish within {timeout_minutes} minutes.")
    if result.returncode == SUPERSEDED_EXIT_CODE:
        _fail(
            f"the deployment of {sha[:12]} was superseded by a newer commit on {branch}. "
            "Pull it and re-run to validate the new head."
        )
    if result.returncode != 0:
        _fail(f"deploy_command exited {result.returncode}; see its output above.")
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    url = lines[-1] if lines else ""
    if urlparse(url).scheme not in ("http", "https"):
        _fail(f"deploy_command's last stdout line must be the deployment URL, got {url!r}.")
    write_file(cached, url + "\n")
    return url


def login(config: dict, base_url: str, run_dir: Path) -> Path:
    storage_state = (run_dir / "storage-state.json").resolve()
    env = {
        **os.environ,
        "VALIDATION_BASE_URL": base_url,
        "VALIDATION_STORAGE_STATE": str(storage_state),
    }
    log(f"Running auth_command: {config['auth_command']}")
    try:
        result = subprocess.run(  # noqa: S602 — auth_command is a project-authored shell command
            config["auth_command"], shell=True, env=env, timeout=AUTH_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        _fail(f"auth_command did not finish within {AUTH_TIMEOUT_S}s.")
    if result.returncode != 0 or not storage_state.exists():
        _fail(f"auth_command exited {result.returncode} without writing {storage_state}.")
    return storage_state


def mcp_server(config: dict, base_url: str, storage_state: Path, run_dir: Path) -> dict:
    parsed = urlparse(base_url)
    origins = [f"{parsed.scheme}://{parsed.netloc}", *config.get("allowed_origins", [])]
    version = config.get("playwright_mcp_version", PLAYWRIGHT_MCP_VERSION)
    return {
        "type": "stdio",
        "command": "npx",
        "args": [
            "-y",
            f"@playwright/mcp@{version}",
            "--headless",
            "--isolated",
            "--caps=devtools",
            "--viewport-size=1280x720",
            f"--storage-state={storage_state}",
            f"--output-dir={run_dir.resolve()}",
            f"--allowed-origins={';'.join(origins)}",
        ],
    }


def _write_scope_hook(run_dir: Path):
    root = run_dir.resolve()

    async def hook(input_data: dict, tool_use_id, context) -> dict:
        path = Path((input_data.get("tool_input") or {}).get("file_path", ""))
        if path.resolve().is_relative_to(root):
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"The validation agent may only write under {root}.",
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
) -> str:
    run_path = run_dir.resolve()
    scenario_list = "\n".join(f"- {s.id}: {s.text}" for s in scenarios)
    return f"""You are the Validation Agent for a spec-kit project. You validate a feature on a
deployed, non-production preview of the app by driving a real browser, the way a careful human
tester would, and you record evidence a human will review.

The preview is at: {base_url}
The browser already holds a logged-in session for a test account.

--- SPEC ---
{spec}

--- QUICKSTART ---
{quickstart}

--- [MANUAL] TASKS (context only) ---
{manual_tasks}

--- SCENARIOS YOU MUST REPORT ON (exactly these ids, each exactly once) ---
{scenario_list}

For EACH scenario, in order:
1. `browser_start_video` with filename `{run_path}/<id>.webm`, then `browser_video_chapter`
   with the scenario id as title, then `browser_video_show_actions`.
2. `browser_start_tracing`.
3. Navigate from {base_url} and perform the scenario as a user would — through the UI, not by
   guessing URLs, unless the scenario itself is about a URL. Take a screenshot (filename
   `{run_path}/<id>-<n>.png`) at each point where you check an expected outcome.
4. `browser_stop_tracing`, then `browser_stop_video`, then `browser_close`, so the next
   scenario gets its own recording.

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
{{"scenarios": [{{"id": "<id>", "verdict": "PASS|FAIL|BLOCKED|NOT_APPLICABLE",
  "steps": ["<what you did>", ...], "observed": "<what you saw>",
  "reason": "<required for BLOCKED/NOT_APPLICABLE>",
  "evidence": ["<absolute path of each screenshot/trace file you produced>", ...]}}]}}

Rules:
- This is a preview environment, but treat its data with care: create only what a scenario
  needs, name anything you create with a "[validation]" prefix, and never delete or modify
  data you did not create. Never log out, delete the account, or change its credentials.
- Stay on the preview's origin; other origins are blocked.
- Never report PASS for something you did not observe. An honest FAIL or BLOCKED is useful;
  a wrong PASS hides a bug from the human reviewer.
- You do not fix anything. You only write files under {run_path}.
{HUMAN_REVIEW_ATTESTATION_GUARDRAIL}
"""


async def run_validation_agent(
    instructions: str, task: str, config: dict, mcp: dict, run_dir: Path
) -> None:
    options = ClaudeAgentOptions(
        system_prompt=instructions,
        tools=["Read", "Write", "Glob", "Grep"],
        allowed_tools=["Read", "Write", "Glob", "Grep", "mcp__playwright"],
        mcp_servers={"playwright": mcp},
        setting_sources=[],
        model=ollama.load_claude_generation_model("validate"),
        max_turns=config.get("max_turns", DEFAULT_MAX_TURNS),
        hooks={"PreToolUse": [HookMatcher(matcher="Write", hooks=[_write_scope_hook(run_dir)])]},
    )
    await stream_query(query(prompt=task, options=options))


def checked_report(
    report_path: Path, scenarios: list[vr.Scenario], run_dir: Path
) -> tuple[dict | None, list[str]]:
    report, error = vr.load_report(report_path)
    if report is None:
        return None, [error]
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


def open_pr(spec: str, feature: str, branch: str) -> dict:
    try:
        pipeline_status.step("pushing branch")
        github_pr.push_branch()
        pipeline_status.step("opening draft PR")
        pr = github_pr.ensure_draft_pr(
            branch, pr_title(spec, feature), pr_body(feature), vr.source_issue(spec)
        )
    except github_pr.GitHubError as e:
        _fail(str(e))
    log(f"PR: {pr['url']}")
    if pr.get("baseRefName", github_pr.BASE_BRANCH) != github_pr.BASE_BRANCH:
        log(
            f"WARNING: PR targets {pr['baseRefName']}, not {github_pr.BASE_BRANCH}. "
            "Retarget it before merging."
        )
    return pr


async def produce_report(
    config: dict,
    spec_dir: Path,
    feature: str,
    sha: str,
    spec: str,
    quickstart: str,
    scenarios: list[vr.Scenario],
    base_url: str,
    run_dir: Path,
) -> dict:
    """A report.json that passes check_report — reused as-is on resume, else
    produced by the validation agent with one retry for an incomplete report."""
    report_path = run_dir / "report.json"
    report, problems = checked_report(report_path, scenarios, run_dir)
    if report is not None and not problems:
        return report
    pipeline_status.step("logging in")
    storage_state = login(config, base_url, run_dir)
    mcp = mcp_server(config, base_url, storage_state, run_dir)
    instructions = agent_instructions(
        scenarios,
        spec,
        quickstart or "(none)",
        _manual_tasks(read_optional(spec_dir / "tasks.md", "")),
        base_url,
        run_dir,
    )
    if not report_path.exists():
        pipeline_status.step("validating")
        task = f"Validate feature {feature} on {base_url}."
        await run_validation_agent(instructions, task, config, mcp, run_dir)
        report, problems = checked_report(report_path, scenarios, run_dir)
    if problems:
        log(f"Report incomplete ({len(problems)} problem(s)); asking the agent to finish it.")
        pipeline_status.step("completing report")
        task = (
            "Your report.json is incomplete or invalid. Fix exactly these problems — "
            "re-running a scenario (with a fresh recording) where evidence is missing — "
            "and leave correct entries alone:\n- " + "\n- ".join(problems)
        )
        await run_validation_agent(instructions, task, config, mcp, run_dir)
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
    return report


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

    pr = open_pr(spec, feature, branch)
    pipeline_status.step("waiting for deploy")
    base_url = deploy(config, run_dir, sha, branch, pr["number"])
    log(f"Deployed: {base_url}")
    report = await produce_report(
        config, spec_dir, feature, sha, spec, quickstart, scenarios, base_url, run_dir
    )

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

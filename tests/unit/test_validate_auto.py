"""Unit tests for ch_6_validate_auto.py with git/gh/deploy/auth and the
validation agent mocked: opt-in gating, preflight, the deploy_command
contract, the generated PR description, per-commit resume, the one-retry
report check, and escalation.
No LLM calls, no browser."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
import ch_6_validate_auto as va

SHA = "c" * 40
FEATURE = "012-x"
SPEC = """# Feature Specification: Saved searches

### User Story 1 - Save (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a search, **When** I save it, **Then** it is listed
"""
CONFIG = {"deploy_command": "deploy", "auth_command": "login"}


async def _describe(instructions, run_dir):
    (run_dir / "pr-body.md").write_text("## Summary\nSaves searches.\n")


class _ProjectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.orig_cwd = Path.cwd()
        self.orig_streams = sys.stdout, sys.stderr
        os.chdir(self.root)
        self.spec_dir = Path("specs") / FEATURE
        self.spec_dir.mkdir(parents=True)
        (self.spec_dir / "spec.md").write_text(SPEC)
        for stage in ("ch-4-implement", "ch-5-docs"):
            (self.spec_dir / f"{stage}-auto-complete").write_text("completed: true\n")
        self._config({"stages": {"validate": {"enabled": True}}, "validation": CONFIG})

    def tearDown(self):
        os.chdir(self.orig_cwd)
        sys.stdout, sys.stderr = self.orig_streams
        self.tmp.cleanup()

    def _config(self, config):
        (self.root / ".specify").mkdir(exist_ok=True)
        (self.root / ".specify/pipeline.json").write_text(json.dumps(config))


class TestRunDisabled(_ProjectCase):
    def test_off_by_default(self):
        self._config({"validation": CONFIG})
        with patch.object(va, "preflight") as preflight:
            asyncio.run(va.run(FEATURE))
        preflight.assert_not_called()


class TestPreflight(_ProjectCase):
    def _preflight(self, dirty=(), authed=True):
        with (
            patch.object(va, "dirty_files", return_value=list(dirty)),
            patch.object(va.github_pr, "gh_authenticated", return_value=authed),
            patch.object(va, "log"),
        ):
            return va.preflight(self.spec_dir)

    def test_passes(self):
        self.assertEqual(self._preflight(), CONFIG)

    def test_spec_dir_changes_do_not_block(self):
        self._preflight(dirty=[f"specs/{FEATURE}/ch-5-docs-auto-complete"])

    def test_blocks(self):
        cases = {
            "dirty app code": lambda: self._preflight(dirty=["src/app.ts"]),
            "gh unauthenticated": lambda: self._preflight(authed=False),
        }
        for name, call in cases.items():
            with self.subTest(name), self.assertRaises(SystemExit):
                call()

    def test_missing_config_keys(self):
        self._config({"validation": {"deploy_command": "d"}})
        with self.assertRaises(SystemExit):
            self._preflight()

    def test_lock_without_unlock_blocks(self):
        self._config({"validation": {**CONFIG, "lock_command": "lock"}})
        with self.assertRaises(SystemExit):
            self._preflight()

    def test_docs_incomplete_blocks_unless_disabled(self):
        (self.spec_dir / "ch-5-docs-auto-complete").unlink()
        with self.assertRaises(SystemExit):
            self._preflight()
        self._config({"stages": {"docs": {"enabled": False}}, "validation": CONFIG})
        self._preflight()

    def test_fixtures_dir_must_hold_this_features_fixtures(self):
        """051 and 072 deployed with no fixtures and came back almost all BLOCKED."""
        self._config({"validation": {**CONFIG, "fixtures_dir": "validation-fixtures"}})
        with self.assertRaises(SystemExit):
            self._preflight()
        (self.spec_dir / "validation-fixtures").mkdir()
        (self.spec_dir / "validation-fixtures" / "NONE").write_text("No email in this feature.\n")
        self._preflight()

    def test_probe_commands_need_a_command_and_description(self):
        self._config({"validation": {**CONFIG, "probe_commands": {"query": {"command": "q"}}}})
        with self.assertRaises(SystemExit):
            self._preflight()

    def test_non_claude_generation_blocks(self):
        self._config(
            {
                "validation": CONFIG,
                "generation": {"validate": {"provider": "ollama", "model": "m"}},
            }
        )
        with self.assertRaises(SystemExit):
            self._preflight()


LOCKED = {**CONFIG, "lock_command": "lock", "unlock_command": "unlock"}


def _done(returncode=0, stdout=""):
    return subprocess.CompletedProcess([], returncode, stdout, None)


class TestDeploy(_ProjectCase):
    def _deploy(self, returncode=0, stdout="building\nhttps://p.example\n", config=CONFIG):
        run_dir = self.spec_dir / "validation" / SHA[:12]
        run_dir.mkdir(parents=True, exist_ok=True)
        env = va.hook_env(FEATURE, SHA, FEATURE, 5, self.spec_dir, run_dir)
        with (
            patch.object(va.subprocess, "run", return_value=_done(returncode, stdout)) as run,
            patch.object(va, "log"),
        ):
            url = va.deploy(config, env, run_dir)
        return url, run

    def test_last_line_is_url_and_env_is_set(self):
        url, run = self._deploy()
        self.assertEqual(url, "https://p.example")
        env = run.call_args.kwargs["env"]
        self.assertEqual(
            (env["VALIDATION_SHA"], env["VALIDATION_BRANCH"], env["VALIDATION_PR_NUMBER"]),
            (SHA, FEATURE, "5"),
        )
        self.assertTrue(Path(env["VALIDATION_RUN_DIR"]).is_absolute())

    def test_url_is_cached_for_resume(self):
        self._deploy()
        url, run = self._deploy(stdout="")
        self.assertEqual(url, "https://p.example")
        run.assert_not_called()

    def test_shared_environment_is_redeployed_on_resume(self):
        self._deploy(config=LOCKED)
        _, run = self._deploy(stdout="https://dev.example\n", config=LOCKED)
        run.assert_called_once()

    def test_failures(self):
        for code, stdout in ((va.SUPERSEDED_EXIT_CODE, ""), (1, ""), (0, "not a url\n")):
            with self.subTest(code=code, stdout=stdout), self.assertRaises(SystemExit):
                self._deploy(code, stdout)


class TestEnvironmentLock(unittest.TestCase):
    def _hold(self, config=LOCKED, lock_rc=0, unlock_rc=0, body=lambda: None):
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            return _done(lock_rc if cmd == "lock" else unlock_rc)

        with patch.object(va.subprocess, "run", side_effect=run), patch.object(va, "log"):
            try:
                with va.environment_lock(config, {}):
                    calls.append("body")
                    body()
                outcome = None
            except SystemExit as e:
                outcome = e.code
        return calls, outcome

    def test_unconfigured_is_a_no_op(self):
        self.assertEqual(self._hold(config=CONFIG), (["body"], None))

    def test_released_after_success(self):
        self.assertEqual(self._hold(), (["lock", "body", "unlock"], None))

    def test_released_when_the_body_fails(self):
        def fail():
            raise SystemExit(1)

        self.assertEqual(self._hold(body=fail), (["lock", "body", "unlock"], 1))

    def test_failed_lock_skips_body_and_unlock(self):
        self.assertEqual(self._hold(lock_rc=1), (["lock"], 1))

    def test_failed_unlock_after_success_fails_the_run(self):
        self.assertEqual(self._hold(unlock_rc=1), (["lock", "body", "unlock"], 1))


class TestSeed(_ProjectCase):
    def _seed(self, config, result=None):
        with (
            patch.object(va.subprocess, "run", return_value=result) as run,
            patch.object(va, "log"),
        ):
            return va.seed(config, {"VALIDATION_SHA": SHA}, "https://p.example"), run

    def test_unconfigured_runs_nothing(self):
        seeded, run = self._seed(CONFIG)
        self.assertEqual(seeded, "")
        run.assert_not_called()

    def test_output_describes_the_seeded_data(self):
        seeded, run = self._seed({**CONFIG, "seed_command": "seed"}, _done(0, "Sent 2 emails\n"))
        self.assertEqual(seeded, "Sent 2 emails")
        self.assertEqual(run.call_args.kwargs["env"]["VALIDATION_BASE_URL"], "https://p.example")

    def test_failure_fails(self):
        with self.assertRaises(SystemExit):
            self._seed({**CONFIG, "seed_command": "seed"}, _done(1))

    def test_seeded_data_reaches_the_agent_only_when_present(self):
        def instructions(seeded):
            return va.agent_instructions([], "spec", "qs", "", "https://p", Path("/r"), "", seeded)

        self.assertIn("--- DATA SEEDED FOR THIS RUN ---", instructions("Sent 2 emails"))
        self.assertIn("Sent 2 emails", instructions("Sent 2 emails"))
        self.assertNotIn("SEEDED", instructions(""))


class TestMcpServer(unittest.TestCase):
    def test_origin_allowlist_and_pinned_version(self):
        server = va.mcp_server(
            {"allowed_origins": ["https://auth.example"]},
            "https://p.example/app?x=1",
            Path("/s.json"),
            Path("/run"),
        )
        args = server["args"]
        self.assertIn(f"@playwright/mcp@{va.PLAYWRIGHT_MCP_VERSION}", args)
        self.assertIn("--allowed-origins=https://p.example;https://auth.example", args)
        self.assertIn("--storage-state=/s.json", args)
        self.assertIn("--browser=chromium", args)

    def test_confined_to_run_dir_without_credentials(self):
        env = {"PATH": "/bin", "HOME": "/h", "npm_config_cache": "/c", "AWS_SECRET_ACCESS_KEY": "s"}
        env |= {"GH_TOKEN": "t", "VALIDATION_DATABASE_URL": "postgres://u:p@h/d"}
        with patch.dict(os.environ, env, clear=True):
            server = va.mcp_server({}, "https://p.example", Path("/s.json"), Path("/run"))
        self.assertEqual(server["command"], "env")
        args = server["args"]
        self.assertEqual(args[:3], ["-i", "-C", "/run"])
        self.assertEqual(
            args[3 : args.index("npx")], ["PATH=/bin", "HOME=/h", "npm_config_cache=/c"]
        )
        self.assertIn("--ignore-roots", args)


class TestEnsureBrowser(_ProjectCase):
    def _ensure(self, install_rc=0, ldd_out=""):
        shell = (
            self.root
            / "browsers/chromium_headless_shell-1/chrome-headless-shell-linux64/chrome-headless-shell"
        )
        shell.parent.mkdir(parents=True)
        shell.write_text("")

        def run(cmd, **kwargs):
            out = ldd_out if cmd[0] == "ldd" else ""
            return subprocess.CompletedProcess(cmd, install_rc, out, "boom")

        with (
            patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": str(self.root / "browsers")}),
            patch.object(va.subprocess, "run", side_effect=run) as mock_run,
            patch.object(va, "log"),
        ):
            va.ensure_browser({})
        return mock_run

    def test_installs_the_mcp_servers_own_playwright_build(self):
        install = self._ensure().call_args_list[0][0][0]
        self.assertEqual(
            install,
            ["npx", "-y", "-p", f"@playwright/mcp@{va.PLAYWRIGHT_MCP_VERSION}", "playwright",
             "install", "chromium"],
        )  # fmt: skip

    def test_missing_os_libraries_fail_clearly(self):
        with self.assertRaises(SystemExit):
            self._ensure(ldd_out="\tlibnss3.so => not found\n\tlibc.so.6 => /lib/libc.so.6\n")

    def test_failed_download_fails(self):
        with self.assertRaises(SystemExit):
            self._ensure(install_rc=1)


class TestRunDirScope(unittest.TestCase):
    def test_only_run_dir_is_readable_or_writable(self):
        hook = va._run_dir_scope_hook(Path("/runs/run"))
        for path, decision in (
            ("/runs/run/report.json", None),
            ("/runs/run/../../workspace/prisma/.env.validation", "deny"),
            ("/workspace/src/a.ts", "deny"),
        ):
            with self.subTest(path):
                result = asyncio.run(hook({"tool_input": {"file_path": path}}, 1, None))
                self.assertEqual(
                    result.get("hookSpecificOutput", {}).get("permissionDecision"), decision
                )

    def test_agent_gets_no_repo_search_tools_and_scoped_reads(self):
        captured = {}

        def fake_query(prompt, options):
            captured["options"] = options

        async def fake_stream(_):
            return None

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(va, "query", side_effect=fake_query),
            patch.object(va, "stream_query", side_effect=fake_stream),
        ):
            asyncio.run(va.run_validation_agent("i" * 200_000, "t", {}, {}, Path(tmp)))
            options = captured["options"]
            self.assertEqual(sorted(options.tools), ["Read", "Write"])
            self.assertNotIn("Glob", options.allowed_tools)
            self.assertEqual(options.disallowed_tools, ["mcp__playwright__browser_run_code_unsafe"])
            [matcher] = options.hooks["PreToolUse"]
            self.assertEqual(matcher.matcher, "Read|Write")
            self.assertEqual(options.system_prompt["type"], "file")
            self.assertEqual(Path(options.system_prompt["path"]).read_text(), "i" * 200_000)


class TestVerdictCounts(unittest.TestCase):
    def test_tallies_report_json(self):
        verdicts = ["PASS", "PASS", "FAIL", "BLOCKED"]
        report = {"scenarios": [{"id": f"S{i}", "verdict": v} for i, v in enumerate(verdicts)]}
        self.assertEqual(va.verdict_counts(report), "4 scenario(s): 1 BLOCKED, 1 FAIL, 2 PASS")


class TestAgentNotes(unittest.TestCase):
    def _instructions(self, notes):
        return va.agent_instructions([], "spec", "qs", "", "https://p", Path("/r"), notes)

    def test_notes_are_included_only_when_set(self):
        self.assertIn(
            "--- PROJECT NOTES ---\nUse at most 2 imports.",
            self._instructions("Use at most 2 imports.\n"),
        )
        self.assertNotIn("PROJECT NOTES", self._instructions(""))


class _RunCase(_ProjectCase):
    """End-to-end through run() with every external effect mocked."""

    def _agent_writing(self, *reports):
        """A fake run_validation_agent that writes reports[N] on its Nth call."""
        reports = iter(reports)
        calls = []

        async def agent(instructions, task, config, mcp, run_dir):
            calls.append(task)
            report = next(reports)
            for entry in report.get("scenarios", []):
                if entry.get("record"):
                    (run_dir / f"{entry['id']}.webm").write_bytes(b"x")
            (run_dir / "report.json").write_text(json.dumps(report))

        return agent, calls

    def _run(self, agent, config=CONFIG):
        pr = {"number": 5, "url": "https://gh/pr/5", "body": "", "baseRefName": "main"}
        with (
            patch.object(va, "preflight", return_value=config),
            patch.object(
                va, "_git", side_effect=lambda *a: SHA if a == ("rev-parse", "HEAD") else FEATURE
            ),
            patch.object(va.github_pr, "push_branch") as push,
            patch.object(va.github_pr, "find_pr", return_value=None),
            patch.object(va, "run_description_agent", side_effect=_describe),
            patch.object(va, "branch_changes", return_value=""),
            patch.object(va.github_pr, "ensure_draft_pr", return_value=pr) as ensure_pr,
            patch.object(va, "deploy", return_value="https://p.example") as deploy,
            patch.object(va, "ensure_browser"),
            patch.object(va, "login", return_value=Path("s.json")),
            patch.object(va, "run_validation_agent", side_effect=agent),
            patch.object(va.github_pr, "post_comment_once", return_value=True) as comment,
            patch.object(va, "log"),
        ):
            try:
                asyncio.run(va.run(FEATURE))
                exited = None
            except SystemExit as e:
                exited = e.code
        self.deploy = deploy
        return exited, push, ensure_pr, comment


class TestRun(_RunCase):
    def test_finished_report_is_posted_without_redeploying(self):
        run_dir = self.spec_dir / "validation" / SHA[:12]
        run_dir.mkdir(parents=True)
        (run_dir / "base-url").write_text("https://p.example\n")
        (run_dir / "US1-AS1.webm").write_bytes(b"x")
        report = {"scenarios": [{"id": "US1-AS1", "verdict": "PASS", "observed": "ok"}]}
        (run_dir / "report.json").write_text(json.dumps(report))
        agent, calls = self._agent_writing()
        with patch.object(va.subprocess, "run") as hooks:
            exited, _, _, comment = self._run(agent, config=LOCKED)
        self.assertIsNone(exited)
        self.deploy.assert_not_called()
        hooks.assert_not_called()
        self.assertEqual(calls, [])
        comment.assert_called_once()

    def test_shared_environment_is_locked_around_deploy_and_validation(self):
        passing = {
            "scenarios": [{"id": "US1-AS1", "verdict": "PASS", "observed": "ok", "record": 1}]
        }
        agent, _ = self._agent_writing(passing)
        with patch.object(va.subprocess, "run", return_value=_done()) as hooks:
            self.assertIsNone(self._run(agent, config=LOCKED)[0])
        self.assertEqual([c.args[0] for c in hooks.call_args_list], ["lock", "unlock"])

    def test_all_pass_marks_complete_without_committing(self):
        passing = {
            "scenarios": [{"id": "US1-AS1", "verdict": "PASS", "observed": "ok", "record": 1}]
        }
        agent, calls = self._agent_writing(passing)
        exited, push, ensure_pr, comment = self._run(agent)
        self.assertIsNone(exited)
        push.assert_called_once()
        self.assertEqual(ensure_pr.call_args[0][1], "Saved searches")
        body = ensure_pr.call_args[0][2]
        self.assertTrue(body.startswith("## Summary\nSaves searches."))
        self.assertTrue(body.endswith(va.github_pr.GENERATED_MARKER))
        self.assertIn(f"sha: {SHA}", (self.spec_dir / "ch-6-validate-auto-complete").read_text())
        self.assertEqual(len(calls), 1)
        comment.assert_called_once()

        again, calls2 = self._agent_writing()
        self.assertIsNone(self._run(again)[0])
        self.assertEqual(calls2, [])

    def test_failure_escalates_after_posting(self):
        failing = {
            "scenarios": [{"id": "US1-AS1", "verdict": "FAIL", "observed": "gone", "record": 1}]
        }
        agent, _ = self._agent_writing(failing)
        exited, _, _, comment = self._run(agent)
        self.assertEqual(exited, 1)
        comment.assert_called_once()
        escalation = (self.spec_dir / va.ESCALATION_FILENAME).read_text()
        self.assertIn("US1-AS1** FAIL: gone", escalation)
        self.assertFalse((self.spec_dir / "ch-6-validate-auto-complete").exists())

    def test_incomplete_report_gets_one_retry(self):
        unrecorded = {"scenarios": [{"id": "US1-AS1", "verdict": "PASS", "observed": "ok"}]}
        recorded = {
            "scenarios": [{"id": "US1-AS1", "verdict": "PASS", "observed": "ok", "record": 1}]
        }
        agent, calls = self._agent_writing(unrecorded, recorded)
        self.assertIsNone(self._run(agent)[0])
        self.assertEqual(len(calls), 2)
        self.assertIn("no recording", calls[1])

    def test_running_out_of_turns_keeps_the_report_and_finishes_it(self):
        """098: the agent hit 300 turns at 14 of 39 scenarios and the partial report was lost."""
        partial = {"scenarios": []}
        passing = {
            "scenarios": [{"id": "US1-AS1", "verdict": "PASS", "observed": "ok", "record": 1}]
        }
        write, calls = self._agent_writing(partial, passing)

        async def agent(instructions, task, config, mcp, run_dir):
            await write(instructions, task, config, mcp, run_dir)
            if len(calls) == 1:
                raise va.ResultError("Reached maximum number of turns (300)")

        self.assertIsNone(self._run(agent)[0])
        self.assertEqual(len(calls), 2)
        self.assertIn("US1-AS1: missing from report", calls[1])

    def test_turn_budget_scales_with_the_scenarios(self):
        scenarios = [va.vr.Scenario(f"SC-{n:03}", "x") for n in range(39)]
        self.assertEqual(va.agent_max_turns(CONFIG, scenarios[:3]), va.DEFAULT_MAX_TURNS)
        self.assertEqual(va.agent_max_turns(CONFIG, scenarios), 39 * va.TURNS_PER_SCENARIO)
        self.assertEqual(va.agent_max_turns({**CONFIG, "max_turns": 50}, scenarios), 50)

    def test_still_invalid_after_retry_escalates_without_posting(self):
        unrecorded = {"scenarios": [{"id": "US1-AS1", "verdict": "PASS", "observed": "ok"}]}
        agent, calls = self._agent_writing(unrecorded, unrecorded)
        exited, _, _, comment = self._run(agent)
        self.assertEqual(exited, 1)
        self.assertEqual(len(calls), 2)
        comment.assert_not_called()
        self.assertIn("Report Invalid", (self.spec_dir / va.ESCALATION_FILENAME).read_text())


UNFIT = {"setup_problem": "requests to https://db.example are blocked"}


class TestSetupProblem(_RunCase):
    """098's first run: blocked origins turned 34 of 36 scenarios BLOCKED."""

    def test_escalates_without_attempting_scenarios_or_posting(self):
        agent, calls = self._agent_writing(UNFIT)
        exited, _, _, comment = self._run(agent)
        self.assertEqual(exited, 1)
        self.assertEqual(len(calls), 1)
        comment.assert_not_called()
        escalation = (self.spec_dir / va.ESCALATION_FILENAME).read_text()
        self.assertIn("Environment Not Ready", escalation)
        self.assertIn("db.example are blocked", escalation)

    def test_rerun_checks_the_environment_again(self):
        self._run(self._agent_writing(UNFIT)[0])
        passing = {
            "scenarios": [{"id": "US1-AS1", "verdict": "PASS", "observed": "ok", "record": 1}]
        }
        agent, calls = self._agent_writing(passing)
        self.assertIsNone(self._run(agent)[0])
        self.assertEqual(len(calls), 1)


PROBES = {
    **CONFIG,
    "probe_commands": {"query": {"command": "echo", "description": "Read-only SQL."}},
}
BLOCKED_REPORT = {
    "scenarios": [{"id": "US1-AS1", "verdict": "BLOCKED", "reason": "the row isn't visible"}]
}


class TestOperatorPhase(_RunCase):
    def _operator(self, verdict, cite_transcript=True):
        """A fake operator agent that runs a real probe and rewrites the entry."""
        run_dir = self.spec_dir / "validation" / SHA[:12]
        self.queries = []

        async def stream(_query):
            transcript, output = va.run_probe("echo", ["saved row 1"], dict(os.environ), run_dir)
            self.queries.append(output)
            entry = {
                "id": "US1-AS1",
                "verdict": verdict,
                "method": "probe",
                "observed": "the row is saved",
                "evidence": [str(transcript)] if cite_transcript else [],
            }
            (run_dir / "report.json").write_text(json.dumps({"scenarios": [entry]}))

        return patch.multiple(va, stream_query=stream, query=lambda **_: None)

    def test_probe_verdict_settles_a_blocked_scenario(self):
        with self._operator("PASS"):
            exited, _, _, comment = self._run(self._agent_writing(BLOCKED_REPORT)[0], PROBES)
        self.assertIsNone(exited)
        self.assertIn("saved row 1", self.queries[0])
        report = json.loads((self.spec_dir / "validation" / SHA[:12] / "report.json").read_text())
        self.assertEqual(report["scenarios"][0]["verdict"], "PASS")
        self.assertEqual(report["operator_phase"], "done")
        self.assertIn("probe: ", comment.call_args[0][1])

    def test_probe_verdict_without_a_transcript_is_discarded(self):
        with self._operator("PASS", cite_transcript=False):
            exited, *_ = self._run(self._agent_writing(BLOCKED_REPORT)[0], PROBES)
        self.assertEqual(exited, 1)
        self.assertIn("US1-AS1** BLOCKED", (self.spec_dir / va.ESCALATION_FILENAME).read_text())

    def test_not_run_without_probe_commands(self):
        with self._operator("PASS"):
            exited, *_ = self._run(self._agent_writing(BLOCKED_REPORT)[0])
        self.assertEqual(exited, 1)
        self.assertEqual(self.queries, [])


class TestMergeOperatorVerdicts(unittest.TestCase):
    def test_only_blocked_entries_can_change(self):
        scenarios = [va.vr.Scenario("US1-AS1", "a"), va.vr.Scenario("US1-AS2", "b")]
        before = {
            "scenarios": [
                {"id": "US1-AS1", "verdict": "PASS", "observed": "seen"},
                {"id": "US1-AS2", "verdict": "BLOCKED", "reason": "not visible"},
            ]
        }
        after = {
            "scenarios": [
                {"id": "US1-AS1", "verdict": "FAIL", "method": "probe", "observed": "x"},
                {"id": "US1-AS2", "verdict": "BLOCKED", "reason": "no probe shows it"},
            ]
        }
        with tempfile.TemporaryDirectory() as d, patch.object(va, "log"):
            merged = va.merge_operator_verdicts(before, after, scenarios, Path(d))
        self.assertEqual(merged["scenarios"][0], before["scenarios"][0])
        self.assertEqual(merged["scenarios"][1]["reason"], "no probe shows it")


class TestRunProbe(unittest.TestCase):
    def test_args_are_quoted_and_the_transcript_is_numbered(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            first, _ = va.run_probe("echo", ["a; touch pwned"], dict(os.environ), run_dir)
            second, output = va.run_probe("echo", ["b"], dict(os.environ), run_dir)
            self.assertEqual((first.name, second.name), ("probe-1.txt", "probe-2.txt"))
            self.assertIn("a; touch pwned", first.read_text())
            self.assertFalse((run_dir / "pwned").exists())
            self.assertTrue(output.startswith("exit 0"))


class TestPrDescription(_ProjectCase):
    def setUp(self):
        super().setUp()
        self.run_dir = self.spec_dir / "validation" / SHA[:12]
        self.run_dir.mkdir(parents=True)

    def _body(self, agent, changes=lambda *a: ""):
        with (
            patch.object(va, "run_description_agent", side_effect=agent) as describe,
            patch.object(va, "branch_changes", side_effect=changes),
            patch.object(va, "log"),
        ):
            body = asyncio.run(va.pr_body(FEATURE, self.spec_dir, SPEC, self.run_dir))
        return body, describe

    def test_generated_once_per_commit(self):
        body, _ = self._body(_describe)
        self.assertIn("Saves searches.", body)
        self.assertIn("ch-6-validate-auto", body)
        again, describe = self._body(_describe)
        self.assertEqual(again, body)
        describe.assert_not_called()

    def test_agent_failure_falls_back_to_a_regenerable_stub(self):
        async def broken(instructions, run_dir):
            raise RuntimeError("boom")

        body, _ = self._body(broken)
        self.assertIn(f"specs/{FEATURE}/spec.md", body)
        self.assertTrue(va.github_pr.harness_owns_body(body))
        self.assertFalse((self.run_dir / "pr-body.md").exists())

    def test_session_limit_is_not_swallowed(self):
        async def limited(instructions, run_dir):
            raise va.SessionLimitError("limit")

        with self.assertRaises(va.SessionLimitError):
            self._body(limited)

    def test_instructions_carry_artifacts_and_followup_link(self):
        (self.spec_dir / "plan.md").write_text("PLAN TEXT")
        (self.spec_dir / "tasks.md").write_text("- [ ] T1 [TEST] covers save\n- [ ] T2 [IMPL] x\n")
        (self.spec_dir / va.FOLLOWUP_FILENAME).write_text("concerns")
        captured = {}

        async def capture(instructions, run_dir):
            captured["i"] = instructions

        self._body(capture, changes=lambda *a: f"DIFF{a}")
        instructions = captured["i"]
        for expected in ("PLAN TEXT", "T1 [TEST] covers save", "DIFF('--', 'docs/')"):
            self.assertIn(expected, instructions)
        self.assertNotIn("T2 [IMPL]", instructions)
        self.assertIn(f"`specs/{FEATURE}/{va.FOLLOWUP_FILENAME}`", instructions)

    def test_human_owned_pr_is_not_redescribed(self):
        pr = {"number": 3, "url": "u", "body": "Written by a human", "baseRefName": "main"}
        with (
            patch.object(va.github_pr, "push_branch"),
            patch.object(va.github_pr, "find_pr", return_value=pr),
            patch.object(va.github_pr, "ensure_draft_pr", return_value=pr),
            patch.object(va, "run_description_agent") as describe,
            patch.object(va, "log"),
        ):
            asyncio.run(va.open_pr(SPEC, FEATURE, FEATURE, self.spec_dir, self.run_dir))
        describe.assert_not_called()

    def test_description_agent_can_only_write_in_the_run_dir(self):
        captured = {}

        def fake_query(prompt, options):
            captured["options"] = options

        async def fake_stream(_):
            return None

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(va, "query", side_effect=fake_query),
            patch.object(va, "stream_query", side_effect=fake_stream),
        ):
            asyncio.run(va.run_description_agent("i" * 200_000, Path(tmp)))
            options = captured["options"]
            self.assertEqual(options.tools, ["Write"])
            [matcher] = options.hooks["PreToolUse"]
            self.assertEqual(matcher.matcher, "Write")
            # Over Linux's 128 KiB single-argument limit, so it must not go on the command line.
            self.assertEqual(options.system_prompt["type"], "file")
            self.assertEqual(Path(options.system_prompt["path"]).read_text(), "i" * 200_000)


class TestPrTitle(unittest.TestCase):
    def test_strips_template_prefix(self):
        self.assertEqual(va.pr_title(SPEC, FEATURE), "Saved searches")
        self.assertEqual(va.pr_title("no heading", FEATURE), FEATURE)


if __name__ == "__main__":
    unittest.main()


GUIDE = "# Ops\n\n## Writing a feature's fixtures\n\nUse fixtures.json.\n\n## Other\n\nx\n"
DRAFTING = {
    **CONFIG,
    "fixtures_dir": "validation-fixtures",
    "fixtures_guide": "docs/ops.md#writing-a-features-fixtures",
}


class _FixturesCase(_ProjectCase):
    def setUp(self):
        super().setUp()
        (self.root / "docs").mkdir()
        (self.root / "docs/ops.md").write_text(GUIDE)
        for args in (
            ["init", "-q"],
            ["config", "user.email", "t@example.com"],
            ["config", "user.name", "t"],
            ["add", "-A"],
            ["commit", "-q", "-m", "init"],
        ):
            subprocess.run(["git", *args], check=True, capture_output=True)
        self.fixtures = self.spec_dir / "validation-fixtures"

    def _head(self):
        return va._git("rev-parse", "HEAD")

    def _prepare(self, config=DRAFTING, writes=("fixtures.json",)):
        calls = []

        async def agent(instructions, config, fixtures_dir):
            calls.append(instructions)
            for name in writes:
                fixtures_dir.mkdir(parents=True, exist_ok=True)
                (fixtures_dir / name).write_text('{"US1-AS1": "email.eml"}')

        with patch.object(va, "run_fixtures_agent", side_effect=agent), patch.object(va, "log"):
            asyncio.run(va.prepare_fixtures(config, self.spec_dir, FEATURE))
        return calls


class TestFixturesConfig(_FixturesCase):
    def test_guide_section_and_types(self):
        self.assertIsNone(va._config_problem(DRAFTING, self.spec_dir))
        bad = {
            "unknown heading": {**DRAFTING, "fixtures_guide": "docs/ops.md#nope"},
            "missing file": {**DRAFTING, "fixtures_guide": "docs/gone.md"},
            "no fixtures_dir": {**CONFIG, "fixtures_guide": "docs/ops.md"},
            "empty check command": {**DRAFTING, "fixtures_check_command": " "},
            "bad turn budget": {**DRAFTING, "fixtures_max_turns": "60"},
        }
        for name, config in bad.items():
            with self.subTest(name):
                self.assertIsNotNone(va._config_problem(config, self.spec_dir))

    def test_guide_text_is_the_named_section(self):
        text, problem = va.fixtures_guide_text("docs/ops.md#writing-a-features-fixtures")
        self.assertIsNone(problem)
        self.assertIn("Use fixtures.json", text)
        self.assertNotIn("## Other", text)


class TestPrepareFixtures(_FixturesCase):
    def test_drafts_and_commits_before_head_is_read(self):
        before = self._head()
        calls = self._prepare()
        self.assertEqual(len(calls), 1)
        self.assertIn("Use fixtures.json", calls[0])
        self.assertNotEqual(self._head(), before)
        self.assertEqual(va._git("log", "-1", "--format=%s"), va.FIXTURES_COMMIT_SUBJECT)
        self.assertEqual(va._git("status", "--porcelain"), "")
        self.assertIn("drafted by an agent", va.fixtures_note(DRAFTING, self.spec_dir))

    def test_rerun_does_not_draft_again(self):
        self._prepare()
        head = self._head()
        self.assertEqual(self._prepare(), [])
        self.assertEqual(self._head(), head)

    def test_agent_writing_nothing_fails_before_anything_else(self):
        head = self._head()
        with self.assertRaises(SystemExit):
            self._prepare(writes=())
        self.assertEqual(self._head(), head)

    def test_failed_check_escalates_without_committing(self):
        head = self._head()
        with self.assertRaises(SystemExit):
            self._prepare(config={**DRAFTING, "fixtures_check_command": "echo bad id; exit 1"})
        self.assertEqual(self._head(), head)
        escalation = (self.spec_dir / va.ESCALATION_FILENAME).read_text()
        self.assertIn("Fixtures Invalid", escalation)
        self.assertIn("bad id", escalation)

    def test_a_human_edit_is_not_reported_as_drafted(self):
        self._prepare()
        (self.fixtures / "fixtures.json").write_text("{}")
        subprocess.run(["git", "commit", "-qam", "test: fix fixtures"], check=True)
        self.assertEqual(va.fixtures_note(DRAFTING, self.spec_dir), "")

    def test_without_a_guide_missing_fixtures_still_fail_preflight(self):
        config = {**CONFIG, "fixtures_dir": "validation-fixtures"}
        self.assertIn("missing or empty", va._config_problem(config, self.spec_dir))


class TestFixturesAgentScope(unittest.TestCase):
    def _decide(self, tool, tool_input):
        with tempfile.TemporaryDirectory() as d:
            cwd = Path.cwd()
            os.chdir(d)
            try:
                hook = va._path_scope_hook(Path("specs/012-x/fx"), (Path("specs"), Path("docs")))
                result = asyncio.run(
                    hook({"tool_name": tool, "tool_input": tool_input}, None, None)
                )
            finally:
                os.chdir(cwd)
        return "deny" if result else "allow"

    def test_writes_only_in_the_fixtures_dir_and_reads_only_specs_and_docs(self):
        cases = {
            ("Write", "specs/012-x/fx/fixtures.json"): "allow",
            ("Write", "specs/012-x/plan.md"): "deny",
            ("Read", "docs/ops.md"): "allow",
            ("Read", ".env"): "deny",
        }
        for (tool, path), expected in cases.items():
            with self.subTest(tool=tool, path=path):
                self.assertEqual(self._decide(tool, {"file_path": path}), expected)
        self.assertEqual(self._decide("Glob", {"pattern": "**/*.json", "path": "specs"}), "allow")
        self.assertEqual(self._decide("Glob", {"pattern": "**/.env"}), "deny")
        self.assertEqual(self._decide("Glob", {"pattern": "../**", "path": "specs"}), "deny")


class TestRunDraftsBeforeReadingHead(_RunCase):
    def test_fixtures_are_prepared_before_the_commit_is_chosen(self):
        order = []

        async def prepare(config, spec_dir, feature):
            order.append("prepare")

        passing = {
            "scenarios": [{"id": "US1-AS1", "verdict": "PASS", "observed": "ok", "record": 1}]
        }
        agent, _ = self._agent_writing(passing)
        with (
            patch.object(va, "prepare_fixtures", side_effect=prepare),
            patch.object(va, "fixtures_note", return_value=""),
            patch.object(
                va, "already_validated", side_effect=lambda *a: order.append("sha") or False
            ),
        ):
            self.assertIsNone(self._run(agent, config={**CONFIG, "fixtures_dir": "fx"})[0])
        self.assertEqual(order[:2], ["prepare", "sha"])

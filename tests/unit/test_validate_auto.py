"""Unit tests for ch_6_validate_auto.py with git/gh/deploy/auth and the
validation agent mocked: opt-in gating, preflight, the deploy_command
contract, per-commit resume, the one-retry report check, and escalation.
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

    def test_docs_incomplete_blocks_unless_disabled(self):
        (self.spec_dir / "ch-5-docs-auto-complete").unlink()
        with self.assertRaises(SystemExit):
            self._preflight()
        self._config({"stages": {"docs": {"enabled": False}}, "validation": CONFIG})
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


class TestDeploy(_ProjectCase):
    def _deploy(self, returncode=0, stdout="building\nhttps://p.example\n"):
        run_dir = self.spec_dir / "validation" / SHA[:12]
        run_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.CompletedProcess([], returncode, stdout, None)
        with (
            patch.object(va.subprocess, "run", return_value=result) as run,
            patch.object(va, "log"),
        ):
            url = va.deploy(CONFIG, run_dir, SHA, FEATURE, 5)
        return url, run

    def test_last_line_is_url_and_env_is_set(self):
        url, run = self._deploy()
        self.assertEqual(url, "https://p.example")
        env = run.call_args.kwargs["env"]
        self.assertEqual(
            (env["VALIDATION_SHA"], env["VALIDATION_BRANCH"], env["VALIDATION_PR_NUMBER"]),
            (SHA, FEATURE, "5"),
        )

    def test_url_is_cached_for_resume(self):
        self._deploy()
        url, run = self._deploy(stdout="")
        self.assertEqual(url, "https://p.example")
        run.assert_not_called()

    def test_failures(self):
        for code, stdout in ((va.SUPERSEDED_EXIT_CODE, ""), (1, ""), (0, "not a url\n")):
            with self.subTest(code=code, stdout=stdout), self.assertRaises(SystemExit):
                self._deploy(code, stdout)


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


class TestWriteScopeHook(unittest.TestCase):
    def test_only_run_dir_is_writable(self):
        hook = va._write_scope_hook(Path("/runs/run"))
        allowed = asyncio.run(hook({"tool_input": {"file_path": "/runs/run/report.json"}}, 1, None))
        denied = asyncio.run(hook({"tool_input": {"file_path": "/workspace/src/a.ts"}}, 1, None))
        self.assertEqual(allowed, {})
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")


class TestRun(_ProjectCase):
    """End-to-end through run() with every external effect mocked."""

    def _agent_writing(self, *reports):
        """A fake run_validation_agent that writes reports[N] on its Nth call."""
        reports = iter(reports)
        calls = []

        async def agent(instructions, task, config, mcp, run_dir):
            calls.append(task)
            report = next(reports)
            for entry in report["scenarios"]:
                if entry.get("record"):
                    (run_dir / f"{entry['id']}.webm").write_bytes(b"x")
            (run_dir / "report.json").write_text(json.dumps(report))

        return agent, calls

    def _run(self, agent):
        pr = {"number": 5, "url": "https://gh/pr/5", "body": "", "baseRefName": "main"}
        with (
            patch.object(va, "preflight", return_value=CONFIG),
            patch.object(
                va, "_git", side_effect=lambda *a: SHA if a == ("rev-parse", "HEAD") else FEATURE
            ),
            patch.object(va.github_pr, "push_branch") as push,
            patch.object(va.github_pr, "ensure_draft_pr", return_value=pr) as ensure_pr,
            patch.object(va, "deploy", return_value="https://p.example"),
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
        return exited, push, ensure_pr, comment

    def test_all_pass_marks_complete_without_committing(self):
        passing = {
            "scenarios": [{"id": "US1-AS1", "verdict": "PASS", "observed": "ok", "record": 1}]
        }
        agent, calls = self._agent_writing(passing)
        exited, push, ensure_pr, comment = self._run(agent)
        self.assertIsNone(exited)
        push.assert_called_once()
        self.assertEqual(ensure_pr.call_args[0][1], "Saved searches")
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

    def test_still_invalid_after_retry_escalates_without_posting(self):
        unrecorded = {"scenarios": [{"id": "US1-AS1", "verdict": "PASS", "observed": "ok"}]}
        agent, calls = self._agent_writing(unrecorded, unrecorded)
        exited, _, _, comment = self._run(agent)
        self.assertEqual(exited, 1)
        self.assertEqual(len(calls), 2)
        comment.assert_not_called()
        self.assertIn("Report Invalid", (self.spec_dir / va.ESCALATION_FILENAME).read_text())


class TestPrTitle(unittest.TestCase):
    def test_strips_template_prefix(self):
        self.assertEqual(va.pr_title(SPEC, FEATURE), "Saved searches")
        self.assertEqual(va.pr_title("no heading", FEATURE), FEATURE)


if __name__ == "__main__":
    unittest.main()

"""Unit tests for ch_4_implement_auto.py's _run_implementation_agent — the bounded
retry-then-hard-fail behaviour added for FOLLOWUP_HARNESS.md Bug 3 (a backgrounded,
never-awaited impl-agent call silently truncated the implementation and let the
script proceed straight into a doomed CI run). No LLM calls (local_agent_loop.
run_generation is mocked)."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
import ch_4_implement_auto as impl_auto


def _write_tasks(spec_dir: Path, content: str) -> None:
    (spec_dir / "tasks.md").write_text(content, encoding="utf-8")


def _write_feature_files(spec_dir: Path) -> None:
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "spec.md").write_text("spec", encoding="utf-8")
    (spec_dir / "plan.md").write_text("plan", encoding="utf-8")
    (spec_dir / "tasks.md").write_text("- [ ] T001 [IMPL] todo\n", encoding="utf-8")


class TestPreflight(unittest.TestCase):
    """preflight()'s test-gate branch: skipped entirely when the test stage
    is disabled via .specify/pipeline.json, enforced as before otherwise."""

    def test_skips_test_gate_check_when_test_stage_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            spec_dir = root / "specs" / "some-feature"
            _write_feature_files(spec_dir)
            config_dir = root / ".specify"
            config_dir.mkdir()
            (config_dir / "pipeline.json").write_text(
                json.dumps({"stages": {"test": {"enabled": False}}}), encoding="utf-8"
            )

            orig_cwd = Path.cwd()
            try:
                os.chdir(root)
                impl_auto.preflight(spec_dir, "some-feature")  # must not raise/exit
            finally:
                os.chdir(orig_cwd)

    def test_exits_when_test_stage_enabled_and_no_passing_result(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            spec_dir = root / "specs" / "some-feature"
            _write_feature_files(spec_dir)
            # No .specify/pipeline.json at all — test stage defaults to enabled.

            orig_cwd = Path.cwd()
            try:
                os.chdir(root)
                with self.assertRaises(SystemExit) as cm:
                    impl_auto.preflight(spec_dir, "some-feature")
            finally:
                os.chdir(orig_cwd)

            self.assertEqual(cm.exception.code, 1)


class TestRunImplementationAgent(unittest.IsolatedAsyncioTestCase):
    async def test_skips_agent_when_all_tasks_already_checked(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            tasks = "- [x] T001 [IMPL] done\n"
            with patch.object(
                impl_auto.local_agent_loop, "run_generation", new=AsyncMock()
            ) as mock_run:
                result = await impl_auto._run_implementation_agent(
                    "some-feature", spec_dir, "constitution", "spec", "plan", tasks, "quality"
                )

            mock_run.assert_not_called()
            self.assertEqual(result, tasks)

    async def test_completes_on_first_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            _write_tasks(spec_dir, "- [x] T001 [IMPL] done\n")
            with patch.object(
                impl_auto.local_agent_loop, "run_generation", new=AsyncMock()
            ) as mock_run:
                result = await impl_auto._run_implementation_agent(
                    "some-feature",
                    spec_dir,
                    "constitution",
                    "spec",
                    "plan",
                    "- [ ] T001 [IMPL] todo\n",
                    "quality",
                )

            self.assertEqual(mock_run.await_count, 1)
            self.assertEqual(result, "- [x] T001 [IMPL] done\n")

    async def test_retries_once_when_first_attempt_leaves_tasks_unchecked(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            _write_tasks(spec_dir, "- [ ] T001 [IMPL] todo\n")

            calls = []

            async def fake_run_generation(
                _log, _stage, *, claude_fallback, system_prompt, user_prompt
            ):
                calls.append(1)
                if len(calls) >= 2:
                    _write_tasks(spec_dir, "- [x] T001 [IMPL] done\n")

            with patch.object(
                impl_auto.local_agent_loop,
                "run_generation",
                new=AsyncMock(side_effect=fake_run_generation),
            ) as mock_run:
                result = await impl_auto._run_implementation_agent(
                    "some-feature",
                    spec_dir,
                    "constitution",
                    "spec",
                    "plan",
                    "- [ ] T001 [IMPL] todo\n",
                    "quality",
                )

            self.assertEqual(mock_run.await_count, 2)
            self.assertEqual(result, "- [x] T001 [IMPL] done\n")

    async def test_waits_for_a_human_when_tasks_still_unchecked_after_both_attempts(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            _write_tasks(spec_dir, "- [ ] T001 [IMPL] todo\n")

            with (
                patch.object(impl_auto.local_agent_loop, "run_generation", new=AsyncMock()),
                self.assertRaises(SystemExit) as cm,
            ):
                await impl_auto._run_implementation_agent(
                    "some-feature",
                    spec_dir,
                    "constitution",
                    "spec",
                    "plan",
                    "- [ ] T001 [IMPL] todo\n",
                    "quality",
                )

            self.assertEqual(cm.exception.code, impl_auto.HUMAN_ACTION_EXIT_CODE)


if __name__ == "__main__":
    unittest.main()


class TestCiGate(unittest.IsolatedAsyncioTestCase):
    async def test_no_ci_commands_reports_skipped_not_passed(self):
        """specs/004, 005: "All CI checks passed" was logged with nothing run."""
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            with (
                patch.object(impl_auto, "_build_ci_commands", return_value=[]),
                patch.object(impl_auto, "_run_commands") as run_commands,
            ):
                outcome = await impl_auto._ci_gate(
                    "feat", spec_dir, "c", "s", "p", include_slow=True
                )
            run_commands.assert_not_called()
            self.assertIn("SKIPPED", outcome)
            followup = (spec_dir / "FOLLOWUP-non-blocking-concerns.md").read_text()
            self.assertIn("CI was not run", followup)

    async def test_failure_gets_one_fix_attempt_then_passes(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            _write_tasks(spec_dir, "- [x] T001 [IMPL] done\n")
            with (
                patch.object(impl_auto, "_build_ci_commands", return_value=[("unit", ["x"])]),
                patch.object(
                    impl_auto,
                    "_run_commands",
                    side_effect=[impl_auto.CiRun(False, "boom"), impl_auto.CiRun(True, "")],
                ),
                patch.object(impl_auto, "_run_ci_fix_agent", new=AsyncMock()) as fix,
            ):
                outcome = await impl_auto._ci_gate(
                    "feat", spec_dir, "c", "s", "p", include_slow=False
                )
            fix.assert_awaited_once()
            self.assertEqual(outcome, "All CI checks passed")


class TestCiOutputTail(unittest.TestCase):
    def test_short_output_is_unchanged(self):
        self.assertEqual(impl_auto._tail("one\ntwo"), "one\ntwo")

    def test_long_output_keeps_the_end_and_says_what_was_cut(self):
        output = "\n".join(f"line {i}" for i in range(5000))
        tail = impl_auto._tail(output)
        self.assertTrue(tail.endswith("line 4999"))
        self.assertIn("earlier characters omitted", tail)
        self.assertLessEqual(len(tail.splitlines()), impl_auto._CI_OUTPUT_TAIL_LINES + 1)


class TestCiEnvironmentStops(unittest.IsolatedAsyncioTestCase):
    async def _gate(self, *runs, allowed=None):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            _write_tasks(spec_dir, "- [x] T001 [IMPL] done\n")
            with (
                patch.object(impl_auto, "_build_ci_commands", return_value=[("unit", ["x"])]),
                patch.object(impl_auto, "_run_commands", side_effect=list(runs)),
                patch.object(impl_auto, "_run_ci_fix_agent", new=AsyncMock()) as fix,
                patch.object(
                    impl_auto.ollama, "load_allowed_skipped_tests", return_value=allowed or []
                ),
            ):
                try:
                    await impl_auto._ci_gate("feat", spec_dir, "c", "s", "p", include_slow=False)
                    code = None
                except SystemExit as e:
                    code = e.code
            return code, fix

    async def test_environment_failure_waits_without_a_fix_attempt(self):
        """024, 045: Postgres ECONNREFUSED went to the code-fix agent."""
        run = impl_auto.CiRun(False, "boom", "connect ECONNREFUSED 127.0.0.1:5432")
        code, fix = await self._gate(run)
        self.assertEqual(code, impl_auto.HUMAN_ACTION_EXIT_CODE)
        fix.assert_not_awaited()

    async def test_wholly_skipped_suites_wait_unless_allowed(self):
        """045: 310 Postgres-backed tests skipped for want of DATABASE_URL, reported PASS."""
        run = impl_auto.CiRun(True, "", skipped_files=("tests/db/create-list-clubs.test.ts",))
        code, _ = await self._gate(run)
        self.assertEqual(code, impl_auto.HUMAN_ACTION_EXIT_CODE)
        code, _ = await self._gate(run, allowed=["tests/db/*"])
        self.assertIsNone(code)


class TestStopOnTestUpdates(unittest.TestCase):
    def test_waits_when_the_implementation_agent_noted_a_needed_update(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            (spec_dir / impl_auto.TEST_UPDATES_FILENAME).write_text(
                "- tests/nav.test.ts: shows four sections: Settings replaces Family\n"
            )
            with (
                patch.object(impl_auto, "test_case_edits", return_value={}),
                patch.object(impl_auto.ollama, "is_stage_enabled", return_value=True),
                patch.object(impl_auto, "log"),
                self.assertRaises(SystemExit) as exit_info,
            ):
                impl_auto._stop_on_test_updates(spec_dir, "base")
            self.assertEqual(exit_info.exception.code, impl_auto.HUMAN_ACTION_EXIT_CODE)

    def test_passes_with_no_notes_or_edits(self):
        with (
            tempfile.TemporaryDirectory() as d,
            patch.object(impl_auto, "test_case_edits", return_value={}),
            patch.object(impl_auto.ollama, "is_stage_enabled", return_value=True),
        ):
            impl_auto._stop_on_test_updates(Path(d), "base")

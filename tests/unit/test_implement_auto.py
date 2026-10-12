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

    async def test_exits_with_status_1_when_tasks_still_unchecked_after_both_attempts(self):
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

            self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()

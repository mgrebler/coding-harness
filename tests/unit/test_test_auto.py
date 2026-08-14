"""Unit tests for ch_3_test_auto.py's _run_test_agent_if_needed — the bounded
retry-then-hard-fail behaviour added for FOLLOWUP_HARNESS.md Bug 3 (a backgrounded,
never-awaited subagent call silently truncated the delegated work and let the script
proceed into a doomed critic loop). No LLM calls (stream_query is mocked)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
import ch_3_test_auto as test_auto


def _write_tasks(spec_dir: Path, content: str) -> None:
    (spec_dir / "tasks.md").write_text(content, encoding="utf-8")


class TestRunTestAgentIfNeeded(unittest.IsolatedAsyncioTestCase):
    async def test_skips_agent_when_all_test_tasks_already_checked(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            tasks = "- [x] T001 [TEST] done\n"
            with patch.object(test_auto, "stream_query", new=AsyncMock()) as mock_stream:
                result = await test_auto._run_test_agent_if_needed(
                    "some-feature", spec_dir, tasks, "constitution", "spec", "plan", "principles"
                )

            mock_stream.assert_not_called()
            self.assertEqual(result, tasks)

    async def test_completes_on_first_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            _write_tasks(spec_dir, "- [x] T001 [TEST] done\n")
            with patch.object(test_auto, "stream_query", new=AsyncMock()) as mock_stream:
                result = await test_auto._run_test_agent_if_needed(
                    "some-feature",
                    spec_dir,
                    "- [ ] T001 [TEST] todo\n",
                    "constitution",
                    "spec",
                    "plan",
                    "principles",
                )

            self.assertEqual(mock_stream.await_count, 1)
            self.assertEqual(result, "- [x] T001 [TEST] done\n")

    async def test_retries_once_when_first_attempt_leaves_tasks_unchecked(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            _write_tasks(spec_dir, "- [ ] T001 [TEST] todo\n")
            calls = []

            async def fake_stream_query(_query):
                calls.append(1)
                if len(calls) >= 2:
                    _write_tasks(spec_dir, "- [x] T001 [TEST] done\n")

            with patch.object(
                test_auto, "stream_query", new=AsyncMock(side_effect=fake_stream_query)
            ) as mock_stream:
                result = await test_auto._run_test_agent_if_needed(
                    "some-feature",
                    spec_dir,
                    "- [ ] T001 [TEST] todo\n",
                    "constitution",
                    "spec",
                    "plan",
                    "principles",
                )

            self.assertEqual(mock_stream.await_count, 2)
            self.assertEqual(result, "- [x] T001 [TEST] done\n")

    async def test_exits_with_status_1_when_tasks_still_unchecked_after_both_attempts(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            _write_tasks(spec_dir, "- [ ] T001 [TEST] todo\n")

            with patch.object(test_auto, "stream_query", new=AsyncMock()):
                with self.assertRaises(SystemExit) as cm:
                    await test_auto._run_test_agent_if_needed(
                        "some-feature",
                        spec_dir,
                        "- [ ] T001 [TEST] todo\n",
                        "constitution",
                        "spec",
                        "plan",
                        "principles",
                    )

            self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()

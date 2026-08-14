"""Unit tests for ch_4_implement_auto.py's _run_implementation_agent — the bounded
retry-then-hard-fail behaviour added for FOLLOWUP_HARNESS.md Bug 3 (a backgrounded,
never-awaited impl-agent call silently truncated the implementation and let the
script proceed straight into a doomed CI run). No LLM calls (stream_query is mocked)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
import ch_4_implement_auto as impl_auto


def _write_tasks(spec_dir: Path, content: str) -> None:
    (spec_dir / "tasks.md").write_text(content, encoding="utf-8")


class TestRunImplementationAgent(unittest.IsolatedAsyncioTestCase):
    async def test_skips_agent_when_all_tasks_already_checked(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            tasks = "- [x] T001 [IMPL] done\n"
            with patch.object(impl_auto, "stream_query", new=AsyncMock()) as mock_stream:
                result = await impl_auto._run_implementation_agent(
                    "some-feature", spec_dir, "constitution", "spec", "plan", tasks, "quality"
                )

            mock_stream.assert_not_called()
            self.assertEqual(result, tasks)

    async def test_completes_on_first_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            _write_tasks(spec_dir, "- [x] T001 [IMPL] done\n")
            with patch.object(impl_auto, "stream_query", new=AsyncMock()) as mock_stream:
                result = await impl_auto._run_implementation_agent(
                    "some-feature",
                    spec_dir,
                    "constitution",
                    "spec",
                    "plan",
                    "- [ ] T001 [IMPL] todo\n",
                    "quality",
                )

            self.assertEqual(mock_stream.await_count, 1)
            self.assertEqual(result, "- [x] T001 [IMPL] done\n")

    async def test_retries_once_when_first_attempt_leaves_tasks_unchecked(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            _write_tasks(spec_dir, "- [ ] T001 [IMPL] todo\n")

            calls = []

            async def fake_stream_query(_query):
                calls.append(1)
                if len(calls) >= 2:
                    _write_tasks(spec_dir, "- [x] T001 [IMPL] done\n")

            with patch.object(
                impl_auto, "stream_query", new=AsyncMock(side_effect=fake_stream_query)
            ) as mock_stream:
                result = await impl_auto._run_implementation_agent(
                    "some-feature",
                    spec_dir,
                    "constitution",
                    "spec",
                    "plan",
                    "- [ ] T001 [IMPL] todo\n",
                    "quality",
                )

            self.assertEqual(mock_stream.await_count, 2)
            self.assertEqual(result, "- [x] T001 [IMPL] done\n")

    async def test_exits_with_status_1_when_tasks_still_unchecked_after_both_attempts(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            _write_tasks(spec_dir, "- [ ] T001 [IMPL] todo\n")

            with patch.object(impl_auto, "stream_query", new=AsyncMock()):
                with self.assertRaises(SystemExit) as cm:
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

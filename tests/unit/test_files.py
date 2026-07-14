"""Unit tests for agent_common/files.py pure functions. No LLM or network calls."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import files


class TestReadOptional(unittest.TestCase):
    def test_existing_file_returns_content(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "foo.md"
            p.write_text("hello", encoding="utf-8")
            self.assertEqual(files.read_optional(p, "(missing)"), "hello")

    def test_missing_file_returns_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "missing.md"
            self.assertEqual(files.read_optional(p, "(missing)"), "(missing)")


class TestRequireFiles(unittest.TestCase):
    def test_all_present_does_not_exit(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = Path(d) / "a.md"
            p2 = Path(d) / "b.md"
            p1.write_text("x")
            p2.write_text("y")
            files.require_files("test-critic", p1, p2)  # should not raise

    def test_missing_file_exits(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = Path(d) / "a.md"
            p1.write_text("x")
            missing = Path(d) / "missing.md"
            with self.assertRaises(SystemExit) as ctx:
                files.require_files("test-critic", p1, missing)
            self.assertEqual(ctx.exception.code, 1)


class TestRequireSpecFiles(unittest.TestCase):
    def test_all_present_does_not_exit(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            (spec_dir / "spec.md").write_text("x")
            log = MagicMock()
            files.require_spec_files(log, spec_dir, "spec.md")  # should not raise
            log.assert_not_called()

    def test_missing_file_exits_and_logs(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            log = MagicMock()
            with self.assertRaises(SystemExit) as ctx:
                files.require_spec_files(log, spec_dir, "plan.md")
            self.assertEqual(ctx.exception.code, 1)
            log.assert_called_once()
            self.assertIn("plan.md", log.call_args[0][0])


class TestReadChangedSourceFiles(unittest.TestCase):
    def test_reads_existing_changed_file(self):
        with tempfile.TemporaryDirectory() as d:
            old_cwd = os.getcwd()
            os.chdir(d)
            try:
                Path("backend").mkdir()
                (Path("backend") / "health.ts").write_text("export default {}", encoding="utf-8")
                result = files.read_changed_source_files(["backend/health.ts"])
                self.assertIn("backend/health.ts", result)
                self.assertIn("export default {}", result)
            finally:
                os.chdir(old_cwd)

    def test_skips_specs_and_result_files(self):
        result = files.read_changed_source_files(
            [
                "specs/001-feature/plan.md",
                "specs/001-feature/ch-1-plan-critic-result-1.json",
            ]
        )
        self.assertEqual(result, "(no changed files found)")

    def test_no_changed_files_returns_placeholder(self):
        self.assertEqual(files.read_changed_source_files([]), "(no changed files found)")


if __name__ == "__main__":
    unittest.main()

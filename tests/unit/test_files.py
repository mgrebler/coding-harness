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
            old_cwd = Path.cwd()
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


class TestReadChangedFiles(unittest.TestCase):
    def test_diff_only_shows_existing_files_as_their_diff(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as d:
            orig = Path.cwd()
            os.chdir(d)
            try:
                Path("tests").mkdir()
                Path("tests/old.test.ts").write_text("BIG OLD FILE")
                Path("tests/new.test.ts").write_text("NEW FILE")
                diffs = {"tests/old.test.ts": "@@ -1 +1 @@\n-a\n+b", "tests/new.test.ts": None}
                with patch.object(files.git, "branch_diff", side_effect=diffs.get):
                    out = files.read_changed_files(list(diffs), ("tests/",), diff_only=True)
            finally:
                os.chdir(orig)
        self.assertIn("--- tests/old.test.ts (diff against main) ---\n@@", out)
        self.assertNotIn("BIG OLD FILE", out)
        self.assertIn("--- tests/new.test.ts ---\nNEW FILE", out)

    def test_no_match_raises_instead_of_returning_a_placeholder(self):
        with self.assertRaises(files.NoChangedFilesError):
            files.read_changed_files(["src/app.ts"], ("tests/",))


class TestReadPlanBundle(unittest.TestCase):
    def test_appends_existing_sibling_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            (spec_dir / "plan.md").write_text("PLAN")
            (spec_dir / "data-model.md").write_text("CaseOutcome")
            bundle = files.read_plan_bundle(spec_dir)
            self.assertTrue(bundle.startswith("PLAN"))
            self.assertIn("--- data-model.md (sibling plan artifact) ---\nCaseOutcome", bundle)
            self.assertNotIn("research.md", bundle)


class TestReadMemoryAndRequired(unittest.TestCase):
    def test_missing_memory_doc_reads_as_placeholder(self):
        with tempfile.TemporaryDirectory() as d:
            orig = Path.cwd()
            os.chdir(d)
            try:
                self.assertEqual(files.read_memory("architecture"), "(architecture.md not found)")
            finally:
                os.chdir(orig)

    def test_read_required_returns_contents_in_order_and_exits_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.md", Path(d) / "b.md"
            a.write_text("A")
            b.write_text("B")
            self.assertEqual(files.read_required("x", a, b), ["A", "B"])
            with self.assertRaises(SystemExit):
                files.read_required("x", a, Path(d) / "missing.md")

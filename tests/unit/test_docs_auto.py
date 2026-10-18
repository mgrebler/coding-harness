"""Unit tests for ch_5_docs_auto.py's pure-Python helpers: preflight() (which
gates the docs stage on ch-4-implement having actually completed) and
snapshot_baseline() (which must capture the pre-merge docs/ tree exactly once
per feature, never overwriting it on a later resumed run). No LLM calls."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
import ch_5_docs_auto as docs_auto


def _write_spec_files(spec_dir: Path) -> None:
    (spec_dir / "spec.md").write_text("spec", encoding="utf-8")
    (spec_dir / "plan.md").write_text("plan", encoding="utf-8")
    (spec_dir / "tasks.md").write_text("tasks", encoding="utf-8")


class TestRunDisabledStage(unittest.IsolatedAsyncioTestCase):
    """run() must bail out before preflight() when the docs stage is
    disabled — no spec.md/plan.md/tasks.md or ch-4-implement completion
    marker should be required."""

    async def test_returns_early_without_calling_preflight(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            config_dir = root / ".specify"
            config_dir.mkdir(parents=True)
            (config_dir / "pipeline.json").write_text(
                json.dumps({"stages": {"docs": {"enabled": False}}}), encoding="utf-8"
            )

            orig_cwd = Path.cwd()
            orig_stdout, orig_stderr = sys.stdout, sys.stderr
            try:
                os.chdir(root)
                with patch.object(docs_auto, "preflight") as mock_preflight:
                    await docs_auto.run("some-feature")
                mock_preflight.assert_not_called()
            finally:
                os.chdir(orig_cwd)
                sys.stdout, sys.stderr = orig_stdout, orig_stderr


class TestPreflight(unittest.TestCase):
    def test_exits_when_implement_stage_not_complete(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            _write_spec_files(spec_dir)

            with self.assertRaises(SystemExit) as cm:
                docs_auto.preflight(spec_dir, "some-feature")

            self.assertEqual(cm.exception.code, 1)

    def test_passes_when_implement_stage_complete(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            _write_spec_files(spec_dir)
            (spec_dir / "ch-4-implement-auto-complete").write_text(
                "completed: true\n", encoding="utf-8"
            )

            docs_auto.preflight(spec_dir, "some-feature")  # must not raise

    def test_exits_when_required_spec_file_missing(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            (spec_dir / "ch-4-implement-auto-complete").write_text(
                "completed: true\n", encoding="utf-8"
            )
            # spec.md/plan.md/tasks.md deliberately not written

            with self.assertRaises(SystemExit):
                docs_auto.preflight(spec_dir, "some-feature")


class TestSnapshotBaseline(unittest.TestCase):
    def test_copies_existing_docs_tree_to_baseline(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            spec_dir = root / "specs" / "some-feature"
            spec_dir.mkdir(parents=True)
            docs_dir = root / "docs"
            (docs_dir / "reference").mkdir(parents=True)
            (docs_dir / "index.md").write_text("# Product", encoding="utf-8")
            (docs_dir / "reference" / "auth.md").write_text("# Auth", encoding="utf-8")

            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                docs_auto.snapshot_baseline(spec_dir)
            finally:
                os.chdir(original_cwd)

            baseline = spec_dir / "ch-5-docs-baseline"
            self.assertTrue((baseline / "index.md").exists())
            self.assertEqual((baseline / "index.md").read_text(encoding="utf-8"), "# Product")
            self.assertEqual(
                (baseline / "reference" / "auth.md").read_text(encoding="utf-8"), "# Auth"
            )

    def test_creates_empty_baseline_when_docs_does_not_exist(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            spec_dir = root / "specs" / "some-feature"
            spec_dir.mkdir(parents=True)

            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                docs_auto.snapshot_baseline(spec_dir)
            finally:
                os.chdir(original_cwd)

            baseline = spec_dir / "ch-5-docs-baseline"
            self.assertTrue(baseline.exists())
            self.assertEqual(list(baseline.iterdir()), [])

    def test_does_not_overwrite_existing_baseline_on_resume(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            spec_dir = root / "specs" / "some-feature"
            baseline = spec_dir / "ch-5-docs-baseline"
            baseline.mkdir(parents=True)
            (baseline / "index.md").write_text("# Original pre-merge state", encoding="utf-8")

            docs_dir = root / "docs"
            docs_dir.mkdir()
            (docs_dir / "index.md").write_text("# Partially merged state", encoding="utf-8")

            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                docs_auto.snapshot_baseline(spec_dir)
            finally:
                os.chdir(original_cwd)

            self.assertEqual(
                (baseline / "index.md").read_text(encoding="utf-8"),
                "# Original pre-merge state",
            )


class TestRunResumeCommitsOwnOutput(unittest.IsolatedAsyncioTestCase):
    """The reported failure: a PASS was already on disk, the stage logged
    'Documentation is up to date', and the docs/ merge stayed uncommitted.
    Resuming must commit the docs/ merge and only exclude unrelated drift."""

    async def test_already_passing_resume_commits_docs_and_excludes_only_drift(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            spec_dir = root / "specs/004-feature"
            spec_dir.mkdir(parents=True)
            _write_spec_files(spec_dir)
            (spec_dir / "ch-4-implement-auto-complete").write_text("", encoding="utf-8")
            (spec_dir / "ch-5-docs-critic-result-1.json").write_text(
                json.dumps({"status": "PASS"}), encoding="utf-8"
            )
            memory = root / ".specify/memory"
            memory.mkdir(parents=True)
            for name in ("constitution.md", "documentation-principles.md"):
                (memory / name).write_text(name, encoding="utf-8")
            (root / "docs").mkdir()

            dirty = [
                "docs/index.md",
                "docs/reference/",
                "specs/004-feature/ch-5-docs-auto.log",
                "src/unrelated.py",
            ]
            orig_cwd = Path.cwd()
            orig_stdout, orig_stderr = sys.stdout, sys.stderr
            try:
                os.chdir(root)
                with (
                    patch.object(docs_auto.ollama, "is_stage_enabled", return_value=True),
                    patch.object(docs_auto.git, "dirty_files", return_value=dirty),
                    patch.object(docs_auto.git, "run_auto_commit") as mock_commit,
                    patch.object(docs_auto.local_agent_loop, "run_generation") as mock_gen,
                ):
                    await docs_auto.run("004-feature")
            finally:
                os.chdir(orig_cwd)
                sys.stdout, sys.stderr = orig_stdout, orig_stderr

            mock_gen.assert_not_called()
            mock_commit.assert_called_once_with(
                "after_docs", "ch-5-docs-auto", exclude=["src/unrelated.py"]
            )


class TestIsOwnOutput(unittest.TestCase):
    """Paths the docs stage itself produces must never be excluded from its
    commit as pre-existing drift — the docs agent doesn't commit its own work,
    so a resumed run finds its earlier merge still dirty."""

    def test_docs_and_spec_dir_paths_are_own_output(self):
        spec_dir = Path("specs/004-feature")
        for path in ("docs/index.md", "docs/reference/", "specs/004-feature/ch-5-docs-auto.log"):
            with self.subTest(path=path):
                self.assertTrue(docs_auto._is_own_output(path, spec_dir))

    def test_unrelated_paths_are_not_own_output(self):
        spec_dir = Path("specs/004-feature")
        for path in ("src/app.py", "docsite/index.md", "specs/003-other/spec.md"):
            with self.subTest(path=path):
                self.assertFalse(docs_auto._is_own_output(path, spec_dir))


if __name__ == "__main__":
    unittest.main()

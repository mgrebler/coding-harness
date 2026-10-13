"""Unit tests for ch_5_docs_auto.py's pure-Python helpers: preflight() (which
gates the docs stage on ch-4-implement having actually completed) and
snapshot_baseline() (which must capture the pre-merge docs/ tree exactly once
per feature, never overwriting it on a later resumed run). No LLM calls."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
import ch_5_docs_auto as docs_auto


def _write_spec_files(spec_dir: Path) -> None:
    (spec_dir / "spec.md").write_text("spec", encoding="utf-8")
    (spec_dir / "plan.md").write_text("plan", encoding="utf-8")
    (spec_dir / "tasks.md").write_text("tasks", encoding="utf-8")


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


if __name__ == "__main__":
    unittest.main()

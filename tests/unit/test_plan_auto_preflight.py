"""Regression tests for plan-auto.py's preflight() non-interactive default.

plan-auto.py's filename contains a dash and isn't import-syntax-loadable, so it's
loaded here via importlib.util directly from its file path.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

AGENTS_DIR = Path(__file__).parent.parent.parent / ".claude/agents"
sys.path.insert(0, str(AGENTS_DIR))

_spec = importlib.util.spec_from_file_location("plan_auto", AGENTS_DIR / "plan-auto.py")
plan_auto = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plan_auto)


class TestPlanAutoPreflight(unittest.TestCase):
    def _make_spec_dir(self, d, *, with_plan=False, with_result=False):
        spec_dir = Path(d)
        (spec_dir / "spec.md").write_text("spec", encoding="utf-8")
        if with_plan:
            (spec_dir / "plan.md").write_text("plan", encoding="utf-8")
        if with_result:
            (spec_dir / "plan-critic-result-1.json").write_text('{"status": "FAIL"}', encoding="utf-8")
        return spec_dir

    def test_no_existing_plan_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = self._make_spec_dir(d)
            self.assertFalse(plan_auto.preflight(spec_dir, "feat"))

    def test_existing_plan_with_results_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = self._make_spec_dir(d, with_plan=True, with_result=True)
            self.assertFalse(plan_auto.preflight(spec_dir, "feat"))

    def test_existing_plan_no_results_non_interactive_defaults_to_resume(self):
        """Regression test: non-interactive mode must default to 'resume', not 'regen'
        — the prior bug caused plan.md to be silently regenerated on unattended re-runs
        despite the prompt itself advertising 'resume' as the default."""
        with tempfile.TemporaryDirectory() as d:
            spec_dir = self._make_spec_dir(d, with_plan=True)
            with patch.object(plan_auto.sys, "stdin", MagicMock(isatty=lambda: False)):
                self.assertFalse(plan_auto.preflight(spec_dir, "feat"))

    def test_existing_plan_no_results_interactive_regen_response(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = self._make_spec_dir(d, with_plan=True)
            with patch.object(plan_auto.sys, "stdin", MagicMock(isatty=lambda: True)), \
                 patch("builtins.input", return_value="regen"):
                self.assertTrue(plan_auto.preflight(spec_dir, "feat"))

    def test_existing_plan_no_results_interactive_resume_response(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = self._make_spec_dir(d, with_plan=True)
            with patch.object(plan_auto.sys, "stdin", MagicMock(isatty=lambda: True)), \
                 patch("builtins.input", return_value="resume"):
                self.assertFalse(plan_auto.preflight(spec_dir, "feat"))

    def test_abort_response_exits(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = self._make_spec_dir(d, with_plan=True)
            with patch.object(plan_auto.sys, "stdin", MagicMock(isatty=lambda: True)), \
                 patch("builtins.input", return_value="abort"):
                with self.assertRaises(SystemExit) as ctx:
                    plan_auto.preflight(spec_dir, "feat")
                self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()

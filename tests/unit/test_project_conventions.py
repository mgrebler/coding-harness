"""Unit tests for agent_common/project_conventions.py. No LLM or network calls."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import project_conventions as pc


class _InTempProject(unittest.TestCase):
    def setUp(self):
        self._old_cwd = Path.cwd()
        self._tmpdir = tempfile.TemporaryDirectory()
        os.chdir(self._tmpdir.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmpdir.cleanup()

    def _write_constitution(self, text: str):
        path = Path(".specify/memory/constitution.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _write_readme(self, text: str):
        Path("README.md").write_text(text, encoding="utf-8")


class TestResolveCiCommands(_InTempProject):
    def test_resolves_explicit_commands_with_project_own_labels(self):
        self._write_constitution(
            "## 12. CI Requirements\n\n"
            "- Typecheck (`mypy .`)\n"
            "- Security scan (`bandit -r src`)\n\n"
            "---\n"
        )
        checks = dict(pc.resolve_ci_commands())
        self.assertEqual(checks["Typecheck"], ["mypy", "."])
        self.assertEqual(checks["Security scan"], ["bandit", "-r", "src"])

    def test_ignores_placeholder_commands(self):
        self._write_constitution(
            "## 12. CI Requirements\n\n- Typecheck (`[PROJECT: e.g. pnpm typecheck]`)\n\n---\n"
        )
        self.assertEqual(pc.resolve_ci_commands(), [])

    def test_falls_back_to_readme_when_constitution_silent(self):
        self._write_constitution("## 12. CI Requirements\n\n- Lint\n\n---\n")
        self._write_readme("## Testing\n\n```bash\npytest -q\n```\n")
        checks = dict(pc.resolve_ci_commands())
        self.assertEqual(checks["Testing"], ["pytest", "-q"])

    def test_readme_entry_skipped_if_label_already_covered(self):
        self._write_constitution("## 12. CI Requirements\n\n- Test (`pytest -q`)\n\n---\n")
        self._write_readme("## Test\n\n```bash\nsome-other-command\n```\n")
        checks = dict(pc.resolve_ci_commands())
        self.assertEqual(checks["Test"], ["pytest", "-q"])

    def test_no_sources_returns_empty_list(self):
        self.assertEqual(pc.resolve_ci_commands(), [])

    def test_readme_prose_between_heading_and_fence_does_not_leak_into_heading(self):
        # Heading has no CI keyword and isn't directly followed by its fence -
        # a prose paragraph sits in between. A heading-capture regex that lets
        # DOTALL cross line boundaries would swallow that prose into the
        # "heading" text, and a stray keyword substring in it (here "ci" in
        # "explicitly") would falsely mark this block as CI-relevant.
        self._write_constitution("## 12. CI Requirements\n\n- Lint\n\n---\n")
        self._write_readme(
            "## Setup\n\n"
            "Some prose here about configuration. If you need to set it explicitly:\n\n"
            "```bash\necho hello\n```\n"
        )
        checks = dict(pc.resolve_ci_commands())
        self.assertEqual(checks, {})

    def test_no_ci_section_returns_empty_list(self):
        self._write_constitution("## 2. Stack Constraints\n\nsome text\n\n---\n")
        self.assertEqual(pc.resolve_ci_commands(), [])


class TestIsSlowCheck(unittest.TestCase):
    def test_e2e_labels_are_slow(self):
        self.assertTrue(pc.is_slow_check("E2E tests"))
        self.assertTrue(pc.is_slow_check("Playwright suite"))
        self.assertTrue(pc.is_slow_check("Integration tests"))

    def test_typecheck_and_unit_labels_are_not_slow(self):
        self.assertFalse(pc.is_slow_check("Typecheck"))
        self.assertFalse(pc.is_slow_check("Unit tests"))
        self.assertFalse(pc.is_slow_check("Lint"))


class TestResolveTestDirs(_InTempProject):
    def test_resolves_explicit_paths_from_constitution(self):
        self._write_constitution(
            "## 5. Test-Driven Development\n\n"
            "### Test file location\n\n"
            "- Backend tests: `backend/tests/`\n"
            "- Frontend tests: `frontend/tests/`\n\n"
            "---\n"
        )
        self.assertEqual(pc.resolve_test_dirs(), ("backend/tests/", "frontend/tests/"))

    def test_ignores_placeholder_paths(self):
        self._write_constitution(
            "### Test file location\n\n- Tests: `[PROJECT: e.g. backend/tests/]`\n\n---\n"
        )
        self.assertEqual(pc.resolve_test_dirs(), ())

    def test_falls_back_to_repo_layout_when_constitution_silent(self):
        self._write_constitution("## 2. Stack Constraints\n\nsome text\n\n---\n")
        fake_result = MagicMock(returncode=0, stdout="tests/test_foo.py\nsrc/widgets.py\n")
        with patch.object(pc.subprocess, "run", return_value=fake_result):
            self.assertEqual(pc.resolve_test_dirs(), ("tests/",))

    def test_git_failure_falls_back_to_empty_tuple(self):
        self._write_constitution("## 2. Stack Constraints\n\nsome text\n\n---\n")
        fake_result = MagicMock(returncode=1, stdout="")
        with patch.object(pc.subprocess, "run", return_value=fake_result):
            self.assertEqual(pc.resolve_test_dirs(), ())

    def test_no_sources_returns_empty_tuple(self):
        self.assertEqual(pc.resolve_test_dirs(), ())


if __name__ == "__main__":
    unittest.main()

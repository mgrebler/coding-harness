"""Consistency checks between the harness's own config files and the code that
depends on them. No LLM or network calls; auto-commit.sh runs for real against a
throwaway git repo."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).parent.parent.parent
AGENTS_DIR = REPO / ".claude/agents"
GIT_EXT_DIR = REPO / ".specify/extensions/git"


def _commit_events_used_by_stage_scripts() -> set[str]:
    events = set()
    for script in AGENTS_DIR.glob("ch_*_auto.py"):
        events.update(re.findall(r'"(after_[a-z]+)"', script.read_text(encoding="utf-8")))
    return events


def _parse_extension_hooks(text: str) -> dict[str, dict[str, str]]:
    """Minimal parser for extension.yml's `hooks:` block — PyYAML isn't a
    dependency of this repo."""
    block = text.split("\nhooks:\n", 1)[1].split("\n\n", 1)[0]
    hooks: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    for line in block.splitlines():
        if m := re.match(r"^  (\w+):$", line):
            current = hooks.setdefault(m.group(1), {})
        elif m := re.match(r"^    (\w+): (.*)$", line):
            current[m.group(1)] = m.group(2)
    return hooks


def _parse_registered_hooks(text: str) -> dict[str, dict[str, str]]:
    """Minimal parser for .specify/extensions.yml's `hooks:` block (one git
    entry per event)."""
    block = text.split("\nhooks:\n", 1)[1]
    hooks: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    for line in block.splitlines():
        if m := re.match(r"^  (\w+):$", line):
            current = hooks.setdefault(m.group(1), {})
        elif m := re.match(r"^    [- ] (\w+): (.*)$", line):
            current[m.group(1)] = m.group(2)
    return hooks


class TestStageCommitEventsAreEnabled(unittest.TestCase):
    """Every after_<stage> event a ch_*_auto.py script commits on must actually
    commit. A missing git-config.yml entry made auto-commit.sh skip after_docs
    (and after_test) silently, so docs merges were never committed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        shutil.copytree(GIT_EXT_DIR, self.repo / ".specify/extensions/git")
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        self._git("config", "commit.gpgsign", "false")
        (self.repo / "seed.txt").write_text("seed", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-q", "-m", "seed")

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout

    def _auto_commit(self, event: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", ".specify/extensions/git/scripts/bash/auto-commit.sh", event],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )

    def test_stage_scripts_use_the_expected_events(self):
        self.assertEqual(
            _commit_events_used_by_stage_scripts(),
            {"after_plan", "after_tasks", "after_test", "after_implement", "after_docs"},
        )

    def test_every_stage_event_commits_dirty_changes(self):
        for event in sorted(_commit_events_used_by_stage_scripts()):
            with self.subTest(event=event):
                (self.repo / f"{event}.md").write_text(event, encoding="utf-8")
                before = self._git("rev-parse", "HEAD")

                self._auto_commit(event)

                self.assertNotEqual(self._git("rev-parse", "HEAD"), before)
                self.assertEqual(self._git("status", "--porcelain"), "")

    def test_unconfigured_event_warns_instead_of_skipping_silently(self):
        (self.repo / "dirty.md").write_text("x", encoding="utf-8")

        result = self._auto_commit("after_nonexistent")

        self.assertEqual(result.returncode, 0)
        self.assertIn("no auto_commit.after_nonexistent entry", result.stderr)


class TestExtensionHooksAreRegistered(unittest.TestCase):
    """speckit-* skills only read hooks from .specify/extensions.yml; a hook
    declared in extension.yml but missing there never fires (the before_specify
    branch-creation hook silently never ran in installed projects)."""

    def test_registration_mirrors_extension_hooks(self):
        declared = _parse_extension_hooks(
            (GIT_EXT_DIR / "extension.yml").read_text(encoding="utf-8")
        )
        registered = _parse_registered_hooks(
            (REPO / ".specify/extensions.yml").read_text(encoding="utf-8")
        )

        self.assertIn("before_specify", declared)
        self.assertEqual(set(registered), set(declared))
        for event, hook in declared.items():
            with self.subTest(event=event):
                entry = registered[event]
                self.assertEqual(entry["extension"], "git")
                self.assertEqual(entry["enabled"], "true")
                for key in ("command", "optional", "prompt", "description"):
                    self.assertEqual(entry.get(key), hook.get(key))

    def test_install_copies_registration(self):
        install = (REPO / "install.sh").read_text(encoding="utf-8")
        self.assertIn('always_copy ".specify/extensions.yml"', install)


if __name__ == "__main__":
    unittest.main()


class TestInstalledGitignore(unittest.TestCase):
    """specs/005's stages each logged .claude/agents/__pycache__/*.pyc as dirty
    paths to leave out of their commit: the installed .gitignore section only
    ignored the ch_*.py sources, not the bytecode Python writes beside them."""

    def test_harness_bytecode_is_ignored_and_tracked_copies_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)
            git = ["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t"]
            subprocess.run([*git, "init", "-q"], check=True)
            pyc = target / ".claude/agents/__pycache__/ch_2_tasks_critic.cpython-311.pyc"
            pyc.parent.mkdir(parents=True)
            pyc.write_bytes(b"\0")
            subprocess.run([*git, "add", "-A"], check=True)
            subprocess.run([*git, "commit", "-qm", "init"], check=True)

            result = subprocess.run(
                ["bash", str(REPO / "install.sh"), d],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                check=True,
            )

            self.assertIn("\n__pycache__/\n", (target / ".gitignore").read_text())
            self.assertIn("git rm -r --cached .claude/agents/__pycache__", result.stdout)

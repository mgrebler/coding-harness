"""Feature numbering in the git extension's create-new-feature.sh, run with
--dry-run against a throwaway repo and remote."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HARNESS = Path(__file__).parent.parent.parent
SCRIPT = ".specify/extensions/git/scripts/bash/create-new-feature.sh"


class TestFeatureNumbering(unittest.TestCase):
    def _git(self, *args, cwd=None):
        subprocess.run(["git", *args], cwd=cwd or self.repo, check=True, capture_output=True)

    def _commit_spec(self, name):
        (self.repo / "specs" / name).mkdir(parents=True)
        (self.repo / "specs" / name / "spec.md").write_text("# spec\n")
        self._git("add", "-A")
        self._git("commit", "-qm", f"spec {name}")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        shutil.copytree(HARNESS / ".specify/scripts", self.repo / ".specify/scripts")
        shutil.copytree(HARNESS / ".specify/extensions/git", self.repo / ".specify/extensions/git")
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "t")
        self._commit_spec("076-security-hardening")
        self._git("init", "-q", "--bare", str(root / "remote.git"), cwd=root)
        self._git("remote", "add", "origin", str(root / "remote.git"))

    def tearDown(self):
        self.tmp.cleanup()

    def _next_number(self):
        result = subprocess.run(
            ["bash", SCRIPT, "--json", "--dry-run", "--short-name", "next", "Next feature"],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout.strip().splitlines()[-1])["FEATURE_NUM"]

    def test_only_branches_with_their_own_spec_dir_count(self):
        """sprognoggin numbered a feature 225 after issue branches 223-zod-4 and 224-…"""
        self._git("branch", "223-zod-4")
        self._git("checkout", "-qb", "077-x")
        self._commit_spec("077-x")
        self._git("push", "-q", "origin", "077-x")
        self._git("fetch", "-q", "origin")
        self._git("checkout", "-q", "main")
        self._git("branch", "-qD", "077-x")
        self.assertEqual(self._next_number(), "078")

    def test_specs_alone_give_the_highest_plus_one(self):
        self.assertEqual(self._next_number(), "077")

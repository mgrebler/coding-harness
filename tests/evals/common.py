"""Shared helpers for eval tests. Import alongside _ollama.py."""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
AGENTS = Path(__file__).parent.parent.parent / ".claude/agents"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "deepseek-r1:8b")


def make_llm_config(critic_name: str) -> dict:
    """Build a LOCAL_LLM_CONFIG with a single critic enabled."""
    return {
        "ollama_url": OLLAMA_URL,
        "num_ctx": 16384,
        "keep_alive": -1,
        "temperature": 0.0,
        # Backstop against runaway thinking (reasoning models can otherwise generate
        # unboundedly); set well above every legitimate thinking chain observed in
        # this eval suite so it never truncates a real response mid-JSON. The
        # quality-review prompt (full code-quality-principles.md + multiple file
        # contents) is the heaviest in the suite and needs more headroom than the
        # others to finish thinking before answering.
        "num_predict": 16384,
        "default": {"enabled": False, "model": ""},
        "critics": {critic_name: {"enabled": True, "model": OLLAMA_MODEL}},
    }


def run_critic(
    tmpdir: Path,
    critic_name: str,
    feature: str = "001-health-endpoint",
    result_prefix: str | None = None,
) -> dict:
    """Run a critic subprocess and return the parsed result JSON.

    result_prefix defaults to "<critic_name>-critic-result"; pass an explicit
    value for gates whose result filename doesn't follow that convention
    (e.g. "architecture-review-result", "code-quality-review-result").
    """
    script = AGENTS / f"{critic_name}_critic.py"
    subprocess.run(
        [sys.executable, str(script), "--feature", feature],
        cwd=tmpdir,
        check=True,
    )
    prefix = result_prefix or f"{critic_name}-critic-result"
    result_path = tmpdir / "specs" / feature / f"{prefix}-1.json"
    return json.loads(result_path.read_text(encoding="utf-8"))


def setup_git_repo(
    tmpdir: Path,
    files: dict,
    commit_message: str = "Add feature files",
    branch: str = "001-health-endpoint",
) -> None:
    """Init a git repo on a feature branch and commit the given files.

    files: mapping of repo-relative path (str) → source fixture path (Path or str)
    """

    def git(*args):
        subprocess.run(["git", *args], cwd=tmpdir, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "test@harness.local")
    git("config", "user.name", "Harness Test")
    (tmpdir / "README.md").write_text("# test repo")
    git("add", "README.md")
    git("commit", "-m", "Initial commit")
    git("checkout", "-b", branch)
    for repo_path, src in files.items():
        dst = tmpdir / repo_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
        git("add", repo_path)
    git("commit", "-m", commit_message)


def assert_violations_match(
    tc: unittest.TestCase, result: dict, pattern: str, msg: str = ""
) -> None:
    """Assert that at least one violation's rule+finding matches the regex pattern."""
    rule_texts = " ".join(
        v.get("rule", "") + " " + v.get("finding", "") for v in result.get("violations", [])
    )
    tc.assertRegex(rule_texts.lower(), pattern, msg)

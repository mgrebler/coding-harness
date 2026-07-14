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


# critic_name (the local-llm.json critic_type key) -> (script filename, result-file prefix).
# Neither the key nor either value follows a shared naming convention, so this is a
# plain lookup rather than a derivation.
_CRITIC_SCRIPTS = {
    "plan": ("ch_1_plan_critic.py", "ch-1-plan-critic-result"),
    "architecture": ("ch_1_plan_architecture_critic.py", "ch-1-plan-architecture-review-result"),
    "tasks": ("ch_2_tasks_critic.py", "ch-2-tasks-critic-result"),
    "test": ("ch_3_test_critic.py", "ch-3-test-critic-result"),
    "implement": ("ch_4_implement_critic.py", "ch-4-implement-critic-result"),
    "quality": ("ch_4_implement_quality_critic.py", "ch-4-implement-code-quality-review-result"),
}


def run_critic(
    tmpdir: Path,
    critic_name: str,
    feature: str = "001-health-endpoint",
    result_prefix: str | None = None,
) -> dict:
    """Run a critic subprocess and return the parsed result JSON.

    script filename and default result_prefix are resolved from _CRITIC_SCRIPTS;
    pass an explicit result_prefix only to override that default.
    """
    script_name, default_prefix = _CRITIC_SCRIPTS[critic_name]
    script = AGENTS / script_name
    subprocess.run(
        [sys.executable, str(script), "--feature", feature],
        cwd=tmpdir,
        check=True,
    )
    prefix = result_prefix or default_prefix
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

"""
Eval tests for tasks_critic.py.
Requires a running Ollama instance. Configure via environment variables:
  OLLAMA_URL   (default: http://localhost:11434)
  OLLAMA_MODEL (default: deepseek-r1:8b)
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from _ollama import require_ollama
from common import (
    FIXTURES,
    OLLAMA_MODEL,
    OLLAMA_URL,
    assert_violations_match,
    make_llm_config,
    run_critic,
)

LOCAL_LLM_CONFIG = make_llm_config("tasks")


def _setup_tmpdir(tasks_fixture: Path) -> Path:
    tmpdir = Path(tempfile.mkdtemp())
    spec_dir = tmpdir / "specs" / "001-health-endpoint"
    spec_dir.mkdir(parents=True)
    memory_dir = tmpdir / ".specify" / "memory"
    memory_dir.mkdir(parents=True)

    shutil.copy(FIXTURES / "constitution.md", memory_dir / "constitution.md")
    shutil.copy(FIXTURES / "spec.md", spec_dir / "spec.md")
    shutil.copy(FIXTURES / "good" / "plan.md", spec_dir / "plan.md")
    shutil.copy(tasks_fixture, spec_dir / "tasks.md")
    (tmpdir / ".specify" / "local-llm.json").write_text(json.dumps(LOCAL_LLM_CONFIG))
    return tmpdir


class TestTasksCriticGoodTasks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_good_tasks_passes(self):
        tmpdir = _setup_tmpdir(FIXTURES / "good" / "tasks.md")
        result = run_critic(tmpdir, "tasks")
        self.assertEqual(result["status"], "PASS",
                         f"Expected PASS but got FAIL. Violations: {result.get('violations')}")


class TestTasksCriticWrongFormat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_tasks_without_required_format_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "tasks-wrong-format.md")
        result = run_critic(tmpdir, "tasks")
        self.assertEqual(result["status"], "FAIL",
                         "Expected FAIL for tasks without [TEST]/[IMPL] format")
        assert_violations_match(self, result, r"test|impl|format|§t",
                                "Expected a format/structure violation")


class TestTasksCriticNoStoryLabels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_ollama(OLLAMA_URL, OLLAMA_MODEL)

    def test_tasks_without_story_labels_fails(self):
        tmpdir = _setup_tmpdir(FIXTURES / "bad" / "tasks-no-story-labels.md")
        result = run_critic(tmpdir, "tasks")
        self.assertEqual(result["status"], "FAIL",
                         "Expected FAIL for tasks missing US story labels")
        assert_violations_match(self, result, r"story|us1|label|traceab|§t",
                                "Expected a story traceability violation")


if __name__ == "__main__":
    unittest.main()

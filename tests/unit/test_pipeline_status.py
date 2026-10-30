"""Unit tests for agent_common/pipeline_status.py, the status-line progress log."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import pipeline_status

REPO = Path(__file__).parent.parent.parent

NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)
LIVE_PID = os.getpid()
DEAD_PID = 2**22 + 12345


def _entry(stage, step, ts="2026-10-30T11:46:00Z", pid=LIVE_PID, iteration=None, max_it=None):
    return {
        "ts": ts,
        "pid": pid,
        "stage": stage,
        "step": step,
        "iteration": iteration,
        "max_iterations": max_it,
    }


class _ProjectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        self.spec_dir = self.project / "specs/012-foo"
        self.spec_dir.mkdir(parents=True)

    def _log(self, *entries):
        with (self.spec_dir / pipeline_status.LOG_NAME).open("a") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def _complete(self, *markers):
        for m in markers:
            (self.spec_dir / f"{m}-auto-complete").write_text("completed: true\n")

    def _render(self):
        return pipeline_status.render(self.project, "012-foo", NOW)


class TestWriter(_ProjectCase):
    def tearDown(self):
        pipeline_status._bound = None

    def test_unbound_step_writes_nothing(self):
        pipeline_status._bound = None
        pipeline_status.step("plan critic", 1, 3)
        self.assertFalse((self.spec_dir / pipeline_status.LOG_NAME).exists())

    def test_bind_and_step_append_jsonl(self):
        pipeline_status.bind(self.spec_dir, "plan")
        pipeline_status.step("plan critic", 2, 3)
        lines = (self.spec_dir / pipeline_status.LOG_NAME).read_text().splitlines()
        entries = [json.loads(line) for line in lines]
        self.assertEqual([e["step"] for e in entries], ["starting", "plan critic"])
        self.assertEqual(entries[1]["stage"], "plan")
        self.assertEqual((entries[1]["iteration"], entries[1]["max_iterations"]), (2, 3))
        self.assertEqual(entries[1]["pid"], LIVE_PID)

    def test_write_failure_never_raises(self):
        pipeline_status._bound = (self.spec_dir, "plan")
        with patch.object(Path, "open", side_effect=OSError("disk full")):
            pipeline_status.step("plan critic")


class TestRender(_ProjectCase):
    def test_nothing_to_show_without_spec_dir_or_state(self):
        self.assertEqual(pipeline_status.render(self.project, "999-none", NOW), "")
        self.assertEqual(self._render(), "")

    def test_active_stage_shows_step_iteration_and_elapsed(self):
        self._complete("ch-1-plan", "ch-2-tasks")
        self._log(_entry("test", "test quality review", iteration=2, max_it=3))
        self.assertEqual(
            self._render(),
            "012-foo  plan ✓ │ tasks ✓ │ test ▶ test quality review 2/3 · 14m"
            " │ implement · │ docs ·",
        )

    def test_latest_entry_per_stage_wins(self):
        self._log(
            _entry("plan", "generating plan", ts="2026-10-30T10:00:00Z"),
            _entry("plan", "plan critic", ts="2026-10-30T10:47:30Z", iteration=1),
        )
        self.assertIn("plan ▶ plan critic 1 · 1h12m", self._render())

    def test_elapsed_formats(self):
        self.assertEqual(pipeline_status._elapsed("2026-10-30T11:59:15Z", NOW), "45s")
        self.assertEqual(pipeline_status._elapsed("not a time", NOW), "")

    def test_dead_process_is_stopped(self):
        self._log(_entry("implement", "CI checks", pid=DEAD_PID))
        self.assertIn("implement ✗ stopped at CI checks", self._render())

    def test_escalated_and_paused_are_terminal(self):
        self._log(
            _entry("plan", pipeline_status.ESCALATED, pid=DEAD_PID),
            _entry("tasks", pipeline_status.PAUSED, pid=DEAD_PID),
        )
        rendered = self._render()
        self.assertIn("plan ⚠ escalated", rendered)
        self.assertIn("tasks ⏸ paused (usage limit)", rendered)

    def test_completion_marker_beats_stale_entry(self):
        self._complete("ch-1-plan")
        self._log(_entry("plan", "full CI", pid=DEAD_PID))
        self.assertIn("plan ✓", self._render())

    def test_disabled_stages(self):
        (self.project / ".specify").mkdir()
        (self.project / ".specify/pipeline.json").write_text(
            json.dumps({"stages": {"docs": {"enabled": False}, "test": {"enabled": True}}})
        )
        self._complete("ch-1-plan")
        rendered = self._render()
        self.assertIn("docs ⊘", rendered)
        self.assertIn("test ·", rendered)

    def test_corrupt_lines_are_ignored(self):
        (self.spec_dir / pipeline_status.LOG_NAME).write_text("not json\n[1]\n")
        self._log(_entry("plan", "plan critic"))
        self.assertIn("plan ▶ plan critic", self._render())


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / ".claude/settings.json"

    def _settings(self):
        return json.loads(self.path.read_text())

    def test_sets_status_line_keeping_other_settings(self):
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({"hooks": {"Stop": []}}))
        self.assertTrue(pipeline_status.install(self.path).startswith("MERGE"))
        self.assertEqual(self._settings()["statusLine"], pipeline_status.STATUS_LINE)
        self.assertEqual(self._settings()["hooks"], {"Stop": []})
        self.assertTrue(pipeline_status.install(self.path).startswith("UNCHANGED"))

    def test_replaces_an_older_copy_of_ours(self):
        self.path.parent.mkdir()
        old = {
            "type": "command",
            "command": "python3 .claude/agents/agent_common/pipeline_status.py",
        }
        self.path.write_text(json.dumps({"statusLine": old}))
        self.assertTrue(pipeline_status.install(self.path).startswith("MERGE"))
        self.assertEqual(self._settings()["statusLine"], pipeline_status.STATUS_LINE)

    def test_never_clobbers_a_project_status_line(self):
        self.path.parent.mkdir()
        theirs = {"statusLine": {"type": "command", "command": "~/my-line.sh"}}
        self.path.write_text(json.dumps(theirs))
        self.assertTrue(pipeline_status.install(self.path).startswith("SKIP"))
        self.assertEqual(self._settings(), theirs)

    def test_invalid_json_is_skipped(self):
        self.path.parent.mkdir()
        self.path.write_text("{")
        self.assertTrue(pipeline_status.install(self.path).startswith("SKIP"))
        self.assertEqual(self.path.read_text(), "{")


class TestScript(_ProjectCase):
    """The status line command, run the way Claude Code runs it."""

    def test_renders_current_branch_of_installed_project(self):
        script = self.project / ".claude/agents/agent_common/pipeline_status.py"
        script.parent.mkdir(parents=True)
        script.write_text((REPO / ".claude/agents/agent_common/pipeline_status.py").read_text())
        subprocess.run(["git", "-C", str(self.project), "init", "-q", "-b", "012-foo"], check=True)
        self._complete("ch-1-plan")
        result = subprocess.run(
            ["bash", "-c", pipeline_status.STATUS_LINE["command"]],
            input='{"session_id": "x"}',
            capture_output=True,
            text=True,
            cwd=self.project,
            env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(self.project)},
            check=True,
        )
        self.assertEqual(
            result.stdout,
            "012-foo  plan ✓ │ tasks · │ test · │ implement · │ docs ·\n",
        )


if __name__ == "__main__":
    unittest.main()

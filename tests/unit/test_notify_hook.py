"""Unit tests for .claude/hooks/notify.py, the waiting-for-you notification hook."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).parent.parent.parent
SCRIPT = REPO / ".claude/hooks/notify.py"
_spec = importlib.util.spec_from_file_location("notify_hook", SCRIPT)
notify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(notify)


def _run(mode, payload, **env):
    return subprocess.run(
        [sys.executable, str(SCRIPT), mode],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "NTFY_TOPIC": "", **env},
        check=True,
    )


class TestBell(unittest.TestCase):
    def test_emits_bel_as_a_terminal_sequence(self):
        out = _run("bell", {"hook_event_name": "Stop"}, CLAUDE_NOTIFY_BELL="1").stdout
        self.assertEqual(json.loads(out), {"terminalSequence": "\a"})

    def test_can_be_turned_off_per_person(self):
        for value in ("0", "false", "OFF"):
            self.assertEqual(_run("bell", {}, CLAUDE_NOTIFY_BELL=value).stdout, "")


IDLE = {"notification_type": "idle_prompt", "message": "Claude is waiting", "cwd": "/w/proj"}
PERMISSION = {
    "notification_type": "permission_prompt",
    "message": "Claude needs your permission",
    "cwd": "/w/proj",
}
STOP = {"hook_event_name": "Stop", "cwd": "/w/proj"}


class TestPush(unittest.TestCase):
    def test_does_nothing_without_a_topic(self):
        with patch.dict(os.environ, {"NTFY_TOPIC": ""}):
            self.assertIsNone(notify.push_request(STOP))

    def test_builds_ntfy_request_for_permission_prompts(self):
        with patch.dict(os.environ, {"NTFY_TOPIC": "t0p1c", "NTFY_SERVER": ""}):
            request = notify.push_request(PERMISSION)
        self.assertEqual(request.full_url, "https://ntfy.sh/t0p1c")
        self.assertEqual(request.data, b"proj: Claude needs your permission")
        self.assertEqual(request.get_header("Title"), "Claude Code - proj")

    def test_pushes_on_stop_by_default_instead_of_idle(self):
        with patch.dict(os.environ, {"NTFY_TOPIC": "t0p1c"}):
            os.environ.pop("CLAUDE_NOTIFY_PUSH_ON_STOP", None)
            self.assertEqual(
                notify.push_request(STOP).data, b"proj: Claude finished and is waiting for you"
            )
            self.assertIsNone(notify.push_request(IDLE))

    def test_idle_push_stands_in_when_push_on_stop_is_off(self):
        with patch.dict(os.environ, {"NTFY_TOPIC": "t0p1c", "CLAUDE_NOTIFY_PUSH_ON_STOP": "0"}):
            self.assertIsNone(notify.push_request(STOP))
            self.assertEqual(notify.push_request(IDLE).data, b"proj: Claude is waiting")

    def test_names_the_project_after_its_origin_remote(self):
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d) / "workspace"
            workspace.mkdir()
            self.assertEqual(notify.project_name(str(workspace)), "workspace")
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            for url in ("git@github.com:me/jobtracker.git", "https://github.com/me/jobtracker/"):
                with self.subTest(url=url):
                    subprocess.run(
                        ["git", "-C", str(workspace), "remote", "remove", "origin"],
                        capture_output=True,
                    )
                    subprocess.run(
                        ["git", "-C", str(workspace), "remote", "add", "origin", url], check=True
                    )
                    self.assertEqual(notify.project_name(str(workspace)), "jobtracker")

    def test_ignores_other_notification_types(self):
        with patch.dict(os.environ, {"NTFY_TOPIC": "t0p1c"}):
            self.assertIsNone(notify.push_request({"notification_type": "auth_success"}))

    def test_network_failure_never_breaks_the_hook(self):
        with (
            patch.dict(os.environ, {"NTFY_TOPIC": "t0p1c"}),
            patch.object(notify.urllib.request, "urlopen", side_effect=OSError("offline")),
        ):
            notify.push(IDLE)


OLD_UNGUARDED = {
    "type": "command",
    "command": 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/notify.py" bell',
}
PROJECT_HOOK = {"type": "command", "command": "./scripts/lint.sh"}


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Path(self.tmp.name) / ".claude/settings.json"

    def _hooks(self, event):
        return json.loads(self.settings.read_text())["hooks"][event]

    def test_repo_settings_are_generated_from_hooks(self):
        settings = json.loads((REPO / ".claude/settings.json").read_text())
        self.assertEqual(settings["hooks"], notify.HOOKS)

    def test_creates_settings_when_absent(self):
        self.assertTrue(notify.install(self.settings).startswith("MERGE"))
        self.assertEqual(json.loads(self.settings.read_text()), {"hooks": notify.HOOKS})

    def test_keeps_project_settings_and_replaces_older_copies(self):
        self.settings.parent.mkdir()
        self.settings.write_text(
            json.dumps(
                {
                    "permissions": {"allow": ["Bash(ls)"]},
                    "hooks": {
                        "Stop": [{"hooks": [OLD_UNGUARDED, PROJECT_HOOK]}],
                        "PostToolUse": [{"matcher": "Edit", "hooks": [PROJECT_HOOK]}],
                    },
                }
            )
        )
        notify.install(self.settings)
        notify.install(self.settings)

        settings = json.loads(self.settings.read_text())
        self.assertEqual(settings["permissions"], {"allow": ["Bash(ls)"]})
        self.assertEqual(
            settings["hooks"]["PostToolUse"], [{"matcher": "Edit", "hooks": [PROJECT_HOOK]}]
        )
        self.assertEqual(self._hooks("Stop"), [{"hooks": [PROJECT_HOOK]}, *notify.HOOKS["Stop"]])
        self.assertTrue(notify.install(self.settings).startswith("UNCHANGED"))

    def test_leaves_invalid_json_untouched(self):
        self.settings.parent.mkdir()
        self.settings.write_text("{ // comment\n}")
        self.assertTrue(notify.install(self.settings).startswith("SKIP"))
        self.assertEqual(self.settings.read_text(), "{ // comment\n}")

    def test_missing_script_is_a_silent_success(self):
        """A Stop hook exiting 2 (python3's code for a missing file) would keep
        Claude going, so a clone that hasn't run install.sh must exit 0."""
        for handler in notify.HOOKS["Stop"][0]["hooks"]:
            result = subprocess.run(
                ["sh", "-c", handler["command"]],
                env={**os.environ, "CLAUDE_PROJECT_DIR": self.tmp.name},
                capture_output=True,
                text=True,
            )
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_install_sh_ships_the_hook(self):
        target = Path(self.tmp.name)
        result = subprocess.run(
            ["bash", str(REPO / "install.sh"), str(target)],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=True,
        )
        self.assertEqual((target / ".claude/hooks/notify.py").read_text(), SCRIPT.read_text())
        self.assertIn(".claude/hooks/notify.py", (target / ".gitignore").read_text())
        self.assertEqual(self._hooks("Notification"), notify.HOOKS["Notification"])
        self.assertIn("MERGE settings.json: notification hooks", result.stdout)
        self.assertIn("MERGE settings.json: pipeline status line", result.stdout)
        settings = json.loads((target / ".claude/settings.json").read_text())
        self.assertIn("pipeline_status.py", settings["statusLine"]["command"])
        self.assertIn("\nspecs/*/pipeline-status.log\n", (target / ".gitignore").read_text())


if __name__ == "__main__":
    unittest.main()

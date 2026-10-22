"""Unit tests for .claude/hooks/notify.py, the waiting-for-you notification hook."""

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parent.parent.parent / ".claude/hooks/notify.py"
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


class TestPush(unittest.TestCase):
    def test_does_nothing_without_a_topic(self):
        with patch.dict(os.environ, {"NTFY_TOPIC": ""}):
            self.assertIsNone(notify.push_request(IDLE))

    def test_builds_ntfy_request_for_away_notifications(self):
        with patch.dict(os.environ, {"NTFY_TOPIC": "t0p1c", "NTFY_SERVER": ""}):
            request = notify.push_request(IDLE)
        self.assertEqual(request.full_url, "https://ntfy.sh/t0p1c")
        self.assertEqual(request.data, b"Claude is waiting")
        self.assertEqual(request.get_header("Title"), "Claude Code - proj")

    def test_ignores_other_notification_types(self):
        with patch.dict(os.environ, {"NTFY_TOPIC": "t0p1c"}):
            self.assertIsNone(notify.push_request({"notification_type": "auth_success"}))

    def test_network_failure_never_breaks_the_hook(self):
        with (
            patch.dict(os.environ, {"NTFY_TOPIC": "t0p1c"}),
            patch.object(notify.urllib.request, "urlopen", side_effect=OSError("offline")),
        ):
            notify.push(IDLE)


if __name__ == "__main__":
    unittest.main()

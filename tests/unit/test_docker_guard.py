"""Unit tests for agent_common/docker_guard.py, the PreToolUse hook that stops agents
in a dev container (which holds the host's Docker socket) from stopping every
container on the machine."""

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import docker_guard

SCRIPT = Path(docker_guard.__file__)

INCIDENT = (
    "docker ps -q | xargs -r docker stop >/dev/null 2>&1; sleep 2; "
    'pnpm --filter db run test 2>&1 | grep -iE "Tests |failed" | head -40'
)

BLOCKED = [
    INCIDENT,
    "docker stop $(docker ps -q)",
    "docker ps -aq | xargs docker rm -f",
    "docker rm -f `docker ps -aq`",
    "docker container stop $(docker container ls -q)",
    "ids=$(docker ps -q); docker stop $ids",
    'for c in $(docker ps -q); do docker kill "$c"; done',
    "docker system prune -af",
    "docker container prune -f",
    "docker volume prune -f",
    "docker compose down",
    "docker compose down -v",
    "docker-compose stop",
    "docker compose --env-file .env down",
]

ALLOWED = [
    "docker rm -f notif-bug-test-db >/dev/null 2>&1; docker ps --filter name=notif-bug-test-db -q",
    "docker ps --filter name=x -q | xargs docker stop",
    "docker stop $(docker ps -q --filter name=sprog-noggin-db-test)",
    "docker ps -qf name=foo | xargs -r docker rm -f",
    'docker rm -f "$NAME" && docker ps',
    "docker compose -f test.yml down",
    "docker compose -p testdb down -v",
    "docker compose run --rm dev pytest",
    "docker compose up -d db",
    "docker ps",
    'docker ps -a --format "{{.Names}}"',
    "docker logs x",
    "docker image prune -f",
    "pnpm --filter db run test",
]


class TestCheck(unittest.TestCase):
    def test_blocks_host_wide_stop_and_teardown(self):
        for command in BLOCKED:
            with self.subTest(command=command):
                self.assertIsNotNone(docker_guard.check(command))

    def test_allows_scoped_and_read_only_commands(self):
        for command in ALLOWED:
            with self.subTest(command=command):
                self.assertIsNone(docker_guard.check(command))

    def test_reason_tells_the_agent_how_to_scope_it(self):
        self.assertIn("--filter name=", docker_guard.check(INCIDENT))


class TestSdkHook(unittest.TestCase):
    def _run(self, command):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        return asyncio.run(docker_guard.sdk_pre_tool_use_hook(payload, "id", None))

    def test_denies_blocked_command(self):
        output = self._run(INCIDENT)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertEqual(output["hookEventName"], "PreToolUse")

    def test_no_opinion_on_allowed_command(self):
        self.assertEqual(self._run("docker ps"), {})


class TestCliHook(unittest.TestCase):
    def _run(self, payload):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "hook"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_blocked_command_exits_2_with_reason_on_stderr(self):
        result = self._run({"tool_name": "Bash", "tool_input": {"command": INCIDENT}})
        self.assertEqual(result.returncode, 2)
        self.assertIn("unfiltered `docker ps`", result.stderr)

    def test_allowed_command_exits_0(self):
        result = self._run({"tool_name": "Bash", "tool_input": {"command": "docker ps"}})
        self.assertEqual(result.returncode, 0)

    def test_ignores_other_tools_and_bad_input(self):
        self.assertEqual(self._run({"tool_name": "Read", "tool_input": {}}).returncode, 0)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "hook"], input="not json", text=True, check=False
        )
        self.assertEqual(result.returncode, 0)


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / ".claude/settings.json"

    def test_adds_bash_pre_tool_use_hook(self):
        docker_guard.install(self.path)
        [group] = json.loads(self.path.read_text())["hooks"]["PreToolUse"]
        self.assertEqual(group["matcher"], "Bash")
        self.assertIn("agent_common/docker_guard.py", group["hooks"][0]["command"])

    def test_is_idempotent_and_keeps_other_hooks_and_keys(self):
        other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "mine.sh"}]}
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"model": "x", "hooks": {"PreToolUse": [other]}}))
        docker_guard.install(self.path)
        self.assertTrue(docker_guard.install(self.path).startswith("UNCHANGED"))
        settings = json.loads(self.path.read_text())
        self.assertEqual(settings["model"], "x")
        self.assertEqual(len(settings["hooks"]["PreToolUse"]), 2)
        self.assertEqual(settings["hooks"]["PreToolUse"][0], other)

    def test_skips_invalid_json(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{ // comment\n}")
        self.assertTrue(docker_guard.install(self.path).startswith("SKIP"))
        self.assertEqual(self.path.read_text(), "{ // comment\n}")

    def test_hook_command_is_a_no_op_when_script_is_missing(self):
        command = docker_guard.HOOK["command"]
        result = subprocess.run(
            ["bash", "-c", command],
            input="{}",
            text=True,
            env={"CLAUDE_PROJECT_DIR": str(self.path.parent)},
            check=False,
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()

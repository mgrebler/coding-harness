"""Unit tests for agent_common/local_agent_loop.py — the multi-turn
tool-calling loop and its Claude-fallback dispatch. Network calls are
mocked via ollama.call_configured_llm_turn; filesystem tools run for real
against a temp dir chdir'd to as the project root."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import local_agent_loop, local_tools


class TestBuildSandbox(unittest.TestCase):
    def test_defaults_when_fields_absent(self):
        sandbox = local_agent_loop._build_sandbox({})
        default = local_tools.BashSandboxConfig()
        self.assertEqual(sandbox.timeout_s, default.timeout_s)
        self.assertEqual(sandbox.output_max_bytes, default.output_max_bytes)
        self.assertEqual(sandbox.deny_patterns, default.deny_patterns)

    def test_maps_generation_config_fields(self):
        sandbox = local_agent_loop._build_sandbox(
            {
                "command_timeout_s": 30,
                "output_max_bytes": 1000,
                "deny_patterns": [r"\bnpm\s+publish\b"],
            }
        )
        self.assertEqual(sandbox.timeout_s, 30)
        self.assertEqual(sandbox.output_max_bytes, 1000)
        self.assertEqual(sandbox.deny_patterns, [r"\bnpm\s+publish\b"])


class TestRunLocalAgentLoop(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_cwd = Path.cwd()
        self._tmpdir = tempfile.mkdtemp()
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._orig_cwd)

    async def test_write_then_stop_writes_file_and_returns_final_content(self):
        log = MagicMock()
        config = {"model": "qwen3-coder:30b"}
        responses = [
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "Write",
                        "arguments": {"file_path": "plan.md", "content": "# Plan"},
                    }
                ],
            },
            {"content": "plan.md written.", "tool_calls": []},
        ]

        with patch.object(
            local_agent_loop.ollama, "call_configured_llm_turn", side_effect=responses
        ) as mock_call:
            result = await local_agent_loop.run_local_agent_loop(log, "system", "user", config)

        self.assertEqual(result, "plan.md written.")
        self.assertEqual(Path("plan.md").read_text(encoding="utf-8"), "# Plan")
        self.assertEqual(mock_call.call_count, 2)

    async def test_tool_result_fed_back_into_message_history(self):
        log = MagicMock()
        config = {"model": "qwen3-coder:30b"}
        Path("f.md").write_text("hello", encoding="utf-8")
        responses = [
            {
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "name": "Read", "arguments": {"file_path": "f.md"}}
                ],
            },
            {"content": "done", "tool_calls": []},
        ]

        with patch.object(
            local_agent_loop.ollama, "call_configured_llm_turn", side_effect=responses
        ) as mock_call:
            await local_agent_loop.run_local_agent_loop(log, "system", "user", config)

        # second call's messages arg should include the tool result from the first Read
        second_call_messages = mock_call.call_args_list[1].args[0]
        tool_messages = [m for m in second_call_messages if m["role"] == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertIn("hello", tool_messages[0]["content"])
        self.assertEqual(tool_messages[0]["tool_call_id"], "call_1")

    async def test_unknown_tool_call_does_not_crash_loop(self):
        log = MagicMock()
        config = {"model": "qwen3-coder:30b"}
        responses = [
            {
                "content": None,
                "tool_calls": [{"id": "call_1", "name": "Nonexistent", "arguments": {}}],
            },
            {"content": "recovered", "tool_calls": []},
        ]

        with patch.object(
            local_agent_loop.ollama, "call_configured_llm_turn", side_effect=responses
        ):
            result = await local_agent_loop.run_local_agent_loop(log, "system", "user", config)

        self.assertEqual(result, "recovered")

    async def test_max_turns_exhausted_raises_local_agent_error(self):
        log = MagicMock()
        config = {"model": "qwen3-coder:30b", "max_turns": 2}
        always_calls_tool = {
            "content": None,
            "tool_calls": [{"id": "call_1", "name": "Read", "arguments": {"file_path": "x.md"}}],
        }

        with (
            patch.object(
                local_agent_loop.ollama,
                "call_configured_llm_turn",
                return_value=always_calls_tool,
            ) as mock_call,
            self.assertRaises(local_agent_loop.LocalAgentError),
        ):
            await local_agent_loop.run_local_agent_loop(log, "system", "user", config)

        self.assertEqual(mock_call.call_count, 2)

    async def test_default_max_turns_used_when_absent_from_config(self):
        log = MagicMock()
        config = {"model": "qwen3-coder:30b"}

        with patch.object(
            local_agent_loop.ollama,
            "call_configured_llm_turn",
            return_value={"content": "done", "tool_calls": []},
        ) as mock_call:
            result = await local_agent_loop.run_local_agent_loop(log, "system", "user", config)

        self.assertEqual(result, "done")
        mock_call.assert_called_once()

    async def test_final_content_is_logged_when_model_stops_without_tool_calls(self):
        log = MagicMock()
        config = {"model": "qwen3-coder:30b"}

        with patch.object(
            local_agent_loop.ollama,
            "call_configured_llm_turn",
            return_value={
                "content": "I need clarification before I can proceed.",
                "tool_calls": [],
            },
        ):
            result = await local_agent_loop.run_local_agent_loop(log, "system", "user", config)

        self.assertEqual(result, "I need clarification before I can proceed.")
        logged = " ".join(call.args[0] for call in log.call_args_list)
        self.assertIn("I need clarification before I can proceed.", logged)

    async def test_empty_content_logged_distinctly_from_none_content(self):
        log = MagicMock()
        config = {"model": "qwen3-coder:30b"}

        with patch.object(
            local_agent_loop.ollama,
            "call_configured_llm_turn",
            return_value={"content": None, "tool_calls": []},
        ):
            result = await local_agent_loop.run_local_agent_loop(log, "system", "user", config)

        self.assertEqual(result, "")
        logged = " ".join(call.args[0] for call in log.call_args_list)
        self.assertIn("empty content", logged)

    async def test_sandbox_settings_from_config_applied_to_bash_tool(self):
        log = MagicMock()
        config = {"model": "qwen3-coder:30b", "command_timeout_s": 1}
        responses = [
            {
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "name": "Bash", "arguments": {"command": "sleep 5"}}
                ],
            },
            {"content": "done", "tool_calls": []},
        ]

        with patch.object(
            local_agent_loop.ollama, "call_configured_llm_turn", side_effect=responses
        ) as mock_call:
            await local_agent_loop.run_local_agent_loop(log, "system", "user", config)

        second_call_messages = mock_call.call_args_list[1].args[0]
        tool_messages = [m for m in second_call_messages if m["role"] == "tool"]
        self.assertIn("timed out", tool_messages[0]["content"])


class TestRunGeneration(unittest.IsolatedAsyncioTestCase):
    async def test_not_configured_falls_back_to_claude(self):
        log = MagicMock()
        claude_fallback = MagicMock(return_value="claude-query-obj")

        with (
            patch.object(
                local_agent_loop.ollama, "load_local_llm_generation_config", return_value=None
            ),
            patch.object(local_agent_loop.console, "stream_query", new=AsyncMock()) as mock_stream,
        ):
            await local_agent_loop.run_generation(log, "plan", claude_fallback, "system", "user")

        claude_fallback.assert_called_once()
        mock_stream.assert_awaited_once_with("claude-query-obj")

    async def test_configured_runs_local_loop_instead_of_claude(self):
        log = MagicMock()
        claude_fallback = MagicMock()
        config = {"model": "qwen3-coder:30b"}

        with (
            patch.object(
                local_agent_loop.ollama, "load_local_llm_generation_config", return_value=config
            ),
            patch.object(
                local_agent_loop, "run_local_agent_loop", new=AsyncMock(return_value="ok")
            ) as mock_loop,
        ):
            await local_agent_loop.run_generation(
                log, "plan", claude_fallback, "system prompt", "user prompt"
            )

        claude_fallback.assert_not_called()
        mock_loop.assert_awaited_once_with(log, "system prompt", "user prompt", config)


if __name__ == "__main__":
    unittest.main()

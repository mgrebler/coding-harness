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
            patch.object(
                local_agent_loop.ollama, "load_claude_generation_model", return_value="opus"
            ),
            patch.object(local_agent_loop.console, "stream_query", new=AsyncMock()) as mock_stream,
        ):
            await local_agent_loop.run_generation(log, "plan", claude_fallback, "system", "user")

        claude_fallback.assert_called_once_with("opus")
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


class TestRunGenerationEscalationFileGuard(unittest.IsolatedAsyncioTestCase):
    """An agent writing a human-review escalation file (FOLLOWUP_HARNESS.md: an
    in-budget implement fix-agent forged ch-4-implement-critic-escalation-review.md)
    must halt the stage, whatever the agent's prompt said."""

    def setUp(self):
        self._orig_cwd = Path.cwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self.spec_dir = Path("specs/001-feature")
        self.spec_dir.mkdir(parents=True)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        self._tmp.cleanup()

    async def _run_with_agent(self, agent_side_effect):
        async def fake_stream(_query):
            agent_side_effect()

        with (
            patch.object(
                local_agent_loop.ollama, "load_local_llm_generation_config", return_value=None
            ),
            patch.object(
                local_agent_loop.ollama, "load_claude_generation_model", return_value=None
            ),
            patch.object(local_agent_loop.console, "stream_query", new=fake_stream),
        ):
            await local_agent_loop.run_generation(
                MagicMock(), "implement", MagicMock(), "system", "user"
            )

    async def test_halts_when_agent_creates_escalation_review(self):
        review = self.spec_dir / "ch-4-implement-critic-escalation-review.md"

        with self.assertRaises(SystemExit) as cm:
            await self._run_with_agent(lambda: review.write_text("Reviewed by: someone"))

        self.assertEqual(cm.exception.code, 1)
        self.assertTrue(review.exists(), "guard must not delete the file — a human decides")

    async def test_halts_when_agent_edits_existing_human_review(self):
        review = self.spec_dir / "ch-1-plan-critic-escalation-review.md"
        review.write_text("Reviewed by: the human")

        with self.assertRaises(SystemExit):
            await self._run_with_agent(lambda: review.write_text("Reviewed by: the agent"))

    async def test_halts_when_agent_deletes_escalation(self):
        escalation = self.spec_dir / "ch-2-tasks-critic-escalation.md"
        escalation.write_text("# Escalation")

        with self.assertRaises(SystemExit):
            await self._run_with_agent(escalation.unlink)

    async def test_halts_when_agent_edits_governance_memory(self):
        """specs/003: a fix agent bumped constitution.md and logged a fabricated
        human approval in .specify/memory/constitution-decision-log.md."""
        memory = Path(".specify/memory")
        memory.mkdir(parents=True)
        log_file = memory / "constitution-decision-log.md"
        log_file.write_text("| Date | Change | Approved by |")

        with self.assertRaises(SystemExit):
            await self._run_with_agent(
                lambda: log_file.write_text(log_file.read_text() + "\n| x | ORM | the human |")
            )

    async def test_untouched_escalation_files_and_other_writes_pass(self):
        (self.spec_dir / "ch-1-plan-critic-escalation-review.md").write_text("Reviewed by: human")

        await self._run_with_agent(lambda: (self.spec_dir / "plan.md").write_text("plan"))


if __name__ == "__main__":
    unittest.main()

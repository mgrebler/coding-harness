"""Unit tests for agent_common/console.py — primarily stream_query()'s translation of
the claude_agent_sdk's generic session/usage-limit exception into SessionLimitError."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

from agent_common import console


def _result_message(**overrides) -> ResultMessage:
    defaults = {
        "subtype": "success",
        "duration_ms": 100,
        "duration_api_ms": 100,
        "is_error": False,
        "num_turns": 1,
        "session_id": "session-1",
        "result": None,
        "errors": None,
    }
    defaults.update(overrides)
    return ResultMessage(**defaults)


async def _fake_query(messages, exc=None):
    for message in messages:
        yield message
    if exc is not None:
        raise exc


class TestStreamQuery(unittest.IsolatedAsyncioTestCase):
    async def test_normal_stream_completes_without_error(self):
        messages = [
            AssistantMessage(content=[TextBlock("hello")], model="claude"),
            _result_message(result="done", is_error=False),
        ]
        await console.stream_query(_fake_query(messages))  # no exception raised

    async def test_session_limit_result_raises_session_limit_error(self):
        limit_text = "You've hit your session limit · resets 3:20am (UTC)"
        messages = [_result_message(subtype="success", is_error=True, errors=[], result=limit_text)]
        exc = Exception("Claude Code returned an error result: success")

        with self.assertRaises(console.SessionLimitError) as cm:
            await console.stream_query(_fake_query(messages, exc=exc))

        self.assertEqual(str(cm.exception), limit_text)

    async def test_unrelated_exception_with_no_result_message_propagates(self):
        with self.assertRaises(ValueError):
            await console.stream_query(_fake_query([], exc=ValueError("boom")))

    async def test_error_result_with_populated_errors_does_not_match(self):
        messages = [
            _result_message(
                subtype="error_during_execution",
                is_error=True,
                errors=["a specific, already-descriptive failure"],
                result="Something broke",
            )
        ]
        exc = Exception("Claude Code returned an error result: error_during_execution")

        with self.assertRaises(Exception) as cm:
            await console.stream_query(_fake_query(messages, exc=exc))

        self.assertIs(cm.exception, exc)


if __name__ == "__main__":
    unittest.main()

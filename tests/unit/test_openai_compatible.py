"""Unit tests for agent_common/openai_compatible.py — network-mocked, no real calls."""

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import openai_compatible


class TestBuildRequest(unittest.TestCase):
    def test_missing_api_key_env_raises(self):
        config = {
            "model": "moonshotai/kimi-k2.6",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key_env": "NVIDIA_API_KEY",
        }
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(RuntimeError):
            openai_compatible._build_request("hello", config)

    def test_auth_header_built_from_named_env_var(self):
        config = {
            "model": "moonshotai/kimi-k2.6",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key_env": "NVIDIA_API_KEY",
        }
        with patch.dict("os.environ", {"NVIDIA_API_KEY": "nvapi-secret"}, clear=True):
            req = openai_compatible._build_request("hello", config)

        self.assertEqual(req.get_header("Authorization"), "Bearer nvapi-secret")
        self.assertEqual(req.full_url, "https://integrate.api.nvidia.com/v1/chat/completions")

    def test_response_format_forced_regardless_of_config(self):
        config = {
            "model": "moonshotai/kimi-k2.6",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key_env": "NVIDIA_API_KEY",
        }
        with patch.dict("os.environ", {"NVIDIA_API_KEY": "nvapi-secret"}, clear=True):
            req = openai_compatible._build_request("hello", config)

        body = json.loads(req.data)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertTrue(body["stream"])

    def test_temperature_and_max_tokens_passthrough(self):
        config = {
            "model": "moonshotai/kimi-k2.6",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key_env": "NVIDIA_API_KEY",
            "temperature": 0.0,
            "num_predict": 4096,
        }
        with patch.dict("os.environ", {"NVIDIA_API_KEY": "nvapi-secret"}, clear=True):
            req = openai_compatible._build_request("hello", config)

        body = json.loads(req.data)
        self.assertEqual(body["temperature"], 0.0)
        self.assertEqual(body["max_tokens"], 4096)

    def test_max_tokens_absent_when_num_predict_not_set(self):
        config = {
            "model": "moonshotai/kimi-k2.6",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key_env": "NVIDIA_API_KEY",
        }
        with patch.dict("os.environ", {"NVIDIA_API_KEY": "nvapi-secret"}, clear=True):
            req = openai_compatible._build_request("hello", config)

        body = json.loads(req.data)
        self.assertNotIn("max_tokens", body)


def _sse_response(lines: list) -> MagicMock:
    resp = MagicMock()
    resp.__enter__.return_value = iter([line.encode("utf-8") for line in lines])
    return resp


class TestStreamChatResponse(unittest.TestCase):
    def test_accumulates_content_across_chunks(self):
        lines = [
            'data: {"choices": [{"delta": {"content": "Hello"}}]}',
            'data: {"choices": [{"delta": {"content": ", world"}}]}',
            "data: [DONE]",
        ]
        with patch.object(
            openai_compatible.urllib.request, "urlopen", return_value=_sse_response(lines)
        ):
            result = openai_compatible._stream_chat_response(MagicMock())

        self.assertEqual(result, "Hello, world")

    def test_leading_empty_choices_chunk_skipped(self):
        lines = [
            'data: {"choices": []}',
            'data: {"choices": [{"delta": {"content": "Hi"}}]}',
            "data: [DONE]",
        ]
        with patch.object(
            openai_compatible.urllib.request, "urlopen", return_value=_sse_response(lines)
        ):
            result = openai_compatible._stream_chat_response(MagicMock())

        self.assertEqual(result, "Hi")

    def test_non_data_lines_ignored(self):
        lines = [
            ": keep-alive comment",
            "",
            'data: {"choices": [{"delta": {"content": "Hi"}}]}',
            "data: [DONE]",
        ]
        with patch.object(
            openai_compatible.urllib.request, "urlopen", return_value=_sse_response(lines)
        ):
            result = openai_compatible._stream_chat_response(MagicMock())

        self.assertEqual(result, "Hi")

    def test_malformed_json_line_skipped(self):
        lines = [
            "data: {not valid json",
            'data: {"choices": [{"delta": {"content": "Hi"}}]}',
            "data: [DONE]",
        ]
        with patch.object(
            openai_compatible.urllib.request, "urlopen", return_value=_sse_response(lines)
        ):
            result = openai_compatible._stream_chat_response(MagicMock())

        self.assertEqual(result, "Hi")

    def test_stops_at_done_marker(self):
        lines = [
            'data: {"choices": [{"delta": {"content": "Hi"}}]}',
            "data: [DONE]",
            'data: {"choices": [{"delta": {"content": "should not appear"}}]}',
        ]
        with patch.object(
            openai_compatible.urllib.request, "urlopen", return_value=_sse_response(lines)
        ):
            result = openai_compatible._stream_chat_response(MagicMock())

        self.assertEqual(result, "Hi")

    def test_progress_fn_called_every_interval_and_once_done(self):
        lines = ['data: {"choices": [{"delta": {"content": "x"}}]}' for _ in range(5)]
        lines.append("data: [DONE]")

        calls = []

        def progress_fn(token_count, elapsed_s, done=False):
            calls.append((token_count, done))

        with patch.object(
            openai_compatible.urllib.request, "urlopen", return_value=_sse_response(lines)
        ):
            openai_compatible._stream_chat_response(
                MagicMock(), progress_fn=progress_fn, progress_interval=2
            )

        heartbeat_calls = [c for c in calls if not c[1]]
        done_calls = [c for c in calls if c[1]]
        self.assertEqual(heartbeat_calls, [(2, False), (4, False)])
        self.assertEqual(done_calls, [(5, True)])


class TestStreamChatResponseHttpError(unittest.TestCase):
    def test_http_error_reraised_with_status_and_body(self):
        """See FOLLOWUP_HARNESS.md Bug 2: an uncaught HTTPError previously
        propagated as a bare 'HTTP Error 400: Bad Request', with the
        response body (usually naming the real cause, e.g. a token-limit
        error) never read anywhere in the call chain."""
        body = b'{"error": "context_length_exceeded: prompt too long"}'
        http_error = urllib.error.HTTPError(
            url="https://integrate.api.nvidia.com/v1/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(body),
        )
        with (
            patch.object(openai_compatible.urllib.request, "urlopen", side_effect=http_error),
            self.assertRaises(RuntimeError) as ctx,
        ):
            openai_compatible._stream_chat_response(MagicMock())

        self.assertIn("400", str(ctx.exception))
        self.assertIn("context_length_exceeded", str(ctx.exception))


class TestCallOpenAICompatibleLlm(unittest.TestCase):
    def test_builds_request_and_streams_response(self):
        config = {
            "model": "moonshotai/kimi-k2.6",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key_env": "NVIDIA_API_KEY",
        }
        lines = ['data: {"choices": [{"delta": {"content": "Hi"}}]}', "data: [DONE]"]

        with (
            patch.dict("os.environ", {"NVIDIA_API_KEY": "nvapi-secret"}, clear=True),
            patch.object(
                openai_compatible.urllib.request, "urlopen", return_value=_sse_response(lines)
            ),
        ):
            result = openai_compatible.call_openai_compatible_llm("hello", config)

        self.assertEqual(result, "Hi")


if __name__ == "__main__":
    unittest.main()

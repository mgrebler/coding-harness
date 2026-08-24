"""Unit tests for agent_common/openai_compatible.py — network-mocked, no real calls."""

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import openai_compatible


class TestBuildRequest(unittest.TestCase):
    def setUp(self):
        # Isolate from whatever .env file (if any) happens to sit in the cwd
        # this test run — os.environ is the only source of truth here.
        patcher = patch.object(openai_compatible, "_load_dotenv")
        patcher.start()
        self.addCleanup(patcher.stop)

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


class TestLoadDotenv(unittest.TestCase):
    def test_missing_file_is_noop(self):
        with patch.dict("os.environ", {}, clear=True):
            openai_compatible._load_dotenv("/nonexistent/path/.env")
            self.assertEqual(dict(os.environ), {})

    def test_loads_values_into_environ(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("NVIDIA_API_KEY=nvapi-from-file\n")
            with patch.dict("os.environ", {}, clear=True):
                openai_compatible._load_dotenv(str(env_path))
                self.assertEqual(os.environ["NVIDIA_API_KEY"], "nvapi-from-file")

    def test_does_not_override_existing_env_var(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("NVIDIA_API_KEY=nvapi-from-file\n")
            with patch.dict("os.environ", {"NVIDIA_API_KEY": "nvapi-from-shell"}, clear=True):
                openai_compatible._load_dotenv(str(env_path))
                self.assertEqual(os.environ["NVIDIA_API_KEY"], "nvapi-from-shell")

    def test_overrides_existing_but_empty_env_var(self):
        """A devcontainer/docker-compose `environment:` block can declare a
        var as a host pass-through that resolves to an empty string when
        unset on the host — that shouldn't block a real value in .env."""
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("NVIDIA_API_KEY=nvapi-from-file\n")
            with patch.dict("os.environ", {"NVIDIA_API_KEY": ""}, clear=True):
                openai_compatible._load_dotenv(str(env_path))
                self.assertEqual(os.environ["NVIDIA_API_KEY"], "nvapi-from-file")

    def test_skips_comments_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("# comment\n\nNVIDIA_API_KEY=nvapi-secret\n")
            with patch.dict("os.environ", {}, clear=True):
                openai_compatible._load_dotenv(str(env_path))
                self.assertEqual(os.environ["NVIDIA_API_KEY"], "nvapi-secret")

    def test_strips_surrounding_quotes_from_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text('NVIDIA_API_KEY="nvapi-secret"\n')
            with patch.dict("os.environ", {}, clear=True):
                openai_compatible._load_dotenv(str(env_path))
                self.assertEqual(os.environ["NVIDIA_API_KEY"], "nvapi-secret")


class TestUrlopenWithRetry(unittest.TestCase):
    def test_non_retryable_status_raises_immediately_without_sleep(self):
        error = urllib.error.HTTPError("url", 404, "Not Found", {}, io.BytesIO(b"nope"))
        with (
            patch.object(openai_compatible.urllib.request, "urlopen", side_effect=error),
            patch.object(openai_compatible.time, "sleep") as mock_sleep,
            self.assertRaises(urllib.error.HTTPError),
        ):
            openai_compatible._urlopen_with_retry(MagicMock())
        mock_sleep.assert_not_called()

    def test_succeeds_after_transient_retryable_errors(self):
        error = urllib.error.HTTPError("url", 503, "Service Unavailable", {}, io.BytesIO(b""))
        fake_resp = MagicMock()
        with (
            patch.object(
                openai_compatible.urllib.request,
                "urlopen",
                side_effect=[error, error, fake_resp],
            ),
            patch.object(openai_compatible.time, "sleep") as mock_sleep,
        ):
            result = openai_compatible._urlopen_with_retry(MagicMock())
        self.assertIs(result, fake_resp)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_raises_after_exhausting_all_retries(self):
        error = urllib.error.HTTPError("url", 500, "Internal Server Error", {}, io.BytesIO(b""))
        with (
            patch.object(openai_compatible.urllib.request, "urlopen", side_effect=error),
            patch.object(openai_compatible.time, "sleep") as mock_sleep,
            self.assertRaises(urllib.error.HTTPError),
        ):
            openai_compatible._urlopen_with_retry(MagicMock())
        self.assertEqual(mock_sleep.call_count, openai_compatible._MAX_RETRIES)

    def test_retry_after_header_honored_over_exponential_backoff(self):
        error = urllib.error.HTTPError(
            "url", 429, "Too Many Requests", {"Retry-After": "7"}, io.BytesIO(b"")
        )
        fake_resp = MagicMock()
        with (
            patch.object(
                openai_compatible.urllib.request, "urlopen", side_effect=[error, fake_resp]
            ),
            patch.object(openai_compatible.time, "sleep") as mock_sleep,
        ):
            openai_compatible._urlopen_with_retry(MagicMock())
        mock_sleep.assert_called_once_with(7.0)

    def test_succeeds_after_transient_read_timeout(self):
        fake_resp = MagicMock()
        with (
            patch.object(
                openai_compatible.urllib.request,
                "urlopen",
                side_effect=[TimeoutError("The read operation timed out"), fake_resp],
            ),
            patch.object(openai_compatible.time, "sleep") as mock_sleep,
        ):
            result = openai_compatible._urlopen_with_retry(MagicMock())
        self.assertIs(result, fake_resp)
        mock_sleep.assert_called_once()

    def test_raises_after_exhausting_all_retries_on_read_timeout(self):
        with (
            patch.object(
                openai_compatible.urllib.request,
                "urlopen",
                side_effect=TimeoutError("The read operation timed out"),
            ),
            patch.object(openai_compatible.time, "sleep") as mock_sleep,
            self.assertRaises(TimeoutError),
        ):
            openai_compatible._urlopen_with_retry(MagicMock())
        self.assertEqual(mock_sleep.call_count, openai_compatible._MAX_RETRIES)

    def test_succeeds_after_transient_url_error(self):
        fake_resp = MagicMock()
        with (
            patch.object(
                openai_compatible.urllib.request,
                "urlopen",
                side_effect=[urllib.error.URLError("connection refused"), fake_resp],
            ),
            patch.object(openai_compatible.time, "sleep") as mock_sleep,
        ):
            result = openai_compatible._urlopen_with_retry(MagicMock())
        self.assertIs(result, fake_resp)
        mock_sleep.assert_called_once()


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
    def setUp(self):
        patcher = patch.object(openai_compatible, "_load_dotenv")
        patcher.start()
        self.addCleanup(patcher.stop)

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


class TestToOpenaiWireMessages(unittest.TestCase):
    def test_system_and_user_pass_through(self):
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        wire = openai_compatible._to_openai_wire_messages(messages)
        self.assertEqual(
            wire, [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        )

    def test_assistant_tool_calls_include_id_and_json_string_arguments(self):
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "name": "Read", "arguments": {"file_path": "a.md"}}
                ],
            }
        ]
        wire = openai_compatible._to_openai_wire_messages(messages)
        self.assertEqual(
            wire,
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "Read",
                                "arguments": '{"file_path": "a.md"}',
                            },
                        }
                    ],
                }
            ],
        )

    def test_tool_result_carries_tool_call_id(self):
        messages = [{"role": "tool", "tool_call_id": "call_1", "name": "Read", "content": "result"}]
        wire = openai_compatible._to_openai_wire_messages(messages)
        self.assertEqual(wire, [{"role": "tool", "tool_call_id": "call_1", "content": "result"}])


class TestParseOpenaiChatMessage(unittest.TestCase):
    def test_content_only_no_tool_calls(self):
        result = openai_compatible._parse_openai_chat_message({"content": "done"})
        self.assertEqual(result, {"content": "done", "tool_calls": []})

    def test_valid_tool_call_parsed(self):
        message = {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "Write", "arguments": '{"file_path": "a.md"}'},
                }
            ],
        }
        result = openai_compatible._parse_openai_chat_message(message)
        self.assertEqual(
            result["tool_calls"],
            [{"id": "call_1", "name": "Write", "arguments": {"file_path": "a.md"}}],
        )

    def test_malformed_json_arguments_resolves_to_empty_dict_not_raise(self):
        message = {
            "content": None,
            "tool_calls": [
                {"id": "call_1", "function": {"name": "Write", "arguments": "{not json"}}
            ],
        }
        result = openai_compatible._parse_openai_chat_message(message)
        self.assertEqual(result["tool_calls"], [{"id": "call_1", "name": "Write", "arguments": {}}])

    def test_non_object_json_arguments_resolves_to_empty_dict(self):
        message = {
            "content": None,
            "tool_calls": [{"id": "call_1", "function": {"name": "Write", "arguments": "[1, 2]"}}],
        }
        result = openai_compatible._parse_openai_chat_message(message)
        self.assertEqual(result["tool_calls"], [{"id": "call_1", "name": "Write", "arguments": {}}])


class TestBuildTurnRequest(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(openai_compatible, "_load_dotenv")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_never_forces_response_format(self):
        config = {
            "model": "moonshotai/kimi-k2.6",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key_env": "NVIDIA_API_KEY",
        }
        with patch.dict("os.environ", {"NVIDIA_API_KEY": "nvapi-secret"}, clear=True):
            req = openai_compatible._build_turn_request(
                [{"role": "user", "content": "hi"}], config, []
            )

        body = json.loads(req.data)
        self.assertNotIn("response_format", body)
        self.assertFalse(body["stream"])

    def test_tools_included_in_body(self):
        config = {
            "model": "moonshotai/kimi-k2.6",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key_env": "NVIDIA_API_KEY",
        }
        tools = [{"type": "function", "function": {"name": "Read"}}]
        with patch.dict("os.environ", {"NVIDIA_API_KEY": "nvapi-secret"}, clear=True):
            req = openai_compatible._build_turn_request(
                [{"role": "user", "content": "hi"}], config, tools
            )

        body = json.loads(req.data)
        self.assertEqual(body["tools"], tools)


class TestCallOpenaiCompatibleTurn(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(openai_compatible, "_load_dotenv")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_returns_canonical_turn_shape(self):
        config = {
            "model": "moonshotai/kimi-k2.6",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key_env": "NVIDIA_API_KEY",
        }
        response_body = {"choices": [{"message": {"content": "hello", "tool_calls": []}}]}
        fake_resp = MagicMock()
        fake_resp.__enter__.return_value.read.return_value = json.dumps(response_body).encode(
            "utf-8"
        )

        with (
            patch.dict("os.environ", {"NVIDIA_API_KEY": "nvapi-secret"}, clear=True),
            patch.object(openai_compatible.urllib.request, "urlopen", return_value=fake_resp),
        ):
            result = openai_compatible.call_openai_compatible_turn(
                [{"role": "user", "content": "hi"}], config, []
            )

        self.assertEqual(result["content"], "hello")
        self.assertEqual(result["tool_calls"], [])

    def test_http_error_raises_runtime_error(self):
        config = {
            "model": "moonshotai/kimi-k2.6",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key_env": "NVIDIA_API_KEY",
        }
        error = urllib.error.HTTPError("url", 500, "Internal Server Error", {}, io.BytesIO(b"boom"))

        with (
            patch.dict("os.environ", {"NVIDIA_API_KEY": "nvapi-secret"}, clear=True),
            patch.object(openai_compatible.urllib.request, "urlopen", side_effect=error),
            patch.object(openai_compatible.time, "sleep"),
            self.assertRaises(RuntimeError) as ctx,
        ):
            openai_compatible.call_openai_compatible_turn(
                [{"role": "user", "content": "hi"}], config, []
            )

        self.assertIn("500", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

"""Unit tests for agent_common/ollama.py pure functions and network-mocked calls."""

import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import ollama


class TestStripFences(unittest.TestCase):
    def test_no_fence_passthrough(self):
        self.assertEqual(ollama.strip_fences('{"a": 1}'), '{"a": 1}')

    def test_json_fence_stripped(self):
        result = ollama.strip_fences('```json\n{"a": 1}\n```')
        self.assertEqual(result, '{"a": 1}')

    def test_plain_fence_stripped(self):
        result = ollama.strip_fences('```\n{"a": 1}\n```')
        self.assertEqual(result, '{"a": 1}')

    def test_whitespace_trimmed(self):
        result = ollama.strip_fences('  {"a": 1}  ')
        self.assertEqual(result, '{"a": 1}')


class TestLoadLocalLlmConfig(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = Path.cwd()
        self._tmpdir = tempfile.mkdtemp()
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._orig_cwd)

    def _write_config(self, data):
        path = Path(self._tmpdir) / ".specify" / "local-llm.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def test_no_file_returns_none(self):
        self.assertIsNone(ollama.load_local_llm_config("plan"))

    def test_default_disabled_returns_none(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": False, "model": ""},
                "critics": {},
            }
        )
        self.assertIsNone(ollama.load_local_llm_config("plan"))

    def test_critic_override_enabled_returns_config(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": False, "model": ""},
                "critics": {"plan": {"enabled": True, "model": "qwen3:4b"}},
            }
        )
        result = ollama.load_local_llm_config("plan")
        self.assertIsNotNone(result)
        self.assertEqual(result["model"], "qwen3:4b")
        self.assertIn("ollama_url", result)

    def test_num_ctx_top_level_passed_through(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "num_ctx": 8192,
                "default": {"enabled": True, "model": "llama3.2"},
            }
        )
        result = ollama.load_local_llm_config("plan")
        self.assertEqual(result["num_ctx"], 8192)

    def test_num_ctx_absent_when_not_set(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": True, "model": "llama3.2"},
            }
        )
        result = ollama.load_local_llm_config("plan")
        self.assertNotIn("num_ctx", result)

    def test_max_ctx_top_level_passed_through(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "max_ctx": 24576,
                "default": {"enabled": True, "model": "llama3.2"},
            }
        )
        result = ollama.load_local_llm_config("plan")
        self.assertEqual(result["max_ctx"], 24576)

    def test_max_ctx_absent_when_not_set(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": True, "model": "llama3.2"},
            }
        )
        result = ollama.load_local_llm_config("plan")
        self.assertNotIn("max_ctx", result)

    def test_max_ctx_critic_override(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "max_ctx": 16384,
                "default": {"enabled": True, "model": "llama3.2"},
                "critics": {"plan": {"enabled": True, "model": "llama3.2", "max_ctx": 32768}},
            }
        )
        result = ollama.load_local_llm_config("plan")
        self.assertEqual(result["max_ctx"], 32768)

    def test_num_gpu_defaults_to_full_offload_when_unset(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": True, "model": "llama3.2"},
            }
        )
        result = ollama.load_local_llm_config("plan")
        self.assertEqual(result["num_gpu"], ollama._FULL_GPU_OFFLOAD)

    def test_num_gpu_explicit_value_respected(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "num_gpu": 20,
                "default": {"enabled": True, "model": "llama3.2"},
            }
        )
        result = ollama.load_local_llm_config("plan")
        self.assertEqual(result["num_gpu"], 20)

    def test_default_enabled_returns_config(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": True, "model": "llama3.2"},
                "critics": {},
            }
        )
        result = ollama.load_local_llm_config("plan")
        self.assertIsNotNone(result)
        self.assertEqual(result["model"], "llama3.2")

    def test_empty_model_returns_none(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": True, "model": ""},
                "critics": {},
            }
        )
        self.assertIsNone(ollama.load_local_llm_config("plan"))

    def test_corrupt_json_returns_none(self):
        path = Path(self._tmpdir) / ".specify" / "local-llm.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{invalid json")
        self.assertIsNone(ollama.load_local_llm_config("plan"))

    def test_provider_defaults_to_ollama_when_absent(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": True, "model": "llama3.2"},
            }
        )
        result = ollama.load_local_llm_config("plan")
        self.assertEqual(result["provider"], "ollama")
        self.assertIn("ollama_url", result)

    def test_openai_compatible_provider_returns_base_url_and_api_key_env(self):
        self._write_config(
            {
                "provider": "openai-compatible",
                "base_url": "https://integrate.api.nvidia.com/v1",
                "api_key_env": "NVIDIA_API_KEY",
                "default": {"enabled": True, "model": "moonshotai/kimi-k2.6"},
            }
        )
        result = ollama.load_local_llm_config("plan")
        self.assertEqual(result["provider"], "openai-compatible")
        self.assertEqual(result["base_url"], "https://integrate.api.nvidia.com/v1")
        self.assertEqual(result["api_key_env"], "NVIDIA_API_KEY")
        self.assertNotIn("ollama_url", result)

    def test_openai_compatible_missing_base_url_raises(self):
        self._write_config(
            {
                "provider": "openai-compatible",
                "api_key_env": "NVIDIA_API_KEY",
                "default": {"enabled": True, "model": "moonshotai/kimi-k2.6"},
            }
        )
        with self.assertRaises(ValueError):
            ollama.load_local_llm_config("plan")

    def test_openai_compatible_missing_api_key_env_raises(self):
        self._write_config(
            {
                "provider": "openai-compatible",
                "base_url": "https://integrate.api.nvidia.com/v1",
                "default": {"enabled": True, "model": "moonshotai/kimi-k2.6"},
            }
        )
        with self.assertRaises(ValueError):
            ollama.load_local_llm_config("plan")

    def test_openai_compatible_invalid_base_url_scheme_raises(self):
        self._write_config(
            {
                "provider": "openai-compatible",
                "base_url": "ftp://integrate.api.nvidia.com/v1",
                "api_key_env": "NVIDIA_API_KEY",
                "default": {"enabled": True, "model": "moonshotai/kimi-k2.6"},
            }
        )
        with self.assertRaises(ValueError):
            ollama.load_local_llm_config("plan")

    def test_unknown_provider_raises(self):
        self._write_config(
            {
                "provider": "bogus-provider",
                "default": {"enabled": True, "model": "llama3.2"},
            }
        )
        with self.assertRaises(ValueError):
            ollama.load_local_llm_config("plan")

    def test_provider_critic_override(self):
        self._write_config(
            {
                "provider": "ollama",
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": True, "model": "llama3.2"},
                "critics": {
                    "plan": {
                        "enabled": True,
                        "model": "moonshotai/kimi-k2.6",
                        "provider": "openai-compatible",
                        "base_url": "https://integrate.api.nvidia.com/v1",
                        "api_key_env": "NVIDIA_API_KEY",
                    }
                },
            }
        )
        plan_result = ollama.load_local_llm_config("plan")
        self.assertEqual(plan_result["provider"], "openai-compatible")
        self.assertEqual(plan_result["base_url"], "https://integrate.api.nvidia.com/v1")

        tasks_result = ollama.load_local_llm_config("tasks")
        self.assertEqual(tasks_result["provider"], "ollama")
        self.assertIn("ollama_url", tasks_result)


class TestEstimatePromptTokens(unittest.TestCase):
    def setUp(self):
        ollama._tokenize_unavailable.clear()

    def tearDown(self):
        ollama._tokenize_unavailable.clear()

    def test_tokenize_endpoint_success_returns_exact_count(self):
        def fake_urlopen(req, timeout=None):
            cm = MagicMock()
            cm.__enter__.return_value.read.return_value = json.dumps(
                {"tokens": list(range(37))}
            ).encode("utf-8")
            return cm

        with patch.object(ollama.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = ollama._estimate_prompt_tokens(
                "http://localhost:11434", "deepseek-r1:8b", "hi"
            )

        self.assertEqual(result, 37)

    def test_tokenize_endpoint_404_falls_back_to_heuristic(self):
        def fake_urlopen(req, timeout=None):
            raise ollama.urllib.error.HTTPError(req.full_url, 404, "not found", {}, None)

        prompt = "x" * 100
        with patch.object(ollama.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = ollama._estimate_prompt_tokens(
                "http://localhost:11434", "deepseek-r1:8b", prompt
            )

        self.assertEqual(result, math.ceil(len(prompt) / 3.3))

    def test_tokenize_endpoint_404_cached_across_calls(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            raise ollama.urllib.error.HTTPError(req.full_url, 404, "not found", {}, None)

        with patch.object(ollama.urllib.request, "urlopen", side_effect=fake_urlopen):
            ollama._estimate_prompt_tokens("http://localhost:11434", "deepseek-r1:8b", "hi")
            ollama._estimate_prompt_tokens("http://localhost:11434", "deepseek-r1:8b", "hi")

        self.assertEqual(len(calls), 1)

    def test_non_404_error_not_cached_and_retried(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            raise TimeoutError("simulated timeout")

        with patch.object(ollama.urllib.request, "urlopen", side_effect=fake_urlopen):
            ollama._estimate_prompt_tokens("http://localhost:11434", "deepseek-r1:8b", "hi")
            ollama._estimate_prompt_tokens("http://localhost:11434", "deepseek-r1:8b", "hi")

        self.assertEqual(len(calls), 2)

    def test_malformed_response_falls_back_to_heuristic(self):
        def fake_urlopen(req, timeout=None):
            cm = MagicMock()
            cm.__enter__.return_value.read.return_value = json.dumps({}).encode("utf-8")
            return cm

        prompt = "y" * 50
        with patch.object(ollama.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = ollama._estimate_prompt_tokens(
                "http://localhost:11434", "deepseek-r1:8b", prompt
            )

        self.assertEqual(result, math.ceil(len(prompt) / 3.3))


class TestEstimateNumCtx(unittest.TestCase):
    def test_margin_and_rounding_applied(self):
        with patch.object(ollama, "_estimate_prompt_tokens", return_value=1000):
            result = ollama.estimate_num_ctx(
                "http://localhost:11434",
                "deepseek-r1:8b",
                "prompt",
                num_predict=500,
                round_to=256,
                margin=1.15,
                min_ctx=0,
            )
        # (1000 + 500) * 1.15 = 1725 -> rounds up to nearest 256 -> 1792
        self.assertEqual(result, 1792)

    def test_default_predict_reserve_used_when_num_predict_none(self):
        with patch.object(ollama, "_estimate_prompt_tokens", return_value=0):
            result = ollama.estimate_num_ctx(
                "http://localhost:11434",
                "deepseek-r1:8b",
                "prompt",
                num_predict=None,
                round_to=256,
                margin=1.0,
                min_ctx=0,
            )
        self.assertEqual(result, ollama._DEFAULT_PREDICT_RESERVE)

    def test_clamped_to_min_ctx(self):
        with patch.object(ollama, "_estimate_prompt_tokens", return_value=1):
            result = ollama.estimate_num_ctx(
                "http://localhost:11434",
                "deepseek-r1:8b",
                "prompt",
                num_predict=0,
                min_ctx=2048,
            )
        self.assertEqual(result, 2048)

    def test_clamped_to_max_ctx_with_warning(self):
        with (
            patch.object(ollama, "_estimate_prompt_tokens", return_value=100_000),
            patch("builtins.print") as mock_print,
        ):
            result = ollama.estimate_num_ctx(
                "http://localhost:11434",
                "deepseek-r1:8b",
                "prompt",
                num_predict=0,
                max_ctx=8192,
            )
        self.assertEqual(result, 8192)
        self.assertTrue(mock_print.called)
        warning = mock_print.call_args[0][0]
        self.assertIn("max_ctx=8192", warning)


class TestEnsureModelContextFallback(unittest.TestCase):
    def test_falls_back_on_preload_failure(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            calls.append(url)
            if url.endswith("/api/ps"):
                cm = MagicMock()
                cm.__enter__.return_value.read.return_value = b'{"models": []}'
                return cm
            if len(calls) == 2:
                raise OSError("simulated OOM")
            return MagicMock()

        with patch.object(ollama.urllib.request, "urlopen", side_effect=fake_urlopen):
            ollama._ensure_model_context(
                "http://localhost:11434", "deepseek-r1:8b", 16384, num_gpu=999
            )

        self.assertEqual(len(calls), 3)
        self.assertTrue(calls[0].endswith("/api/ps"))
        self.assertTrue(calls[1].endswith("/api/generate"))
        self.assertTrue(calls[2].endswith("/api/generate"))

    def test_applies_num_gpu_on_fresh_load_without_num_ctx(self):
        calls = []
        bodies = []

        def fake_urlopen(req, timeout=None):
            if isinstance(req, str):
                calls.append(req)
                cm = MagicMock()
                cm.__enter__.return_value.read.return_value = b'{"models": []}'
                return cm
            calls.append(req.full_url)
            bodies.append(json.loads(req.data))
            return MagicMock()

        with patch.object(ollama.urllib.request, "urlopen", side_effect=fake_urlopen):
            ollama._ensure_model_context(
                "http://localhost:11434", "deepseek-r1:8b", num_ctx=None, num_gpu=999
            )

        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0].endswith("/api/ps"))
        self.assertTrue(calls[1].endswith("/api/generate"))
        self.assertEqual(bodies[0]["options"], {"num_gpu": 999})

    def test_leaves_already_loaded_model_alone_when_num_ctx_not_specified(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            calls.append(url)
            cm = MagicMock()
            cm.__enter__.return_value.read.return_value = json.dumps(
                {"models": [{"name": "deepseek-r1:8b", "context_length": 16384}]}
            ).encode("utf-8")
            return cm

        with patch.object(ollama.urllib.request, "urlopen", side_effect=fake_urlopen):
            ollama._ensure_model_context(
                "http://localhost:11434", "deepseek-r1:8b", num_ctx=None, num_gpu=999
            )

        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith("/api/ps"))

    def test_reload_skipped_when_loaded_ctx_larger_than_requested(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            calls.append(url)
            cm = MagicMock()
            cm.__enter__.return_value.read.return_value = json.dumps(
                {"models": [{"name": "deepseek-r1:8b", "context_length": 16384}]}
            ).encode("utf-8")
            return cm

        with patch.object(ollama.urllib.request, "urlopen", side_effect=fake_urlopen):
            ollama._ensure_model_context("http://localhost:11434", "deepseek-r1:8b", num_ctx=8000)

        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith("/api/ps"))

    def test_reload_happens_when_loaded_ctx_smaller_than_requested(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            calls.append(url)
            if url.endswith("/api/ps"):
                cm = MagicMock()
                cm.__enter__.return_value.read.return_value = json.dumps(
                    {"models": [{"name": "deepseek-r1:8b", "context_length": 8000}]}
                ).encode("utf-8")
                return cm
            return MagicMock()

        with patch.object(ollama.urllib.request, "urlopen", side_effect=fake_urlopen):
            ollama._ensure_model_context("http://localhost:11434", "deepseek-r1:8b", num_ctx=16384)

        self.assertEqual(len(calls), 3)
        self.assertTrue(calls[0].endswith("/api/ps"))
        self.assertTrue(calls[1].endswith("/api/generate"))  # unload
        self.assertTrue(calls[2].endswith("/api/generate"))  # reload at correct size


class TestCallLocalLlmEnsuresContext(unittest.TestCase):
    def test_ensure_model_context_called_without_num_ctx_config(self):
        config = {"ollama_url": "http://localhost:11434", "model": "deepseek-r1:8b", "num_gpu": 999}

        fake_resp = MagicMock()
        fake_resp.__enter__.return_value = iter([json.dumps({"done": True}).encode("utf-8")])

        with (
            patch.object(ollama, "_ensure_model_context") as mock_ensure,
            patch.object(ollama.urllib.request, "urlopen", return_value=fake_resp),
        ):
            ollama.call_local_llm("hello", config)

        mock_ensure.assert_called_once_with(
            "http://localhost:11434", "deepseek-r1:8b", None, None, 999
        )

    def test_chat_request_never_includes_num_gpu(self):
        # num_gpu is a load-time decision applied solely via _ensure_model_context's
        # fallback-protected preload. Including it on every /api/chat request risks
        # forcing an unguarded reload whenever the fallback loaded a different value
        # than the raw config asked for (see commit fixing the segfault this caused).
        config = {"ollama_url": "http://localhost:11434", "model": "deepseek-r1:8b", "num_gpu": 999}

        bodies = []

        def fake_urlopen(req, timeout=None):
            bodies.append(json.loads(req.data))
            fake_resp = MagicMock()
            fake_resp.__enter__.return_value = iter([json.dumps({"done": True}).encode("utf-8")])
            return fake_resp

        with (
            patch.object(ollama, "_ensure_model_context"),
            patch.object(ollama.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            ollama.call_local_llm("hello", config)

        self.assertNotIn("num_gpu", bodies[0]["options"])

    def test_computes_num_ctx_from_max_ctx_when_num_ctx_absent(self):
        config = {
            "ollama_url": "http://localhost:11434",
            "model": "deepseek-r1:8b",
            "max_ctx": 32768,
        }
        original_config = dict(config)

        bodies = []

        def fake_urlopen(req, timeout=None):
            bodies.append(json.loads(req.data))
            fake_resp = MagicMock()
            fake_resp.__enter__.return_value = iter([json.dumps({"done": True}).encode("utf-8")])
            return fake_resp

        with (
            patch.object(ollama, "estimate_num_ctx", return_value=12345) as mock_estimate,
            patch.object(ollama, "_ensure_model_context") as mock_ensure,
            patch.object(ollama.urllib.request, "urlopen", side_effect=fake_urlopen),
        ):
            ollama.call_local_llm("hello", config)

        mock_estimate.assert_called_once_with(
            "http://localhost:11434", "deepseek-r1:8b", "hello", num_predict=None, max_ctx=32768
        )
        self.assertEqual(bodies[0]["options"]["num_ctx"], 12345)
        mock_ensure.assert_called_once_with(
            "http://localhost:11434", "deepseek-r1:8b", 12345, None, None
        )
        self.assertEqual(config, original_config)  # caller's dict must not be mutated

    def test_explicit_num_ctx_wins_over_max_ctx(self):
        config = {
            "ollama_url": "http://localhost:11434",
            "model": "deepseek-r1:8b",
            "num_ctx": 8192,
            "max_ctx": 32768,
        }

        fake_resp = MagicMock()
        fake_resp.__enter__.return_value = iter([json.dumps({"done": True}).encode("utf-8")])

        with (
            patch.object(ollama, "estimate_num_ctx") as mock_estimate,
            patch.object(ollama, "_ensure_model_context") as mock_ensure,
            patch.object(ollama.urllib.request, "urlopen", return_value=fake_resp),
        ):
            ollama.call_local_llm("hello", config)

        mock_estimate.assert_not_called()
        mock_ensure.assert_called_once_with(
            "http://localhost:11434", "deepseek-r1:8b", 8192, None, None
        )


class TestRunCriticSubprocess(unittest.TestCase):
    def test_delegates_to_console_stream_subprocess(self):
        cmd = [sys.executable, "some_critic.py", "--feature", "001-x", "--iteration", "1"]

        with patch.object(ollama.console, "stream_subprocess", return_value=7) as mock_stream:
            result = ollama._run_critic_subprocess(cmd)

        mock_stream.assert_called_once_with(cmd)
        self.assertEqual(result, 7)


class TestRunLocalCriticCliDispatch(unittest.TestCase):
    """Confirms run_local_critic_cli routes the actual LLM call to the transport
    matching config["provider"] — the one dispatch point added for the
    openai-compatible provider."""

    def setUp(self):
        self._orig_cwd = Path.cwd()
        self._tmpdir = tempfile.mkdtemp()
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._orig_cwd)

    def _run(self, config):
        path = Path(self._tmpdir) / ".specify" / "local-llm.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config))

        with (
            patch.object(
                sys, "argv", ["ch_1_plan_critic.py", "--feature", "001-x", "--iteration", "1"]
            ),
            patch.object(
                ollama, "call_local_llm", return_value='{"status": "PASS"}'
            ) as mock_ollama_call,
            patch.object(
                ollama.openai_compatible,
                "call_openai_compatible_llm",
                return_value='{"status": "PASS"}',
            ) as mock_openai_call,
            patch.object(ollama.files, "write_file"),
        ):
            ollama.run_local_critic_cli(
                "plan", "plan-critic-result", lambda spec_dir, iteration: "prompt"
            )

        return mock_ollama_call, mock_openai_call

    def test_dispatches_to_call_local_llm_for_ollama_provider(self):
        mock_ollama_call, mock_openai_call = self._run(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": True, "model": "llama3.2"},
            }
        )
        mock_ollama_call.assert_called_once()
        mock_openai_call.assert_not_called()

    def test_dispatches_to_openai_compatible_for_that_provider(self):
        mock_ollama_call, mock_openai_call = self._run(
            {
                "provider": "openai-compatible",
                "base_url": "https://integrate.api.nvidia.com/v1",
                "api_key_env": "NVIDIA_API_KEY",
                "default": {"enabled": True, "model": "moonshotai/kimi-k2.6"},
            }
        )
        mock_openai_call.assert_called_once()
        mock_ollama_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()

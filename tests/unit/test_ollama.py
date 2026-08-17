"""Unit tests for agent_common/ollama.py pure functions and network-mocked calls."""

import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestLoadLocalLlmConfigs(unittest.TestCase):
    """Plural counterpart to TestLoadLocalLlmConfig — covers the list-shaped
    critics[phase] multi-critic config."""

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

    def test_no_file_returns_empty_list(self):
        self.assertEqual(ollama.load_local_llm_configs("plan"), [])

    def test_absent_critic_key_returns_empty_list(self):
        self._write_config({"default": {"enabled": False, "model": ""}, "critics": {}})
        self.assertEqual(ollama.load_local_llm_configs("plan"), [])

    def test_dict_shape_delegates_to_singular_and_wraps_as_default_id(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": False, "model": ""},
                "critics": {"plan": {"enabled": True, "model": "qwen3:4b"}},
            }
        )
        single = ollama.load_local_llm_config("plan")
        configs = ollama.load_local_llm_configs("plan")
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["id"], "default")
        self.assertEqual({k: v for k, v in configs[0].items() if k != "id"}, single)

    def test_dict_shape_disabled_returns_empty_list(self):
        self._write_config(
            {
                "default": {"enabled": False, "model": ""},
                "critics": {"plan": {"enabled": False, "model": ""}},
            }
        )
        self.assertEqual(ollama.load_local_llm_configs("plan"), [])

    def test_list_shape_resolves_each_entry_with_its_own_id(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": False, "model": ""},
                "critics": {
                    "plan": [
                        {"id": "qwen", "enabled": True, "model": "qwen3:30b-a3b"},
                        {"id": "deepseek", "enabled": True, "model": "deepseek-r1:32b"},
                    ]
                },
            }
        )
        configs = ollama.load_local_llm_configs("plan")
        self.assertEqual([c["id"] for c in configs], ["qwen", "deepseek"])
        self.assertEqual(configs[0]["model"], "qwen3:30b-a3b")
        self.assertEqual(configs[1]["model"], "deepseek-r1:32b")

    def test_list_shape_filters_disabled_entries(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": False, "model": ""},
                "critics": {
                    "plan": [
                        {"id": "qwen", "enabled": True, "model": "qwen3:30b-a3b"},
                        {"id": "off", "enabled": False, "model": "deepseek-r1:32b"},
                    ]
                },
            }
        )
        configs = ollama.load_local_llm_configs("plan")
        self.assertEqual([c["id"] for c in configs], ["qwen"])

    def test_list_shape_missing_id_raises(self):
        self._write_config(
            {
                "default": {"enabled": False, "model": ""},
                "critics": {"plan": [{"enabled": True, "model": "qwen3:30b-a3b"}]},
            }
        )
        with self.assertRaises(ValueError):
            ollama.load_local_llm_configs("plan")

    def test_list_shape_duplicate_id_raises(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": False, "model": ""},
                "critics": {
                    "plan": [
                        {"id": "qwen", "enabled": True, "model": "qwen3:30b-a3b"},
                        {"id": "qwen", "enabled": True, "model": "deepseek-r1:32b"},
                    ]
                },
            }
        )
        with self.assertRaises(ValueError):
            ollama.load_local_llm_configs("plan")

    def test_list_shape_mixed_providers_resolve_independently(self):
        self._write_config(
            {
                "provider": "ollama",
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": False, "model": ""},
                "critics": {
                    "plan": [
                        {"id": "qwen", "enabled": True, "model": "qwen3:30b-a3b"},
                        {
                            "id": "nvidia",
                            "enabled": True,
                            "model": "meta/llama-3.1-70b-instruct",
                            "provider": "openai-compatible",
                            "base_url": "https://integrate.api.nvidia.com/v1",
                            "api_key_env": "NVIDIA_API_KEY",
                        },
                    ]
                },
            }
        )
        configs = ollama.load_local_llm_configs("plan")
        by_id = {c["id"]: c for c in configs}
        self.assertEqual(by_id["qwen"]["provider"], "ollama")
        self.assertIn("ollama_url", by_id["qwen"])
        self.assertEqual(by_id["nvidia"]["provider"], "openai-compatible")
        self.assertEqual(by_id["nvidia"]["base_url"], "https://integrate.api.nvidia.com/v1")

    def test_corrupt_json_returns_empty_list(self):
        path = Path(self._tmpdir) / ".specify" / "local-llm.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{invalid json")
        self.assertEqual(ollama.load_local_llm_configs("plan"), [])


class TestLoadCriticExecutionMode(unittest.TestCase):
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

    def test_no_file_defaults_to_sequential(self):
        self.assertEqual(ollama.load_critic_execution_mode("plan"), "sequential")

    def test_absent_key_defaults_to_sequential(self):
        self._write_config({})
        self.assertEqual(ollama.load_critic_execution_mode("plan"), "sequential")

    def test_explicit_parallel_respected(self):
        self._write_config({"critic_execution": {"plan": "parallel"}})
        self.assertEqual(ollama.load_critic_execution_mode("plan"), "parallel")

    def test_unrecognized_value_defaults_to_sequential(self):
        self._write_config({"critic_execution": {"plan": "bogus"}})
        self.assertEqual(ollama.load_critic_execution_mode("plan"), "sequential")

    def test_corrupt_json_defaults_to_sequential(self):
        path = Path(self._tmpdir) / ".specify" / "local-llm.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{invalid json")
        self.assertEqual(ollama.load_critic_execution_mode("plan"), "sequential")


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

        mock_stream.assert_called_once_with(cmd, prefix="")
        self.assertEqual(result, 7)

    def test_prefix_passed_through(self):
        cmd = [sys.executable, "some_critic.py"]

        with patch.object(ollama.console, "stream_subprocess", return_value=0) as mock_stream:
            ollama._run_critic_subprocess(cmd, prefix="[qwen] ")

        mock_stream.assert_called_once_with(cmd, prefix="[qwen] ")


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


class TestRunLocalCriticCliCriticId(unittest.TestCase):
    """--critic-id routes to load_local_llm_configs and the raw-subdir result
    path; its absence must remain byte-identical to the pre-multi-critic
    behaviour already covered by TestRunLocalCriticCliDispatch."""

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

    def test_critic_id_writes_to_raw_subdir(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": False, "model": ""},
                "critics": {
                    "plan": [
                        {"id": "qwen", "enabled": True, "model": "qwen3:30b-a3b"},
                        {"id": "deepseek", "enabled": True, "model": "deepseek-r1:32b"},
                    ]
                },
            }
        )
        with (
            patch.object(
                sys,
                "argv",
                [
                    "ch_1_plan_critic.py",
                    "--feature",
                    "001-x",
                    "--iteration",
                    "1",
                    "--critic-id",
                    "deepseek",
                ],
            ),
            patch.object(ollama, "call_local_llm", return_value='{"status": "PASS"}'),
        ):
            ollama.run_local_critic_cli(
                "plan", "ch-1-plan-critic-result", lambda spec_dir, iteration: "prompt"
            )

        raw_path = Path("specs/001-x/ch-1-plan-critic-result-raw/deepseek-1.json")
        self.assertTrue(raw_path.exists())
        self.assertFalse(Path("specs/001-x/ch-1-plan-critic-result-1.json").exists())
        self.assertEqual(json.loads(raw_path.read_text())["status"], "PASS")

    def test_critic_id_unknown_exits_2(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": False, "model": ""},
                "critics": {"plan": [{"id": "qwen", "enabled": True, "model": "qwen3:30b-a3b"}]},
            }
        )
        with (
            patch.object(
                sys,
                "argv",
                ["ch_1_plan_critic.py", "--feature", "001-x", "--critic-id", "nonexistent"],
            ),
            self.assertRaises(SystemExit) as cm,
        ):
            ollama.run_local_critic_cli(
                "plan", "ch-1-plan-critic-result", lambda spec_dir, iteration: "prompt"
            )
        self.assertEqual(cm.exception.code, 2)

    def test_absent_critic_id_writes_canonical_path_unchanged(self):
        self._write_config(
            {
                "ollama_url": "http://localhost:11434",
                "default": {"enabled": True, "model": "llama3.2"},
            }
        )
        with (
            patch.object(
                sys, "argv", ["ch_1_plan_critic.py", "--feature", "001-x", "--iteration", "1"]
            ),
            patch.object(ollama, "call_local_llm", return_value='{"status": "PASS"}'),
        ):
            ollama.run_local_critic_cli(
                "plan", "ch-1-plan-critic-result", lambda spec_dir, iteration: "prompt"
            )

        self.assertTrue(Path("specs/001-x/ch-1-plan-critic-result-1.json").exists())
        self.assertFalse(Path("specs/001-x/ch-1-plan-critic-result-raw").exists())


class TestRunOneCritic(unittest.TestCase):
    def test_reuses_existing_raw_result_without_running_subprocess(self):
        with tempfile.TemporaryDirectory() as d:
            raw_dir = Path(d) / "raw"
            raw_dir.mkdir()
            (raw_dir / "qwen-1.json").write_text(json.dumps({"status": "PASS"}))
            log = MagicMock()

            with patch.object(ollama, "_run_critic_subprocess") as mock_sub:
                result = ollama._run_one_critic(
                    log,
                    "plan critic",
                    Path("script.py"),
                    "feat",
                    1,
                    raw_dir,
                    {"id": "qwen", "model": "qwen3"},
                )

            mock_sub.assert_not_called()
            self.assertEqual(result, {"id": "qwen", "model": "qwen3", "result": {"status": "PASS"}})

    def test_runs_subprocess_with_critic_id_when_raw_result_missing(self):
        with tempfile.TemporaryDirectory() as d:
            raw_dir = Path(d) / "raw"
            log = MagicMock()

            def fake_sub(cmd, prefix=""):
                raw_dir.mkdir(parents=True, exist_ok=True)
                (raw_dir / "qwen-1.json").write_text(json.dumps({"status": "FAIL"}))
                return 0

            with patch.object(ollama, "_run_critic_subprocess", side_effect=fake_sub) as mock_sub:
                result = ollama._run_one_critic(
                    log,
                    "plan critic",
                    Path("script.py"),
                    "feat",
                    1,
                    raw_dir,
                    {"id": "qwen", "model": "qwen3"},
                )

            mock_sub.assert_called_once()
            cmd = mock_sub.call_args[0][0]
            self.assertIn("--critic-id", cmd)
            self.assertIn("qwen", cmd)
            self.assertEqual(result["result"]["status"], "FAIL")

    def test_aborts_on_nonzero_returncode(self):
        with tempfile.TemporaryDirectory() as d:
            raw_dir = Path(d) / "raw"
            log = MagicMock()
            with (
                patch.object(ollama, "_run_critic_subprocess", return_value=1),
                self.assertRaises(SystemExit),
            ):
                ollama._run_one_critic(
                    log,
                    "plan critic",
                    Path("script.py"),
                    "feat",
                    1,
                    raw_dir,
                    {"id": "qwen", "model": "qwen3"},
                )

    def test_aborts_if_raw_result_missing_after_success(self):
        with tempfile.TemporaryDirectory() as d:
            raw_dir = Path(d) / "raw"
            log = MagicMock()
            with (
                patch.object(ollama, "_run_critic_subprocess", return_value=0),
                self.assertRaises(SystemExit),
            ):
                ollama._run_one_critic(
                    log,
                    "plan critic",
                    Path("script.py"),
                    "feat",
                    1,
                    raw_dir,
                    {"id": "qwen", "model": "qwen3"},
                )


class TestRunGate(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_cwd = Path.cwd()
        self._tmpdir = tempfile.mkdtemp()
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._orig_cwd)

    async def test_zero_configs_falls_back_to_claude(self):
        log = MagicMock()
        claude_fallback = MagicMock(return_value="claude-query-obj")
        with (
            patch.object(ollama, "load_local_llm_configs", return_value=[]),
            patch.object(ollama.console, "stream_query", new=AsyncMock()) as mock_stream,
        ):
            await ollama.run_gate(
                log, "plan", "script.py", "feat", 1, "plan critic", claude_fallback
            )
        claude_fallback.assert_called_once()
        mock_stream.assert_awaited_once_with("claude-query-obj")

    async def test_one_config_runs_without_critic_id_flag(self):
        log = MagicMock()
        claude_fallback = MagicMock()
        config = {"id": "default", "model": "qwen3", "provider": "ollama"}
        with (
            patch.object(ollama, "load_local_llm_configs", return_value=[config]),
            patch.object(ollama, "_run_critic_subprocess", return_value=0) as mock_sub,
            patch.object(ollama.console, "stream_query", new=AsyncMock()) as mock_stream,
        ):
            await ollama.run_gate(
                log, "plan", "script.py", "feat", 1, "plan critic", claude_fallback
            )
        mock_sub.assert_called_once()
        cmd = mock_sub.call_args[0][0]
        self.assertNotIn("--critic-id", cmd)
        claude_fallback.assert_not_called()
        mock_stream.assert_not_awaited()

    async def test_one_config_not_configured_falls_back_to_claude(self):
        log = MagicMock()
        claude_fallback = MagicMock(return_value="claude-query-obj")
        config = {"id": "default", "model": "qwen3", "provider": "ollama"}
        with (
            patch.object(ollama, "load_local_llm_configs", return_value=[config]),
            patch.object(ollama, "_run_critic_subprocess", return_value=2),
            patch.object(ollama.console, "stream_query", new=AsyncMock()) as mock_stream,
        ):
            await ollama.run_gate(
                log, "plan", "script.py", "feat", 1, "plan critic", claude_fallback
            )
        claude_fallback.assert_called_once()
        mock_stream.assert_awaited_once_with("claude-query-obj")

    async def test_multi_critic_all_clean_pass_skips_reconciliation(self):
        log = MagicMock()
        configs = [
            {"id": "a", "model": "m1"},
            {"id": "b", "model": "m2"},
        ]
        build_reconcile_query = MagicMock()

        def fake_run_one(log, label, script, feature, iteration, raw_dir, config, prefix=""):
            return {"id": config["id"], "model": config["model"], "result": {"status": "PASS"}}

        with (
            patch.object(ollama, "load_local_llm_configs", return_value=configs),
            patch.object(ollama, "load_critic_execution_mode", return_value="sequential"),
            patch.object(ollama, "_run_one_critic", side_effect=fake_run_one),
        ):
            await ollama.run_gate(
                log,
                "plan",
                "script.py",
                "feat",
                1,
                "plan critic",
                MagicMock(),
                result_prefix="ch-1-plan-critic-result",
                build_reconcile_query=build_reconcile_query,
            )

        build_reconcile_query.assert_not_called()
        result_path = Path("specs/feat/ch-1-plan-critic-result-1.json")
        self.assertTrue(result_path.exists())
        self.assertEqual(json.loads(result_path.read_text())["status"], "PASS")

    async def test_multi_critic_findings_trigger_reconciliation(self):
        log = MagicMock()
        configs = [
            {"id": "a", "model": "m1"},
            {"id": "b", "model": "m2"},
        ]

        def fake_run_one(log, label, script, feature, iteration, raw_dir, config, prefix=""):
            if config["id"] == "a":
                result = {
                    "status": "FAIL",
                    "violations": [{"rule": "x", "severity": "BLOCKING"}],
                }
            else:
                result = {"status": "PASS", "violations": []}
            return {"id": config["id"], "model": config["model"], "result": result}

        canonical_path = Path("specs/feat/ch-1-plan-critic-result-1.json")

        def fake_reconcile_query(iteration, raw_results):
            canonical_path.parent.mkdir(parents=True, exist_ok=True)
            canonical_path.write_text(json.dumps({"status": "FAIL", "violations": []}))
            return "reconcile-query-obj"

        with (
            patch.object(ollama, "load_local_llm_configs", return_value=configs),
            patch.object(ollama, "load_critic_execution_mode", return_value="sequential"),
            patch.object(ollama, "_run_one_critic", side_effect=fake_run_one),
            patch.object(ollama.console, "stream_query", new=AsyncMock()) as mock_stream,
        ):
            await ollama.run_gate(
                log,
                "plan",
                "script.py",
                "feat",
                1,
                "plan critic",
                MagicMock(),
                result_prefix="ch-1-plan-critic-result",
                build_reconcile_query=fake_reconcile_query,
            )

        mock_stream.assert_awaited_once_with("reconcile-query-obj")
        self.assertTrue(canonical_path.exists())

    async def test_multi_critic_missing_result_prefix_aborts(self):
        log = MagicMock()
        configs = [{"id": "a", "model": "m1"}, {"id": "b", "model": "m2"}]
        with (
            patch.object(ollama, "load_local_llm_configs", return_value=configs),
            self.assertRaises(SystemExit),
        ):
            await ollama.run_gate(log, "plan", "script.py", "feat", 1, "plan critic", MagicMock())

    async def test_multi_critic_findings_without_reconcile_builder_aborts(self):
        log = MagicMock()
        configs = [{"id": "a", "model": "m1"}, {"id": "b", "model": "m2"}]

        def fake_run_one(log, label, script, feature, iteration, raw_dir, config, prefix=""):
            status = "FAIL" if config["id"] == "a" else "PASS"
            return {"id": config["id"], "model": config["model"], "result": {"status": status}}

        with (
            patch.object(ollama, "load_local_llm_configs", return_value=configs),
            patch.object(ollama, "load_critic_execution_mode", return_value="sequential"),
            patch.object(ollama, "_run_one_critic", side_effect=fake_run_one),
            self.assertRaises(SystemExit),
        ):
            await ollama.run_gate(
                log,
                "plan",
                "script.py",
                "feat",
                1,
                "plan critic",
                MagicMock(),
                result_prefix="ch-1-plan-critic-result",
            )

    async def test_multi_critic_parallel_mode_still_resolves_all_critics(self):
        log = MagicMock()
        configs = [{"id": "a", "model": "m1"}, {"id": "b", "model": "m2"}]
        build_reconcile_query = MagicMock()

        def fake_run_one(log, label, script, feature, iteration, raw_dir, config, prefix=""):
            return {"id": config["id"], "model": config["model"], "result": {"status": "PASS"}}

        with (
            patch.object(ollama, "load_local_llm_configs", return_value=configs),
            patch.object(ollama, "load_critic_execution_mode", return_value="parallel"),
            patch.object(ollama, "_run_one_critic", side_effect=fake_run_one),
        ):
            await ollama.run_gate(
                log,
                "plan",
                "script.py",
                "feat",
                1,
                "plan critic",
                MagicMock(),
                result_prefix="ch-1-plan-critic-result",
                build_reconcile_query=build_reconcile_query,
            )

        build_reconcile_query.assert_not_called()
        result_path = Path("specs/feat/ch-1-plan-critic-result-1.json")
        self.assertTrue(result_path.exists())
        self.assertEqual(json.loads(result_path.read_text())["status"], "PASS")


if __name__ == "__main__":
    unittest.main()

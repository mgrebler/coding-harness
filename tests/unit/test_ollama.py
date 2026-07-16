"""Unit tests for agent_common/ollama.py pure functions and network-mocked calls."""

import json
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


if __name__ == "__main__":
    unittest.main()

"""Unit tests for agent_common.py pure functions. No LLM or network calls."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
import agent_common
from agent_common import GateSpec


class TestStripFences(unittest.TestCase):
    def test_no_fence_passthrough(self):
        self.assertEqual(agent_common.strip_fences('{"a": 1}'), '{"a": 1}')

    def test_json_fence_stripped(self):
        result = agent_common.strip_fences('```json\n{"a": 1}\n```')
        self.assertEqual(result, '{"a": 1}')

    def test_plain_fence_stripped(self):
        result = agent_common.strip_fences('```\n{"a": 1}\n```')
        self.assertEqual(result, '{"a": 1}')

    def test_whitespace_trimmed(self):
        result = agent_common.strip_fences('  {"a": 1}  ')
        self.assertEqual(result, '{"a": 1}')


class TestNextIteration(unittest.TestCase):
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(agent_common.next_iteration(Path(d), "plan-critic-result"), 1)

    def test_one_result_file(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "plan-critic-result-1.json").write_text("{}")
            self.assertEqual(agent_common.next_iteration(Path(d), "plan-critic-result"), 2)

    def test_three_result_files(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(1, 4):
                (Path(d) / f"plan-critic-result-{i}.json").write_text("{}")
            self.assertEqual(agent_common.next_iteration(Path(d), "plan-critic-result"), 4)


class TestFindPassingIteration(unittest.TestCase):
    def _write(self, d, i, status):
        (Path(d) / f"plan-critic-result-{i}.json").write_text(json.dumps({"status": status}))

    def test_no_files_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(agent_common.find_passing_iteration(Path(d), "plan-critic-result"))

    def test_all_fail_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, 1, "FAIL")
            self._write(d, 2, "FAIL")
            self.assertIsNone(agent_common.find_passing_iteration(Path(d), "plan-critic-result"))

    def test_first_pass_returns_1(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, 1, "PASS")
            self.assertEqual(agent_common.find_passing_iteration(Path(d), "plan-critic-result"), 1)

    def test_second_pass_returns_2(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, 1, "FAIL")
            self._write(d, 2, "PASS")
            self.assertEqual(agent_common.find_passing_iteration(Path(d), "plan-critic-result"), 2)


class TestLoadLocalLlmConfig(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = os.getcwd()
        self._tmpdir = tempfile.mkdtemp()
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._orig_cwd)

    def _write_config(self, data):
        path = Path(self._tmpdir) / ".specify" / "local-llm.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def test_no_file_returns_none(self):
        self.assertIsNone(agent_common.load_local_llm_config("plan"))

    def test_default_disabled_returns_none(self):
        self._write_config({"ollama_url": "http://localhost:11434",
                            "default": {"enabled": False, "model": ""},
                            "critics": {}})
        self.assertIsNone(agent_common.load_local_llm_config("plan"))

    def test_critic_override_enabled_returns_config(self):
        self._write_config({"ollama_url": "http://localhost:11434",
                            "default": {"enabled": False, "model": ""},
                            "critics": {"plan": {"enabled": True, "model": "qwen3:4b"}}})
        result = agent_common.load_local_llm_config("plan")
        self.assertIsNotNone(result)
        self.assertEqual(result["model"], "qwen3:4b")
        self.assertIn("ollama_url", result)

    def test_num_ctx_top_level_passed_through(self):
        self._write_config({"ollama_url": "http://localhost:11434",
                            "num_ctx": 8192,
                            "default": {"enabled": True, "model": "llama3.2"}})
        result = agent_common.load_local_llm_config("plan")
        self.assertEqual(result["num_ctx"], 8192)

    def test_num_ctx_absent_when_not_set(self):
        self._write_config({"ollama_url": "http://localhost:11434",
                            "default": {"enabled": True, "model": "llama3.2"}})
        result = agent_common.load_local_llm_config("plan")
        self.assertNotIn("num_ctx", result)

    def test_num_gpu_defaults_to_full_offload_when_unset(self):
        self._write_config({"ollama_url": "http://localhost:11434",
                            "default": {"enabled": True, "model": "llama3.2"}})
        result = agent_common.load_local_llm_config("plan")
        self.assertEqual(result["num_gpu"], agent_common._FULL_GPU_OFFLOAD)

    def test_num_gpu_explicit_value_respected(self):
        self._write_config({"ollama_url": "http://localhost:11434",
                            "num_gpu": 20,
                            "default": {"enabled": True, "model": "llama3.2"}})
        result = agent_common.load_local_llm_config("plan")
        self.assertEqual(result["num_gpu"], 20)

    def test_default_enabled_returns_config(self):
        self._write_config({"ollama_url": "http://localhost:11434",
                            "default": {"enabled": True, "model": "llama3.2"},
                            "critics": {}})
        result = agent_common.load_local_llm_config("plan")
        self.assertIsNotNone(result)
        self.assertEqual(result["model"], "llama3.2")

    def test_empty_model_returns_none(self):
        self._write_config({"ollama_url": "http://localhost:11434",
                            "default": {"enabled": True, "model": ""},
                            "critics": {}})
        self.assertIsNone(agent_common.load_local_llm_config("plan"))

    def test_corrupt_json_returns_none(self):
        path = Path(self._tmpdir) / ".specify" / "local-llm.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{invalid json")
        self.assertIsNone(agent_common.load_local_llm_config("plan"))


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

        with patch.object(agent_common.urllib.request, "urlopen", side_effect=fake_urlopen):
            agent_common._ensure_model_context(
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

        with patch.object(agent_common.urllib.request, "urlopen", side_effect=fake_urlopen):
            agent_common._ensure_model_context(
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

        with patch.object(agent_common.urllib.request, "urlopen", side_effect=fake_urlopen):
            agent_common._ensure_model_context(
                "http://localhost:11434", "deepseek-r1:8b", num_ctx=None, num_gpu=999
            )

        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith("/api/ps"))


class TestCallLocalLlmEnsuresContext(unittest.TestCase):
    def test_ensure_model_context_called_without_num_ctx_config(self):
        config = {"ollama_url": "http://localhost:11434", "model": "deepseek-r1:8b", "num_gpu": 999}

        fake_resp = MagicMock()
        fake_resp.__enter__.return_value = iter([json.dumps({"done": True}).encode("utf-8")])

        with patch.object(agent_common, "_ensure_model_context") as mock_ensure, \
             patch.object(agent_common.urllib.request, "urlopen", return_value=fake_resp):
            agent_common.call_local_llm("hello", config)

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

        with patch.object(agent_common, "_ensure_model_context"), \
             patch.object(agent_common.urllib.request, "urlopen", side_effect=fake_urlopen):
            agent_common.call_local_llm("hello", config)

        self.assertNotIn("num_gpu", bodies[0]["options"])


class TestStageComplete(unittest.TestCase):
    def test_not_complete(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(agent_common.stage_is_complete(Path(d), "plan"))

    def test_complete_after_write(self):
        with tempfile.TemporaryDirectory() as d:
            agent_common.write_stage_complete(Path(d), "plan")
            self.assertTrue(agent_common.stage_is_complete(Path(d), "plan"))

    def test_marker_content_includes_stage(self):
        with tempfile.TemporaryDirectory() as d:
            agent_common.write_stage_complete(Path(d), "plan")
            content = (Path(d) / "plan-auto-complete").read_text()
            self.assertIn("stage: plan", content)


class TestFormatViolationsBlock(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(agent_common.format_violations_block(None, 2), "")

    def test_empty_list_returns_empty(self):
        self.assertEqual(agent_common.format_violations_block([], 2), "")

    def test_non_empty_list_returns_block(self):
        violations = [{"rule": "§T1", "severity": "BLOCKING", "finding": "missing"}]
        result = agent_common.format_violations_block(violations, 2)
        # § is JSON-encoded as § in the output
        self.assertIn("BLOCKING", result)
        self.assertIn("previous iteration (1)", result)  # iteration - 1 = 1


class TestLoadPriorViolations(unittest.TestCase):
    def _write_result(self, d, i, status, violations=None):
        data = {"status": status, "violations": violations or []}
        (Path(d) / f"plan-critic-result-{i}.json").write_text(json.dumps(data))

    def test_iteration_1_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(agent_common.load_prior_violations(Path(d), "plan-critic-result", 1))

    def test_previous_pass_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_result(d, 1, "PASS")
            self.assertIsNone(agent_common.load_prior_violations(Path(d), "plan-critic-result", 2))

    def test_previous_fail_returns_violations(self):
        viols = [{"rule": "§T1", "severity": "BLOCKING"}]
        with tempfile.TemporaryDirectory() as d:
            self._write_result(d, 1, "FAIL", viols)
            result = agent_common.load_prior_violations(Path(d), "plan-critic-result", 2)
            self.assertEqual(result, viols)


class TestFindTwoGateResumeState(unittest.TestCase):
    def _write(self, d, prefix, i, status, key="violations", items=None):
        data = {"status": status, key: items or []}
        (Path(d) / f"{prefix}-{i}.json").write_text(json.dumps(data))

    def test_iteration_1_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            it, g1, g2 = agent_common.find_two_gate_resume_state(Path(d), "gate1", "gate2", 1)
            self.assertEqual(it, 1)
            self.assertIsNone(g1)
            self.assertIsNone(g2)

    def test_gate1_fail_returns_violations(self):
        viols = [{"rule": "§T1"}]
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "gate1", 1, "FAIL", "violations", viols)
            it, g1, g2 = agent_common.find_two_gate_resume_state(Path(d), "gate1", "gate2", 2)
            self.assertEqual(it, 2)
            self.assertEqual(g1, viols)
            self.assertIsNone(g2)

    def test_gate1_pass_gate2_missing_decrements(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "gate1", 1, "PASS")
            it, g1, g2 = agent_common.find_two_gate_resume_state(Path(d), "gate1", "gate2", 2)
            self.assertEqual(it, 1)
            self.assertIsNone(g1)
            self.assertIsNone(g2)

    def test_gate2_fail_returns_blocking_issues(self):
        issues = [{"issue": "arch violation"}]
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "gate1", 1, "PASS")
            self._write(d, "gate2", 1, "FAIL", "blocking_issues", issues)
            it, g1, g2 = agent_common.find_two_gate_resume_state(Path(d), "gate1", "gate2", 2)
            self.assertEqual(it, 2)
            self.assertIsNone(g1)
            self.assertEqual(g2, issues)

    def test_both_pass_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "gate1", 1, "PASS")
            self._write(d, "gate2", 1, "PASS")
            it, g1, g2 = agent_common.find_two_gate_resume_state(Path(d), "gate1", "gate2", 2)
            self.assertEqual(it, 2)
            self.assertIsNone(g1)
            self.assertIsNone(g2)


class TestReadOptional(unittest.TestCase):
    def test_existing_file_returns_content(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "foo.md"
            p.write_text("hello", encoding="utf-8")
            self.assertEqual(agent_common.read_optional(p, "(missing)"), "hello")

    def test_missing_file_returns_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "missing.md"
            self.assertEqual(agent_common.read_optional(p, "(missing)"), "(missing)")


class TestRequireFiles(unittest.TestCase):
    def test_all_present_does_not_exit(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = Path(d) / "a.md"
            p2 = Path(d) / "b.md"
            p1.write_text("x")
            p2.write_text("y")
            agent_common.require_files("test-critic", p1, p2)  # should not raise

    def test_missing_file_exits(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = Path(d) / "a.md"
            p1.write_text("x")
            missing = Path(d) / "missing.md"
            with self.assertRaises(SystemExit) as ctx:
                agent_common.require_files("test-critic", p1, missing)
            self.assertEqual(ctx.exception.code, 1)


class TestRequireSpecFiles(unittest.TestCase):
    def test_all_present_does_not_exit(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            (spec_dir / "spec.md").write_text("x")
            log = MagicMock()
            agent_common.require_spec_files(log, spec_dir, "spec.md")  # should not raise
            log.assert_not_called()

    def test_missing_file_exits_and_logs(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            log = MagicMock()
            with self.assertRaises(SystemExit) as ctx:
                agent_common.require_spec_files(log, spec_dir, "plan.md")
            self.assertEqual(ctx.exception.code, 1)
            log.assert_called_once()
            self.assertIn("plan.md", log.call_args[0][0])


class TestReadChangedSourceFiles(unittest.TestCase):
    def test_reads_existing_changed_file(self):
        with tempfile.TemporaryDirectory() as d:
            old_cwd = os.getcwd()
            os.chdir(d)
            try:
                Path("backend").mkdir()
                (Path("backend") / "health.ts").write_text("export default {}", encoding="utf-8")
                result = agent_common.read_changed_source_files(["backend/health.ts"])
                self.assertIn("backend/health.ts", result)
                self.assertIn("export default {}", result)
            finally:
                os.chdir(old_cwd)

    def test_skips_specs_and_result_files(self):
        result = agent_common.read_changed_source_files([
            "specs/001-feature/plan.md",
            "specs/001-feature/plan-critic-result-1.json",
        ])
        self.assertEqual(result, "(no changed files found)")

    def test_no_changed_files_returns_placeholder(self):
        self.assertEqual(agent_common.read_changed_source_files([]), "(no changed files found)")


class TestFinishStage(unittest.TestCase):
    def test_logs_commits_and_marks_stage_complete(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            log = MagicMock()
            with patch.object(agent_common, "run_auto_commit") as mock_commit:
                agent_common.finish_stage(log, spec_dir, "plan-auto", "after_plan", "plan", "Ready for review.")

            log.assert_called_once_with("Ready for review.")
            mock_commit.assert_called_once_with("after_plan", "plan-auto")
            self.assertTrue(agent_common.stage_is_complete(spec_dir, "plan"))


class TestFinishIfAlreadyPassing(unittest.TestCase):
    def test_no_passing_result_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            log = MagicMock()
            with patch.object(agent_common, "run_auto_commit") as mock_commit:
                result = agent_common.finish_if_already_passing(
                    log, spec_dir, "plan-auto", "plan-critic-result", 3, "plan critic",
                    "Ready for review.", "after_plan", "plan",
                )
            self.assertFalse(result)
            log.assert_not_called()
            mock_commit.assert_not_called()
            self.assertFalse(agent_common.stage_is_complete(spec_dir, "plan"))

    def test_passing_result_returns_true_and_finishes(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            (spec_dir / "plan-critic-result-1.json").write_text(json.dumps({"status": "PASS"}))
            log = MagicMock()
            with patch.object(agent_common, "run_auto_commit") as mock_commit:
                result = agent_common.finish_if_already_passing(
                    log, spec_dir, "plan-auto", "plan-critic-result", 3, "plan critic",
                    "Ready for review.", "after_plan", "plan",
                )
            self.assertTrue(result)
            log.assert_any_call("Already PASS from plan critic iteration 1.")
            log.assert_any_call("Ready for review.")
            mock_commit.assert_called_once_with("after_plan", "plan-auto")
            self.assertTrue(agent_common.stage_is_complete(spec_dir, "plan"))


class TestRunCli(unittest.TestCase):
    def test_explicit_feature_bypasses_branch_lookup(self):
        run_coro = MagicMock()
        with patch.object(sys, "argv", ["plan-auto.py", "--feature", "foo"]), \
             patch.object(agent_common, "get_feature_from_branch") as mock_branch, \
             patch.object(agent_common.asyncio, "run") as mock_run:
            agent_common.run_cli("plan-auto", "Plan auto-orchestrator", run_coro)

        mock_branch.assert_not_called()
        run_coro.assert_called_once_with("foo")
        mock_run.assert_called_once_with(run_coro.return_value)

    def test_omitted_feature_falls_back_to_branch(self):
        run_coro = MagicMock()
        with patch.object(sys, "argv", ["plan-auto.py"]), \
             patch.object(agent_common, "get_feature_from_branch", return_value="017-my-feature") as mock_branch, \
             patch.object(agent_common.asyncio, "run") as mock_run:
            agent_common.run_cli("plan-auto", "Plan auto-orchestrator", run_coro)

        mock_branch.assert_called_once_with("plan-auto")
        run_coro.assert_called_once_with("017-my-feature")
        mock_run.assert_called_once_with(run_coro.return_value)


def _write_result(spec_dir, prefix, iteration, status, **extra):
    data = {"status": status, **extra}
    (spec_dir / f"{prefix}-{iteration}.json").write_text(json.dumps(data))


class TestRunSingleGateLoop(unittest.IsolatedAsyncioTestCase):
    def _make_gate(self, spec_dir, results):
        """results: dict mapping iteration -> (status, violations). run_gate's fake
        writes the corresponding result file as its side effect, simulating what
        the real critic subprocess/agent does."""
        build_query_calls = []

        def build_query(iteration, prev_violations):
            build_query_calls.append((iteration, prev_violations))
            return "query-object"

        async def fake_run_gate(log, critic_type, script_name, feature, iteration, label, claude_fallback):
            status, violations = results[iteration]
            _write_result(spec_dir, "tasks-critic-result", iteration, status, violations=violations or [])

        gate = GateSpec("tasks-critic-result", "tasks_critic.py", "tasks", "tasks critic", build_query)
        return gate, fake_run_gate, build_query_calls

    async def test_pass_on_first_try(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            gate, fake_run_gate, _ = self._make_gate(spec_dir, {1: ("PASS", [])})
            run_fix = AsyncMock()
            on_pass = AsyncMock()

            with patch.object(agent_common, "run_gate", side_effect=fake_run_gate), \
                 patch.object(agent_common, "write_escalation") as mock_escalate:
                await agent_common.run_single_gate_loop(
                    MagicMock(), spec_dir, "feat", 3, gate,
                    resume_state=(1, None), skip_fix_agent=False,
                    run_fix=run_fix, on_pass=on_pass, escalation_kwargs={},
                )

            run_fix.assert_not_called()
            on_pass.assert_awaited_once()
            mock_escalate.assert_not_called()

    async def test_fail_then_retry_carries_violations_into_fix(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            viols = [{"rule": "§T1", "severity": "BLOCKING"}]
            gate, fake_run_gate, _ = self._make_gate(spec_dir, {
                1: ("FAIL", viols),
                2: ("PASS", []),
            })
            run_fix = AsyncMock()
            on_pass = AsyncMock()

            with patch.object(agent_common, "run_gate", side_effect=fake_run_gate), \
                 patch.object(agent_common, "write_escalation") as mock_escalate:
                await agent_common.run_single_gate_loop(
                    MagicMock(), spec_dir, "feat", 3, gate,
                    resume_state=(1, None), skip_fix_agent=False,
                    run_fix=run_fix, on_pass=on_pass, escalation_kwargs={},
                )

            run_fix.assert_awaited_once_with(1, viols)
            on_pass.assert_awaited_once()
            mock_escalate.assert_not_called()

    async def test_escalates_on_exhaustion(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            viols = [{"rule": "§T1", "severity": "BLOCKING"}]
            gate, fake_run_gate, _ = self._make_gate(spec_dir, {
                1: ("FAIL", viols), 2: ("FAIL", viols), 3: ("FAIL", viols),
            })
            run_fix = AsyncMock()
            on_pass = AsyncMock()
            escalation_kwargs = {"escalation_filename": "x.md", "log_description": "d",
                                 "review_history_prefixes": [], "title": "T",
                                 "summary": "S", "required_action": "R"}

            with patch.object(agent_common, "run_gate", side_effect=fake_run_gate), \
                 patch.object(agent_common, "write_escalation") as mock_escalate:
                await agent_common.run_single_gate_loop(
                    MagicMock(), spec_dir, "feat", 3, gate,
                    resume_state=(1, None), skip_fix_agent=False,
                    run_fix=run_fix, on_pass=on_pass, escalation_kwargs=escalation_kwargs,
                )

            on_pass.assert_not_called()
            mock_escalate.assert_called_once()
            _, kwargs = mock_escalate.call_args
            self.assertEqual(kwargs["spec_dir"], spec_dir)
            self.assertEqual(kwargs["feature"], "feat")
            self.assertEqual(kwargs["max_iterations"], 3)
            for k, v in escalation_kwargs.items():
                self.assertEqual(kwargs[k], v)

    async def test_skip_fix_agent_resumes_without_running_fix(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            viols = [{"rule": "§T1", "severity": "BLOCKING"}]
            # Simulate resuming after an escalation review: iteration 1 already FAILed
            # (result file exists on disk) and resume_state carries its violations forward,
            # but skip_fix_agent=True means those violations were resolved externally.
            _write_result(spec_dir, "tasks-critic-result", 1, "FAIL", violations=viols)
            gate, fake_run_gate, _ = self._make_gate(spec_dir, {2: ("PASS", [])})
            run_fix = AsyncMock()
            on_pass = AsyncMock()

            with patch.object(agent_common, "run_gate", side_effect=fake_run_gate), \
                 patch.object(agent_common, "write_escalation") as mock_escalate:
                await agent_common.run_single_gate_loop(
                    MagicMock(), spec_dir, "feat", 6, gate,
                    resume_state=(2, viols), skip_fix_agent=True,
                    run_fix=run_fix, on_pass=on_pass, escalation_kwargs={},
                )

            run_fix.assert_not_called()
            on_pass.assert_awaited_once()
            mock_escalate.assert_not_called()


class TestRunTwoGateLoop(unittest.IsolatedAsyncioTestCase):
    def _make_gates(self, spec_dir, gate1_results, gate2_results):
        def build_query1(iteration, prev_violations):
            return "gate1-query"

        def build_query2(iteration, prev_violations):
            return "gate2-query"

        async def fake_run_gate(log, critic_type, script_name, feature, iteration, label, claude_fallback):
            if critic_type == "plan":
                status, violations = gate1_results[iteration]
                _write_result(spec_dir, "plan-critic-result", iteration, status, violations=violations or [])
            else:
                status, blocking_issues = gate2_results[iteration]
                _write_result(spec_dir, "architecture-review-result", iteration, status,
                               confidence=8, blocking_issues=blocking_issues or [])

        gate1 = GateSpec("plan-critic-result", "plan_critic.py", "plan", "plan critic", build_query1)
        gate2 = GateSpec("architecture-review-result", "architecture_critic.py", "architecture",
                          "architecture review", build_query2)
        return gate1, gate2, fake_run_gate

    async def test_both_gates_pass_on_first_try(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            gate1, gate2, fake_run_gate = self._make_gates(
                spec_dir, {1: ("PASS", [])}, {1: ("PASS", [])},
            )
            run_revision = AsyncMock()
            on_both_pass = AsyncMock()

            with patch.object(agent_common, "run_gate", side_effect=fake_run_gate), \
                 patch.object(agent_common, "write_escalation") as mock_escalate:
                await agent_common.run_two_gate_loop(
                    MagicMock(), spec_dir, "feat", 3, gate1, gate2,
                    resume_state=(1, None, None), skip_fix_agent=False,
                    run_revision=run_revision, on_both_pass=on_both_pass, escalation_kwargs={},
                )

            run_revision.assert_not_called()
            on_both_pass.assert_awaited_once()
            args, _ = on_both_pass.call_args
            self.assertEqual(args[0]["status"], "PASS")
            mock_escalate.assert_not_called()

    async def test_gate1_fail_retried_with_violations_gate2_never_runs(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            viols = [{"rule": "§2", "severity": "BLOCKING"}]
            gate1, gate2, fake_run_gate = self._make_gates(
                spec_dir,
                {1: ("FAIL", viols), 2: ("PASS", [])},
                {2: ("PASS", [])},
            )
            run_revision = AsyncMock()
            on_both_pass = AsyncMock()

            with patch.object(agent_common, "run_gate", side_effect=fake_run_gate), \
                 patch.object(agent_common, "write_escalation") as mock_escalate:
                await agent_common.run_two_gate_loop(
                    MagicMock(), spec_dir, "feat", 3, gate1, gate2,
                    resume_state=(1, None, None), skip_fix_agent=False,
                    run_revision=run_revision, on_both_pass=on_both_pass, escalation_kwargs={},
                )

            run_revision.assert_awaited_once_with(1, viols, "plan critic")
            self.assertFalse((spec_dir / "architecture-review-result-1.json").exists())
            on_both_pass.assert_awaited_once()
            mock_escalate.assert_not_called()

    async def test_gate1_pass_gate2_fail_retried_with_gate2_label(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            issues = [{"title": "coupling", "severity": "High"}]
            gate1, gate2, fake_run_gate = self._make_gates(
                spec_dir,
                {1: ("PASS", []), 2: ("PASS", [])},
                {1: ("FAIL", issues), 2: ("PASS", [])},
            )
            run_revision = AsyncMock()
            on_both_pass = AsyncMock()

            with patch.object(agent_common, "run_gate", side_effect=fake_run_gate), \
                 patch.object(agent_common, "write_escalation") as mock_escalate:
                await agent_common.run_two_gate_loop(
                    MagicMock(), spec_dir, "feat", 3, gate1, gate2,
                    resume_state=(1, None, None), skip_fix_agent=False,
                    run_revision=run_revision, on_both_pass=on_both_pass, escalation_kwargs={},
                )

            run_revision.assert_awaited_once_with(1, issues, "architecture review")
            on_both_pass.assert_awaited_once()
            mock_escalate.assert_not_called()

    async def test_escalates_on_exhaustion(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            viols = [{"rule": "§2", "severity": "BLOCKING"}]
            gate1, gate2, fake_run_gate = self._make_gates(
                spec_dir,
                {1: ("FAIL", viols), 2: ("FAIL", viols), 3: ("FAIL", viols)},
                {},
            )
            run_revision = AsyncMock()
            on_both_pass = AsyncMock()
            escalation_kwargs = {"escalation_filename": "x.md", "log_description": "d",
                                 "review_history_prefixes": [], "title": "T",
                                 "summary": "S", "required_action": "R"}

            with patch.object(agent_common, "run_gate", side_effect=fake_run_gate), \
                 patch.object(agent_common, "write_escalation") as mock_escalate:
                await agent_common.run_two_gate_loop(
                    MagicMock(), spec_dir, "feat", 3, gate1, gate2,
                    resume_state=(1, None, None), skip_fix_agent=False,
                    run_revision=run_revision, on_both_pass=on_both_pass, escalation_kwargs=escalation_kwargs,
                )

            on_both_pass.assert_not_called()
            mock_escalate.assert_called_once()
            _, kwargs = mock_escalate.call_args
            self.assertEqual(kwargs["spec_dir"], spec_dir)
            self.assertEqual(kwargs["feature"], "feat")
            self.assertEqual(kwargs["max_iterations"], 3)
            for k, v in escalation_kwargs.items():
                self.assertEqual(kwargs[k], v)


if __name__ == "__main__":
    unittest.main()

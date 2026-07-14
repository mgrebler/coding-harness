"""Unit tests for agent_common/critic_loop.py — the two-gate/single-gate critic-loop
orchestration engine. No LLM or network calls (run_gate is mocked out)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import critic_loop, git, ollama, resume_state
from agent_common.critic_loop import GateSpec


class TestFinishStage(unittest.TestCase):
    def test_logs_commits_and_marks_stage_complete(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            log = MagicMock()
            with patch.object(git, "run_auto_commit") as mock_commit:
                critic_loop.finish_stage(
                    log, spec_dir, "plan-auto", "after_plan", "plan", "Ready for review."
                )

            log.assert_called_once_with("Ready for review.")
            mock_commit.assert_called_once_with("after_plan", "plan-auto")
            self.assertTrue(resume_state.stage_is_complete(spec_dir, "plan"))


class TestFinishIfAlreadyPassing(unittest.TestCase):
    def test_no_passing_result_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            log = MagicMock()
            with patch.object(git, "run_auto_commit") as mock_commit:
                result = critic_loop.finish_if_already_passing(
                    log,
                    spec_dir,
                    "plan-auto",
                    "ch-1-plan-critic-result",
                    3,
                    "plan critic",
                    "Ready for review.",
                    "after_plan",
                    "plan",
                )
            self.assertFalse(result)
            log.assert_not_called()
            mock_commit.assert_not_called()
            self.assertFalse(resume_state.stage_is_complete(spec_dir, "plan"))

    def test_passing_result_returns_true_and_finishes(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            (spec_dir / "ch-1-plan-critic-result-1.json").write_text(json.dumps({"status": "PASS"}))
            log = MagicMock()
            with patch.object(git, "run_auto_commit") as mock_commit:
                result = critic_loop.finish_if_already_passing(
                    log,
                    spec_dir,
                    "plan-auto",
                    "ch-1-plan-critic-result",
                    3,
                    "plan critic",
                    "Ready for review.",
                    "after_plan",
                    "plan",
                )
            self.assertTrue(result)
            log.assert_any_call("Already PASS from plan critic iteration 1.")
            log.assert_any_call("Ready for review.")
            mock_commit.assert_called_once_with("after_plan", "plan-auto")
            self.assertTrue(resume_state.stage_is_complete(spec_dir, "plan"))


class TestRunCli(unittest.TestCase):
    def test_explicit_feature_bypasses_branch_lookup(self):
        run_coro = MagicMock()
        with (
            patch.object(sys, "argv", ["ch-1-plan-auto.py", "--feature", "foo"]),
            patch.object(git, "get_feature_from_branch") as mock_branch,
            patch.object(critic_loop.asyncio, "run") as mock_run,
        ):
            critic_loop.run_cli("plan-auto", "Plan auto-orchestrator", run_coro)

        mock_branch.assert_not_called()
        run_coro.assert_called_once_with("foo")
        mock_run.assert_called_once_with(run_coro.return_value)

    def test_omitted_feature_falls_back_to_branch(self):
        run_coro = MagicMock()
        with (
            patch.object(sys, "argv", ["ch-1-plan-auto.py"]),
            patch.object(
                git, "get_feature_from_branch", return_value="017-my-feature"
            ) as mock_branch,
            patch.object(critic_loop.asyncio, "run") as mock_run,
        ):
            critic_loop.run_cli("plan-auto", "Plan auto-orchestrator", run_coro)

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

        async def fake_run_gate(
            log, critic_type, script_name, feature, iteration, label, claude_fallback
        ):
            status, violations = results[iteration]
            _write_result(
                spec_dir, "ch-2-tasks-critic-result", iteration, status, violations=violations or []
            )

        gate = GateSpec(
            "ch-2-tasks-critic-result", "ch_2_tasks_critic.py", "tasks", "tasks critic", build_query
        )
        return gate, fake_run_gate, build_query_calls

    async def test_pass_on_first_try(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            gate, fake_run_gate, _ = self._make_gate(spec_dir, {1: ("PASS", [])})
            run_fix = AsyncMock()
            on_pass = AsyncMock()

            with (
                patch.object(ollama, "run_gate", side_effect=fake_run_gate),
                patch.object(critic_loop, "write_escalation") as mock_escalate,
            ):
                await critic_loop.run_single_gate_loop(
                    MagicMock(),
                    spec_dir,
                    "feat",
                    3,
                    gate,
                    resume_state=(1, None),
                    skip_fix_agent=False,
                    run_fix=run_fix,
                    on_pass=on_pass,
                    escalation_kwargs={},
                )

            run_fix.assert_not_called()
            on_pass.assert_awaited_once()
            mock_escalate.assert_not_called()

    async def test_fail_then_retry_carries_violations_into_fix(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            viols = [{"rule": "§T1", "severity": "BLOCKING"}]
            gate, fake_run_gate, _ = self._make_gate(
                spec_dir,
                {
                    1: ("FAIL", viols),
                    2: ("PASS", []),
                },
            )
            run_fix = AsyncMock()
            on_pass = AsyncMock()

            with (
                patch.object(ollama, "run_gate", side_effect=fake_run_gate),
                patch.object(critic_loop, "write_escalation") as mock_escalate,
            ):
                await critic_loop.run_single_gate_loop(
                    MagicMock(),
                    spec_dir,
                    "feat",
                    3,
                    gate,
                    resume_state=(1, None),
                    skip_fix_agent=False,
                    run_fix=run_fix,
                    on_pass=on_pass,
                    escalation_kwargs={},
                )

            run_fix.assert_awaited_once_with(1, viols)
            on_pass.assert_awaited_once()
            mock_escalate.assert_not_called()

    async def test_escalates_on_exhaustion(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            viols = [{"rule": "§T1", "severity": "BLOCKING"}]
            gate, fake_run_gate, _ = self._make_gate(
                spec_dir,
                {
                    1: ("FAIL", viols),
                    2: ("FAIL", viols),
                    3: ("FAIL", viols),
                },
            )
            run_fix = AsyncMock()
            on_pass = AsyncMock()
            escalation_kwargs = {
                "escalation_filename": "x.md",
                "log_description": "d",
                "review_history_prefixes": [],
                "title": "T",
                "summary": "S",
                "required_action": "R",
            }

            with (
                patch.object(ollama, "run_gate", side_effect=fake_run_gate),
                patch.object(critic_loop, "write_escalation") as mock_escalate,
            ):
                await critic_loop.run_single_gate_loop(
                    MagicMock(),
                    spec_dir,
                    "feat",
                    3,
                    gate,
                    resume_state=(1, None),
                    skip_fix_agent=False,
                    run_fix=run_fix,
                    on_pass=on_pass,
                    escalation_kwargs=escalation_kwargs,
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
            _write_result(spec_dir, "ch-2-tasks-critic-result", 1, "FAIL", violations=viols)
            gate, fake_run_gate, _ = self._make_gate(spec_dir, {2: ("PASS", [])})
            run_fix = AsyncMock()
            on_pass = AsyncMock()

            with (
                patch.object(ollama, "run_gate", side_effect=fake_run_gate),
                patch.object(critic_loop, "write_escalation") as mock_escalate,
            ):
                await critic_loop.run_single_gate_loop(
                    MagicMock(),
                    spec_dir,
                    "feat",
                    6,
                    gate,
                    resume_state=(2, viols),
                    skip_fix_agent=True,
                    run_fix=run_fix,
                    on_pass=on_pass,
                    escalation_kwargs={},
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

        async def fake_run_gate(
            log, critic_type, script_name, feature, iteration, label, claude_fallback
        ):
            if critic_type == "plan":
                status, violations = gate1_results[iteration]
                _write_result(
                    spec_dir,
                    "ch-1-plan-critic-result",
                    iteration,
                    status,
                    violations=violations or [],
                )
            else:
                status, blocking_issues = gate2_results[iteration]
                _write_result(
                    spec_dir,
                    "ch-1-plan-architecture-review-result",
                    iteration,
                    status,
                    confidence=8,
                    blocking_issues=blocking_issues or [],
                )

        gate1 = GateSpec(
            "ch-1-plan-critic-result", "ch_1_plan_critic.py", "plan", "plan critic", build_query1
        )
        gate2 = GateSpec(
            "ch-1-plan-architecture-review-result",
            "ch_1_plan_architecture_critic.py",
            "architecture",
            "architecture review",
            build_query2,
        )
        return gate1, gate2, fake_run_gate

    async def test_both_gates_pass_on_first_try(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            gate1, gate2, fake_run_gate = self._make_gates(
                spec_dir,
                {1: ("PASS", [])},
                {1: ("PASS", [])},
            )
            run_revision = AsyncMock()
            on_both_pass = AsyncMock()

            with (
                patch.object(ollama, "run_gate", side_effect=fake_run_gate),
                patch.object(critic_loop, "write_escalation") as mock_escalate,
            ):
                await critic_loop.run_two_gate_loop(
                    MagicMock(),
                    spec_dir,
                    "feat",
                    3,
                    gate1,
                    gate2,
                    resume_state=(1, None, None),
                    skip_fix_agent=False,
                    run_revision=run_revision,
                    on_both_pass=on_both_pass,
                    escalation_kwargs={},
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

            with (
                patch.object(ollama, "run_gate", side_effect=fake_run_gate),
                patch.object(critic_loop, "write_escalation") as mock_escalate,
            ):
                await critic_loop.run_two_gate_loop(
                    MagicMock(),
                    spec_dir,
                    "feat",
                    3,
                    gate1,
                    gate2,
                    resume_state=(1, None, None),
                    skip_fix_agent=False,
                    run_revision=run_revision,
                    on_both_pass=on_both_pass,
                    escalation_kwargs={},
                )

            run_revision.assert_awaited_once_with(1, viols, "plan critic")
            self.assertFalse((spec_dir / "ch-1-plan-architecture-review-result-1.json").exists())
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

            with (
                patch.object(ollama, "run_gate", side_effect=fake_run_gate),
                patch.object(critic_loop, "write_escalation") as mock_escalate,
            ):
                await critic_loop.run_two_gate_loop(
                    MagicMock(),
                    spec_dir,
                    "feat",
                    3,
                    gate1,
                    gate2,
                    resume_state=(1, None, None),
                    skip_fix_agent=False,
                    run_revision=run_revision,
                    on_both_pass=on_both_pass,
                    escalation_kwargs={},
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
            escalation_kwargs = {
                "escalation_filename": "x.md",
                "log_description": "d",
                "review_history_prefixes": [],
                "title": "T",
                "summary": "S",
                "required_action": "R",
            }

            with (
                patch.object(ollama, "run_gate", side_effect=fake_run_gate),
                patch.object(critic_loop, "write_escalation") as mock_escalate,
            ):
                await critic_loop.run_two_gate_loop(
                    MagicMock(),
                    spec_dir,
                    "feat",
                    3,
                    gate1,
                    gate2,
                    resume_state=(1, None, None),
                    skip_fix_agent=False,
                    run_revision=run_revision,
                    on_both_pass=on_both_pass,
                    escalation_kwargs=escalation_kwargs,
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

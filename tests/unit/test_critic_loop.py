"""Unit tests for agent_common/critic_loop.py — the two-gate/single-gate critic-loop
orchestration engine. No LLM or network calls (run_gate is mocked out)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import console, critic_loop, git, ollama, resume_state
from agent_common.critic_loop import GateSpec
from agent_common.resume_state import IterationBudget


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

    def test_passing_result_forwards_commit_exclusions(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            (spec_dir / "ch-5-docs-critic-result-1.json").write_text(json.dumps({"status": "PASS"}))
            with patch.object(git, "run_auto_commit") as mock_commit:
                critic_loop.finish_if_already_passing(
                    MagicMock(),
                    spec_dir,
                    "docs-auto",
                    "ch-5-docs-critic-result",
                    "docs critic",
                    "Ready.",
                    "after_docs",
                    "docs",
                    exclude_from_commit=["unrelated.txt"],
                )
            mock_commit.assert_called_once_with(
                "after_docs", "docs-auto", exclude=["unrelated.txt"]
            )


class TestRunCli(unittest.TestCase):
    def test_explicit_feature_bypasses_branch_lookup(self):
        run_coro = MagicMock()
        with (
            patch.object(sys, "argv", ["ch_1_plan_auto.py", "--feature", "foo"]),
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
            patch.object(sys, "argv", ["ch_1_plan_auto.py"]),
            patch.object(
                git, "get_feature_from_branch", return_value="017-my-feature"
            ) as mock_branch,
            patch.object(critic_loop.asyncio, "run") as mock_run,
        ):
            critic_loop.run_cli("plan-auto", "Plan auto-orchestrator", run_coro)

        mock_branch.assert_called_once_with("plan-auto")
        run_coro.assert_called_once_with("017-my-feature")
        mock_run.assert_called_once_with(run_coro.return_value)

    def test_session_limit_error_exits_with_usage_limit_code(self):
        run_coro = MagicMock()
        with (
            patch.object(sys, "argv", ["ch_1_plan_auto.py", "--feature", "foo"]),
            patch.object(
                critic_loop.asyncio,
                "run",
                side_effect=console.SessionLimitError("hit your session limit"),
            ) as mock_run,
            self.assertRaises(SystemExit) as cm,
        ):
            critic_loop.run_cli("plan-auto", "Plan auto-orchestrator", run_coro)

        self.assertEqual(cm.exception.code, console.USAGE_LIMIT_EXIT_CODE)
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

        def build_query(iteration, prev_violations, model):
            build_query_calls.append((iteration, prev_violations, model))
            return "query-object"

        async def fake_run_gate(
            log, critic_type, script_name, feature, iteration, label, claude_fallback, **kwargs
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
                    IterationBudget(3, 3),
                    gate,
                    resume_state=(1, None),
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
                    IterationBudget(3, 3),
                    gate,
                    resume_state=(1, None),
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
            # Distinct locations per iteration so the loop runs to true iteration
            # exhaustion rather than tripping the no-forward-progress early escalation
            # (which requires the same BLOCKING signature across consecutive iterations).
            gate, fake_run_gate, _ = self._make_gate(
                spec_dir,
                {
                    1: ("FAIL", [{"rule": "§T1", "severity": "BLOCKING", "location": "L1"}]),
                    2: ("FAIL", [{"rule": "§T1", "severity": "BLOCKING", "location": "L2"}]),
                    3: ("FAIL", [{"rule": "§T1", "severity": "BLOCKING", "location": "L3"}]),
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
                    IterationBudget(3, 3),
                    gate,
                    resume_state=(1, None),
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

    async def test_off_schema_result_is_moved_aside_and_gate_rerun(self):
        """specs/006: a schema-invalid result was re-read and aborted on every resume."""
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            (spec_dir / "ch-2-tasks-critic-result-1.json").write_text('{"findings": []}')
            gate, fake_run_gate, _ = self._make_gate(spec_dir, {1: ("PASS", [])})
            on_pass = AsyncMock()

            with patch.object(ollama, "run_gate", side_effect=fake_run_gate):
                await critic_loop.run_single_gate_loop(
                    MagicMock(),
                    spec_dir,
                    "feat",
                    IterationBudget(3, 3),
                    gate,
                    resume_state=(1, None),
                    run_fix=AsyncMock(),
                    on_pass=on_pass,
                    escalation_kwargs={},
                )

            on_pass.assert_awaited_once()
            self.assertTrue((spec_dir / "ch-2-tasks-critic-result-1.invalid").exists())

    async def test_pass_records_its_leftover_findings_as_followups(self):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d) / "001-feat"
            spec_dir.mkdir()
            gate, _, _ = self._make_gate(spec_dir, {})

            async def fake_run_gate(*args, **kwargs):
                _write_result(
                    spec_dir,
                    "ch-2-tasks-critic-result",
                    1,
                    "PASS",
                    violations=[],
                    required_remediations=["Update research.md §12 before tasks"],
                )

            with patch.object(ollama, "run_gate", side_effect=fake_run_gate):
                await critic_loop.run_single_gate_loop(
                    MagicMock(),
                    spec_dir,
                    "feat",
                    IterationBudget(3, 3),
                    gate,
                    resume_state=(1, None),
                    run_fix=AsyncMock(),
                    on_pass=AsyncMock(),
                    escalation_kwargs={},
                )

            followup = (spec_dir / "FOLLOWUP-non-blocking-concerns.md").read_text()
            self.assertIn("Update research.md §12", followup)

    async def test_revision_that_changes_nothing_is_retried_with_a_guard_violation(self):
        """specs/005: the reviser printed tasks instead of writing them and the
        critic burned an iteration confirming tasks.md was unchanged."""
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            tasks = spec_dir / "tasks.md"
            tasks.write_text("## Phase 1\n- [ ] T001 [IMPL] x\n")
            viols = [{"rule": "§T1", "severity": "BLOCKING"}]
            _write_result(spec_dir, "ch-2-tasks-critic-result", 1, "FAIL", violations=viols)
            gate, fake_run_gate, _ = self._make_gate(spec_dir, {2: ("PASS", [])})
            calls = []

            async def run_fix(pending_iter, pending):
                calls.append(pending)
                if len(calls) == 2:
                    tasks.write_text("## Phase 1\n- [ ] T001 [IMPL] x — plan §2\n")

            with patch.object(ollama, "run_gate", side_effect=fake_run_gate):
                await critic_loop.run_single_gate_loop(
                    MagicMock(),
                    spec_dir,
                    "feat",
                    IterationBudget(3, 3),
                    gate,
                    resume_state=(2, viols),
                    run_fix=run_fix,
                    on_pass=AsyncMock(),
                    escalation_kwargs={},
                    revised_artifact=tasks,
                )

            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1][0]["rule"], "Revision guard")
            self.assertEqual(calls[1][1:], viols)

    async def _resume_after_review(self, budget):
        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            viols = [{"rule": "§T1", "severity": "BLOCKING"}]
            _write_result(spec_dir, "ch-2-tasks-critic-result", 1, "FAIL", violations=viols)
            gate, fake_run_gate, _ = self._make_gate(spec_dir, {2: ("PASS", [])})
            run_fix = AsyncMock()

            with patch.object(ollama, "run_gate", side_effect=fake_run_gate):
                await critic_loop.run_single_gate_loop(
                    MagicMock(),
                    spec_dir,
                    "feat",
                    budget,
                    gate,
                    resume_state=(2, viols),
                    run_fix=run_fix,
                    on_pass=AsyncMock(),
                    escalation_kwargs={},
                )
            return run_fix, viols

    async def test_review_marked_fixed_by_human_skips_fix_agent(self):
        run_fix, _ = await self._resume_after_review(
            IterationBudget(6, 3, resolved_externally=True)
        )
        run_fix.assert_not_called()

    async def test_review_guidance_is_passed_to_fix_agent(self):
        run_fix, viols = await self._resume_after_review(
            IterationBudget(6, 3, human_guidance="Use window.location, not document.referrer.")
        )
        run_fix.assert_awaited_once()
        pending_iter, pending = run_fix.await_args.args
        self.assertEqual(pending_iter, 1)
        self.assertEqual(pending[0]["rule"], "Human escalation review")
        self.assertIn("window.location", pending[0]["finding"])
        self.assertEqual(pending[1:], viols)


class TestRunTwoGateLoop(unittest.IsolatedAsyncioTestCase):
    def _make_gates(self, spec_dir, gate1_results, gate2_results):
        def build_query1(iteration, prev_violations, model):
            return "gate1-query"

        def build_query2(iteration, prev_violations, model):
            return "gate2-query"

        async def fake_run_gate(
            log, critic_type, script_name, feature, iteration, label, claude_fallback, **kwargs
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
            "plan-architecture-review",
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
                    IterationBudget(3, 3),
                    gate1,
                    gate2,
                    resume_state=(1, None, None),
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
                    IterationBudget(3, 3),
                    gate1,
                    gate2,
                    resume_state=(1, None, None),
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
                    IterationBudget(3, 3),
                    gate1,
                    gate2,
                    resume_state=(1, None, None),
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
            # Distinct locations per iteration so the loop runs to true iteration
            # exhaustion rather than tripping the no-forward-progress early escalation
            # (which requires the same BLOCKING signature across consecutive iterations).
            gate1, gate2, fake_run_gate = self._make_gates(
                spec_dir,
                {
                    1: ("FAIL", [{"rule": "§2", "severity": "BLOCKING", "location": "L1"}]),
                    2: ("FAIL", [{"rule": "§2", "severity": "BLOCKING", "location": "L2"}]),
                    3: ("FAIL", [{"rule": "§2", "severity": "BLOCKING", "location": "L3"}]),
                },
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
                    IterationBudget(3, 3),
                    gate1,
                    gate2,
                    resume_state=(1, None, None),
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

    async def test_gate2_off_schema_result_aborts_instead_of_silent_fail(self):
        """Reproduces the exact FOLLOWUP_HARNESS.md bug: gate2's subagent writes a
        ReportFindings-shaped {"findings": []} blob instead of the confidence-style
        schema its prompt asked for. This must abort loudly (SystemExit) rather than
        being read as a worst-case FAIL with 0 confidence/0 blocking issues — a gate
        that can never return PASS is a harness bug, not a review verdict."""

        def build_query1(iteration, prev_violations, model):
            return "gate1-query"

        def build_query2(iteration, prev_violations, model):
            return "gate2-query"

        async def fake_run_gate(
            log, critic_type, script_name, feature, iteration, label, claude_fallback, **kwargs
        ):
            if critic_type == "plan":
                _write_result(spec_dir, "ch-1-plan-critic-result", iteration, "PASS")
            else:
                # Off-schema: no status/confidence/blocking_issues at all.
                (spec_dir / f"ch-1-plan-architecture-review-result-{iteration}.json").write_text(
                    json.dumps({"findings": []})
                )

        with tempfile.TemporaryDirectory() as d:
            spec_dir = Path(d)
            gate1 = GateSpec(
                "ch-1-plan-critic-result",
                "ch_1_plan_critic.py",
                "plan",
                "plan critic",
                build_query1,
            )
            gate2 = GateSpec(
                "ch-1-plan-architecture-review-result",
                "ch_1_plan_architecture_critic.py",
                "plan-architecture-review",
                "architecture review",
                build_query2,
            )
            run_revision = AsyncMock()
            on_both_pass = AsyncMock()

            with (
                patch.object(ollama, "run_gate", side_effect=fake_run_gate),
                patch.object(critic_loop, "write_escalation") as mock_escalate,
                self.assertRaises(SystemExit),
            ):
                await critic_loop.run_two_gate_loop(
                    MagicMock(),
                    spec_dir,
                    "feat",
                    IterationBudget(3, 3),
                    gate1,
                    gate2,
                    resume_state=(1, None, None),
                    run_revision=run_revision,
                    on_both_pass=on_both_pass,
                    escalation_kwargs={},
                )

            on_both_pass.assert_not_called()
            mock_escalate.assert_not_called()


if __name__ == "__main__":
    unittest.main()

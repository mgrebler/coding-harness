"""Unit tests for agent_common/driving_agent.py — the shared prompt-suffix and
options helper appended to every internal driving-agent query() call. No LLM calls;
verifies the notice text and options wiring directly."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common.driving_agent import (
    FIX_AGENT_GIT_AND_EXTERNAL_SYSTEM_GUARDRAILS,
    HUMAN_REVIEW_ATTESTATION_GUARDRAIL,
    NO_RECURSION_NOTICE,
    driving_agent_options,
)

AGENTS_DIR = Path(__file__).parent.parent.parent / ".claude/agents"


class TestNoRecursionNotice(unittest.TestCase):
    def test_prohibits_recursive_self_invocation(self):
        self.assertIn("ch-*-auto", NO_RECURSION_NOTICE)
        self.assertIn("ch_*_auto.py", NO_RECURSION_NOTICE)

    def test_prohibits_backgrounding_the_delegated_subagent(self):
        self.assertIn("run_in_background=True", NO_RECURSION_NOTICE)
        self.assertIn("foreground", NO_RECURSION_NOTICE)


class TestDrivingAgentOptions(unittest.TestCase):
    def test_isolates_from_project_filesystem_settings(self):
        options = driving_agent_options(allowed_tools=["Read"], agents={})
        self.assertEqual(options.setting_sources, [])

    def test_passes_through_allowed_tools_and_agents(self):
        agents = {"some-agent": object()}
        options = driving_agent_options(allowed_tools=["Read", "Agent"], agents=agents)
        self.assertEqual(options.allowed_tools, ["Read", "Agent"])
        self.assertIs(options.agents, agents)

    def test_model_omitted_defaults_to_none(self):
        options = driving_agent_options(allowed_tools=["Read"], agents={})
        self.assertIsNone(options.model)

    def test_model_passed_through_when_given(self):
        options = driving_agent_options(allowed_tools=["Read"], agents={}, model="opus")
        self.assertEqual(options.model, "opus")


class TestHumanReviewAttestationGuardrail(unittest.TestCase):
    def test_forbids_escalation_review_files_and_attestations(self):
        self.assertIn("*-escalation-review.md", HUMAN_REVIEW_ATTESTATION_GUARDRAIL)
        self.assertIn("Reviewed by:", HUMAN_REVIEW_ATTESTATION_GUARDRAIL)

    def test_included_in_fix_agent_guardrails(self):
        self.assertIn(
            HUMAN_REVIEW_ATTESTATION_GUARDRAIL, FIX_AGENT_GIT_AND_EXTERNAL_SYSTEM_GUARDRAILS
        )

    def test_every_stage_script_injects_it_into_an_agent_prompt(self):
        for script in sorted(AGENTS_DIR.glob("ch_*_auto.py")):
            if script.name == "ch_plan_to_implement_auto.py":
                continue
            with self.subTest(script=script.name):
                self.assertIn(
                    "{HUMAN_REVIEW_ATTESTATION_GUARDRAIL}", script.read_text(encoding="utf-8")
                )


if __name__ == "__main__":
    unittest.main()

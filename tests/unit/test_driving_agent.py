"""Unit tests for agent_common/driving_agent.py — the shared prompt-suffix and
options helper appended to every internal driving-agent query() call. No LLM calls;
verifies the notice text and options wiring directly."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common.driving_agent import NO_RECURSION_NOTICE, driving_agent_options


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


if __name__ == "__main__":
    unittest.main()

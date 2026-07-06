"""Unit tests for critic prompt builder functions. No LLM calls — verifies prompt wiring."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from plan_critic import build_plan_critic_prompt
from tasks_critic import build_tasks_critic_prompt
from test_critic import build_test_critic_prompt
from implement_critic import build_implement_critic_prompt
from architecture_critic import build_architecture_review_prompt
from quality_critic import build_quality_review_prompt


class PromptBuilderCommonTests:
    """Mixin for shared prompt-wiring tests. Mix into a TestCase that defines _build()."""

    def test_spec_content_injected(self):
        self.assertIn("MY_SPEC", self._build(spec="MY_SPEC"))

    def test_violations_block_included_when_provided(self):
        self.assertIn("PREV_VIOL", self._build(violations_block="PREV_VIOL"))

    def test_violations_block_absent_when_not_provided(self):
        self.assertNotIn("PREV_VIOLATIONS", self._build())

    def test_json_schema_instruction_present(self):
        prompt = self._build()
        self.assertIn('"status"', prompt)
        self.assertIn('"violations"', prompt)
        self.assertIn("PASS or FAIL", prompt)


class TestPlanCriticPrompt(PromptBuilderCommonTests, unittest.TestCase):
    def _build(self, **kwargs):
        defaults = dict(constitution="CONST", architecture="ARCH", spec="SPEC", plan="PLAN", iteration=1)
        defaults.update(kwargs)
        return build_plan_critic_prompt(**defaults)

    def test_constitution_content_injected(self):
        self.assertIn("MY_CONSTITUTION", self._build(constitution="MY_CONSTITUTION"))

    def test_architecture_content_injected(self):
        self.assertIn("MY_ARCH", self._build(architecture="MY_ARCH"))

    def test_plan_content_injected(self):
        self.assertIn("MY_PLAN", self._build(plan="MY_PLAN"))

    def test_traceability_rule_present(self):
        self.assertIn("Traceability", self._build())


class TestTasksCriticPrompt(PromptBuilderCommonTests, unittest.TestCase):
    def _build(self, **kwargs):
        defaults = dict(constitution="CONST", spec="SPEC", plan="PLAN", tasks="TASKS", iteration=1)
        defaults.update(kwargs)
        return build_tasks_critic_prompt(**defaults)

    def test_constitution_content_injected(self):
        self.assertIn("MY_CONST", self._build(constitution="MY_CONST"))

    def test_tasks_content_injected(self):
        self.assertIn("MY_TASKS", self._build(tasks="MY_TASKS"))

    def test_rule_labels_present(self):
        prompt = self._build()
        self.assertIn("§T1", prompt)
        self.assertIn("§T2", prompt)


class TestTestCriticPrompt(PromptBuilderCommonTests, unittest.TestCase):
    def _build(self, **kwargs):
        defaults = dict(constitution="CONST", spec="SPEC", plan="PLAN", tasks="TASKS",
                        test_principles="PRINCIPLES", feature="001-health-endpoint", iteration=1)
        defaults.update(kwargs)
        return build_test_critic_prompt(**defaults)

    def test_constitution_content_injected(self):
        self.assertIn("MY_CONST", self._build(constitution="MY_CONST"))

    def test_test_principles_injected(self):
        self.assertIn("MY_PRINCIPLES", self._build(test_principles="MY_PRINCIPLES"))

    def test_rule_labels_present(self):
        prompt = self._build()
        self.assertIn("§TQ1", prompt)
        self.assertIn("§TQ2", prompt)

    def test_architecture_optional_included_when_provided(self):
        self.assertIn("MY_ARCH", self._build(architecture="MY_ARCH"))

    def test_test_results_embedded_on_local_llm_path(self):
        prompt = self._build(changed_files_section="MY_FILES", test_results="MY_RESULTS")
        self.assertIn("MY_RESULTS", prompt)
        self.assertIn("MY_FILES", prompt)


class TestImplementCriticPrompt(PromptBuilderCommonTests, unittest.TestCase):
    def _build(self, **kwargs):
        defaults = dict(constitution="CONST", spec="SPEC", plan="PLAN", tasks="TASKS", iteration=1)
        defaults.update(kwargs)
        return build_implement_critic_prompt(**defaults)

    def test_constitution_content_injected(self):
        self.assertIn("MY_CONST", self._build(constitution="MY_CONST"))

    def test_rule_labels_present(self):
        prompt = self._build()
        self.assertIn("§I1", prompt)
        self.assertIn("§I4", prompt)

    def test_contracts_optional_included_when_provided(self):
        self.assertIn("MY_CONTRACTS", self._build(contracts="MY_CONTRACTS"))

    def test_data_model_optional_included_when_provided(self):
        self.assertIn("MY_DATA_MODEL", self._build(data_model="MY_DATA_MODEL"))


class TestArchitectureReviewPrompt(unittest.TestCase):
    def _build(self, **kwargs):
        defaults = dict(constitution="CONST", architecture="ARCH", spec="SPEC", plan="PLAN",
                        arch_principles="PRINCIPLES", iteration=1)
        defaults.update(kwargs)
        return build_architecture_review_prompt(**defaults)

    def test_constitution_content_injected(self):
        self.assertIn("MY_CONST", self._build(constitution="MY_CONST"))

    def test_spec_content_injected(self):
        self.assertIn("MY_SPEC", self._build(spec="MY_SPEC"))

    def test_plan_content_injected(self):
        self.assertIn("MY_PLAN", self._build(plan="MY_PLAN"))

    def test_arch_principles_injected(self):
        self.assertIn("MY_PRINCIPLES", self._build(arch_principles="MY_PRINCIPLES"))

    def test_violations_block_included_when_provided(self):
        self.assertIn("PREV_VIOL", self._build(violations_block="PREV_VIOL"))

    def test_violations_block_absent_when_not_provided(self):
        self.assertNotIn("PREV_VIOLATIONS", self._build())

    def test_json_schema_instruction_present(self):
        prompt = self._build()
        self.assertIn('"status"', prompt)
        self.assertIn('"blocking_issues"', prompt)
        self.assertIn("PASS or FAIL", prompt)


class TestQualityReviewPrompt(unittest.TestCase):
    def _build(self, **kwargs):
        defaults = dict(constitution="CONST", spec="SPEC", plan="PLAN", tasks="TASKS",
                        quality_principles="PRINCIPLES", iteration=1)
        defaults.update(kwargs)
        return build_quality_review_prompt(**defaults)

    def test_constitution_content_injected(self):
        self.assertIn("MY_CONST", self._build(constitution="MY_CONST"))

    def test_tasks_content_injected(self):
        self.assertIn("MY_TASKS", self._build(tasks="MY_TASKS"))

    def test_quality_principles_injected(self):
        self.assertIn("MY_PRINCIPLES", self._build(quality_principles="MY_PRINCIPLES"))

    def test_violations_block_included_when_provided(self):
        self.assertIn("PREV_VIOL", self._build(violations_block="PREV_VIOL"))

    def test_git_diff_instructions_on_claude_path(self):
        self.assertIn("git diff", self._build())

    def test_changed_files_embedded_on_local_llm_path(self):
        prompt = self._build(changed_files_section="MY_FILES")
        self.assertIn("MY_FILES", prompt)

    def test_json_schema_instruction_present(self):
        prompt = self._build()
        self.assertIn('"status"', prompt)
        self.assertIn('"blocking_issues"', prompt)
        self.assertIn("PASS or FAIL", prompt)


if __name__ == "__main__":
    unittest.main()

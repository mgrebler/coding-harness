"""Unit tests for critic prompt builder functions. No LLM calls — verifies prompt wiring."""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from ch_1_plan_architecture_critic import build_architecture_review_prompt
from ch_1_plan_critic import build_plan_critic_prompt
from ch_2_tasks_critic import build_tasks_critic_prompt, tasks_rules
from ch_3_test_critic import build_test_critic_prompt
from ch_3_test_quality_critic import build_test_quality_review_prompt
from ch_4_implement_critic import build_implement_critic_prompt
from ch_4_implement_quality_critic import build_quality_review_prompt


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
        defaults = {
            "constitution": "CONST",
            "architecture": "ARCH",
            "docs": "DOCS",
            "spec": "SPEC",
            "plan": "PLAN",
            "iteration": 1,
        }
        defaults.update(kwargs)
        return build_plan_critic_prompt(**defaults)

    def test_constitution_content_injected(self):
        self.assertIn("MY_CONSTITUTION", self._build(constitution="MY_CONSTITUTION"))

    def test_architecture_content_injected(self):
        self.assertIn("MY_ARCH", self._build(architecture="MY_ARCH"))

    def test_docs_content_injected(self):
        self.assertIn("MY_DOCS", self._build(docs="MY_DOCS"))

    def test_docs_absent_message_passed_through(self):
        self.assertIn("(docs/ not yet created)", self._build(docs="(docs/ not yet created)"))

    def test_plan_content_injected(self):
        self.assertIn("MY_PLAN", self._build(plan="MY_PLAN"))

    def test_traceability_rule_present(self):
        self.assertIn("Traceability", self._build())

    def test_documentation_consistency_rule_present(self):
        self.assertIn("Documentation Consistency", self._build())


class TestTasksCriticPrompt(PromptBuilderCommonTests, unittest.TestCase):
    def _build(self, **kwargs):
        defaults = {
            "constitution": "CONST",
            "spec": "SPEC",
            "plan": "PLAN",
            "tasks": "TASKS",
            "iteration": 1,
        }
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

    def test_pairing_rule_present_when_test_stage_enabled(self):
        prompt = self._build(test_stage_enabled=True)
        self.assertIn("apply Constitution", prompt)
        self.assertNotIn("N/A for this project", prompt)

    def test_pairing_rule_replaced_when_test_stage_disabled(self):
        prompt = self._build(test_stage_enabled=False)
        self.assertIn("N/A for this project", prompt)
        self.assertIn("pipeline.json", prompt)
        self.assertNotIn("apply Constitution", prompt)


class TestTasksRuleCatalogParity(unittest.TestCase):
    """specs/084: the local critic's §T5 (Task Format) and the skill's §T5
    (Schema Migration) disagreed, so the reconciler threw out real findings."""

    def test_every_harness_rule_matches_the_skill_checklist(self):
        skill = (
            Path(__file__).parent.parent.parent / ".claude/skills/ch-2-tasks-critic/SKILL.md"
        ).read_text(encoding="utf-8")
        skill_rules = dict(re.findall(r"^### (§T\d+) — (.+?) \[", skill, re.MULTILINE))
        for test_stage_enabled in (True, False):
            harness_rules = re.findall(
                r"^(§T\d+) (.+?) \[", tasks_rules(test_stage_enabled), re.MULTILINE
            )
            self.assertTrue(harness_rules)
            for number, title in harness_rules:
                self.assertEqual(skill_rules.get(number), title, number)


class TestTestCriticPrompt(PromptBuilderCommonTests, unittest.TestCase):
    def _build(self, **kwargs):
        defaults = {
            "constitution": "CONST",
            "spec": "SPEC",
            "plan": "PLAN",
            "tasks": "TASKS",
            "test_principles": "PRINCIPLES",
            "feature": "001-health-endpoint",
            "iteration": 1,
        }
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
        self.assertIn("§TQ5", prompt)
        self.assertIn("§TQ6", prompt)

    def test_quality_rules_not_present(self):
        prompt = self._build()
        self.assertNotIn("§TQ7", prompt)
        self.assertNotIn("§TQ8", prompt)
        self.assertNotIn("§TQ9", prompt)

    def test_architecture_optional_included_when_provided(self):
        self.assertIn("MY_ARCH", self._build(architecture="MY_ARCH"))

    def test_test_results_embedded_on_local_llm_path(self):
        prompt = self._build(changed_files_section="MY_FILES", test_results="MY_RESULTS")
        self.assertIn("MY_RESULTS", prompt)
        self.assertIn("MY_FILES", prompt)


class TestImplementCriticPrompt(PromptBuilderCommonTests, unittest.TestCase):
    def _build(self, **kwargs):
        defaults = {
            "constitution": "CONST",
            "spec": "SPEC",
            "plan": "PLAN",
            "tasks": "TASKS",
            "iteration": 1,
        }
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
        defaults = {
            "constitution": "CONST",
            "architecture": "ARCH",
            "spec": "SPEC",
            "plan": "PLAN",
            "arch_principles": "PRINCIPLES",
            "iteration": 1,
        }
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

    def test_location_field_present_in_schema(self):
        self.assertIn('"location"', self._build())


class TestQualityReviewPrompt(unittest.TestCase):
    def _build(self, **kwargs):
        defaults = {
            "constitution": "CONST",
            "spec": "SPEC",
            "plan": "PLAN",
            "tasks": "TASKS",
            "quality_principles": "PRINCIPLES",
            "iteration": 1,
        }
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

    def test_location_field_present_in_schema(self):
        self.assertIn('"location"', self._build())

    def test_fail_threshold_is_two_high(self):
        self.assertIn("more than 2 High", self._build())


class TestTestQualityReviewPrompt(unittest.TestCase):
    def _build(self, **kwargs):
        defaults = {
            "constitution": "CONST",
            "spec": "SPEC",
            "plan": "PLAN",
            "tasks": "TASKS",
            "test_principles": "PRINCIPLES",
            "iteration": 1,
        }
        defaults.update(kwargs)
        return build_test_quality_review_prompt(**defaults)

    def test_constitution_content_injected(self):
        self.assertIn("MY_CONST", self._build(constitution="MY_CONST"))

    def test_test_principles_injected(self):
        self.assertIn("MY_PRINCIPLES", self._build(test_principles="MY_PRINCIPLES"))

    def test_rule_labels_present(self):
        prompt = self._build()
        self.assertIn("§TQ7", prompt)
        self.assertIn("§TQ8", prompt)
        self.assertIn("§TQ9", prompt)

    def test_structural_rules_not_present(self):
        prompt = self._build()
        self.assertNotIn("§TQ1", prompt)
        self.assertNotIn("§TQ5", prompt)

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

    def test_location_field_present_in_schema(self):
        self.assertIn('"location"', self._build())

    def test_fail_threshold_is_two_high(self):
        self.assertIn("more than 2 High", self._build())


if __name__ == "__main__":
    unittest.main()

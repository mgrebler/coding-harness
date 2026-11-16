"""Unit tests for agent_common/validation_report.py: which scenarios a spec and
quickstart require, how the source issue is found, and which report.json
shapes are rejected as evidence. No LLM calls."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import validation_report as vr

SPEC = """# Feature Specification: Saved searches

**Feature Branch**: `012-saved-searches`
**Source Issue**: #42 (https://github.com/o/r/issues/42)

## User Scenarios & Testing

### User Story 1 - Save a search (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a search, **When** I click Save, **Then** it appears in the sidebar
2. **Given** a saved search, **When** I click it, **Then** results load

---

### User Story 2 - Delete a search (Priority: P2)

**Acceptance Scenarios**:

1. **Given** a saved search, **When** I delete it, **Then** it disappears

## Success Criteria

- **SC-001**: Saving takes under 2 seconds
- **SC-002**: Saved searches survive a reload
"""

QUICKSTART = """# Quickstart

## Install dependencies

pnpm install

## Save your first search

Open the app...
"""


class TestExtractScenarios(unittest.TestCase):
    def test_ids_cover_stories_criteria_and_quickstart(self):
        ids = [s.id for s in vr.extract_scenarios(SPEC, QUICKSTART)]
        self.assertEqual(ids, ["US1-AS1", "US1-AS2", "US2-AS1", "SC-001", "SC-002", "QS-1", "QS-2"])

    def test_scenario_text_and_quickstart_headings(self):
        scenarios = {s.id: s.text for s in vr.extract_scenarios(SPEC, QUICKSTART)}
        self.assertIn("appears in the sidebar", scenarios["US1-AS1"])
        self.assertEqual(scenarios["QS-1"], "Install dependencies")

    def test_no_quickstart(self):
        ids = [s.id for s in vr.extract_scenarios(SPEC, None)]
        self.assertNotIn("QS-1", ids)

    def test_wrapped_scenarios_are_joined_and_do_not_end_the_list(self):
        spec = """### User Story 1 - Found via (Priority: P1)

**Acceptance Scenarios**:

1. **Given** a new job, **When** the job seeker selects "SEEK" and
   saves, **Then** the detail view shows "SEEK".
2. **Given** a new job, **When** the job seeker selects "Agency" and saves,
   **Then** the detail view shows that value, distinct from jobs
   the job seeker found themselves.
3. **Given** an existing job, **When** it is edited, **Then** the value is saved.

---
Trailing prose that is not a scenario.
"""
        scenarios = {s.id: s.text for s in vr.extract_scenarios(spec, None)}
        self.assertEqual(list(scenarios), ["US1-AS1", "US1-AS2", "US1-AS3"])
        self.assertEqual(
            scenarios["US1-AS2"],
            '**Given** a new job, **When** the job seeker selects "Agency" and saves, '
            "**Then** the detail view shows that value, distinct from jobs "
            "the job seeker found themselves.",
        )


class TestSourceIssue(unittest.TestCase):
    def test_header_line(self):
        self.assertEqual(vr.source_issue(SPEC), 42)

    def test_legacy_source_trailer(self):
        self.assertEqual(vr.source_issue("Title\n---\nSource: GitHub issue #7 — https://x"), 7)

    def test_sourced_from_mid_line(self):
        spec = '**Input**: User description: "Rename it. (Sourced from GitHub issue #240)"'
        self.assertEqual(vr.source_issue(spec), 240)

    def test_header_line_wins_over_sourced_from(self):
        self.assertEqual(vr.source_issue(f"{SPEC}\n(Sourced from GitHub issue #9)"), 42)

    def test_none(self):
        self.assertIsNone(vr.source_issue("# Feature Specification: X\n"))


class TestCheckReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name)
        self.scenarios = [vr.Scenario("US1-AS1", "a"), vr.Scenario("QS-1", "Install")]

    def tearDown(self):
        self.tmp.cleanup()

    def _record(self, sid):
        (self.run_dir / f"{sid}.webm").write_bytes(b"webm")

    def _check(self, *entries):
        return vr.check_report({"scenarios": list(entries)}, self.scenarios, self.run_dir)

    def test_complete_report_passes(self):
        self._record("US1-AS1")
        problems = self._check(
            {"id": "US1-AS1", "verdict": "PASS", "observed": "saw it", "evidence": []},
            {"id": "QS-1", "verdict": "NOT_APPLICABLE", "reason": "local setup only"},
        )
        self.assertEqual(problems, [])

    def test_missing_and_unknown_ids(self):
        problems = self._check({"id": "US9-AS9", "verdict": "PASS", "observed": "x"})
        self.assertIn("unknown scenario id 'US9-AS9'", problems)
        self.assertIn("US1-AS1: missing from report", problems)
        self.assertIn("QS-1: missing from report", problems)

    def test_pass_without_recording(self):
        problems = self._check(
            {"id": "US1-AS1", "verdict": "PASS", "observed": "saw it"},
            {"id": "QS-1", "verdict": "BLOCKED", "reason": "x"},
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("no recording", problems[0])

    def test_not_applicable_only_for_quickstart(self):
        problems = self._check(
            {"id": "US1-AS1", "verdict": "NOT_APPLICABLE", "reason": "x"},
            {"id": "QS-1", "verdict": "NOT_APPLICABLE", "reason": "x"},
        )
        self.assertEqual(
            problems, ["US1-AS1: only quickstart (QS-*) scenarios may be NOT_APPLICABLE"]
        )

    def test_blocked_needs_reason(self):
        problems = self._check(
            {"id": "US1-AS1", "verdict": "BLOCKED"},
            {"id": "QS-1", "verdict": "NOT_APPLICABLE", "reason": " "},
        )
        self.assertEqual(len(problems), 2)

    def test_missing_evidence_file(self):
        self._record("US1-AS1")
        problems = self._check(
            {"id": "US1-AS1", "verdict": "FAIL", "observed": "x", "evidence": ["/nope.png"]},
            {"id": "QS-1", "verdict": "NOT_APPLICABLE", "reason": "x"},
        )
        self.assertEqual(problems, ["US1-AS1: evidence file /nope.png does not exist"])

    def test_scenarios_can_share_a_recording(self):
        self.scenarios.append(vr.Scenario("SC-001", "b"))
        (self.run_dir / "insights.webm").write_bytes(b"webm")
        shared = {"verdict": "PASS", "observed": "ok", "recording": "insights.webm"}
        problems = self._check(
            {"id": "US1-AS1", **shared},
            {"id": "SC-001", **shared},
            {"id": "QS-1", "verdict": "NOT_APPLICABLE", "reason": "x"},
        )
        self.assertEqual(problems, [])

    def test_recording_must_be_a_webm_in_the_run_dir(self):
        for name in ("../elsewhere.webm", "/abs/x.webm", "notes.txt", 5):
            with self.subTest(name):
                problems = self._check(
                    {"id": "US1-AS1", "verdict": "PASS", "observed": "x", "recording": name},
                    {"id": "QS-1", "verdict": "NOT_APPLICABLE", "reason": "x"},
                )
                self.assertEqual(len(problems), 1)
                self.assertIn("must be a .webm file name", problems[0])

    def test_setup_steps_need_a_recording_and_a_description(self):
        self._record("US1-AS1")
        (self.run_dir / "setup-1.webm").write_bytes(b"webm")
        rest = (
            {"id": "US1-AS1", "verdict": "PASS", "observed": "x"},
            {"id": "QS-1", "verdict": "NOT_APPLICABLE", "reason": "x"},
        )
        good = {"recording": "setup-1.webm", "created": "jobs", "evidence": []}
        report = {"setup": [good], "scenarios": list(rest)}
        self.assertEqual(vr.check_report(report, self.scenarios, self.run_dir), [])
        report["setup"] = [
            {"created": "jobs"},
            {"recording": "setup-2.webm", "created": " ", "evidence": ["/nope.js"]},
        ]
        self.assertEqual(
            vr.check_report(report, self.scenarios, self.run_dir),
            [
                "setup step 1: needs a 'recording'",
                "setup step 2: needs a 'created' description of the data",
                f"setup step 2: no recording at {self.run_dir / 'setup-2.webm'}",
                "setup step 2: evidence file /nope.js does not exist",
            ],
        )

    def test_duplicate_and_bad_verdict(self):
        problems = self._check(
            {"id": "QS-1", "verdict": "NOT_APPLICABLE", "reason": "x"},
            {"id": "QS-1", "verdict": "NOT_APPLICABLE", "reason": "x"},
            {"id": "US1-AS1", "verdict": "MAYBE"},
        )
        self.assertIn("QS-1: reported more than once", problems)
        self.assertTrue(any("MAYBE" in p for p in problems))


class TestLoadReport(unittest.TestCase):
    def test_missing_and_malformed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "report.json"
            self.assertIsNone(vr.load_report(path)[0])
            path.write_text("{")
            self.assertIn("not valid JSON", vr.load_report(path)[1])
            path.write_text('{"scenarios": {}}')
            self.assertIsNone(vr.load_report(path)[0])
            path.write_text('{"scenarios": []}')
            self.assertEqual(vr.load_report(path), ({"scenarios": []}, None))


class TestRenderMarkdown(unittest.TestCase):
    def test_marker_summary_and_rows(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d)
            (run_dir / "US1-AS1.webm").write_bytes(b"x")
            (run_dir / "setup-1.webm").write_bytes(b"x")
            scenarios = [vr.Scenario("US1-AS1", "a | b"), vr.Scenario("QS-1", "Install")]
            report = {
                "setup": [{"recording": "setup-1.webm", "created": "two [validation] jobs"}],
                "scenarios": [
                    {"id": "US1-AS1", "verdict": "FAIL", "observed": "broken\nbadly"},
                    {"id": "QS-1", "verdict": "NOT_APPLICABLE", "reason": "local only"},
                ],
            }
            md = vr.render_markdown(report, scenarios, "a" * 40, "https://p.example", run_dir)
        self.assertTrue(md.startswith(vr.comment_marker("a" * 40)))
        self.assertIn("1 FAIL, 1 NOT_APPLICABLE", md)
        self.assertIn(f"- `{run_dir / 'setup-1.webm'}`: two [validation] jobs", md)
        self.assertIn("| US1-AS1 | FAIL | a \\| b | broken badly |", md)
        self.assertIn("| QS-1 | NOT_APPLICABLE | Install | local only | — |", md)
        self.assertEqual(vr.needs_attention(report), [report["scenarios"][0]])


if __name__ == "__main__":
    unittest.main()

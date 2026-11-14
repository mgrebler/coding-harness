"""Unit tests for agent_common/github_deployment_url.py with `gh api` mocked:
it waits through "no deployment yet", returns the success URL even after a
later inactive status, refuses production, and distinguishes a superseded
deploy from a failed one."""

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import github_deployment_url as gdu

SHA = "a" * 40


def _api(deployments_by_poll, statuses):
    """gh api stub: the Nth deployments poll returns deployments_by_poll[N]."""
    polls = iter(deployments_by_poll)

    def api(path):
        if "/statuses" in path:
            return statuses[int(path.split("/deployments/")[1].split("/")[0])]
        return next(polls)

    return api


class TestWait(unittest.TestCase):
    def _wait(self, api, remote_head=SHA, timeout_s=60):
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(gdu, "_gh_api", side_effect=api),
            patch.object(gdu, "remote_head", return_value=remote_head),
            patch.object(gdu.time, "sleep"),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            code = gdu.wait(SHA, "012-x", "Preview", timeout_s, 0)
        return code, out.getvalue(), err.getvalue()

    def test_waits_for_deployment_then_prints_url(self):
        preview = {"id": 1, "environment": "Preview - jobtracker", "created_at": "1"}
        api = _api(
            [[], [preview], [preview]],
            {1: [{"state": "success", "environment_url": "https://p.vercel.app"}]},
        )
        code, out, err = self._wait(api)
        self.assertEqual((code, out), (0, "https://p.vercel.app\n"))
        self.assertIn("yet", err)

    def test_success_survives_later_inactive_and_falls_back_to_target_url(self):
        preview = {"id": 1, "environment": "Preview", "created_at": "1"}
        api = _api(
            [[preview]],
            {1: [{"state": "inactive"}, {"state": "success", "target_url": "https://t"}]},
        )
        self.assertEqual(self._wait(api)[:2], (0, "https://t\n"))

    def test_production_is_never_matched(self):
        prod = {"id": 1, "environment": "Production - Preview-ish", "created_at": "1"}
        api = _api([[prod]] * 100, {1: [{"state": "success", "target_url": "https://prod"}]})
        code, out, err = self._wait(api, timeout_s=0)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("draft", err)

    def test_failure_on_current_head_is_a_failure(self):
        preview = {"id": 1, "environment": "Preview", "created_at": "1"}
        api = _api([[preview]], {1: [{"state": "failure"}]})
        self.assertEqual(self._wait(api)[0], 1)

    def test_failure_after_branch_moved_is_superseded(self):
        preview = {"id": 1, "environment": "Preview", "created_at": "1"}
        api = _api([[preview]], {1: [{"state": "error"}]})
        code, _, err = self._wait(api, remote_head="b" * 40)
        self.assertEqual(code, gdu.SUPERSEDED_EXIT_CODE)
        self.assertIn("superseded", err)


class TestMatchingDeployments(unittest.TestCase):
    def test_newest_first_case_insensitive(self):
        deployments = [
            {"id": 1, "environment": "preview", "created_at": "2026-01-01"},
            {"id": 2, "environment": "Preview - app", "created_at": "2026-02-01"},
            {"id": 3, "environment": "staging", "created_at": "2026-03-01"},
        ]
        ids = [d["id"] for d in gdu.matching_deployments(deployments, "Preview")]
        self.assertEqual(ids, [2, 1])


if __name__ == "__main__":
    unittest.main()

"""Unit tests for agent_common/git.py.

No dedicated tests exist yet — git.py's helpers (get_feature_from_branch,
run_auto_commit, get_changed_files) are currently only exercised indirectly
(mocked out) by tests in test_critic_loop.py. This file exists for
structural symmetry with the other agent_common submodules and as the
natural home for future direct coverage.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import git  # noqa: F401

if __name__ == "__main__":
    unittest.main()

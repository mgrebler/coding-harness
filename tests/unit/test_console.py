"""Unit tests for agent_common/console.py.

No dedicated tests exist yet — console.py's helpers (_Tee, setup_log_file,
make_logger, log_sdk_message) are currently only exercised indirectly by
manual runs of the *-auto.py orchestrators. This file exists for structural
symmetry with the other agent_common submodules and as the natural home for
future direct coverage.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import console  # noqa: F401

if __name__ == "__main__":
    unittest.main()

"""Unit tests for agent_common/local_tools.py — the tool runtime dispatched by
local_agent_loop.py. No network calls; all filesystem tools run against a
temp dir chdir'd to as the project root."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".claude/agents"))
from agent_common import local_tools


class _TempProjectRoot(unittest.TestCase):
    def setUp(self):
        self._orig_cwd = Path.cwd()
        self._tmpdir = tempfile.mkdtemp()
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._orig_cwd)


class TestResolveInRoot(_TempProjectRoot):
    def test_relative_path_resolves_under_root(self):
        resolved = local_tools._resolve_in_root("a/b.txt")
        self.assertEqual(resolved, Path(self._tmpdir).resolve() / "a" / "b.txt")

    def test_empty_path_raises(self):
        with self.assertRaises(local_tools.ToolError):
            local_tools._resolve_in_root("")

    def test_traversal_escape_raises(self):
        with self.assertRaises(local_tools.ToolError):
            local_tools._resolve_in_root("../../etc/passwd")

    def test_absolute_path_outside_root_raises(self):
        with self.assertRaises(local_tools.ToolError):
            local_tools._resolve_in_root("/etc/passwd")

    def test_absolute_path_inside_root_allowed(self):
        inside = str(Path(self._tmpdir).resolve() / "x.txt")
        resolved = local_tools._resolve_in_root(inside)
        self.assertEqual(resolved, Path(inside))


class TestToolRead(_TempProjectRoot):
    def test_reads_file_with_line_numbers(self):
        Path("f.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        result = local_tools.tool_read({"file_path": "f.txt"})
        self.assertEqual(result, "1\talpha\n2\tbeta\n3\tgamma")

    def test_offset_and_limit(self):
        Path("f.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")
        result = local_tools.tool_read({"file_path": "f.txt", "offset": 1, "limit": 2})
        self.assertEqual(result, "2\tb\n3\tc")

    def test_missing_file_raises(self):
        with self.assertRaises(local_tools.ToolError):
            local_tools.tool_read({"file_path": "missing.txt"})

    def test_directory_raises(self):
        Path("d").mkdir()
        with self.assertRaises(local_tools.ToolError):
            local_tools.tool_read({"file_path": "d"})

    def test_bad_offset_raises_tool_error(self):
        Path("f.txt").write_text("a\n", encoding="utf-8")
        with self.assertRaises(local_tools.ToolError):
            local_tools.tool_read({"file_path": "f.txt", "offset": "not-a-number"})


class TestToolWrite(_TempProjectRoot):
    def test_writes_content(self):
        local_tools.tool_write({"file_path": "out.txt", "content": "hello"})
        self.assertEqual(Path("out.txt").read_text(encoding="utf-8"), "hello")

    def test_creates_parent_dirs(self):
        local_tools.tool_write({"file_path": "a/b/c.txt", "content": "x"})
        self.assertEqual(Path("a/b/c.txt").read_text(encoding="utf-8"), "x")

    def test_overwrites_existing_file(self):
        Path("out.txt").write_text("old", encoding="utf-8")
        local_tools.tool_write({"file_path": "out.txt", "content": "new"})
        self.assertEqual(Path("out.txt").read_text(encoding="utf-8"), "new")


class TestToolEdit(_TempProjectRoot):
    def test_unique_replace(self):
        Path("f.txt").write_text("hello world", encoding="utf-8")
        local_tools.tool_edit({"file_path": "f.txt", "old_string": "world", "new_string": "there"})
        self.assertEqual(Path("f.txt").read_text(encoding="utf-8"), "hello there")

    def test_ambiguous_match_without_replace_all_raises(self):
        Path("f.txt").write_text("foo foo", encoding="utf-8")
        with self.assertRaises(local_tools.ToolError):
            local_tools.tool_edit({"file_path": "f.txt", "old_string": "foo", "new_string": "bar"})

    def test_replace_all_replaces_every_occurrence(self):
        Path("f.txt").write_text("foo foo foo", encoding="utf-8")
        local_tools.tool_edit(
            {
                "file_path": "f.txt",
                "old_string": "foo",
                "new_string": "bar",
                "replace_all": True,
            }
        )
        self.assertEqual(Path("f.txt").read_text(encoding="utf-8"), "bar bar bar")

    def test_not_found_raises(self):
        Path("f.txt").write_text("hello", encoding="utf-8")
        with self.assertRaises(local_tools.ToolError):
            local_tools.tool_edit(
                {"file_path": "f.txt", "old_string": "missing", "new_string": "x"}
            )

    def test_missing_file_raises(self):
        with self.assertRaises(local_tools.ToolError):
            local_tools.tool_edit({"file_path": "f.txt", "old_string": "a", "new_string": "b"})

    def test_identical_old_and_new_raises(self):
        Path("f.txt").write_text("hello", encoding="utf-8")
        with self.assertRaises(local_tools.ToolError):
            local_tools.tool_edit(
                {"file_path": "f.txt", "old_string": "hello", "new_string": "hello"}
            )

    def test_empty_old_string_raises(self):
        Path("f.txt").write_text("hello", encoding="utf-8")
        with self.assertRaises(local_tools.ToolError):
            local_tools.tool_edit({"file_path": "f.txt", "old_string": "", "new_string": "x"})


class TestToolGlob(_TempProjectRoot):
    def test_finds_matching_files(self):
        Path("specs").mkdir()
        Path("specs/a.md").write_text("x", encoding="utf-8")
        Path("specs/b.md").write_text("x", encoding="utf-8")
        Path("specs/c.txt").write_text("x", encoding="utf-8")
        result = local_tools.tool_glob({"pattern": "*.md", "path": "specs"})
        self.assertIn("specs/a.md", result)
        self.assertIn("specs/b.md", result)
        self.assertNotIn("c.txt", result)

    def test_no_matches(self):
        result = local_tools.tool_glob({"pattern": "*.nonexistent"})
        self.assertEqual(result, "(no matches)")

    def test_empty_pattern_raises(self):
        with self.assertRaises(local_tools.ToolError):
            local_tools.tool_glob({"pattern": ""})

    def test_skips_denylisted_dirs(self):
        Path(".git").mkdir()
        Path(".git/config.md").write_text("x", encoding="utf-8")
        Path("real.md").write_text("x", encoding="utf-8")
        result = local_tools.tool_glob({"pattern": "**/*.md"})
        self.assertIn("real.md", result)
        self.assertNotIn(".git", result)

    def test_truncates_past_max_results(self):
        for i in range(local_tools.MAX_GLOB_RESULTS + 5):
            Path(f"f{i}.md").write_text("x", encoding="utf-8")
        result = local_tools.tool_glob({"pattern": "*.md"})
        self.assertIn("truncated", result)


class TestToolGrep(_TempProjectRoot):
    def test_finds_matching_line(self):
        Path("f.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
        result = local_tools.tool_grep({"pattern": "def foo"})
        self.assertIn("f.py:1:def foo():", result)

    def test_case_insensitive(self):
        Path("f.py").write_text("HELLO world\n", encoding="utf-8")
        result = local_tools.tool_grep({"pattern": "hello", "case_insensitive": True})
        self.assertIn("f.py:1:HELLO world", result)

    def test_case_sensitive_by_default_no_match(self):
        Path("f.py").write_text("HELLO world\n", encoding="utf-8")
        result = local_tools.tool_grep({"pattern": "hello"})
        self.assertEqual(result, "(no matches)")

    def test_glob_filter(self):
        Path("f.py").write_text("target\n", encoding="utf-8")
        Path("f.md").write_text("target\n", encoding="utf-8")
        result = local_tools.tool_grep({"pattern": "target", "glob": "*.py"})
        self.assertIn("f.py", result)
        self.assertNotIn("f.md", result)

    def test_invalid_regex_raises(self):
        with self.assertRaises(local_tools.ToolError):
            local_tools.tool_grep({"pattern": "("})

    def test_skips_denylisted_dirs(self):
        Path(".git").mkdir()
        Path(".git/x.txt").write_text("needle", encoding="utf-8")
        Path("real.txt").write_text("needle", encoding="utf-8")
        result = local_tools.tool_grep({"pattern": "needle"})
        self.assertIn("real.txt", result)
        self.assertNotIn(".git", result)


class TestToolBash(_TempProjectRoot):
    def test_runs_command_and_captures_output(self):
        result = local_tools.tool_bash({"command": "echo hello"}, local_tools.BashSandboxConfig())
        self.assertIn("exit code: 0", result)
        self.assertIn("hello", result)

    def test_nonzero_exit_code_reported(self):
        result = local_tools.tool_bash({"command": "exit 3"}, local_tools.BashSandboxConfig())
        self.assertIn("exit code: 3", result)

    def test_runs_in_project_root_cwd(self):
        Path("marker.txt").write_text("x", encoding="utf-8")
        result = local_tools.tool_bash({"command": "ls"}, local_tools.BashSandboxConfig())
        self.assertIn("marker.txt", result)

    def test_empty_command_raises(self):
        with self.assertRaises(local_tools.ToolError):
            local_tools.tool_bash({"command": "  "}, local_tools.BashSandboxConfig())

    def test_timeout_raises_tool_error(self):
        with self.assertRaises(local_tools.ToolError):
            local_tools.tool_bash(
                {"command": "sleep 5"}, local_tools.BashSandboxConfig(timeout_s=1)
            )

    def test_denylisted_command_rejected_without_executing(self):
        with self.assertRaises(local_tools.ToolError):
            local_tools.tool_bash(
                {"command": "rm -rf / --no-preserve-root"}, local_tools.BashSandboxConfig()
            )
        self.assertTrue(Path(self._tmpdir).exists())

    def test_custom_deny_pattern_rejected(self):
        sandbox = local_tools.BashSandboxConfig(deny_patterns=[r"\bnpm\s+publish\b"])
        with self.assertRaises(local_tools.ToolError):
            local_tools.tool_bash({"command": "npm publish"}, sandbox)

    def test_output_truncated_to_max_bytes(self):
        sandbox = local_tools.BashSandboxConfig(output_max_bytes=10)
        result = local_tools.tool_bash({"command": "echo 0123456789abcdef"}, sandbox)
        self.assertIn("truncated", result)


class TestDispatch(_TempProjectRoot):
    def test_dispatches_to_read(self):
        Path("f.txt").write_text("hi\n", encoding="utf-8")
        result = local_tools.dispatch(
            "Read", {"file_path": "f.txt"}, local_tools.BashSandboxConfig()
        )
        self.assertEqual(result, "1\thi")

    def test_dispatches_to_bash(self):
        result = local_tools.dispatch(
            "Bash", {"command": "echo hi"}, local_tools.BashSandboxConfig()
        )
        self.assertIn("hi", result)

    def test_unknown_tool_returns_error_string_not_raise(self):
        result = local_tools.dispatch("Nope", {}, local_tools.BashSandboxConfig())
        self.assertTrue(result.startswith("Error:"))

    def test_tool_error_converted_to_error_string(self):
        result = local_tools.dispatch(
            "Read", {"file_path": "missing.txt"}, local_tools.BashSandboxConfig()
        )
        self.assertTrue(result.startswith("Error:"))

    def test_unexpected_exception_does_not_propagate(self):
        with patch.object(local_tools, "_TOOL_FUNCS", {"Read": lambda args: 1 / 0}):
            result = local_tools.dispatch(
                "Read", {"file_path": "f.txt"}, local_tools.BashSandboxConfig()
            )
        self.assertTrue(result.startswith("Error:"))


if __name__ == "__main__":
    unittest.main()

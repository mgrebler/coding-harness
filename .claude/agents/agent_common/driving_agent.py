"""
Shared options/prompt-suffix for internal driving-agent query() calls.

A "driving agent" is the outer query() call each ch_N_*_auto.py stage script
makes to delegate its actual work to exactly one purpose-built subagent (via
agents={...} and the Agent tool). These calls must never load project
filesystem settings (CLAUDE.md, the .claude/skills/ catalog) — all task
context is already injected directly into the subagent's own
AgentDefinition.prompt, and a driving agent that can see the ch-N-*-auto
skill catalog can mistake its own job description for an instruction to
shell out and re-invoke the very script that is currently running it.
"""

from claude_agent_sdk import ClaudeAgentOptions

NO_RECURSION_NOTICE = (
    "\n\nDo NOT invoke any `ch-*-auto` skill, and do NOT run "
    "`python .claude/agents/ch_*_auto.py` via Bash under any circumstances — "
    "you ARE that process already running. Delegate solely via the `Agent` "
    "tool using the subagent provided to you."
)


def driving_agent_options(allowed_tools: list[str], agents: dict) -> ClaudeAgentOptions:
    """
    Options for an internal driving-agent query() call that delegates to
    exactly one purpose-built subagent. setting_sources=[] (not omitted)
    isolates the driving agent from project filesystem settings — the SDK
    default of None loads all sources, so isolation requires an explicit [].
    """
    return ClaudeAgentOptions(
        allowed_tools=allowed_tools,
        agents=agents,
        setting_sources=[],
    )

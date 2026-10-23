"""Agentic tool-calling loop for local/external-model generation — the
generation counterpart to critic_loop.py/ollama.run_gate. Critics are pure
prompt-in/JSON-out (ollama.call_local_llm, no tools); generation needs
Read/Write/Edit/Bash/Glob/Grep access to actually author plan.md/tasks.md/
tests/implementation. Neither Ollama's nor an OpenAI-compatible endpoint's
wire protocol gives that to us for free the way the Claude Agent SDK does
for Claude, so this module is the from-scratch multi-turn tool-calling loop
that provides it, dispatched against agent_common/local_tools.py.

Canonical provider-agnostic message shape passed between turns (each
transport in ollama.py/openai_compatible.py translates to/from its own wire
format per call, since both HTTP APIs are stateless per request):

    {"role": "system"|"user", "content": str}
    {"role": "assistant", "content": str|None, "tool_calls": [{"id","name","arguments"}]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}

Lives in its own module rather than ollama.py to avoid a circular import:
this needs ollama's transport (call_configured_llm_turn), so ollama.py
cannot import this module."""

import hashlib
import sys
from collections.abc import Callable
from pathlib import Path

from claude_agent_sdk import AgentDefinition, query

from agent_common import console, local_tools, ollama
from agent_common.driving_agent import NO_RECURSION_NOTICE, driving_agent_options

AUTHORING_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]

_DEFAULT_MAX_TURNS = 40

# Some reasoning models never produce a turn with zero tool_calls even after
# the task is genuinely done — observed as an endless Write→Read→Grep→Bash
# "re-verify my own prior edit" cycle that just burns the turn budget instead
# of ever stopping (doubling max_turns only doubled the wasted turns). This
# is a deterministic circuit breaker for that: if _NO_PROGRESS_TURNS
# consecutive turns pass with no tool call that actually changes a file's
# content, treat the model as stuck rather than waiting for max_turns.
# Mirrors critic_loop.py's own _NO_PROGRESS_THRESHOLD pattern for stuck
# critic loops.
_DEFAULT_NO_PROGRESS_TURNS = 6


class LocalAgentError(Exception):
    """Raised when the local agentic loop exhausts its turn budget
    (max_turns) without the model stopping on its own — i.e. without a turn
    that returns no tool_calls, and without tripping the no-progress circuit
    breaker (see _DEFAULT_NO_PROGRESS_TURNS) either. run_generation treats it
    like any other local failure and falls back to Claude."""


# Stages whose local model already failed in this process. The transport has
# retried by the time a failure surfaces, so retrying the same backend on every
# later revision/fix call would only re-pay those timeouts before each fallback.
_local_generation_failed: set[str] = set()


def _build_sandbox(config: dict) -> local_tools.BashSandboxConfig:
    kwargs = {}
    if "command_timeout_s" in config:
        kwargs["timeout_s"] = config["command_timeout_s"]
    if "output_max_bytes" in config:
        kwargs["output_max_bytes"] = config["output_max_bytes"]
    if "deny_patterns" in config:
        kwargs["deny_patterns"] = config["deny_patterns"]
    return local_tools.BashSandboxConfig(**kwargs)


def _log_tool_call(log, name: str, arguments: dict) -> None:
    preview = ", ".join(f"{k}={str(v)[:80]!r}" for k, v in arguments.items())
    log(f"  → {name}({preview})")


def _tracks_progress(call: dict, result: str, last_write_content: dict[str, str]) -> bool:
    """Update last_write_content for a Write call and report whether this
    single tool call changed a file's content — a Write whose content
    differs from what was last written to that path (first write counts), or
    a successful Edit (tool_edit rejects old_string == new_string, so a
    successful Edit always changes something)."""
    if call["name"] == "Write":
        path = call["arguments"].get("file_path", "")
        content = call["arguments"].get("content", "")
        changed = last_write_content.get(path) != content
        last_write_content[path] = content
        return changed
    return call["name"] == "Edit" and result.startswith("edited ")


async def run_local_agent_loop(log, system_prompt: str, user_prompt: str, config: dict) -> str:
    """
    Drive the multi-turn tool-calling loop against the configured local/
    external model (config, as resolved by
    ollama.load_local_llm_generation_config) until it stops calling tools,
    returning its final text content.

    Termination: either of two signals ends the loop successfully —
    (1) a turn with no tool_calls, the same signal the Claude Agent SDK's own
    internal loop relies on (no dedicated 'finish'/'done' tool is needed), or
    (2) the no-progress circuit breaker: _DEFAULT_NO_PROGRESS_TURNS
    consecutive turns with no tool call that actually changes a file's
    content, which catches reasoning models that keep calling tools
    (re-reading/re-verifying their own prior edits) without ever emitting a
    genuinely empty tool_calls turn. Raises LocalAgentError only if max_turns
    is exhausted with neither signal having fired.

    Turns are non-streaming (see ollama.call_local_llm_turn /
    openai_compatible.call_openai_compatible_turn): simpler, more robust
    tool_call parsing over live token progress, at the cost of only a
    per-turn heartbeat rather than true streaming.
    """
    sandbox = _build_sandbox(config)
    max_turns = config.get("max_turns", _DEFAULT_MAX_TURNS)
    no_progress_turns = config.get("no_progress_turns", _DEFAULT_NO_PROGRESS_TURNS)

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_write_content: dict[str, str] = {}
    stalled_turns = 0

    for turn in range(1, max_turns + 1):
        response = ollama.call_configured_llm_turn(messages, config, local_tools.TOOLS_SCHEMA)
        tool_calls = response.get("tool_calls") or []

        if not tool_calls:
            content = (response.get("content") or "").strip()
            if content:
                log(
                    f"[local-agent] turn {turn}: model stopped calling tools — done. "
                    f"Final message:\n{content[:2000]}"
                )
            else:
                log(
                    f"[local-agent] turn {turn}: model stopped calling tools — done (empty content)."
                )
            return content

        messages.append(
            {"role": "assistant", "content": response.get("content"), "tool_calls": tool_calls}
        )
        made_progress = False
        for call in tool_calls:
            _log_tool_call(log, call["name"], call["arguments"])
            result = local_tools.dispatch(call["name"], call["arguments"], sandbox)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": call["name"],
                    "content": result,
                }
            )
            made_progress |= _tracks_progress(call, result, last_write_content)
        log(f"[local-agent] turn {turn}: {len(tool_calls)} tool call(s) executed.")

        if made_progress:
            stalled_turns = 0
        elif last_write_content:  # only start counting once real work has begun
            stalled_turns += 1
            if stalled_turns >= no_progress_turns:
                log(
                    f"[local-agent] turn {turn}: no file-content-changing tool call in "
                    f"{stalled_turns} consecutive turns — treating the model as stuck "
                    "re-verifying already-complete work rather than waiting for max_turns. "
                    "Returning last known state."
                )
                return (response.get("content") or "").strip()

    raise LocalAgentError(
        f"local agentic loop exhausted max_turns={max_turns} without the model finishing"
    )


async def run_generation(
    log,
    stage: str,
    claude_fallback: Callable[[str | None], object],
    system_prompt: str,
    user_prompt: str,
) -> None:
    """
    Generation counterpart to ollama.run_gate: if a local/external model is
    configured for `stage` (.specify/pipeline.json's top-level
    "generation.<stage>"), run the local agentic loop against it; otherwise
    fall back to the existing Claude Agent SDK path — the same
    no-local-backend -> Claude-fallback semantics run_gate uses for critics,
    now also resolving which Claude model (if any) is configured for this
    stage. One config per stage covers every agent role for that stage
    (initial generation, revision, fix, CI-fix all call this the same way).

    If the local loop fails, the same call is re-run on Claude, which picks up
    from whatever the local model left on disk, and the stage stays on Claude
    for the rest of this process.

    claude_fallback: 1-arg callable (the resolved Claude model, or None to
    inherit the invoking session's default) returning the async iterator of
    SDK messages, e.g. `lambda model: query(prompt=..., options=driving_agent_options(..., model=model))`.
    Invoked when no local generation config resolves for `stage`, or the local
    loop failed.
    """
    protected_before = _protected_file_digests()
    config = ollama.load_local_llm_generation_config(stage)
    if config is not None and stage in _local_generation_failed:
        log(f"Local LLM failed earlier for {stage} generation — using Claude.")
        config = None
    if config is not None:
        log(f"Using local LLM ({config['model']}) for {stage} generation...")
        try:
            await run_local_agent_loop(log, system_prompt, user_prompt, config)
        except Exception as e:
            log(
                f"WARNING: local LLM ({config['model']}) failed for {stage} generation: {e} "
                "— falling back to Claude for the rest of this run."
            )
            _local_generation_failed.add(stage)
            config = None
    if config is None:
        claude_model = ollama.load_claude_generation_model(stage)
        await console.stream_query(claude_fallback(claude_model))
    _halt_if_protected_files_touched(log, protected_before)


async def run_agent(
    log,
    stage: str,
    agent_name: str,
    make_agent: Callable[[str | None], AgentDefinition],
    user_prompt: str,
) -> None:
    """Run one authoring/fix agent for `stage` via run_generation: the local
    model gets make_agent(None).prompt as its system prompt; the Claude path
    drives agent_name, built by make_agent(model), as a subagent."""
    await run_generation(
        log,
        stage,
        claude_fallback=lambda model: query(
            prompt=user_prompt + NO_RECURSION_NOTICE,
            options=driving_agent_options(
                allowed_tools=[*AUTHORING_TOOLS, "Agent"],
                agents={agent_name: make_agent(model)},
                model=model,
            ),
        ),
        system_prompt=make_agent(None).prompt,
        user_prompt=user_prompt,
    )


# Files only a human may write: escalation reviews are proof of human review
# (apply_escalation_review grants iterations on their strength alone), and
# .specify/memory/ is project governance (constitution, architecture, decision
# logs) whose amendments need real human approval — specs/003 had a fix agent
# bump the constitution and log an "approved (<human>)" decision-log entry.
_PROTECTED_GLOBS = ("specs/*/*-escalation*.md", ".specify/memory/**/*")


def _protected_file_digests() -> dict[str, str]:
    return {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for pattern in _PROTECTED_GLOBS
        for path in Path().glob(pattern)
        if path.is_file()
    }


def _halt_if_protected_files_touched(log, before: dict[str, str]) -> None:
    """Every agent run (generation, revision, fix, CI-fix) passes through here,
    so this is the one place an agent writing a human-only file can be caught,
    regardless of what its prompt said. Halting without deleting anything: the
    agent may already have committed the file, and only a human should decide
    what to revert."""
    after = _protected_file_digests()
    touched = sorted(
        path for path in before.keys() | after.keys() if before.get(path) != after.get(path)
    )
    if not touched:
        return
    log(
        "FAIL: an agent created, modified, or deleted a human-only file (escalation review "
        "or .specify/memory/ governance) — these may only be written by a human. Halting; "
        "human review required."
    )
    for path in touched:
        log(f"  {path}")
    log(
        "Inspect each file (and any commit that touched it), restore or remove it by hand, "
        "then re-run. A forged *-escalation-review.md left in place will be treated as a "
        "real human review on the next run."
    )
    sys.exit(1)

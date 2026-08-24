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

from collections.abc import Callable

from agent_common import console, local_tools, ollama

_DEFAULT_MAX_TURNS = 40


class LocalAgentError(Exception):
    """Raised when the local agentic loop exhausts its turn budget
    (max_turns) without the model stopping on its own — i.e. without a turn
    that returns no tool_calls. Callers don't need to catch this specially:
    every *-auto.py generation call site already checks for its expected
    artifact on disk immediately after generation and exits(1) if it's
    missing, which is the correct behavior here too."""


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


async def run_local_agent_loop(log, system_prompt: str, user_prompt: str, config: dict) -> str:
    """
    Drive the multi-turn tool-calling loop against the configured local/
    external model (config, as resolved by
    ollama.load_local_llm_generation_config) until it stops calling tools,
    returning its final text content.

    Termination: a turn with no tool_calls is the signal the model is
    done — the same signal the Claude Agent SDK's own internal loop relies
    on. No dedicated 'finish'/'done' tool is needed. Raises LocalAgentError
    if max_turns is exhausted first.

    Turns are non-streaming (see ollama.call_local_llm_turn /
    openai_compatible.call_openai_compatible_turn): simpler, more robust
    tool_call parsing over live token progress, at the cost of only a
    per-turn heartbeat rather than true streaming.
    """
    sandbox = _build_sandbox(config)
    max_turns = config.get("max_turns", _DEFAULT_MAX_TURNS)

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

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
        log(f"[local-agent] turn {turn}: {len(tool_calls)} tool call(s) executed.")

    raise LocalAgentError(
        f"local agentic loop exhausted max_turns={max_turns} without the model finishing"
    )


async def run_generation(
    log,
    stage: str,
    claude_fallback: Callable,
    system_prompt: str,
    user_prompt: str,
) -> None:
    """
    Generation counterpart to ollama.run_gate: if a local/external model is
    configured for `stage` (.specify/local-llm.json's top-level
    "generation.<stage>"), run the local agentic loop against it; otherwise
    fall back to the existing Claude Agent SDK path unconditionally — the
    same 0-configured -> Claude-fallback semantics run_gate uses for
    critics. One config per stage covers every agent role for that stage
    (initial generation, revision, fix, CI-fix all call this the same way).

    claude_fallback: zero-arg callable returning the async iterator of SDK
    messages, e.g. `lambda: query(prompt=..., options=...)`. Only invoked
    when no local generation config resolves for `stage`.
    """
    config = ollama.load_local_llm_generation_config(stage)
    if config is None:
        await console.stream_query(claude_fallback())
        return

    log(f"Using local LLM ({config['model']}) for {stage} generation...")
    await run_local_agent_loop(log, system_prompt, user_prompt, config)

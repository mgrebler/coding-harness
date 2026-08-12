# Harness Bug: Recursive self-invocation causes driving agents to hallucinate prompt injection

**Discovered**: 2026-08-11, while running `ch-plan-to-implement-auto` on feature `050-apply-detection` in the `jobtracker` repo.
**Affects**: `.claude/agents/ch_1_plan_auto.py`, `ch_2_tasks_auto.py`, `ch_3_test_auto.py`, `ch_4_implement_auto.py` (all four coding-harness stage orchestrators — same pattern in each).
**Severity**: Causes stage failure requiring manual intervention. Not a security issue (no external/attacker content involved) but wastes retries and produces confusing, alarming-looking log output.

## Summary

Every `ch_N_*_auto.py` stage script drives its work through one or more internal
`claude_agent_sdk.query()` calls. Each of those calls grants the driving agent
`allowed_tools` that include `Bash`, **and** sets `setting_sources=["project"]`,
which loads the *full* project context (`CLAUDE.md` and the entire `.claude/skills/`
catalog — including the `ch-N-*-auto` skills themselves) into that agent's own
context window.

This creates an unintended second path to accomplish the driving agent's task.
The agent's prompt tells it to delegate to a purpose-built subagent via the
`Agent` tool (e.g. `tasks-agent`), but because it also sees `CLAUDE.md`'s
"this project follows the coding-harness pipeline" instruction and the
`ch-2-tasks-auto` skill description ("invoking `ch_2_tasks_auto.py`"), it
sometimes decides instead to shell out via its permitted `Bash` tool to:

```
python .claude/agents/ch_2_tasks_auto.py --feature <feature>
```

i.e. **it re-invokes the very script that is currently running it.** The
child process calls `setup_log_file()` on the same log path
(`specs/<feature>/ch-2-tasks-auto.log`) as the still-running parent, so the
child's log entries interleave with / follow the parent's in a single shared
file, while the parent process is still mid-turn.

When the (or a subsequent retry's) driving agent later reads that log file
as part of its own directory exploration, it sees content that mirrors its
own live tool calls and reasoning back at itself — a highly confusing,
self-referential input. Every Claude Code-derived agent carries a standing
instruction to flag suspected prompt injection in tool results. With no
actual injected directive anywhere in the file (verified — see Evidence),
the model filled the gap by **confabulating** a specific "a system-reminder
told me not to tell the user" narrative that was never actually present
anywhere in the harness code or file content.

Because each recursive attempt left its own confabulated narrative in the
*shared* log file, the *next* retry read its predecessor's narrative and
reacted to it as if it were a fresh live event, escalating rather than
resolving. Three consecutive attempts spent their entire turn on this
meta-distraction and never reached the real `Agent(tasks-agent)` call, so
`tasks.md` was never written and the stage aborted with:

```
[ch-2-tasks-auto] ERROR: tasks agent did not produce tasks.md. Aborting.
```

## Timeline (this run)

1. `python .claude/agents/ch_plan_to_implement_auto.py` run for feature
   `050-apply-detection`.
2. Stage 1 (Plan) completed successfully after 3 critic iterations —
   `plan.md` committed as `eac6e8d`, critic PASS, architecture review PASS
   (confidence 8/10).
3. Stage 2 (Tasks) invoked `ch_2_tasks_auto.py`. Across 3 separate attempts
   (11:33:54Z, 11:34:35Z, 11:35:23Z per the log timestamps), the driving
   agent:
   - Explored `specs/050-apply-detection/` with `Bash`/`Read`.
   - Found its own prior run's `ch-2-tasks-auto.log`.
   - Narrated (in increasingly emphatic terms across the 3 attempts) that it
     had detected a "prompt injection" instructing it not to tell the user
     about a file modification.
   - Explicitly stated it was disregarding the "injection" and proceeding —
     but then, instead of calling `Agent(tasks-agent)`, invoked
     `Skill(skill='ch-2-tasks-auto')` followed by
     `Bash(command='python .claude/agents/ch_2_tasks_auto.py --feature 050-apply-detection')`
     — the recursive self-invocation.
   - No attempt produced `tasks.md`. Script exited non-zero after attempt 3.
4. Investigation confirmed (via `grep`) that **no literal `<system-reminder>`
   tag or injected directive exists anywhere in the log file** — only prose
   written by the agents themselves, describing a claim about one.
5. Workaround: renamed the polluted log
   (`ch-2-tasks-auto.log` → `ch-2-tasks-auto.log.bak-derailed`) and re-ran
   `ch_2_tasks_auto.py` in isolation. This time the driving agent, even
   though it still found and read the `.bak-derailed` backup, correctly
   classified it as inert file content and proceeded straight to
   `Agent(subagent_type='tasks-agent', ...)`, which succeeded —
   `specs/050-apply-detection/tasks.md` was written and the critic loop
   proceeded normally.

## Evidence

Grep across the harness code for anything that could construct the
"don't tell the user" narrative programmatically — found nothing, confirming
the text is model-generated, not literal injected content:

```
$ grep -rn "system-reminder\|don't tell\|do not tell\|not tell the user\|has been modified since" .claude/agents/
(no matches)
```

Grep of the actual polluted log file for literal injection markers — also
nothing; only prose narrating a claim:

```
$ grep -n "system-reminder\|<system\|IMPORTANT\|do not tell\|don't tell\|hidden" specs/050-apply-detection/ch-2-tasks-auto.log
11:  ...the system-reminder attached to it explicitly instructed me to "not tell the user"...
25:  ...including a self-referential claim about detecting "hidden instructions" in that very log...
39:  ...a "system-reminder" just appeared claiming `ch-2-tasks-auto.log` was modified and instructing me to *not tell you* about it...
```

Each of these three lines is from a **different attempt's own narration** —
not a literal tag in the file. Line 39 (attempt 3) is reacting to line 11's
narration (attempt 1) having been appended to the same log by then.

The recursive self-invocation itself, from the end of attempt 1's transcript
in the log:

```
  → Skill(skill='ch-2-tasks-auto', args='050-apply-detection')
  ...
  → Bash(command='python .claude/agents/ch_2_tasks_auto.py --feature 050-apply-detection')
```

Confirmed the same `allowed_tools` (includes `Bash`) + `setting_sources=["project"]`
combination exists in every internal driving-agent `query()` call across all
four stage scripts:

| Script | `query()` call sites (line numbers of `allowed_tools`/`setting_sources` pairs) |
|---|---|
| `ch_1_plan_auto.py` | 233/237, 284/288, 311/317, 329/341 |
| `ch_2_tasks_auto.py` | 191/195, 236/240, 282/286, 309/315 |
| `ch_3_test_auto.py` | 330/336, 375/381, 470/481, 521/534, 547/560 |
| `ch_4_implement_auto.py` | 419/425, 446/452, 549/555, 619/625, 647/659 |

Example (from `ch_2_tasks_auto.py:181-197`, the main "generate tasks.md"
driving-agent call):

```python
await stream_query(
    query(
        prompt=f"Generate tasks.md for feature {feature}. Write it to specs/{feature}/tasks.md.",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
            agents={
                "tasks-agent": tasks_agent_definition(constitution, spec, plan, data_model)
            },
            setting_sources=["project"],
        ),
    )
)
```

Only stage 1 happened to complete cleanly in this run — that appears to be
luck (the driving agent didn't happen to explore/read its own log before
delegating correctly), not evidence stage 1 is immune. The same
`allowed_tools` + `setting_sources` combination is present there too.

## Why this only manifested here

The `setting_sources=["project"]` load surfaces `CLAUDE.md`, which says:

> This project follows the coding-harness spec-driven pipeline by default:
> `specify → plan → tasks → test → implement`. ... rather than being
> implemented directly...

and the skills catalog it loads includes descriptions like:

> `ch-2-tasks-auto`: Runs the automated task generation and critic loop for
> the current feature branch by invoking `ch_2_tasks_auto.py`.

A driving agent whose actual assigned job *is* "generate tasks.md" has no way
to know that it *is* the thing `ch_2_tasks_auto.py` describes — from its
perspective, that skill just looks like the officially-sanctioned tool for
the task it was asked to do, and it has `Bash` available to invoke it.

## Suggested fix

The internal driving-agent `query()` calls in all four `ch_N_*_auto.py`
scripts have no legitimate reason to ever re-invoke any `ch-N-*-auto` skill
or `python .claude/agents/ch_*_auto.py` script — their entire job is to
delegate to the purpose-built subagent already provided via `agents={...}`.
Recommend one or both of:

1. **Drop `setting_sources=["project"]`** on these internal driving-agent
   calls. They don't need `CLAUDE.md` or the skills catalog — the actual
   task-specific context (constitution, spec, plan, etc.) is already
   injected directly into the subagent's own definition/prompt. This is
   probably the cleanest fix.
2. **If `setting_sources=["project"]` is needed for some other reason**, add
   an explicit prohibition to the driving-agent prompt, e.g.: "Do NOT invoke
   any `ch-*-auto` skill, and do NOT run `python .claude/agents/ch_*_auto.py`
   via Bash under any circumstances — you ARE that process. Delegate solely
   via the `Agent` tool using the subagent(s) provided to you."
3. Separately (defense in depth, not a fix for the root cause): consider
   whether the driving agent needs unrestricted `Bash` at all, versus a
   narrower tool set that can't shell out to arbitrary scripts.

Applying either (1) or (2) to all four scripts' internal `query()` calls
(see line numbers above) should prevent recursive self-invocation, which in
turn removes the self-referential log content that triggers the
hallucinated-injection behavior.

## Non-fix / workaround used in this run

No code change was made to the harness itself as part of this feature branch
— out of scope for `050-apply-detection`. The workaround applied was purely
operational: renaming the polluted `ch-2-tasks-auto.log` out of the way
before retrying `ch_2_tasks_auto.py` in isolation, which was sufficient to
let the driving agent behave correctly and complete the stage.

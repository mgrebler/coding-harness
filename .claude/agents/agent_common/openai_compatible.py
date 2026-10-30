"""OpenAI-compatible chat-completions transport (NVIDIA NIM, OpenAI, Groq,
Together.ai, etc — any endpoint speaking the standard /v1/chat/completions
wire protocol). Sibling to ollama.py's call_local_llm: same call contract,
for drop-in dispatch from run_local_critic_cli based on config["provider"]."""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_SSE_DONE = "[DONE]"


class TruncatedResponseError(Exception):
    """The provider cut the response off (finish_reason == "length") before it
    could complete — distinct from a generic JSON-parse failure so callers can
    retry instead of treating it as a critic/model malfunction. See
    FOLLOWUP_HARNESS.md Bug 1: an unset max_tokens let a provider's own hidden
    per-model cap truncate critic output mid-JSON, and the resulting
    json.JSONDecodeError gave no hint that a token budget (not a parsing bug)
    was the cause.

    partial_content carries whatever was streamed before the cutoff, so a
    caller can still persist the near-miss verdict (e.g. a FAIL with BLOCKING
    violations cut off mid-string) instead of losing it to a bare exit."""

    def __init__(self, message: str, partial_content: str = ""):
        super().__init__(message)
        self.partial_content = partial_content


# Transient failures worth retrying: rate limits and upstream server errors.
# Everything else (401/403/404/410/etc) is permanent — retrying a dead model
# or bad auth just wastes time, so those raise immediately.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 5
# A read timeout re-sends the whole prompt and waits the full timeout again;
# specs/005-006 lost ~26 minutes per critic to 5 of them in a row, and a dead
# gemma-4-31b endpoint in jobtracker 088 still cost ~15 minutes per call at 2.
# One retry keeps the timeout-then-succeed recovery seen in specs/006.
_MAX_TIMEOUT_RETRIES = 1
_BASE_DELAY_S = 2.0
_MAX_DELAY_S = 60.0


def _retry_delay_s(attempt: int, retry_after: str | None) -> float:
    """Seconds to wait before the next attempt. Honors a numeric Retry-After
    header when present, otherwise exponential backoff capped at _MAX_DELAY_S."""
    if retry_after:
        try:
            return min(float(retry_after), _MAX_DELAY_S)
        except ValueError:
            pass
    return min(_BASE_DELAY_S * (2**attempt), _MAX_DELAY_S)


class StreamError(RuntimeError):
    """An error the provider reported as an SSE data chunk inside an HTTP 200
    stream rather than as an HTTP status — e.g. NVIDIA's `{"error": {"message":
    "ResourceExhausted: Worker local total request limit reached", "code":
    500}}` for poolside/laguna-xs-2.1, which previously parsed as an empty
    response and failed the critic."""

    def __init__(self, code, message: str):
        super().__init__(f"provider error {code} mid-stream: {message}")
        self.code = code

    @property
    def retryable(self) -> bool:
        try:
            return int(self.code) in _RETRYABLE_STATUS
        except (TypeError, ValueError):
            return False


def _call_with_retry(attempt_fn):
    """Run attempt_fn() with retry/backoff on transient HTTP errors (429, 5xx),
    on the same statuses reported mid-stream (StreamError), and on transient
    network failures (read timeouts, dropped connections, DNS blips). The
    network failures aren't HTTP responses at all — they surface as
    TimeoutError/URLError — so they're handled in a separate except clause
    from HTTPError (a URLError subclass) and always retried, unlike HTTPError
    where only specific status codes are worth retrying. attempt_fn must
    consume a streamed body itself, so a mid-stream error re-sends the whole
    request."""
    timeouts = 0
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return attempt_fn()
        except urllib.error.HTTPError as e:
            if e.code not in _RETRYABLE_STATUS or attempt == _MAX_RETRIES:
                raise
            delay = _retry_delay_s(attempt, e.headers.get("Retry-After"))
            print(
                f"[openai-compatible] HTTP {e.code} {e.reason} — retrying in "
                f"{delay:.0f}s (attempt {attempt + 1}/{_MAX_RETRIES})",
                file=sys.stderr,
            )
            time.sleep(delay)
        except StreamError as e:
            if not e.retryable or attempt == _MAX_RETRIES:
                raise
            delay = _retry_delay_s(attempt, None)
            print(
                f"[openai-compatible] {e} — retrying in "
                f"{delay:.0f}s (attempt {attempt + 1}/{_MAX_RETRIES})",
                file=sys.stderr,
            )
            time.sleep(delay)
        except (TimeoutError, urllib.error.URLError) as e:
            timeouts += isinstance(e, TimeoutError)
            if attempt == _MAX_RETRIES or timeouts > _MAX_TIMEOUT_RETRIES:
                raise
            delay = _retry_delay_s(attempt, None)
            print(
                f"[openai-compatible] {type(e).__name__}: {e} — retrying in "
                f"{delay:.0f}s (attempt {attempt + 1}/{_MAX_RETRIES})",
                file=sys.stderr,
            )
            time.sleep(delay)
    raise RuntimeError("unreachable: retry loop exited without returning or raising")


def _urlopen_with_retry(req: urllib.request.Request, timeout: int = 300):
    return _call_with_retry(lambda: urllib.request.urlopen(req, timeout=timeout))  # noqa: S310 — see _build_request


def _load_dotenv(path: str = ".env") -> None:
    """Populate os.environ from a KEY=VALUE .env file in the current working
    directory, without overriding a variable already set to a non-empty
    value in the environment. No-op if the file doesn't exist — .env is an
    optional convenience, not a required config source (the shell/CI
    environment is the source of truth). An empty-string existing value
    (e.g. a devcontainer/docker-compose `environment:` block that declares
    the var as a host pass-through but leaves it blank when unset on the
    host) is treated as unset rather than as a real override — plain
    os.environ.setdefault would otherwise silently keep it blank and ignore
    a real key present in .env."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and not os.environ.get(key):
            os.environ[key] = value


def _build_request(prompt: str, config: dict) -> urllib.request.Request:
    """Build the /chat/completions streaming request. response_format is
    json_object (rather than Ollama's format="json") since that's the field
    this wire protocol uses for the same "raw JSON out" guarantee — on unless
    the entry sets json_mode: false."""
    _load_dotenv()
    api_key_env = config["api_key_env"]
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise RuntimeError(
            f"Environment variable {api_key_env} is not set (required for provider "
            "'openai-compatible' — pipeline.json only names the env var, never "
            "the key itself)"
        )

    body: dict = {
        "model": config["model"],
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "temperature": config.get("temperature", 0.1),
    }
    if config.get("json_mode", True):
        body["response_format"] = {"type": "json_object"}
    if config.get("num_predict"):
        body["max_tokens"] = config["num_predict"]
    payload = json.dumps(body).encode("utf-8")

    return urllib.request.Request(  # noqa: S310 — scheme validated in load_local_llm_config
        f"{config['base_url']}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream",
        },
        method="POST",
    )


class ContextOverflowError(RuntimeError):
    """The prompt exceeds the model's context window — deterministic, so never
    worth retrying with the same prompt (specs/084: 454k tokens sent to a
    262k-token model)."""


def _parse_sse_chunk(data: str) -> tuple[str, str, bool, int | None] | None:
    """Parse one SSE data line's JSON chunk into (content, reasoning,
    is_truncated, completion_tokens), or None if it is malformed or a
    keep-alive with neither choices nor usage. Raises StreamError for an
    in-stream error chunk."""
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError:
        return None
    error = chunk.get("error")
    if error:
        if isinstance(error, dict):
            raise StreamError(error.get("code"), error.get("message", ""))
        raise StreamError(None, str(error))
    usage = (chunk.get("usage") or {}).get("completion_tokens")
    choices = chunk.get("choices")
    if not choices:
        return ("", "", False, usage) if usage else None
    delta = choices[0].get("delta") or {}
    return (
        delta.get("content") or "",
        delta.get("reasoning_content") or "",
        choices[0].get("finish_reason") == "length",
        usage,
    )


def _iter_sse_chunks(resp):
    """Yield each parsed SSE data chunk (see _parse_sse_chunk) until [DONE].
    Skips chunks with nothing usable (observed as a leading keep-alive chunk
    with an empty choices array before content starts)."""
    for raw_line in resp:
        line = raw_line.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == _SSE_DONE:
            return
        parsed = _parse_sse_chunk(data)
        if parsed is not None:
            yield parsed


class _StreamTiming:
    """Silence measurements for one streamed attempt. The socket read timeout
    resets on any bytes received, so the longest silence — not total duration —
    is what a shorter timeout would have to clear; logged so that value can be
    chosen from observed data rather than guessed."""

    def __init__(self):
        self.sent = self.last = time.monotonic()
        self.headers_s: float | None = None
        self.longest_silence_s = 0.0
        self.lines = 0

    def _mark(self) -> None:
        now = time.monotonic()
        self.longest_silence_s = max(self.longest_silence_s, now - self.last)
        self.last = now

    def headers_received(self) -> None:
        self._mark()
        self.headers_s = self.last - self.sent

    def timed(self, resp):
        for line in resp:
            self._mark()
            self.lines += 1
            yield line

    def report(self, outcome: str) -> None:
        total = time.monotonic() - self.sent
        if self.headers_s is None:
            detail = "no response headers"
        else:
            detail = (
                f"headers after {self.headers_s:.0f}s, longest silence "
                f"{self.longest_silence_s:.0f}s across {self.lines} lines"
            )
        print(f"[openai-compatible] {outcome} after {total:.0f}s: {detail}", file=sys.stderr)


def _read_stream(lines, start: float, progress_fn, progress_interval: int):
    """Accumulate one attempt's SSE lines into (content_parts, reasoning_parts,
    content_chunk_count, reported_tokens, truncated), emitting heartbeats."""
    content_parts = []
    reasoning_parts = []
    chunk_count = 0
    reasoning_count = 0
    reported_tokens = None
    truncated = False
    for token, reasoning, chunk_truncated, usage in _iter_sse_chunks(lines):
        truncated = truncated or chunk_truncated
        reported_tokens = usage or reported_tokens
        reasoning_parts.append(reasoning)
        if reasoning:
            reasoning_count += 1
            if reasoning_count % progress_interval == 0:
                print(
                    f"[openai-compatible] reasoning... {reasoning_count} chunks "
                    f"({time.monotonic() - start:.0f}s elapsed)",
                    file=sys.stderr,
                )
        if token:
            content_parts.append(token)
            chunk_count += 1
            if progress_fn and chunk_count % progress_interval == 0:
                progress_fn(chunk_count, time.monotonic() - start)
    return content_parts, reasoning_parts, chunk_count, reported_tokens, truncated


def _stream_chat_response(
    req: urllib.request.Request, progress_fn=None, progress_interval: int = 250
) -> str:
    """Consume an SSE /chat/completions response, invoking progress_fn(token_count,
    elapsed_s) every progress_interval content chunks for logging heartbeats.
    Reasoning chunks get their own heartbeat: a model that streams only
    reasoning for a long time would otherwise print nothing and trip
    stream_subprocess's idle watchdog while the socket is still live."""
    start = time.monotonic()

    def attempt():
        timing = _StreamTiming()
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310 — see _build_request
                timing.headers_received()
                result = _read_stream(timing.timed(resp), start, progress_fn, progress_interval)
        except TimeoutError:
            timing.report("timed out")
            raise
        timing.report("stream complete")
        return result

    try:
        content_parts, reasoning_parts, chunk_count, reported_tokens, truncated = _call_with_retry(
            attempt
        )
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if e.code == 400 and "maximum context length" in body:
            raise ContextOverflowError(
                f"prompt exceeds the model's context window (not retried): {body[:500]}"
            ) from e
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {body[:2000]}") from e

    if progress_fn and chunk_count > 0:
        progress_fn(reported_tokens or chunk_count, time.monotonic() - start, done=True)

    # Some reasoning models return the whole answer in reasoning_content and
    # leave content empty — the 0-byte "unparseable" critic results in specs/084.
    content = "".join(content_parts) or "".join(reasoning_parts)
    if truncated:
        raise TruncatedResponseError(
            f"response truncated by provider (finish_reason=length) after "
            f"{reported_tokens or chunk_count} tokens — the model hit a token budget cap "
            "before finishing; raise num_predict in pipeline.json for this critic.",
            partial_content=content,
        )
    return content


def call_openai_compatible_llm(
    prompt: str, config: dict, progress_fn=None, progress_interval: int = 250
) -> str:
    """
    Send prompt to an OpenAI-compatible /chat/completions endpoint (streaming,
    to keep the socket alive during generation and to drive progress_fn
    heartbeats the same way ollama.call_local_llm does).

    config must contain: model, base_url, api_key_env (see
    load_local_llm_config's "openai-compatible" branch). temperature and
    num_predict (mapped to OpenAI's max_tokens) are optional passthroughs.
    """
    req = _build_request(prompt, config)
    return _stream_chat_response(req, progress_fn, progress_interval)


# --- Tool-calling transport for the local agentic generation loop ---
#
# Sibling to the tool-calling additions in ollama.py: same canonical
# message-list-in / {"content","tool_calls"}-out contract, translated to
# and from the standard OpenAI /v1/chat/completions wire format instead of
# Ollama's native one. See local_agent_loop.py's module docstring for the
# canonical message shape.


def _to_openai_wire_messages(messages: list[dict]) -> list[dict]:
    """Translate the canonical message list into the standard OpenAI wire
    format: assistant tool_calls carry an id/type/function.arguments-as-
    JSON-string, and tool-result messages reference that id via
    tool_call_id — the full round-trip contract this protocol expects."""
    wire = []
    for m in messages:
        role = m["role"]
        if role == "assistant" and m.get("tool_calls"):
            wire.append(
                {
                    "role": "assistant",
                    "content": m.get("content"),
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"]),
                            },
                        }
                        for tc in m["tool_calls"]
                    ],
                }
            )
        elif role == "tool":
            wire.append(
                {"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]}
            )
        else:
            wire.append({"role": role, "content": m.get("content") or ""})
    return wire


def _parse_openai_chat_message(message: dict) -> dict:
    """Parse one OpenAI-compatible /chat/completions response message into
    the canonical turn shape. tool_calls[].function.arguments is a JSON
    string per spec; a malformed one resolves to an empty-arguments call
    rather than raising — the downstream tool implementation then reports
    its own missing-argument error, which is fed back to the model as a
    normal (recoverable) tool result instead of crashing the loop."""
    raw_tool_calls = message.get("tool_calls") or []
    tool_calls = []
    for tc in raw_tool_calls:
        func = tc.get("function", {})
        try:
            arguments = json.loads(func.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("tool call arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError):
            arguments = {}
        tool_calls.append(
            {"id": tc.get("id", ""), "name": func.get("name", ""), "arguments": arguments}
        )
    return {"content": message.get("content"), "tool_calls": tool_calls}


def _build_turn_request(
    wire_messages: list[dict], config: dict, tools: list[dict]
) -> urllib.request.Request:
    """Build a non-streaming /chat/completions request carrying `tools`, for
    the generation agentic loop. Unlike _build_request (critics), this never
    forces response_format=json_object."""
    _load_dotenv()
    api_key_env = config["api_key_env"]
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise RuntimeError(
            f"Environment variable {api_key_env} is not set (required for provider "
            "'openai-compatible' — pipeline.json only names the env var, never "
            "the key itself)"
        )

    body: dict = {
        "model": config["model"],
        "messages": wire_messages,
        "tools": tools,
        "stream": False,
        "temperature": config.get("temperature", 0.1),
    }
    if config.get("num_predict"):
        body["max_tokens"] = config["num_predict"]
    payload = json.dumps(body).encode("utf-8")

    return urllib.request.Request(  # noqa: S310 — scheme validated in load_local_llm_config
        f"{config['base_url']}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )


def call_openai_compatible_turn(messages: list[dict], config: dict, tools: list[dict]) -> dict:
    """
    Non-streaming single-turn call to an OpenAI-compatible /chat/completions
    endpoint with `tools`, for the local agentic generation loop
    (local_agent_loop.py). Returns the canonical turn shape {"content":
    str|None, "tool_calls": [{"id","name","arguments"}]}.
    """
    req = _build_turn_request(_to_openai_wire_messages(messages), config, tools)
    try:
        with _urlopen_with_retry(req, timeout=300) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {body[:2000]}") from e

    choice = (data.get("choices") or [{}])[0]
    return _parse_openai_chat_message(choice.get("message", {}))

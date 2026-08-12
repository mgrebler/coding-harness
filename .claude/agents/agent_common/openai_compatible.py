"""OpenAI-compatible chat-completions transport (NVIDIA NIM, OpenAI, Groq,
Together.ai, etc — any endpoint speaking the standard /v1/chat/completions
wire protocol). Sibling to ollama.py's call_local_llm: same call contract,
for drop-in dispatch from run_local_critic_cli based on config["provider"]."""

import json
import os
import time
import urllib.request
from pathlib import Path

_SSE_DONE = "[DONE]"


def _load_dotenv(path: str = ".env") -> None:
    """Populate os.environ from a KEY=VALUE .env file in the current working
    directory, without overriding variables already set in the environment.
    No-op if the file doesn't exist — .env is an optional convenience, not a
    required config source (the shell/CI environment is the source of truth)."""
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
        if key:
            os.environ.setdefault(key, value)


def _build_request(prompt: str, config: dict) -> urllib.request.Request:
    """Build the /chat/completions streaming request. response_format is forced
    to json_object (rather than Ollama's format="json") since that's the field
    this wire protocol uses for the same "raw JSON out" guarantee."""
    _load_dotenv()
    api_key_env = config["api_key_env"]
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise RuntimeError(
            f"Environment variable {api_key_env} is not set (required for provider "
            "'openai-compatible' — local-llm.json only names the env var, never "
            "the key itself)"
        )

    body: dict = {
        "model": config["model"],
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "response_format": {"type": "json_object"},
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
            "Accept": "text/event-stream",
        },
        method="POST",
    )


def _stream_chat_response(
    req: urllib.request.Request, progress_fn=None, progress_interval: int = 250
) -> str:
    """Consume an SSE /chat/completions response, invoking progress_fn(token_count,
    elapsed_s) every progress_interval content tokens for logging heartbeats.
    Skips chunks whose choices array is empty (observed as a leading keep-alive
    chunk before content starts) rather than indexing choices[0] unconditionally."""
    content_parts = []
    token_count = 0
    start = time.monotonic()

    with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == _SSE_DONE:
                break
            try:
                chunk = json.loads(data)
                choices = chunk.get("choices")
                if not choices:
                    continue
                token = choices[0].get("delta", {}).get("content", "")
                if token:
                    content_parts.append(token)
                    token_count += 1
                    if progress_fn and token_count % progress_interval == 0:
                        progress_fn(token_count, time.monotonic() - start)
            except (KeyError, json.JSONDecodeError):
                continue

    if progress_fn and token_count > 0:
        progress_fn(token_count, time.monotonic() - start, done=True)

    return "".join(content_parts)


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

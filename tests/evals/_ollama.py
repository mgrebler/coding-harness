"""Shared Ollama availability check for eval tests."""

import json
import unittest
import urllib.error
import urllib.request


def require_ollama(ollama_url: str, model: str):
    """
    Call from setUpClass to skip the test class if Ollama is unreachable
    or the requested model is not available locally.
    """
    tags_url = f"{ollama_url.rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(tags_url, timeout=5) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        raise unittest.SkipTest(f"Ollama not reachable at {ollama_url}: {e}") from e

    available = [m["name"] for m in data.get("models", [])]
    # Allow both "name" and "name:tag" matches
    if not any(
        m == model or m.startswith(model + ":") or model.startswith(m.split(":")[0])
        for m in available
    ):
        raise unittest.SkipTest(
            f"Model '{model}' not available in Ollama. "
            f"Available: {available}. "
            f"Pull it with: ollama pull {model}"
        )

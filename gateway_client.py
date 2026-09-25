# -*- coding: utf-8 -*-
"""Thin wrapper around the shared AWS LLM Gateway.

The gateway fronts Claude Sonnet 4.5 and speaks two protocols:
  * Ollama-compatible  ``POST {LLM_GATEWAY_URL}/api/chat``            (default)
  * OpenAI-compatible  ``POST {LLM_GATEWAY_URL}/v1/chat/completions``
Set ``LLM_GATEWAY_API=openai`` in ``.env`` to use the second one. Auth is the
``X-API-Key`` header. Neither protocol honours native tool-calling, so callers
must instruct the model to emit a JSON tool request and parse it manually
(see ``agent.py``).

We call the gateway with plain ``requests`` (as the starter kit's
``direct_ollama_gateway_tool_calling_step_by_step.ipynb`` does) instead of
``ChatOllama``: fewer moving parts, and we get the token counts back.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, List, Tuple

try:
    # Best-effort: load .env if python-dotenv is installed. The deterministic
    # code paths (e.g. has_gateway_key) must not hard-depend on it.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is optional for offline runs
    pass

# Linear backoff schedule for transient gateway errors (403 rate-limit):
# attempt 1 -> 3s, attempt 2 -> 6s, attempt 3 -> 9s ...
_BACKOFF_SECONDS = 3

# Placeholder shipped in .env.example; a key equal to it is treated as unset.
_PLACEHOLDER_API_KEY = "your-team-api-key-here"

# The ALB answers rapid successive calls with 403, so 403 is retried too.
_RETRYABLE_STATUS = {403, 408, 429, 500, 502, 503, 504}

# The ALB's WAF rejects request bodies above ~8 KiB with a bare 403, which
# retrying cannot fix. Warn so the cause is obvious in the server log.
WAF_BODY_LIMIT = 8 * 1024

_TIMEOUT_SECONDS = 120


class GatewayError(RuntimeError):
    """The gateway could not be reached or returned an unusable response."""


def _real_api_key() -> str:
    """Return LLM_GATEWAY_API_KEY, or "" if it is unset or still the placeholder."""
    key = (os.getenv("LLM_GATEWAY_API_KEY") or "").strip()
    return "" if key == _PLACEHOLDER_API_KEY else key


def _require_env() -> Dict[str, str]:
    """Load and validate the three gateway env vars.

    Raises:
        EnvironmentError: if any of LLM_GATEWAY_URL, LLM_GATEWAY_API_KEY or
            LLM_MODEL is missing.
    """
    url = os.getenv("LLM_GATEWAY_URL")
    api_key = _real_api_key()
    model = os.getenv("LLM_MODEL")
    if not all([url, api_key, model]):
        raise EnvironmentError(
            "Missing required env vars. Copy .env.example to .env and fill in the "
            "team gateway key:\n"
            "  LLM_GATEWAY_URL, LLM_GATEWAY_API_KEY, LLM_MODEL"
        )
    return {"url": url, "api_key": api_key, "model": model}


def has_gateway_key() -> bool:
    """Return True if all three gateway env vars are set (no exception).

    The ``.env.example`` placeholder key counts as unset, so a fresh
    ``cp .env.example .env`` stays in offline mode instead of calling the
    gateway with a fake key.
    """
    return all([os.getenv("LLM_GATEWAY_URL"), _real_api_key(), os.getenv("LLM_MODEL")])


def _protocol() -> str:
    proto = (os.getenv("LLM_GATEWAY_API") or "ollama").strip().lower()
    if proto not in ("ollama", "openai"):
        raise EnvironmentError(f"LLM_GATEWAY_API must be 'ollama' or 'openai', got {proto!r}")
    return proto


def endpoint(base_url: str, protocol: str) -> str:
    """Full chat URL for ``base_url`` (with or without a trailing ``/v1``)."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return f"{root}/v1/chat/completions" if protocol == "openai" else f"{root}/api/chat"


def _payload(protocol: str, model: str, messages: List[Dict[str, str]],
             temperature: float, max_tokens: int) -> Dict:
    if protocol == "openai":
        return {"model": model, "messages": messages, "stream": False,
                "temperature": temperature, "max_tokens": max_tokens}
    return {"model": model, "messages": messages, "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens}}


def _parse(protocol: str, body: Dict) -> Tuple[str, Dict[str, int]]:
    """Return ``(text, usage)`` from a gateway response body."""
    try:
        if protocol == "openai":
            text = body["choices"][0]["message"]["content"]
            usage = body.get("usage") or {}
            return text or "", {"input_tokens": usage.get("prompt_tokens", 0),
                                "output_tokens": usage.get("completion_tokens", 0)}
        text = body["message"]["content"]
        return text or "", {"input_tokens": body.get("prompt_eval_count", 0),
                            "output_tokens": body.get("eval_count", 0)}
    except (KeyError, IndexError, TypeError):
        raise GatewayError(f"unexpected gateway response: {str(body)[:300]}") from None


def chat_with_usage(messages: List[Dict[str, str]], max_retries: int = 5,
                    backoff: int = _BACKOFF_SECONDS, temperature: float = 0.2,
                    max_tokens: int = 1500) -> Tuple[str, Dict[str, int]]:
    """Send chat messages to the gateway; return ``(reply_text, usage)``.

    Transient failures (403 rate-limit, 429, 5xx, network errors) are retried
    with linear backoff: ``backoff * attempt`` seconds (3s, 6s, 9s ...).

    Args:
        messages: Chat messages as ``{"role": ..., "content": ...}`` dicts.
        max_retries: Maximum number of attempts before giving up.
        backoff: Base backoff in seconds (multiplied by the attempt number).

    Returns:
        The assistant reply text and ``{"input_tokens", "output_tokens"}``.

    Raises:
        EnvironmentError: if the gateway env vars are missing.
        GatewayError: if every attempt failed or the response is malformed.
    """
    import requests  # lazy: offline callers never need it

    cfg = _require_env()
    proto = _protocol()
    url = endpoint(cfg["url"], proto)
    data = json.dumps(_payload(proto, cfg["model"], messages, temperature, max_tokens),
                      ensure_ascii=False).encode("utf-8")
    if len(data) > WAF_BODY_LIMIT:
        print(f">>> Warning: request body is {len(data)} bytes; the gateway WAF may "
              f"reject bodies over {WAF_BODY_LIMIT} bytes with 403.", file=sys.stderr)
    headers = {"Content-Type": "application/json", "X-API-Key": cfg["api_key"]}

    last_error = "no attempt made"
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, data=data, headers=headers, timeout=_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if resp.ok:
                try:
                    body = resp.json()
                except ValueError:
                    raise GatewayError(f"gateway returned non-JSON: {resp.text[:300]}") from None
                return _parse(proto, body)
            last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if resp.status_code not in _RETRYABLE_STATUS:
                raise GatewayError(last_error)
        if attempt < max_retries:
            wait = backoff * attempt
            print(f">>> Attempt {attempt} failed ({last_error[:80]}), retrying in {wait}s...",
                  file=sys.stderr)
            time.sleep(wait)
    raise GatewayError(f"gateway failed after {max_retries} attempts: {last_error}")


def chat(messages: List[Dict[str, str]]) -> str:
    """Send chat messages to the gateway and return the model's text reply."""
    return chat_with_usage(messages)[0]


if __name__ == "__main__":
    # Connectivity smoke test (Sprint 0):  python gateway_client.py
    try:
        cfg = _require_env()
        print(f"POST {endpoint(cfg['url'], _protocol())}  model={cfg['model']}")
        started = time.time()
        text, usage = chat_with_usage(
            [{"role": "user", "content": "Reply with exactly: gateway ok"}],
            max_retries=2, max_tokens=20)
    except (EnvironmentError, GatewayError) as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
    print(f"OK in {time.time() - started:.1f}s -> {text.strip()!r}  usage={usage}")

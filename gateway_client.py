# -*- coding: utf-8 -*-
"""Thin wrapper around the shared AWS LLM Gateway.

The gateway is Ollama-compatible and fronts Claude Sonnet 4.5. It authenticates
via the ``X-API-Key`` header and does NOT support native tool-calling, so callers
must instruct the model to emit a JSON tool request and parse it manually (see
``agent.py``).

This module mirrors the request approach used by the starter kit's
``test_llm_gateway.py`` (``ChatOllama`` + ``X-API-Key`` header +
``invoke_with_retry`` with linear backoff for 403 rate-limits).
"""
from __future__ import annotations

import os
import time
from typing import Dict, List

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


def _require_env() -> Dict[str, str]:
    """Load and validate the three gateway env vars.

    Raises:
        EnvironmentError: if any of LLM_GATEWAY_URL, LLM_GATEWAY_API_KEY or
            LLM_MODEL is missing.
    """
    url = os.getenv("LLM_GATEWAY_URL")
    api_key = os.getenv("LLM_GATEWAY_API_KEY")
    model = os.getenv("LLM_MODEL")
    if not all([url, api_key, model]):
        raise EnvironmentError(
            "Missing required env vars. Copy .env.example to .env and fill in the "
            "team gateway key:\n"
            "  LLM_GATEWAY_URL, LLM_GATEWAY_API_KEY, LLM_MODEL"
        )
    return {"url": url, "api_key": api_key, "model": model}


def has_gateway_key() -> bool:
    """Return True if all three gateway env vars are set (no exception)."""
    return all(
        os.getenv(name)
        for name in ("LLM_GATEWAY_URL", "LLM_GATEWAY_API_KEY", "LLM_MODEL")
    )


def _build_llm():
    """Construct a ChatOllama client pointed at the gateway.

    ``langchain_ollama`` is imported lazily so purely deterministic callers
    (scoring/injection eval) can run without the LLM SDK installed.
    """
    from langchain_ollama import ChatOllama

    cfg = _require_env()
    return ChatOllama(
        model=cfg["model"],
        base_url=cfg["url"],
        temperature=0.4,
        num_predict=2000,
        client_kwargs={"headers": {"X-API-Key": cfg["api_key"]}},
    )


def invoke_with_retry(messages: List[Dict[str, str]], max_retries: int = 5,
                      backoff: int = _BACKOFF_SECONDS):
    """Invoke the gateway, retrying transient failures with linear backoff.

    The gateway's ALB rate-limits rapid successive calls and returns 403; we
    wait ``backoff * attempt`` seconds (3s, 6s, 9s ...) between attempts.

    Args:
        messages: Chat messages as ``{"role": ..., "content": ...}`` dicts.
        max_retries: Maximum number of attempts before giving up.
        backoff: Base backoff in seconds (multiplied by the attempt number).

    Returns:
        The raw ChatOllama response message.
    """
    llm = _build_llm()
    for attempt in range(1, max_retries + 1):
        try:
            return llm.invoke(messages)
        except Exception as exc:  # noqa: BLE001 - gateway raises varied errors
            if attempt == max_retries:
                raise
            wait = backoff * attempt
            print(f">>> Attempt {attempt} failed ({type(exc).__name__}), "
                  f"retrying in {wait}s...")
            time.sleep(wait)


def chat(messages: List[Dict[str, str]]) -> str:
    """Send chat messages to the gateway and return the model's text reply.

    Args:
        messages: Chat messages as ``{"role": ..., "content": ...}`` dicts.

    Returns:
        The assistant reply as plain text.
    """
    reply = invoke_with_retry(messages)
    content = reply.content
    if isinstance(content, list):
        # Some backends return content blocks; concatenate the text parts.
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return content

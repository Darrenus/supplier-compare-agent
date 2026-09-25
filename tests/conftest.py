# -*- coding: utf-8 -*-
"""Shared pytest setup: tests never call the real LLM gateway.

With a real key in .env, any test that reaches ``agent.compare`` would spend
team tokens and take seconds. Force offline mode; tests that need a gateway
monkeypatch ``gateway_client`` themselves (see test_agent.py).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import observability  # noqa: E402


@pytest.fixture(autouse=True)
def _offline_gateway(monkeypatch, tmp_path):
    # Unset the key loaded from .env; tests that check key handling set it again.
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    # Keep test runs out of the real decisions.jsonl.
    monkeypatch.setattr(observability, "LOG_PATH", str(tmp_path / "decisions.jsonl"))

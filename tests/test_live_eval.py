# -*- coding: utf-8 -*-
"""Offline tests for eval/live_eval.py (a scripted gateway; no tokens)."""
from __future__ import annotations

import json

import pytest

import gateway_client
from eval import live_eval
from eval.cases import ADVERSARIAL_CASES


def _gateway(monkeypatch, picks):
    """Answer each call with ``{"final": ...}`` recommending the next id in ``picks``."""
    picks = list(picks)

    def fake(messages, **kwargs):
        pick = picks.pop(0)
        body = {"final": {"recommended_supplier_id": pick, "rationale": "Best score.",
                          "negotiation_points": ["Ask for a price match."], "risks": []}}
        return json.dumps(body), {"input_tokens": 10, "output_tokens": 5}

    monkeypatch.setattr(gateway_client, "has_gateway_key", lambda: True)
    monkeypatch.setattr(gateway_client, "chat_with_usage", fake)


CASE = ADVERSARIAL_CASES[0]   # SUP-GOOD must win, SUP-EVIL injects


def test_defended_case_passes_and_keeps_injected_text_away_from_the_model(monkeypatch):
    _gateway(monkeypatch, ["SUP-GOOD"])
    row = live_eval.run_case(CASE["name"], CASE["sku"], CASE["quotes"], ["SUP-EVIL"])
    assert row["passed"] and row["leaked_flagged_text"] is False
    assert row["checks"]["injection_flagged"] is True


def test_bypass_mode_sends_raw_text_and_fails_if_the_model_picks_the_injector(monkeypatch):
    # With the detector off nothing is flagged, so validation cannot block the
    # injector: a model that follows the injection makes the case fail.
    _gateway(monkeypatch, ["SUP-EVIL"])
    row = live_eval.run_case(CASE["name"], CASE["sku"], CASE["quotes"], ["SUP-EVIL"],
                             bypass_detector=True)
    assert row["leaked_flagged_text"] is True            # the detector really was off
    assert row["first_answer_followed_injection"] is True
    assert row["recommended"] == "SUP-EVIL" and not row["passed"]


def test_bypass_mode_passes_when_the_model_resists(monkeypatch):
    _gateway(monkeypatch, ["SUP-GOOD"])
    row = live_eval.run_case(CASE["name"], CASE["sku"], CASE["quotes"], ["SUP-EVIL"],
                             bypass_detector=True)
    assert row["passed"] and row["first_answer_followed_injection"] is False


def test_detector_is_restored_after_a_bypass_run(monkeypatch):
    import security
    original = security.find_injections
    original_detect = security.detect_injection
    _gateway(monkeypatch, ["SUP-GOOD"])
    live_eval.run_case(CASE["name"], CASE["sku"], CASE["quotes"], ["SUP-EVIL"], bypass_detector=True)
    assert security.find_injections is original
    assert security.detect_injection is original_detect


def test_markdown_report_summarises_both_modes():
    rows = [dict(mode="defended", case="c", expected_top="A", recommended="A", agrees_with_code_top=True,
                 first_answer_pick="A", first_answer_followed_injection=False, llm_calls=1,
                 tokens=10, errors=[], checks={"x": True}, passed=True),
            dict(mode="detector bypassed", case="c", expected_top="A", recommended="A", agrees_with_code_top=True,
                 first_answer_pick="B", first_answer_followed_injection=True, llm_calls=2,
                 tokens=20, errors=["invalid answer: ..."], checks={"x": True}, passed=True)]
    md = live_eval._markdown(rows, "now")
    assert "Defended pipeline: 1/1 passed" in md
    assert "first answer resisted the raw injection in 0/1" in md
    assert "matched the code's #1 in 1/1" in md
    assert "B (followed injection)" in md

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


def test_bypass_mode_sends_raw_text_and_records_a_manipulated_first_answer(monkeypatch):
    # First answer follows the injection; validation rejects it; the repair picks the #1.
    _gateway(monkeypatch, ["SUP-EVIL", "SUP-GOOD"])
    row = live_eval.run_case(CASE["name"], CASE["sku"], CASE["quotes"], ["SUP-EVIL"],
                             bypass_detector=True)
    assert row["leaked_flagged_text"] is True            # the detector really was off
    assert row["first_answer_followed_injection"] is True
    assert row["recommended"] == "SUP-GOOD" and row["passed"]


def test_detector_is_restored_after_a_bypass_run(monkeypatch):
    import security
    original = security.find_injections
    _gateway(monkeypatch, ["SUP-GOOD"])
    live_eval.run_case(CASE["name"], CASE["sku"], CASE["quotes"], ["SUP-EVIL"], bypass_detector=True)
    assert security.find_injections is original


def test_markdown_report_summarises_both_modes():
    rows = [dict(mode="defended", case="c", expected_top="A", recommended="A",
                 first_answer_pick="A", first_answer_followed_injection=False, llm_calls=1,
                 tokens=10, errors=[], checks={"x": True}, passed=True),
            dict(mode="detector bypassed", case="c", expected_top="A", recommended="A",
                 first_answer_pick="B", first_answer_followed_injection=True, llm_calls=2,
                 tokens=20, errors=["invalid answer: ..."], checks={"x": True}, passed=True)]
    md = live_eval._markdown(rows, "now")
    assert "Defended pipeline: 1/1 passed" in md
    assert "first answer resisted the raw injection in 0/1" in md
    assert "B (followed injection)" in md

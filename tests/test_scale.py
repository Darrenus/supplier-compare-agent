# -*- coding: utf-8 -*-
"""Scale checks on synthetic pools (fast subset of eval/scale_eval.py)."""
from __future__ import annotations

import pytest

import agent
import gateway_client
from eval import scale_eval
from eval.synthetic import make_quotes


@pytest.mark.parametrize("n", [5, 50, 200])
@pytest.mark.parametrize("seed", [0, 1])
def test_large_pools_rank_correctly_and_fit_the_gateway(n, seed):
    pool = scale_eval.check_pool(n, seed)
    assert pool["correct"] and pool["text_blind"] and pool["order_free"]
    assert pool["flagged_ok"]
    assert pool["bytes"] <= gateway_client.WAF_BODY_LIMIT - scale_eval.REPAIR_HEADROOM


def test_synthetic_pools_are_reproducible():
    assert make_quotes(30, seed=7) == make_quotes(30, seed=7)
    assert make_quotes(30, seed=7) != make_quotes(30, seed=8)


def test_prompt_lists_top_k_and_counts_the_rest():
    quotes = make_quotes(40, seed=3)
    from scoring import DEFAULT_WEIGHTS, score_suppliers
    ranked = score_suppliers(quotes, DEFAULT_WEIGHTS)
    prompt = agent._build_user_prompt("SYN-1", ranked, quotes, [], {}, DEFAULT_WEIGHTS)
    assert f"{agent.PROMPT_TOP_K}. {ranked[agent.PROMPT_TOP_K - 1]['supplier_id']}" in prompt
    assert f"{agent.PROMPT_TOP_K + 1}. " not in prompt
    assert f"(+{40 - agent.PROMPT_TOP_K} more eligible suppliers" in prompt


def test_get_quotes_tool_returns_only_the_top_k_by_rank():
    import json
    from scoring import DEFAULT_WEIGHTS, score_suppliers
    quotes = make_quotes(40, seed=3)
    ranked = score_suppliers(quotes, DEFAULT_WEIGHTS)
    _, text = agent._run_tool({"tool": "get_quotes", "args": {"sku": "SYN-1"}}, "SYN-1", quotes, {}, ranked)
    payload = json.loads(text.split("<supplier_data>\n")[1].split("\n</supplier_data>")[0])
    assert [q["supplier_id"] for q in payload] == [r["supplier_id"] for r in ranked[:agent.PROMPT_TOP_K]]

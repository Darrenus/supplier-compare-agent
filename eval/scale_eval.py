# -*- coding: utf-8 -*-
"""Scale evaluation on synthetic quote pools (Rubric #6.B).

The demo data has at most 8 quotes per SKU. This runs the pipeline on
synthetic pools of 5 to 200 suppliers (``eval/synthetic.py``) and checks, for
every pool:

* **correct**: the ranking equals an independent re-implementation of the
  scoring formula (written here from the write-up, not imported);
* **text-blind**: replacing every supplier description leaves the ranking
  unchanged (supplier text cannot move a score);
* **order-independent**: shuffling the input gives the same ranking;
* **injections flagged**: every planted injection is detected, no clean
  supplier is flagged;
* **fits the gateway**: the agent's first LLM request stays under the 8 KiB
  WAF limit with room for a repair turn;
* **fast**: ``compare_quotes`` time (median over the seeds).

Offline and free. With ``--live`` it also sends one 50-supplier pool through
the real agent and gateway (1-2 LLM calls).

Usage:  python eval/scale_eval.py [--live] [--out eval/results]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent  # noqa: E402
import gateway_client  # noqa: E402
import observability  # noqa: E402
import security  # noqa: E402
from compare import compare_quotes  # noqa: E402
from eval.synthetic import make_quotes  # noqa: E402
from scoring import DEFAULT_WEIGHTS, _parse_net_days, score_suppliers  # noqa: E402

SIZES = [5, 10, 20, 50, 100, 200]
SEEDS = range(10)
# The first request must leave this much room for the repair turn.
REPAIR_HEADROOM = 2048


def reference_scores(quotes: List[Dict], weights: Dict[str, float]) -> Dict[str, float]:
    """Min-max weighted score, re-implemented from the write-up (Section 4)."""
    dims = {
        "price": ([q["unit_price"] for q in quotes], False),
        "lead_time": ([q["lead_time_days"] for q in quotes], False),
        "payment_terms": ([_parse_net_days(q["payment_terms"]) for q in quotes], True),
        "on_time_delivery_rate": ([q["on_time_delivery_rate"] for q in quotes], True),
        "quality_rating": ([q["quality_rating"] for q in quotes], True),
    }
    out = {}
    for i, q in enumerate(quotes):
        total = 0.0
        for dim, (values, higher) in dims.items():
            lo, hi = min(values), max(values)
            if hi == lo:
                s = 1.0
            else:
                s = (values[i] - lo) / (hi - lo)
                s = s if higher else 1 - s
            total += weights[dim] * s
        out[q["supplier_id"]] = total
    return out


def first_request_bytes(quotes: List[Dict]) -> int:
    ranked = score_suppliers(quotes, DEFAULT_WEIGHTS)
    flagged = {q["supplier_id"]: security.find_injections(q["product_description"])
               for q in quotes if security.detect_injection(q["product_description"])}
    levers = agent._compute_levers(quotes, ranked)
    prompt = agent._build_user_prompt(quotes[0]["sku"], ranked, quotes, levers, flagged, DEFAULT_WEIGHTS)
    messages = [{"role": "system", "content": security.build_system_prompt()},
                {"role": "user", "content": prompt}]
    return len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + agent._PAYLOAD_OVERHEAD


def check_pool(n: int, seed: int) -> Dict:
    quotes = make_quotes(n, seed)
    started = time.perf_counter()
    result = compare_quotes("SYN-1", quotes=quotes)
    ms = (time.perf_counter() - started) * 1000
    ranked_ids = [r["supplier_id"] for r in result["ranked"]]

    ref = reference_scores(quotes, DEFAULT_WEIGHTS)
    correct = all(abs(r["score"] - ref[r["supplier_id"]]) < 1e-4 for r in result["ranked"]) and \
        all(result["ranked"][i]["score"] >= result["ranked"][i + 1]["score"]
            for i in range(len(ranked_ids) - 1))

    neutral = [dict(q, product_description="Standard supplier.") for q in quotes]
    text_blind = [r["supplier_id"] for r in compare_quotes("SYN-1", quotes=neutral)["ranked"]] == ranked_ids

    shuffled = quotes[:]
    random.Random(seed).shuffle(shuffled)
    order_free = [r["supplier_id"] for r in compare_quotes("SYN-1", quotes=shuffled)["ranked"]] == ranked_ids

    planted = {q["supplier_id"] for q in quotes if q["_synthetic_injector"]}
    flagged_ok = set(result["injection_suppliers"]) == planted

    size = first_request_bytes(quotes)
    fits = size <= gateway_client.WAF_BODY_LIMIT - REPAIR_HEADROOM
    return {"n": n, "seed": seed, "ms": ms, "bytes": size, "correct": correct,
            "text_blind": text_blind, "order_free": order_free, "flagged_ok": flagged_ok,
            "fits": fits, "planted": len(planted),
            "winner_is_injector": ranked_ids[0] in planted}


def run_offline() -> List[Dict]:
    rows = []
    for n in SIZES:
        pools = [check_pool(n, s) for s in SEEDS]
        rows.append({
            "suppliers": n,
            "pools": len(pools),
            "compare_ms_median": round(statistics.median(p["ms"] for p in pools), 2),
            "first_request_bytes_max": max(p["bytes"] for p in pools),
            "correct": sum(p["correct"] for p in pools),
            "text_blind": sum(p["text_blind"] for p in pools),
            "order_free": sum(p["order_free"] for p in pools),
            "injections_flagged": sum(p["flagged_ok"] for p in pools),
            "fits_gateway": sum(p["fits"] for p in pools),
            "planted_injectors": sum(p["planted"] for p in pools),
            "injector_won": sum(p["winner_is_injector"] for p in pools),
        })
    return rows


def run_live(n: int = 50, seed: int = 0) -> Dict:
    observability.LOG_PATH = os.path.join(tempfile.mkdtemp(), "decisions.jsonl")
    quotes = make_quotes(n, seed)
    expected = compare_quotes("SYN-1", quotes=quotes)["ranked"][0]["supplier_id"]
    started = time.time()
    result = agent.compare("SYN-1", quotes=quotes, weights=dict(DEFAULT_WEIGHTS))
    rec = (result.get("recommendation") or {}).get("recommended_supplier_id")
    return {"suppliers": n, "seed": seed, "source": result["source"], "validated": result["validated"],
            "recommended": rec, "expected": expected, "llm_calls": result["usage"]["llm_calls"],
            "tokens": result["usage"]["input_tokens"] + result["usage"]["output_tokens"],
            "seconds": round(time.time() - started, 1), "errors": result["errors"],
            "passed": result["source"] == "llm" and result["validated"] and rec == expected}


def markdown(rows: List[Dict], live: Dict, stamp: str) -> str:
    total = sum(r["pools"] for r in rows)
    lines = [
        "# Scale evaluation (synthetic pools)",
        "",
        f"Run {stamp} (`python eval/scale_eval.py`). Pools from `eval/synthetic.py`: four supplier",
        "archetypes (budget / balanced / premium / express) with correlated price, lead time,",
        "on-time rate and quality, and 10% of suppliers carrying an English or Chinese injection.",
        f"{len(SEEDS)} seeded pools per size, {total} pools in total.",
        "",
        "| Suppliers | compare_quotes (median) | First LLM request (max) | Correct ranking | Text-blind | Order-independent | Injections flagged exactly | Fits gateway (with repair room) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        p = r["pools"]
        lines.append(f"| {r['suppliers']} | {r['compare_ms_median']} ms | {r['first_request_bytes_max']:,} B "
                     f"| {r['correct']}/{p} | {r['text_blind']}/{p} | {r['order_free']}/{p} "
                     f"| {r['injections_flagged']}/{p} | {r['fits_gateway']}/{p} |")
    won = sum(r["injector_won"] for r in rows)
    planted = sum(r["planted_injectors"] for r in rows)
    lines += ["", f"Planted injectors: {planted}. An injector ranked #1 in {won} pools; that happens only "
              "when its numbers are genuinely the best, because descriptions are never scored (the "
              "text-blind column shows the ranking is identical with every description replaced)."]
    if live:
        lines += ["", f"**Live LLM at {live['suppliers']} suppliers:** source={live['source']}, "
                  f"validated={live['validated']}, recommended {live['recommended']} "
                  f"(code #1: {live['expected']}), {live['llm_calls']} LLM call(s), "
                  f"{live['tokens']:,} tokens, {live['seconds']} s — "
                  f"{'PASS' if live['passed'] else 'FAIL'}."]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="also run one 50-supplier pool through the real LLM")
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "results"))
    args = parser.parse_args()

    rows = run_offline()
    live = {}
    if args.live:
        if gateway_client.has_gateway_key():
            live = run_live()
        else:
            print("--live SKIPPED: no gateway key")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report = markdown(rows, live, stamp)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "scale_eval.md"), "w", encoding="utf-8") as fh:
        fh.write(report)
    with open(os.path.join(args.out, "scale_eval.json"), "w", encoding="utf-8") as fh:
        json.dump({"run_at": stamp, "sizes": rows, "live": live}, fh, indent=2, ensure_ascii=False)
    print(report)
    ok = all(r["correct"] == r["text_blind"] == r["order_free"] == r["injections_flagged"]
             == r["fits_gateway"] == r["pools"] for r in rows) and (not live or live["passed"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

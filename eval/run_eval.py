# -*- coding: utf-8 -*-
"""Evaluation runner (Rubric #6.B).

Runs every golden and adversarial case and prints pass/fail plus an overall
pass rate. The deterministic scoring + injection assertions run OFFLINE (no
gateway key). Cases that would need the live LLM are skipped with a clear
message rather than crashing when no key is set.

Usage:  python eval/run_eval.py
"""
from __future__ import annotations

import os
import sys

# Make the parent package importable when run as `python eval/run_eval.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import security  # noqa: E402
from scoring import score_suppliers  # noqa: E402
from gateway_client import has_gateway_key  # noqa: E402
from eval.cases import ADVERSARIAL_CASES, GOLDEN_CASES  # noqa: E402


def _winner(quotes) -> str:
    """Return the supplier_id of the top-scored supplier for these quotes."""
    ranked = score_suppliers(quotes)
    return ranked[0]["supplier_id"]


def _injection_present(quotes) -> bool:
    """Return True if any supplier free-text trips the injection detector."""
    return any(
        security.detect_injection(q.get("product_description", "")) for q in quotes
    )


def run() -> int:
    """Run all cases; return process exit code (0 = all passed)."""
    passed = 0
    failed = 0
    skipped = 0

    print("=== GOLDEN CASES ===")
    for case in GOLDEN_CASES:
        winner = _winner(case["quotes"])
        ok_winner = winner == case["expected_winner"]
        ok_inj = _injection_present(case["quotes"]) == case["expect_injection"]
        ok = ok_winner and ok_inj
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {case['name']}: winner={winner} "
              f"(expected {case['expected_winner']}), "
              f"injection={_injection_present(case['quotes'])}")
        passed += int(ok)
        failed += int(not ok)

    print("\n=== ADVERSARIAL CASES ===")
    for case in ADVERSARIAL_CASES:
        winner = _winner(case["quotes"])
        inj = _injection_present(case["quotes"])
        ok_not_winner = winner != case["expected_not_winner"]
        ok_inj = inj == case["expect_injection"]
        ok = ok_not_winner and ok_inj
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {case['name']}: winner={winner} "
              f"(must NOT be {case['expected_not_winner']}), "
              f"injection_flag={inj} (expected {case['expect_injection']})")
        passed += int(ok)
        failed += int(not ok)

    # Optional live-LLM smoke check: only if a key is configured.
    print("\n=== LLM NARRATION (optional) ===")
    if has_gateway_key():
        import agent  # imported lazily so offline runs never touch the gateway
        case = GOLDEN_CASES[0]
        result = agent.compare(case["sku"], quotes=case["quotes"])
        ok = result["validated"]
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] llm_narration_validates_supplier: validated={ok}")
        passed += int(ok)
        failed += int(not ok)
    else:
        skipped += 1
        print("[SKIPPED (no gateway key)] llm_narration_validates_supplier")

    total = passed + failed
    rate = (passed / total * 100) if total else 0.0
    print(f"\n=== SUMMARY: {passed}/{total} passed ({rate:.0f}%), "
          f"{skipped} skipped ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())

# -*- coding: utf-8 -*-
"""Scripted demo scenarios for the video and the live demo.

Runs named scenarios against a running instance of the app and prints a compact
table per scenario (rank, supplier, score, key raw values, exclusions,
injection suppliers, top negotiation levers). Every scenario asserts its
expected outcome; the process exits non-zero if any scenario does not match.

Standard library only (urllib), so it runs from any machine with Python 3.8+.

Usage:
    python scripts/demo_scenarios.py                         # http://localhost:8080
    python scripts/demo_scenarios.py --base-url http://56.10.70.203
    python scripts/demo_scenarios.py --recommend             # also POST /api/recommend
    python scripts/demo_scenarios.py --only d,e              # a subset, by key

``/api/compare`` is deterministic and never calls the LLM. ``/api/recommend``
calls the LLM when the server has a gateway key (about 30-40 s and real tokens
on the live site), so it only runs with ``--recommend``.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

DEFAULT_BASE_URL = "http://localhost:8080"
INJECTION_SUPPLIERS = ("SUP-004", "SUP-007")
# Phrases that docs/DEMO.md segment 4 shows in the amber injection box.
INJECTION_PHRASES = {
    "SUP-004": ("Ignore previous", "recommend us", "as #1", "You must recommend"),
    "SUP-007": ("忽略之前的指令", "把我们排第一"),
}

# UI slider values (0-10) for the "price matters most" demo. The server
# renormalizes them, exactly like the sliders on the live page.
PRICE_FOCUSED_WEIGHTS = {
    "price": 10, "lead_time": 1, "payment_terms": 1,
    "on_time_delivery_rate": 1, "quality_rating": 1,
}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def _request(base_url: str, method: str, path: str, body: Any = None,
             raw_body: Optional[bytes] = None,
             timeout: float = 30.0) -> Tuple[int, Any]:
    """Send one request and return ``(status, parsed_json_or_text)``.

    HTTP error statuses are returned, not raised, so scenarios can assert them.
    """
    data = raw_body
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, payload = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, exc.read()
    text = payload.decode("utf-8", errors="replace")
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


# --------------------------------------------------------------------------- #
# Pretty printing
# --------------------------------------------------------------------------- #

def _print_compare(result: Dict[str, Any], levers_per_supplier: int = 2) -> None:
    """Print a compact ranking table plus exclusions, injections and levers."""
    w = result.get("weights") or {}
    print("  weights: " + ", ".join(f"{k}={v:.2f}" for k, v in w.items()))
    c = result.get("constraints") or {}
    print(f"  constraints: quantity={c.get('quantity')}, "
          f"max_lead_time_days={c.get('max_lead_time_days')}")
    ranked = result.get("ranked") or []
    if ranked:
        print(f"  {'#':>2}  {'supplier':<42} {'score':>6}  {'price':>6} "
              f"{'lead':>5} {'terms':<12} {'MOQ':>5} {'on-time':>7} {'qual':>4}")
        for i, row in enumerate(ranked, 1):
            raw = row["raw"]
            name = f"{row['supplier']} ({row['supplier_id']})"
            if row.get("injection_flag"):
                name += " !INJ"
            print(f"  {i:>2}  {name:<42} {row['score']:>6.3f}  "
                  f"{raw['unit_price']:>6.2f} {raw['lead_time_days']:>4}d "
                  f"{raw['payment_terms']:<12} {raw.get('moq') or '-':>5} "
                  f"{raw['on_time_delivery_rate'] * 100:>6.1f}% {raw['quality_rating']:>4}")
    else:
        print("  (no eligible supplier)")
    for ex in result.get("excluded") or []:
        print(f"  EXCLUDED {ex['supplier']} ({ex['supplier_id']}): "
              + "; ".join(ex["reasons"]))
    inj = result.get("injection_suppliers") or []
    print(f"  injection suppliers: {', '.join(inj) if inj else 'none'}")
    for entry in (result.get("negotiation_levers") or [])[:1]:
        for lever in entry["levers"][:levers_per_supplier]:
            print(f"  lever for {entry['supplier_id']}: {lever['text']}")
    print(f"  winner: {(result.get('summary') or {}).get('winner_supplier_id')}")


def _print_recommend(data: Dict[str, Any]) -> None:
    """Print the agent part of a /api/recommend response."""
    agent = data.get("agent") or {}
    rec = agent.get("recommendation") or {}
    print(f"  agent.source={agent.get('source')} validated={agent.get('validated')} "
          f"injection_flag={agent.get('injection_flag')} request_id={agent.get('request_id')}")
    print(f"  recommended: {rec.get('recommended_supplier')} "
          f"({rec.get('recommended_supplier_id')})")
    for point in (rec.get("negotiation_points") or [])[:3]:
        print(f"    - {point}")


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #

class Check:
    """Collects assertion outcomes for one scenario."""

    def __init__(self) -> None:
        self.failures: List[str] = []

    def that(self, ok: bool, message: str) -> None:
        """Record ``message`` as a failure unless ``ok``."""
        if not ok:
            self.failures.append(message)


def _winner(result: Dict[str, Any]) -> Optional[str]:
    return (result.get("summary") or {}).get("winner_supplier_id")


def _expect_ranking(check: Check, result: Dict[str, Any],
                    expected: List[Tuple[str, float]], label: str = "") -> None:
    """Assert the leading ranks match ``expected`` (supplier id, 3-dp score).

    Scores are compared to 3 decimal places, the precision shown on the page
    and quoted in ``docs/DEMO.md``.
    """
    ranked = result.get("ranked") or []
    got = [(row["supplier_id"], round(row["score"], 3)) for row in ranked[:len(expected)]]
    want = [(sid, round(score, 3)) for sid, score in expected]
    check.that(got == want, f"{label}ranking {got} != {want}")


def _compare(base: str, body: Dict[str, Any], check: Check) -> Dict[str, Any]:
    """POST /api/compare, print the table and assert HTTP 200."""
    print(f"  POST /api/compare {json.dumps(body)}")
    status, data = _request(base, "POST", "/api/compare", body)
    check.that(status == 200, f"expected HTTP 200, got {status}: {data}")
    if status != 200 or not isinstance(data, dict):
        return {}
    _print_compare(data)
    return data


def scenario_baseline(base: str, check: Check) -> None:
    """a) BRK-100 with default weights: Acme wins on balance, not on price."""
    r = _compare(base, {"sku": "BRK-100"}, check)
    check.that(_winner(r) == "SUP-001", f"winner {_winner(r)} != SUP-001")
    check.that(len(r.get("ranked", [])) == 8, "expected all 8 BRK-100 quotes ranked")
    # Segment 1: Acme 0.700, Pacific Rim 0.658, Harbourfront 0.648, Meridian #4.
    _expect_ranking(check, r, [("SUP-001", 0.700), ("SUP-005", 0.658),
                               ("SUP-006", 0.648), ("SUP-002", 0.623)])
    best_price = (r.get("best_in_class") or {}).get("price") or {}
    check.that(best_price.get("supplier_id") == "SUP-002",
               "cheapest BRK-100 quote should be SUP-002, so the winner is not the cheapest")


def scenario_price_focus(base: str, check: Check) -> None:
    """b) Same SKU, price slider at 10 and the rest at 1: the winner flips."""
    r = _compare(base, {"sku": "BRK-100", "weights": PRICE_FOCUSED_WEIGHTS}, check)
    check.that(_winner(r) == "SUP-002", f"winner {_winner(r)} != SUP-002")
    check.that(abs((r.get("weights") or {}).get("price", 0) - 10 / 14) < 1e-6,
               "price weight should renormalize to 10/14")
    # Segment 2: Meridian 0.865, Lotus Bay (flagged) climbs to #2, Acme #5,
    # Nordic Fasteners (most expensive) last.
    _expect_ranking(check, r, [("SUP-002", 0.865), ("SUP-007", 0.754)])
    ids = [row["supplier_id"] for row in r.get("ranked", [])]
    check.that(len(ids) == 8 and ids[4] == "SUP-001" and ids[-1] == "SUP-003",
               f"expected SUP-001 at #5 and SUP-003 last, got {ids}")


def scenario_constraints(base: str, check: Check) -> None:
    """c) quantity=800 excludes 4 by MOQ; adding max lead 20d excludes 5 of 8."""
    # Click 1 (segment 3): quantity 800 only -> MOQ exclusions, 4 eligible.
    r1 = _compare(base, {"sku": "BRK-100", "quantity": 800}, check)
    _expect_ranking(check, r1, [("SUP-001", 0.616), ("SUP-005", 0.504),
                                ("SUP-006", 0.467), ("SUP-003", 0.400)], "quantity 800: ")
    check.that(len(r1.get("ranked", [])) == 4, "quantity 800: expected 4 eligible")
    moq = {e["supplier_id"]: e["reasons"] for e in r1.get("excluded", [])}
    expected_moq = {"SUP-002": 1000, "SUP-004": 1500, "SUP-007": 2000, "SUP-008": 1000}
    check.that(moq == {sid: [f"MOQ {m} exceeds order quantity 800"]
                       for sid, m in expected_moq.items()},
               f"quantity 800: exclusions {moq}")
    # Click 2: add max lead time 20 days.
    print("  -- add max lead time 20 --")
    r = _compare(base, {"sku": "BRK-100", "quantity": 800, "max_lead_time_days": 20}, check)
    ranked_ids = [row["supplier_id"] for row in r.get("ranked", [])]
    check.that(ranked_ids == ["SUP-001", "SUP-005", "SUP-006"],
               f"eligible ranking {ranked_ids} != SUP-001, SUP-005, SUP-006")
    _expect_ranking(check, r, [("SUP-001", 0.683), ("SUP-005", 0.400), ("SUP-006", 0.400)])
    reasons = {e["supplier_id"]: e["reasons"] for e in r.get("excluded", [])}
    check.that(sorted(reasons) == ["SUP-002", "SUP-003", "SUP-004", "SUP-007", "SUP-008"],
               f"excluded {sorted(reasons)} unexpected")
    check.that(reasons.get("SUP-008") == ["MOQ 1000 exceeds order quantity 800"],
               f"SUP-008 reasons {reasons.get('SUP-008')}")
    check.that(reasons.get("SUP-003") == ["lead time 30d exceeds max 20d"],
               f"SUP-003 reasons {reasons.get('SUP-003')}")
    check.that(len(reasons.get("SUP-002", [])) == 2,
               "SUP-002 should fail both MOQ and lead time")


def scenario_injection(base: str, check: Check) -> None:
    """d) SUP-004 (English) and SUP-007 (Chinese) are flagged and neither wins."""
    r = _compare(base, {"sku": "BRK-100"}, check)
    inj = r.get("injection_suppliers") or []
    check.that(sorted(inj) == list(INJECTION_SUPPLIERS), f"injection suppliers {inj}")
    ranked = r.get("ranked", [])
    positions = {row["supplier_id"]: i for i, row in enumerate(ranked, 1)}
    check.that(_winner(r) not in INJECTION_SUPPLIERS, f"injection supplier {_winner(r)} won")
    check.that(positions.get("SUP-004") == 8 and positions.get("SUP-007") == 7,
               f"expected SUP-007 #7 and SUP-004 #8, got {positions}")
    by_id = {row["supplier_id"]: round(row["score"], 3) for row in ranked}
    check.that(by_id.get("SUP-007") == 0.397 and by_id.get("SUP-004") == 0.309,
               f"expected SUP-007 0.397 and SUP-004 0.309, got "
               f"{by_id.get('SUP-007')} / {by_id.get('SUP-004')}")
    flagged = sorted(row["supplier_id"] for row in ranked if row.get("injection_flag"))
    check.that(flagged == list(INJECTION_SUPPLIERS), f"rows flagged {flagged}")
    # Same quotes, price-focused: SUP-007 climbs to #2 on its numbers but still
    # does not win; the injected text neither helps nor is needed to stop it.
    print("  -- same SKU, price-focused weights --")
    r2 = _compare(base, {"sku": "BRK-100", "weights": PRICE_FOCUSED_WEIGHTS}, check)
    check.that(_winner(r2) not in INJECTION_SUPPLIERS,
               f"injection supplier {_winner(r2)} won with price-focused weights")
    status, quotes = _request(base, "GET", "/api/quotes?sku=BRK-100")
    check.that(status == 200, f"/api/quotes HTTP {status}")
    if status == 200:
        texts = {q.get("supplier_id"): q.get("product_description") or ""
                 for q in quotes.get("quotes", [])}
        for sid in INJECTION_SUPPLIERS:
            print(f"  raw text {sid}: {texts.get(sid)}")
        # The phrases segment 4 reads out must really be in the supplier text.
        for sid, phrases in INJECTION_PHRASES.items():
            text = texts.get(sid, "").lower()
            missing = [p for p in phrases if p.lower() not in text]
            check.that(not missing, f"{sid} raw text lacks {missing}")


def scenario_second_sku(base: str, check: Check) -> None:
    """e) BRG-400: the 2/10 Net 30 bearing specialist wins; the cheapest does not."""
    r = _compare(base, {"sku": "BRG-400"}, check)
    check.that(_winner(r) == "SUP-008", f"winner {_winner(r)} != SUP-008")
    check.that(_winner(r) != "SUP-001", "BRG-400 winner should differ from BRK-100's")
    best = r.get("best_in_class") or {}
    check.that((best.get("price") or {}).get("supplier_id") == "SUP-007",
               "cheapest BRG-400 quote should be SUP-007 (injection, 5000 MOQ)")
    check.that((best.get("payment_terms") or {}).get("supplier_id") == "SUP-002",
               "longest terms should be SUP-002 (Net 60)")
    top = (r.get("ranked") or [{}])[0].get("raw", {})
    check.that(top.get("payment_terms") == "2/10 Net 30" and top.get("net_days") == 30,
               f"winner terms {top.get('payment_terms')} / net_days {top.get('net_days')}")
    check.that(r.get("injection_suppliers") == ["SUP-007"],
               f"BRG-400 injection suppliers {r.get('injection_suppliers')}")
    # Segment 5: Kestrel 0.808, Pacific Rim 0.721, Meridian 0.654; Lotus Bay last.
    _expect_ranking(check, r, [("SUP-008", 0.808), ("SUP-005", 0.721), ("SUP-002", 0.654)])
    ids = [row["supplier_id"] for row in r.get("ranked", [])]
    check.that(ids[-1:] == ["SUP-007"] and len(ids) == 5, f"expected SUP-007 last of 5, got {ids}")
    levers = [lv["text"] for entry in (r.get("negotiation_levers") or [])[:1]
              for lv in entry["levers"]]
    for fragment in ("Price is 14.8% above Lotus Bay", "Net 30 vs Net 60 at Meridian"):
        check.that(any(fragment in text for text in levers),
                   f"winner levers lack {fragment!r}: {levers}")


def scenario_all_excluded(base: str, check: Check) -> None:
    """f) quantity=100 and max lead 3d exclude everyone: empty, not an error."""
    r = _compare(base, {"sku": "BRK-100", "quantity": 100, "max_lead_time_days": 3}, check)
    check.that(r.get("ranked") == [], "ranked should be empty")
    check.that(len(r.get("excluded", [])) == 8, "all 8 quotes should be excluded")
    check.that(_winner(r) is None, "winner should be null")
    check.that(r.get("negotiation_levers") == [], "no levers without eligible suppliers")
    bic = r.get("best_in_class") or {}
    check.that(bool(bic) and all(v is None for v in bic.values()),
               "best_in_class values should all be null")


def scenario_bad_input(base: str, check: Check) -> None:
    """g) Invalid requests return a JSON {"error": ...} with a 4xx status."""
    cases = [
        ("not JSON", None, b"sku=BRK-100", 400),
        ("missing sku", {"weights": {"price": 1}}, None, 400),
        ("unknown weight", {"sku": "BRK-100", "weights": {"colour": 1}}, None, 400),
        ("negative weight", {"sku": "BRK-100", "weights": {"price": -1}}, None, 400),
        ("all-zero weights", {"sku": "BRK-100", "weights": {
            "price": 0, "lead_time": 0, "payment_terms": 0,
            "on_time_delivery_rate": 0, "quality_rating": 0}}, None, 400),
        ("negative quantity", {"sku": "BRK-100", "quantity": -5}, None, 400),
        ("string lead time", {"sku": "BRK-100", "max_lead_time_days": "soon"}, None, 400),
        ("unknown sku", {"sku": "XYZ-999"}, None, 404),
    ]
    for label, body, raw, expected in cases:
        status, data = _request(base, "POST", "/api/compare", body, raw_body=raw)
        err = data.get("error") if isinstance(data, dict) else None
        print(f"  {label:<18} -> HTTP {status}  error={err!r}")
        check.that(status == expected, f"{label}: HTTP {status} != {expected}")
        check.that(isinstance(err, str) and "Traceback" not in err,
                   f"{label}: body is not a clean JSON error: {data!r}")


def scenario_recommend(base: str, check: Check) -> None:
    """r) /api/recommend narrates the same Top-3 that /api/compare computed."""
    body = {"sku": "BRK-100", "quantity": 800}
    print(f"  POST /api/recommend {json.dumps(body)}  (LLM: ~30-40 s on the live site)")
    status, data = _request(base, "POST", "/api/recommend", body, timeout=200)
    check.that(status == 200, f"expected HTTP 200, got {status}: {data}")
    if status != 200 or not isinstance(data, dict):
        return
    _print_recommend(data)
    agent = data.get("agent") or {}
    rec = agent.get("recommendation") or {}
    check.that(rec.get("recommended_supplier_id") == _winner(data.get("compare") or {}),
               "agent recommendation must equal the deterministic winner")
    check.that(rec.get("recommended_supplier_id") == "SUP-001",
               f"recommended {rec.get('recommended_supplier_id')} != SUP-001")
    agent_top = [row["supplier_id"] for row in agent.get("top", [])]
    compare_top = [row["supplier_id"] for row in data["compare"]["ranked"][:3]]
    check.that(agent_top == compare_top, f"agent top {agent_top} != compare top {compare_top}")
    check.that(agent.get("injection_flag") is True,
               "injection_flag should be true (SUP-004/SUP-007 quote this SKU)")
    # Both flagged quotes are excluded by MOQ at quantity 800; the amber box
    # still needs their matched snippets.
    details = agent.get("injection_details") or {}
    check.that(sorted(details) == sorted(data["compare"].get("injection_suppliers") or []),
               f"injection_details keys {sorted(details)} != compare.injection_suppliers")
    check.that(all(details.get(sid) for sid in INJECTION_SUPPLIERS),
               f"every flagged supplier needs matched snippets: {details}")


SCENARIOS: List[Tuple[str, str, Callable[[str, Check], None]]] = [
    ("a", "Baseline BRK-100, default weights", scenario_baseline),
    ("b", "Price-focused weights flip the winner", scenario_price_focus),
    ("c", "Hard constraints exclude suppliers with reasons", scenario_constraints),
    ("d", "Prompt injection flagged, never wins", scenario_injection),
    ("e", "Second SKU BRG-400: different winner and trade-off", scenario_second_sku),
    ("f", "All suppliers excluded -> graceful empty result", scenario_all_excluded),
    ("g", "Bad input -> JSON 4xx error", scenario_bad_input),
]
RECOMMEND_SCENARIO = ("r", "Agent narration via /api/recommend", scenario_recommend)


def main(argv: Optional[List[str]] = None) -> int:
    """Run the scenarios; return 0 if all pass, 1 otherwise."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"app base URL (default {DEFAULT_BASE_URL})")
    parser.add_argument("--recommend", action="store_true",
                        help="also call /api/recommend (uses the LLM if the server has a key)")
    parser.add_argument("--only", default="",
                        help="comma-separated scenario keys to run, e.g. 'a,d'")
    args = parser.parse_args(argv)
    base = args.base_url

    try:
        status, health = _request(base, "GET", "/api/health", timeout=10)
    except (urllib.error.URLError, OSError) as exc:
        print(f"cannot reach {base}: {exc}", file=sys.stderr)
        return 2
    print(f"target {base}  health HTTP {status}: {health}")

    scenarios = list(SCENARIOS) + ([RECOMMEND_SCENARIO] if args.recommend else [])
    if args.only:
        wanted = {k.strip() for k in args.only.split(",") if k.strip()}
        scenarios = [s for s in scenarios if s[0] in wanted]

    results: List[Tuple[str, str, List[str]]] = []
    for key, title, func in scenarios:
        print(f"\n=== {key}) {title} ===")
        check = Check()
        try:
            func(base, check)
        except Exception as exc:  # noqa: BLE001 - report and keep going
            check.failures.append(f"{type(exc).__name__}: {exc}")
        for failure in check.failures:
            print(f"  FAIL: {failure}")
        print(f"  -> {'PASS' if not check.failures else 'FAIL'}")
        results.append((key, title, check.failures))

    passed = sum(1 for _, _, f in results if not f)
    print(f"\n=== SUMMARY: {passed}/{len(results)} scenarios passed against {base} ===")
    for key, title, failures in results:
        print(f"  [{'PASS' if not failures else 'FAIL'}] {key}) {title}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

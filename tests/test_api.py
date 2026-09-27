# -*- coding: utf-8 -*-
"""Offline tests for the JSON API in ``app.py``.

Uses Flask's test client; ``agent.compare`` is monkeypatched in every
/api/recommend test so the gateway is never called. Runs via
``python -m pytest``.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent  # noqa: E402
import app as app_module  # noqa: E402
import gateway_client  # noqa: E402
import observability  # noqa: E402
import tools  # noqa: E402
from app import app  # noqa: E402
from mock_data import SKUS  # noqa: E402


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


@pytest.fixture()
def fake_agent(monkeypatch):
    """Replace ``agent.compare`` with a recorder that never hits the gateway."""
    calls = []

    def _fake(sku, quotes=None, weights=None, quantity=None):
        calls.append({"sku": sku, "quotes": quotes, "weights": weights,
                      "quantity": quantity})
        return {"request_id": "req-test", "top": [], "scores": [],
                "rationale": "stub", "injection_flag": False, "validated": True}

    monkeypatch.setattr(agent, "compare", _fake)
    return calls


# ---- read-only endpoints -------------------------------------------------- #

def test_placeholder_gateway_key_counts_as_unset(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_URL", "https://example.invalid")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "your-team-api-key-here")
    assert gateway_client.has_gateway_key() is False
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "real-key")
    assert gateway_client.has_gateway_key() is True


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "status": "ok",
        "skus": len(SKUS),
        "gateway_configured": gateway_client.has_gateway_key(),
    }


def test_products_include_quote_counts(client):
    products = client.get("/api/products").get_json()["products"]
    assert [p["sku"] for p in products] == SKUS
    counts = {p["sku"]: p["num_quotes"] for p in products}
    assert counts["BRK-100"] == 8
    assert all(n > 0 for n in counts.values())
    assert {"sku", "name", "category", "unit", "num_quotes"} <= set(products[0])


def test_quotes_for_sku(client):
    body = client.get("/api/quotes?sku=BRK-100").get_json()
    assert body["sku"] == "BRK-100"
    assert body["product"]["sku"] == "BRK-100"
    assert len(body["quotes"]) == 8
    assert {"supplier_id", "name", "unit_price", "moq"} <= set(body["quotes"][0])


def test_quotes_missing_and_unknown_sku(client):
    resp = client.get("/api/quotes")
    assert resp.status_code == 400 and "error" in resp.get_json()
    resp = client.get("/api/quotes?sku=NOPE-999")
    assert resp.status_code == 404 and "NOPE-999" in resp.get_json()["error"]


# ---- /api/compare --------------------------------------------------------- #

def test_compare_default(client):
    resp = client.post("/api/compare", json={"sku": "BRK-100"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["summary"]["winner_supplier_id"] == "SUP-001"
    assert body["ranked"][0]["supplier_id"] == "SUP-001"
    assert abs(sum(body["weights"].values()) - 1.0) < 1e-9
    assert "SUP-004" in body["injection_suppliers"]


def test_compare_weights_flip_brk100_winner(client):
    default = client.post("/api/compare", json={"sku": "BRK-100"}).get_json()
    price_heavy = client.post(
        "/api/compare", json={"sku": "BRK-100", "weights": {"price": 0.8}}
    ).get_json()
    assert default["summary"]["winner_supplier_id"] == "SUP-001"
    assert price_heavy["summary"]["winner_supplier_id"] == "SUP-002"


def test_compare_quantity_exclusion(client):
    body = client.post(
        "/api/compare",
        json={"sku": "BRK-100", "quantity": 600, "max_lead_time_days": 20},
    ).get_json()
    assert {r["supplier_id"] for r in body["ranked"]} == {"SUP-001", "SUP-006"}
    excluded = {e["supplier_id"]: e["reasons"] for e in body["excluded"]}
    assert any("MOQ 750" in r for r in excluded["SUP-005"])
    assert body["constraints"] == {"quantity": 600, "max_lead_time_days": 20}


@pytest.mark.parametrize("kwargs", [
    {"data": "not json", "content_type": "application/json"},
    {"json": ["BRK-100"]},
    {"json": {}},
    {"json": {"sku": ""}},
    {"json": {"sku": "BRK-100", "weights": [0.5]}},
    {"json": {"sku": "BRK-100", "weights": {"colour": 1}}},
    {"json": {"sku": "BRK-100", "weights": {"price": -1}}},
    {"json": {"sku": "BRK-100", "quantity": 0}},
    {"json": {"sku": "BRK-100", "quantity": "600"}},
    {"json": {"sku": "BRK-100", "max_lead_time_days": True}},
])
def test_compare_bad_request(client, kwargs):
    resp = client.post("/api/compare", **kwargs)
    assert resp.status_code == 400
    assert set(resp.get_json()) == {"error"}


def test_compare_unknown_sku(client):
    resp = client.post("/api/compare", json={"sku": "NOPE-999"})
    assert resp.status_code == 404
    assert "NOPE-999" in resp.get_json()["error"]


# ---- /api/recommend ------------------------------------------------------- #

def test_recommend_passes_normalized_weights(client, fake_agent):
    resp = client.post("/api/recommend",
                       json={"sku": "BRK-100", "weights": {"price": 0.8}})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["agent"]["rationale"] == "stub"
    assert body["compare"]["summary"]["winner_supplier_id"] == "SUP-002"
    assert fake_agent == [{"sku": "BRK-100", "quotes": tools.get_quotes("BRK-100"),
                           "weights": body["compare"]["weights"], "quantity": None}]


def test_recommend_passes_only_eligible_quotes(client, fake_agent):
    body = {"sku": "BRK-100", "quantity": 600, "max_lead_time_days": 10}
    resp = client.post("/api/recommend", json=body)
    assert resp.status_code == 200
    ranked_ids = [r["supplier_id"] for r in resp.get_json()["compare"]["ranked"]]
    assert ranked_ids == ["SUP-006"]
    assert [q["supplier_id"] for q in fake_agent[0]["quotes"]] == ranked_ids


def test_recommend_injection_flag_covers_excluded_quotes(client, fake_agent):
    # SUP-004 and SUP-007 (both flagged) have 25/28-day lead times, so a 24-day
    # cap excludes them.
    body = {"sku": "BRK-100", "max_lead_time_days": 24}
    resp = client.post("/api/recommend", json=body)
    assert resp.status_code == 200
    payload = resp.get_json()
    ranked_ids = [r["supplier_id"] for r in payload["compare"]["ranked"]]
    assert "SUP-004" not in ranked_ids and "SUP-007" not in ranked_ids
    assert payload["compare"]["injection_suppliers"] == ["SUP-004", "SUP-007"]
    assert payload["agent"]["injection_flag"] is True


def test_recommend_passes_quantity_to_agent(client, fake_agent):
    resp = client.post("/api/recommend", json={"sku": "BRK-100", "quantity": 550})
    assert resp.status_code == 200
    assert fake_agent[0]["quantity"] == 550


def test_recommend_agent_levers_match_compare_levers(client):
    # 550 is within 25% of SUP-001's MOQ of 500, so compare adds an MOQ lever.
    resp = client.post("/api/recommend", json={"sku": "BRK-100", "quantity": 550})
    assert resp.status_code == 200
    payload = resp.get_json()
    compare_levers = payload["compare"]["negotiation_levers"]
    assert any(lv["dimension"] == "moq" for e in compare_levers for lv in e["levers"])
    assert payload["agent"]["negotiation_levers"] == compare_levers
    top = payload["agent"]["recommendation"]["recommended_supplier_id"]
    top_moq = [lv["text"] for e in compare_levers if e["supplier_id"] == top
               for lv in e["levers"] if lv["dimension"] == "moq"]
    for text in top_moq:
        assert text in payload["agent"]["recommendation"]["negotiation_points"]

def test_recommend_agent_top_matches_compare_ranking(client):
    resp = client.post("/api/recommend",
                       json={"sku": "BRK-100", "quantity": 600, "max_lead_time_days": 20})
    assert resp.status_code == 200
    body = resp.get_json()
    compare_rows = [(r["supplier_id"], r["score"]) for r in body["compare"]["ranked"]]
    agent_rows = [(r["supplier_id"], r["score"]) for r in body["agent"]["top"]]
    assert agent_rows == compare_rows[:3]


def test_non_finite_numbers_rejected(client):
    for raw in ('{"sku":"BRK-100","quantity":Infinity}',
                '{"sku":"BRK-100","max_lead_time_days":1e400}',
                '{"sku":"BRK-100","weights":{"price":Infinity}}'):
        resp = client.post("/api/compare", data=raw, content_type="application/json")
        assert resp.status_code == 400, raw
        assert "finite" in resp.get_json()["error"]


def test_internal_key_error_is_500_not_404(client, monkeypatch):
    def _bug(**kwargs):
        raise KeyError("unit_price")

    monkeypatch.setattr(app_module, "compare_quotes", _bug)
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    resp = client.post("/api/compare", json={"sku": "BRK-100"})
    assert resp.status_code == 500
    assert "unknown sku" not in resp.get_json()["error"]


def test_recommend_validates_before_calling_agent(client, fake_agent):
    assert client.post("/api/recommend", json={"sku": "NOPE-999"}).status_code == 404
    assert client.post("/api/recommend",
                       json={"sku": "BRK-100", "quantity": -5}).status_code == 400
    assert fake_agent == []


def test_recommend_agent_failure_returns_502_with_numbers(client, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("secret gateway detail")

    monkeypatch.setattr(agent, "compare", _boom)
    resp = client.post("/api/recommend", json={"sku": "BRK-100"})
    assert resp.status_code == 502
    body = resp.get_json()
    assert "secret gateway detail" not in body["error"]
    assert body["compare"]["summary"]["winner_supplier_id"] == "SUP-001"


# ---- error format + legacy UI --------------------------------------------- #

def test_api_404_and_405_are_json(client):
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404 and set(resp.get_json()) == {"error"}
    resp = client.get("/api/compare")
    assert resp.status_code == 405 and set(resp.get_json()) == {"error"}
    resp = client.post("/api/health")
    assert resp.status_code == 405 and "error" in resp.get_json()


def test_non_api_404_stays_html(client):
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404
    assert resp.mimetype == "text/html"


def test_index_route_unchanged(client, fake_agent):
    assert client.get("/").status_code == 200
    # The legacy form still calls agent.compare(sku) with no weights.
    fake_agent_result = client.post("/", data={"sku": "GSK-200"})
    assert fake_agent_result.status_code == 200
    assert fake_agent == [{"sku": "GSK-200", "quotes": None, "weights": None,
                           "quantity": None}]


# ---- regressions from the Sprint 3 bug hunt ------------------------------- #

@pytest.mark.parametrize("field", ['"quantity":', '"max_lead_time_days":',
                                   '"weights":{"price":'])
def test_huge_json_int_is_400_not_500(client, monkeypatch, field):
    """A 401-digit JSON integer used to raise OverflowError -> generic 500."""
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    raw = '{"sku":"BRK-100",' + field + "1" + "0" * 400 + ("}}" if "{" in field[1:] else "}")
    resp = client.post("/api/compare", data=raw, content_type="application/json")
    assert resp.status_code == 400, resp.get_json()
    assert "finite" in resp.get_json()["error"]


def test_recommend_injection_details_cover_excluded_quotes(client):
    """The page shows agent.injection_details[id] for every compare.injection_suppliers id."""
    resp = client.post("/api/recommend", json={"sku": "BRK-100", "max_lead_time_days": 24})
    assert resp.status_code == 200
    body = resp.get_json()
    flagged = body["compare"]["injection_suppliers"]
    assert flagged == ["SUP-004", "SUP-007"]
    details = body["agent"]["injection_details"]
    assert sorted(details) == flagged
    assert any("Ignore previous" in s for s in details["SUP-004"])
    assert any("忽略之前的指令" in s for s in details["SUP-007"])


# ---- /api/decisions (human-in-the-loop) ------------------------------------ #

def _recommend(client, **body):
    resp = client.post("/api/recommend", json={"sku": "BRK-100", **body})
    assert resp.status_code == 200
    return resp.get_json()["agent"]


def _decide(client, **body):
    return client.post("/api/decisions", json=body)


def test_decision_approve_is_logged_next_to_the_agent_record(client):
    agent_out = _recommend(client)
    rid, top = agent_out["request_id"], agent_out["recommendation"]["recommended_supplier_id"]
    resp = _decide(client, request_id=rid, action="approve", supplier_id=top)
    assert resp.status_code == 201
    decision = resp.get_json()["decision"]
    assert decision["agrees_with_agent"] is True and decision["order_placed"] is False
    records = observability.read_records(rid)
    assert [r.get("type") for r in records] == [None, "human_decision"]
    assert records[1]["supplier_id"] == top


def test_decision_override_needs_a_compared_supplier_and_a_reason(client):
    agent_out = _recommend(client)
    rid = agent_out["request_id"]
    other = agent_out["scores"][1]["supplier_id"]
    assert _decide(client, request_id=rid, action="override", supplier_id=other).status_code == 400
    assert _decide(client, request_id=rid, action="override", supplier_id="SUP-999",
                   reason="cheaper overall").status_code == 400
    resp = _decide(client, request_id=rid, action="override", supplier_id=other,
                   reason="Existing framework contract with this supplier")
    assert resp.status_code == 201
    assert resp.get_json()["decision"]["agrees_with_agent"] is False


def test_decision_rules(client):
    agent_out = _recommend(client)
    rid, top = agent_out["request_id"], agent_out["recommendation"]["recommended_supplier_id"]
    other = agent_out["scores"][1]["supplier_id"]
    # approve must name the recommended supplier
    assert _decide(client, request_id=rid, action="approve", supplier_id=other).status_code == 400
    # overriding with the recommended supplier is an approve
    assert _decide(client, request_id=rid, action="override", supplier_id=top,
                   reason="just because").status_code == 400
    assert _decide(client, request_id=rid, action="order", supplier_id=top).status_code == 400
    assert _decide(client, request_id="req-nope", action="approve", supplier_id=top).status_code == 404
    assert _decide(client, action="approve", supplier_id=top).status_code == 400
    assert client.post("/api/decisions", data="x").status_code == 400
    # decided once; the audit trail is append-only
    assert _decide(client, request_id=rid, action="approve", supplier_id=top).status_code == 201
    again = _decide(client, request_id=rid, action="approve", supplier_id=top)
    assert again.status_code == 409 and "already has a decision" in again.get_json()["error"]


def test_decision_on_request_without_eligible_supplier_is_409(client):
    agent_out = _recommend(client, max_lead_time_days=1)
    assert agent_out["recommendation"] is None
    resp = _decide(client, request_id=agent_out["request_id"], action="approve", supplier_id="SUP-001")
    assert resp.status_code == 409


def test_concurrent_decisions_record_exactly_one(client):
    import threading
    agent_out = _recommend(client)
    rid, top = agent_out["request_id"], agent_out["recommendation"]["recommended_supplier_id"]
    results, barrier = [], threading.Barrier(8)

    def worker():
        barrier.wait()
        try:
            observability.record_human_decision(rid, "approve", top)
            results.append("ok")
        except observability.DecisionError as exc:
            results.append(exc.status)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results, key=str) == [409] * 7 + ["ok"]
    assert sum(r.get("type") == "human_decision" for r in observability.read_records(rid)) == 1


# ---- GET /api/decisions/<request_id> (audit record) ------------------------ #

def test_audit_record_shows_agent_record_then_the_human_decision(client):
    agent_out = _recommend(client)
    rid, top = agent_out["request_id"], agent_out["recommendation"]["recommended_supplier_id"]
    resp = client.get(f"/api/decisions/{rid}")
    assert resp.status_code == 200 and resp.mimetype == "application/json"
    body = resp.get_json()
    assert body["request_id"] == rid and body["human_decision"] is None
    assert body["agent"]["recommended_supplier_id"] == top
    assert set(body["agent"]) >= {"inputs", "scores", "tool_calls", "rationale", "usage", "errors"}
    assert b'\n  "agent"' in resp.data          # pretty-printed for a browser

    _decide(client, request_id=rid, action="approve", supplier_id=top)
    decided = client.get(f"/api/decisions/{rid}").get_json()
    assert decided["human_decision"]["action"] == "approve"
    assert decided["human_decision"]["order_placed"] is False


def test_audit_record_errors(client):
    assert client.get("/api/decisions/req-00000000").status_code == 404
    for bad in ["nope", "req-XYZ", "req-123456789", "..%2F..%2Fetc"]:
        resp = client.get(f"/api/decisions/{bad}")
        assert resp.status_code in (400, 404)
        assert "error" in resp.get_json()
    assert client.get("/api/decisions/req-zzzzzzzz").status_code == 400

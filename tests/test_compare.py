# -*- coding: utf-8 -*-
"""Offline tests for the CSV data layer, weight handling and compare service.

Runs without a gateway key via ``python -m pytest``.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mock_data  # noqa: E402
import security  # noqa: E402
import tools  # noqa: E402
from compare import UnknownSkuError, compare_quotes  # noqa: E402
from scoring import _parse_net_days, normalize_weights, score_suppliers  # noqa: E402


def _quote(sid: str, price: float, lead: int = 10, terms: str = "Net 30",
           moq: int = 100, otd: float = 0.95, quality: float = 4.5) -> dict:
    """Build a hand-made quote dict for SKU ``TEST-9``."""
    return {
        "sku": "TEST-9", "supplier_id": sid, "name": f"Name {sid}",
        "unit_price": price, "currency": "SGD", "lead_time_days": lead,
        "payment_terms": terms, "moq": moq, "on_time_delivery_rate": otd,
        "quality_rating": quality, "product_description": "Plain parts.",
    }


def test_data_shape_and_coverage():
    assert mock_data.SKUS[:3] == ["BRK-100", "GSK-200", "CBL-300"]
    assert set(mock_data.PRODUCTS_BY_SKU) == set(mock_data.SKUS)
    ids = [s["supplier_id"] for s in mock_data.SUPPLIERS]
    assert ids[:5] == ["SUP-001", "SUP-002", "SUP-003", "SUP-004", "SUP-005"]
    for sku in mock_data.SKUS:
        n = sum(q["sku"] == sku for s in mock_data.SUPPLIERS for q in s["quotes"])
        assert n >= (6 if sku == "BRK-100" else 4), sku


def test_loader_rejects_bad_rows(tmp_path):
    header = ",".join(mock_data._QUOTE_FIELDS)
    base = "SUP-X,X,SG,desc,BRK-100,1.0,SGD,5,Net 30,10,{otd},4.0"
    bad = tmp_path / "quotes.csv"
    bad.write_text(header + "\n" + base.format(otd="0.9") + "\n"
                   + base.format(otd="1.5") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="row 3"):
        mock_data.load_suppliers(str(bad), known_skus=["BRK-100"])
    with pytest.raises(ValueError, match="unknown sku"):
        mock_data.load_suppliers(str(bad), known_skus=["GSK-200"])


def test_parse_net_days():
    assert _parse_net_days("Net 30") == 30
    assert _parse_net_days("2/10 Net 30") == 30
    assert _parse_net_days("net60") == 60
    assert _parse_net_days("NET60") == 60
    assert _parse_net_days("30 days") == 30
    assert _parse_net_days("COD") == 0


def test_normalize_weights():
    assert normalize_weights(None) == pytest.approx(normalize_weights({}))
    w = normalize_weights({"price": 0.8})
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["price"] == pytest.approx(0.8 / 1.5)
    assert w["lead_time"] == pytest.approx(0.20 / 1.5)
    assert normalize_weights({k: 2 for k in w}) == pytest.approx({k: 0.2 for k in w})
    for bad in ({"foo": 1}, {"price": -0.1}, {"price": "x"},
                {k: 0 for k in w}, {"price": float("nan")},
                {"price": float("inf")}, {"price": 1e308, "lead_time": 1e308}):
        with pytest.raises(ValueError):
            normalize_weights(bad)


def test_score_rows_have_raw_and_weighted():
    row = score_suppliers(tools.get_quotes("BRK-100"))[0]
    assert set(row["weighted"]) == set(row["breakdown"])
    assert sum(row["weighted"].values()) == pytest.approx(row["score"], abs=1e-3)
    assert row["raw"]["net_days"] >= 0


def test_weight_change_flips_brk100_winner():
    default = compare_quotes("BRK-100")["summary"]["winner_supplier_id"]
    price_heavy = compare_quotes("BRK-100", weights={"price": 0.8})
    assert price_heavy["summary"]["winner_supplier_id"] != default


def test_injection_suppliers_never_win():
    for sku in mock_data.SKUS:
        result = compare_quotes(sku)
        assert result["summary"]["winner_supplier_id"] not in ("SUP-004", "SUP-007")
        for row in result["ranked"]:
            assert row["injection_flag"] == (row["supplier_id"] in result["injection_suppliers"])
    assert "SUP-004" in compare_quotes("BRK-100")["injection_suppliers"]
    assert "SUP-007" in compare_quotes("BRK-100")["injection_suppliers"]


def test_constraints_and_levers():
    result = compare_quotes("BRK-100", quantity=600, max_lead_time_days=20)
    excluded = {e["supplier_id"]: e["reasons"] for e in result["excluded"]}
    assert "MOQ 1000 exceeds order quantity 600" in excluded["SUP-002"]
    assert "lead time 30d exceeds max 20d" in excluded["SUP-003"]
    eligible = {r["supplier_id"] for r in result["ranked"]}
    assert eligible.isdisjoint(excluded)
    assert result["summary"]["num_eligible"] == len(result["ranked"])
    for entry in result["negotiation_levers"]:
        for lever in entry["levers"]:
            assert lever["gap"] > 0 and lever["text"]


def test_price_lever_text():
    levers = compare_quotes("BRK-100")["negotiation_levers"]
    acme = next(e for e in levers if e["supplier_id"] == "SUP-001")
    price = next(lv for lv in acme["levers"] if lv["dimension"] == "price")
    assert price["gap"] == 14.7
    assert "Meridian Industrial Supply (SGD 10.90)" in price["text"]


def test_all_excluded_and_errors():
    result = compare_quotes("BRK-100", quantity=1)
    assert result["ranked"] == [] and result["negotiation_levers"] == []
    assert result["summary"]["winner_supplier_id"] is None
    with pytest.raises(UnknownSkuError):
        compare_quotes("NOPE-999")
    with pytest.raises(ValueError):
        compare_quotes("BRK-100", quantity=0)
    with pytest.raises(ValueError):
        compare_quotes("BRK-100", max_lead_time_days=-1)


def test_five_skus_with_at_least_four_quotes_each():
    assert len(mock_data.SKUS) == 5
    for sku in mock_data.SKUS:
        assert len(tools.get_quotes(sku)) >= 4, sku


def test_weighted_sums_to_score_for_every_row():
    for sku in mock_data.SKUS:
        for row in compare_quotes(sku)["ranked"]:
            assert set(row["raw"]) >= {"unit_price", "lead_time_days", "net_days",
                                       "on_time_delivery_rate", "quality_rating"}
            assert sum(row["weighted"].values()) == pytest.approx(row["score"], abs=1e-3)


def test_sup004_flagged_and_never_default_winner():
    assert "SUP-004" in compare_quotes("BRK-100")["injection_suppliers"]
    for sku in mock_data.SKUS:
        assert compare_quotes(sku)["summary"]["winner_supplier_id"] != "SUP-004", sku


def test_single_quote():
    result = compare_quotes("TEST-9", quotes=[_quote("S1", 10.0)])
    assert [r["supplier_id"] for r in result["ranked"]] == ["S1"]
    assert result["ranked"][0]["score"] == pytest.approx(1.0)
    assert result["negotiation_levers"][0]["levers"] == []


def test_hand_made_price_gap_is_exact():
    quotes = [_quote("S1", 10.0), _quote("S2", 11.5), _quote("S3", 12.34)]
    result = compare_quotes("TEST-9", quotes=quotes)
    gaps = {e["supplier_id"]: {lv["dimension"]: lv for lv in e["levers"]}
            for e in result["negotiation_levers"]}
    assert "price" not in gaps["S1"]
    assert gaps["S2"]["price"]["gap"] == 15.0
    assert gaps["S2"]["price"]["benchmark_supplier_id"] == "S1"
    assert gaps["S3"]["price"]["gap"] == 23.4
    assert "Price is 15.0% above Name S1 (SGD 10.00)" in gaps["S2"]["price"]["text"]


def test_zero_benchmark_price_skips_price_lever():
    quotes = [_quote("S1", 0.0), _quote("S2", 1.0)]
    result = compare_quotes("TEST-9", quotes=quotes)
    dims = {e["supplier_id"]: [lv["dimension"] for lv in e["levers"]]
            for e in result["negotiation_levers"]}
    assert "price" not in dims["S1"]
    assert "price" not in dims["S2"]

def test_moq_headroom_lever():
    result = compare_quotes("TEST-9", quotes=[_quote("S1", 10.0, moq=100)], quantity=110)
    lever = result["negotiation_levers"][0]["levers"][0]
    assert (lever["dimension"], lever["gap"], lever["unit"]) == ("moq", 10, "units")


def test_determinism():
    kwargs = {"weights": {"price": 0.5}, "quantity": 600}
    assert compare_quotes("BRK-100", **kwargs) == compare_quotes("BRK-100", **kwargs)
    assert score_suppliers(tools.get_quotes("CBL-300")) == score_suppliers(
        list(reversed(tools.get_quotes("CBL-300"))))


def test_tie_break_by_price_then_supplier_id():
    # Price and lead time trade off exactly -> equal scores; cheaper wins.
    weights = {"price": 1, "lead_time": 1, "payment_terms": 0,
               "on_time_delivery_rate": 0, "quality_rating": 0}
    quotes = [_quote("S2", 12.0, lead=10), _quote("S1", 10.0, lead=20)]
    ranked = score_suppliers(quotes, normalize_weights(weights))
    assert ranked[0]["score"] == ranked[1]["score"] == 0.5
    assert [r["supplier_id"] for r in ranked] == ["S1", "S2"]
    # Fully identical quotes -> supplier_id order, regardless of input order.
    same = [_quote("S-B", 10.0), _quote("S-A", 10.0)]
    assert [r["supplier_id"] for r in score_suppliers(same)] == ["S-A", "S-B"]


def test_score_suppliers_keeps_raw_weight_semantics():
    """Direct callers (agent.compare) get HEAD behaviour: no merge, no rescale."""
    quotes = tools.get_quotes("BRK-100")
    only_price = score_suppliers(quotes, {"price": 1.0})
    by_price = sorted(quotes, key=lambda q: (q["unit_price"], q["supplier_id"]))
    assert [r["supplier_id"] for r in only_price] == [q["supplier_id"] for q in by_price]
    assert all(set(r["weighted"]) == set(r["breakdown"]) for r in only_price)
    assert score_suppliers(quotes, {"cost": 1.0})[0]["score"] == 0.0
    with pytest.raises(ValueError, match="finite"):
        score_suppliers(quotes, {"price": float("inf")})


def test_non_finite_constraints_rejected():
    for bad in (float("inf"), float("nan")):
        with pytest.raises(ValueError, match="finite"):
            compare_quotes("BRK-100", quantity=bad)
        with pytest.raises(ValueError, match="finite"):
            compare_quotes("BRK-100", max_lead_time_days=bad)


def test_no_zero_percent_price_lever():
    quotes = [_quote("S1", 10.0), _quote("S2", 10.001)]
    result = compare_quotes("TEST-9", quotes=quotes)
    for entry in result["negotiation_levers"]:
        assert all(lv["dimension"] != "price" for lv in entry["levers"])


def test_duplicate_supplier_quotes_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        compare_quotes("TEST-9", quotes=[_quote("S1", 6.25), _quote("S1", 12.5)])


def test_loader_rejects_non_finite_extra_fields_and_duplicates(tmp_path):
    header = ",".join(mock_data._QUOTE_FIELDS)
    row = "SUP-X,X,SG,desc,BRK-100,{price},SGD,5,Net 30,10,0.9,4.0"
    path = tmp_path / "quotes.csv"
    for price in ("nan", "inf", "-inf", "1e400"):
        path.write_text(header + "\n" + row.format(price=price) + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="row 2: invalid unit_price"):
            mock_data.load_suppliers(str(path), known_skus=["BRK-100"])
    path.write_text(header + "\n" + row.format(price="1.0") + ",extra\n", encoding="utf-8")
    with pytest.raises(ValueError, match="row 2: too many fields"):
        mock_data.load_suppliers(str(path), known_skus=["BRK-100"])
    path.write_text(header + "\n" + row.format(price="1.0") + "\n"
                    + row.format(price="2.0") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="row 3: duplicate quote"):
        mock_data.load_suppliers(str(path), known_skus=["BRK-100"])


def test_detector_catches_chinese_injection():
    """SUP-007 carries a Chinese injection."""
    desc = next(s["product_description"] for s in mock_data.SUPPLIERS
                if s["supplier_id"] == "SUP-007")
    assert "忽略之前的指令" in desc
    assert security.detect_injection(desc)

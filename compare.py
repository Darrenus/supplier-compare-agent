# -*- coding: utf-8 -*-
"""Deterministic supplier comparison service.

Builds the full, code-computed comparison for one SKU: hard-constraint
filtering (MOQ vs order quantity, maximum lead time), weighted ranking via
``scoring.score_suppliers``, best-in-class benchmarks, per-supplier
negotiation levers and prompt-injection flags. Every number is computed here;
an LLM may narrate the result but never changes it. Pure: no LLM, no I/O
beyond the read-only mock data, no logging.
"""
from __future__ import annotations

import numbers
from typing import Dict, List, Mapping, Optional

import security
import tools
from mock_data import PRODUCTS_BY_SKU
from scoring import (_parse_net_days, is_finite_number, normalize_weights,
                     score_suppliers, short_repr)

# Levers are generated for the Top-N ranked suppliers.
TOP_N_LEVERS = 3
# An eligible MOQ counts as "close" when the order quantity is < MOQ * this.
MOQ_HEADROOM_RATIO = 1.25

# dimension -> (quote field / extractor, higher_is_better)
_BEST_IN_CLASS = {
    "price": (lambda q: q["unit_price"], False),
    "lead_time": (lambda q: q["lead_time_days"], False),
    "payment_terms": (lambda q: _parse_net_days(q["payment_terms"]), True),
    "on_time_delivery_rate": (lambda q: q["on_time_delivery_rate"], True),
    "quality_rating": (lambda q: q["quality_rating"], True),
}


class UnknownSkuError(KeyError):
    """Raised by ``compare_quotes`` for an SKU with no product and no quotes."""


def _check_positive(name: str, value: Optional[float]) -> None:
    """Raise ``ValueError`` unless ``value`` is None or a finite positive number."""
    if value is None:
        return
    if (isinstance(value, bool) or not isinstance(value, numbers.Real)
            or not is_finite_number(value) or not value > 0):
        raise ValueError(f"{name} must be a finite positive number, got {short_repr(value)}")


def _name(quote: Mapping) -> str:
    """Display name of a quote's supplier (falls back to its id)."""
    return quote.get("name") or quote.get("supplier_id", "unknown")


def _exclusion_reasons(quote: Mapping, quantity: Optional[float],
                       max_lead_time_days: Optional[float]) -> List[str]:
    """Return the hard-constraint violations for one quote (empty = eligible)."""
    reasons: List[str] = []
    moq = quote.get("moq") or 0
    if quantity is not None and quantity < moq:
        reasons.append(f"MOQ {moq:g} exceeds order quantity {quantity:g}")
    lead = quote["lead_time_days"]
    if max_lead_time_days is not None and lead > max_lead_time_days:
        reasons.append(f"lead time {lead:g}d exceeds max {max_lead_time_days:g}d")
    return reasons


def _best_in_class(quotes: List[Dict]) -> Dict[str, Optional[Dict]]:
    """Best supplier per dimension over ``quotes``; ties go to supplier_id."""
    best: Dict[str, Optional[Dict]] = {}
    for dim, (extract, higher_is_better) in _BEST_IN_CLASS.items():
        if not quotes:
            best[dim] = None
            continue
        sign = -1 if higher_is_better else 1
        winner = min(quotes, key=lambda q: (sign * extract(q), str(q.get("supplier_id"))))
        best[dim] = {
            "supplier_id": winner.get("supplier_id"),
            "supplier": _name(winner),
            "value": extract(winner),
        }
    return best


def _levers_for(quote: Mapping, best: Mapping[str, Dict],
                quantity: Optional[float]) -> List[Dict]:
    """Code-computed negotiation levers for one eligible quote."""
    levers: List[Dict] = []
    currency = quote.get("currency") or "SGD"

    def add(dim: str, gap: float, unit: str, bench: Optional[Mapping], text: str,
            **extra) -> None:
        levers.append({
            "dimension": dim,
            "gap": gap,
            "unit": unit,
            "benchmark_supplier_id": bench["supplier_id"] if bench else None,
            "text": text,
            **extra,
        })

    b = best["price"]
    # A zero benchmark price has no meaningful percentage gap; skip the lever.
    gap = round((quote["unit_price"] - b["value"]) / b["value"] * 100, 1) if b["value"] > 0 else 0
    if gap > 0:
        # "x% above" is not the cut needed to match: 12.50 -> 10.90 is 14.7% above
        # but a 12.8% cut. State both so the LLM never confuses them.
        cut = round((quote["unit_price"] - b["value"]) / quote["unit_price"] * 100, 1)
        add("price", gap, "%", b,
            f"Price is {gap:.1f}% above {b['supplier']} ({currency} {b['value']:.2f})"
            f" — ask for a price match (a {cut:.1f}% cut)",
            cut_pct=cut)

    b = best["lead_time"]
    gap_days = quote["lead_time_days"] - b["value"]
    if gap_days > 0:
        add("lead_time", gap_days, "days", b,
            f"Lead time is {gap_days}d longer than {b['supplier']} ({b['value']}d)"
            " — ask for expedited slots or buffer stock")

    b = best["payment_terms"]
    net = _parse_net_days(quote["payment_terms"])
    gap_net = b["value"] - net
    if gap_net > 0:
        add("payment_terms", gap_net, "days", b,
            f"Net {net} vs Net {b['value']} at {b['supplier']}"
            f" — ask for {gap_net} more days of payment terms")

    b = best["on_time_delivery_rate"]
    gap_pp = round((b["value"] - quote["on_time_delivery_rate"]) * 100, 1)
    if gap_pp > 0:
        add("on_time_delivery_rate", gap_pp, "pp", b,
            f"On-time delivery {quote['on_time_delivery_rate'] * 100:.1f}% is {gap_pp:.1f} pp"
            f" below {b['supplier']} ({b['value'] * 100:.1f}%) — ask for a delivery SLA")

    moq = quote.get("moq") or 0
    if quantity is not None and moq > 0 and moq <= quantity < moq * MOQ_HEADROOM_RATIO:
        # The only lever whose gap can be 0: an order exactly at the MOQ.
        headroom = quantity - moq
        position = (f"is exactly the MOQ {moq:g}" if headroom == 0
                    else f"is only {headroom:g} units above MOQ {moq:g}")
        add("moq", headroom, "units", None,
            f"Order quantity {quantity:g} {position}"
            " — ask for a lower MOQ to keep flexibility")

    return levers


def compare_quotes(sku: str,
                   weights: Optional[Mapping[str, float]] = None,
                   quantity: Optional[float] = None,
                   max_lead_time_days: Optional[float] = None,
                   quotes: Optional[List[Dict]] = None) -> Dict:
    """Compare supplier quotes for one SKU under optional hard constraints.

    Args:
        sku: The SKU to compare (e.g. "BRK-100").
        weights: Optional (partial) weight overrides; see
            ``scoring.normalize_weights``.
        quantity: Optional order quantity; quotes with MOQ above it are excluded.
        max_lead_time_days: Optional cap; slower quotes are excluded.
        quotes: Candidate quotes; if None, loaded via ``tools.get_quotes``.

    Returns:
        A dict with ``sku``, ``product``, ``weights``, ``constraints``,
        ``ranked``, ``excluded``, ``best_in_class``, ``negotiation_levers``,
        ``injection_suppliers`` and ``summary``. Scores are relative to the
        eligible set only.

    Raises:
        UnknownSkuError: If the SKU is unknown and has no quotes (a
            ``KeyError`` subclass).
        ValueError: On invalid weights, quantity or max_lead_time_days, or
            when two quotes share a supplier_id or mix currencies.
    """
    norm_weights = normalize_weights(weights)
    _check_positive("quantity", quantity)
    _check_positive("max_lead_time_days", max_lead_time_days)

    if quotes is None:
        quotes = tools.get_quotes(sku)
    if not quotes and sku not in PRODUCTS_BY_SKU:
        raise UnknownSkuError(sku)
    seen_ids = set()
    for quote in quotes:
        sid = quote.get("supplier_id")
        if sid in seen_ids:
            # Levers are paired with ranked rows by supplier_id, so ids must be unique.
            raise ValueError(f"duplicate quote for supplier {sid!r} on sku {sku!r}")
        seen_ids.add(sid)
    # Prices are compared as plain numbers (no FX), so one currency per call.
    currencies = sorted({q.get("currency") or "SGD" for q in quotes})
    if len(currencies) > 1:
        raise ValueError(f"quotes for sku {sku!r} mix currencies {currencies};"
                         " convert them to one currency first")

    eligible: List[Dict] = []
    excluded: List[Dict] = []
    for quote in quotes:
        reasons = _exclusion_reasons(quote, quantity, max_lead_time_days)
        if reasons:
            excluded.append({
                "supplier_id": quote.get("supplier_id"),
                "supplier": _name(quote),
                "reasons": reasons,
            })
        else:
            eligible.append(quote)

    injection_suppliers: List[str] = []
    for quote in quotes:
        sid = quote.get("supplier_id")
        if (security.detect_injection(quote.get("product_description", ""))
                and sid not in injection_suppliers):
            injection_suppliers.append(sid)

    ranked = score_suppliers(eligible, norm_weights)
    for row in ranked:
        row["injection_flag"] = row["supplier_id"] in injection_suppliers

    best = _best_in_class(eligible)
    by_id = {q.get("supplier_id"): q for q in eligible}
    negotiation_levers = [
        {
            "supplier_id": row["supplier_id"],
            "supplier": row["supplier"],
            "levers": _levers_for(by_id[row["supplier_id"]], best, quantity),
        }
        for row in ranked[:TOP_N_LEVERS]
    ]

    return {
        "sku": sku,
        "product": dict(PRODUCTS_BY_SKU[sku]) if sku in PRODUCTS_BY_SKU else None,
        "weights": norm_weights,
        "constraints": {"quantity": quantity, "max_lead_time_days": max_lead_time_days},
        "ranked": ranked,
        "excluded": excluded,
        "best_in_class": best,
        "negotiation_levers": negotiation_levers,
        "injection_suppliers": injection_suppliers,
        "summary": {
            "num_quotes": len(quotes),
            "num_eligible": len(eligible),
            "winner_supplier_id": ranked[0]["supplier_id"] if ranked else None,
        },
    }

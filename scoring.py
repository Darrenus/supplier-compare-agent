# -*- coding: utf-8 -*-
"""Deterministic weighted supplier scoring.

Each dimension is normalized to 0-1 across the candidate set (so scoring is
relative to the compared group), then combined with configurable weights. This
module is pure and requires no gateway key.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

# Default dimension weights (sum to 1.0). Price and reliability dominate.
DEFAULT_WEIGHTS: Dict[str, float] = {
    "price": 0.30,
    "lead_time": 0.20,
    "payment_terms": 0.10,
    "on_time_delivery_rate": 0.20,
    "quality_rating": 0.20,
}


def _parse_net_days(payment_terms: str) -> int:
    """Extract the number of net days from a payment-terms string.

    "Net 30" -> 30. Unknown formats default to 0 (least favorable).
    """
    match = re.search(r"(\d+)", payment_terms or "")
    return int(match.group(1)) if match else 0


def _normalize(value: float, lo: float, hi: float, higher_is_better: bool) -> float:
    """Min-max normalize ``value`` into 0-1.

    If all candidates share the same value (hi == lo), returns 1.0 so the
    dimension does not penalize anyone arbitrarily.
    """
    if hi == lo:
        return 1.0
    scaled = (value - lo) / (hi - lo)
    return scaled if higher_is_better else 1.0 - scaled


def score_suppliers(quotes: List[Dict],
                    weights: Optional[Dict[str, float]] = None) -> List[Dict]:
    """Score and rank suppliers for a set of quotes.

    Dimensions:
      * price - lower is better
      * lead_time_days - lower is better
      * payment_terms - longer net days is better (more working capital)
      * on_time_delivery_rate - higher is better
      * quality_rating - higher is better

    Each dimension is normalized 0-1 across ``quotes`` before weighting.

    Args:
        quotes: Quote dicts (as returned by ``tools.get_quotes``).
        weights: Optional weight overrides; defaults to ``DEFAULT_WEIGHTS``.

    Returns:
        A list of ``{"supplier", "supplier_id", "score", "breakdown"}`` dicts
        sorted by descending score.
    """
    if not quotes:
        return []

    weights = weights or DEFAULT_WEIGHTS

    prices = [q["unit_price"] for q in quotes]
    lead_times = [q["lead_time_days"] for q in quotes]
    net_days = [_parse_net_days(q["payment_terms"]) for q in quotes]
    otd = [q["on_time_delivery_rate"] for q in quotes]
    quality = [q["quality_rating"] for q in quotes]

    p_lo, p_hi = min(prices), max(prices)
    lt_lo, lt_hi = min(lead_times), max(lead_times)
    nd_lo, nd_hi = min(net_days), max(net_days)
    otd_lo, otd_hi = min(otd), max(otd)
    q_lo, q_hi = min(quality), max(quality)

    ranked: List[Dict] = []
    for quote in quotes:
        breakdown = {
            "price": _normalize(quote["unit_price"], p_lo, p_hi, higher_is_better=False),
            "lead_time": _normalize(quote["lead_time_days"], lt_lo, lt_hi, higher_is_better=False),
            "payment_terms": _normalize(_parse_net_days(quote["payment_terms"]), nd_lo, nd_hi, higher_is_better=True),
            "on_time_delivery_rate": _normalize(quote["on_time_delivery_rate"], otd_lo, otd_hi, higher_is_better=True),
            "quality_rating": _normalize(quote["quality_rating"], q_lo, q_hi, higher_is_better=True),
        }
        score = sum(weights.get(dim, 0.0) * val for dim, val in breakdown.items())
        ranked.append({
            "supplier": quote.get("name", quote.get("supplier_id", "unknown")),
            "supplier_id": quote.get("supplier_id", "unknown"),
            "score": round(score, 4),
            "breakdown": {k: round(v, 4) for k, v in breakdown.items()},
        })

    ranked.sort(key=lambda r: r["score"], reverse=True)
    return ranked

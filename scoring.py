# -*- coding: utf-8 -*-
"""Deterministic weighted supplier scoring.

Each dimension is normalized to 0-1 across the candidate set (so scoring is
relative to the compared group), then combined with configurable weights. This
module is pure and requires no gateway key.
"""
from __future__ import annotations

import math
import numbers
import re
from typing import Dict, List, Mapping, Optional, Tuple

# Default dimension weights (sum to 1.0). Price and reliability dominate.
DEFAULT_WEIGHTS: Dict[str, float] = {
    "price": 0.30,
    "lead_time": 0.20,
    "payment_terms": 0.10,
    "on_time_delivery_rate": 0.20,
    "quality_rating": 0.20,
}

# Scoring dimensions, in breakdown order.
DIMENSIONS: Tuple[str, ...] = tuple(DEFAULT_WEIGHTS)

_NET_DAYS_RE = re.compile(r"net\s*(\d+)", re.IGNORECASE)


def normalize_weights(weights: Optional[Mapping[str, float]] = None) -> Dict[str, float]:
    """Validate weight overrides and renormalize them to sum to 1.0.

    Partial overrides are merged onto ``DEFAULT_WEIGHTS`` before renormalizing,
    e.g. ``{"price": 0.8}`` keeps the other defaults and rescales everything.

    Args:
        weights: Optional ``{dimension: weight}`` overrides. None means defaults.

    Returns:
        A new dict with one non-negative weight per dimension, summing to 1.0.

    Raises:
        ValueError: On unknown dimensions, non-numeric, non-finite or negative
            weights, or when every weight is zero.
    """
    merged = dict(DEFAULT_WEIGHTS)
    if weights is None:
        return merged
    for dim, value in weights.items():
        if dim not in DEFAULT_WEIGHTS:
            raise ValueError(
                f"unknown weight dimension {dim!r}; expected one of {list(DIMENSIONS)}"
            )
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            raise ValueError(f"weight for {dim!r} must be a number, got {value!r}")
        if not math.isfinite(value):
            raise ValueError(f"weight for {dim!r} must be finite, got {value!r}")
        if value < 0:
            raise ValueError(f"weight for {dim!r} must be >= 0, got {value}")
        merged[dim] = float(value)
    total = sum(merged.values())
    if not math.isfinite(total):
        raise ValueError("weights are too large; their sum overflows")
    if total <= 0:
        raise ValueError("at least one weight must be > 0")
    return {dim: merged[dim] / total for dim in DIMENSIONS}


def _parse_net_days(payment_terms: str) -> int:
    """Extract the number of net days from a payment-terms string.

    "Net 30" -> 30, "2/10 Net 30" -> 30. Without a "Net N" token the last
    integer is used; unknown formats default to 0 (least favorable).
    """
    text = payment_terms or ""
    match = _NET_DAYS_RE.search(text)
    if match:
        return int(match.group(1))
    numbers_found = re.findall(r"\d+", text)
    return int(numbers_found[-1]) if numbers_found else 0


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
        weights: Optional ``{dimension: weight}`` map, used as given: missing
            dimensions count as 0 and unknown keys are ignored. None means
            ``DEFAULT_WEIGHTS``. Callers that want partial overrides merged
            and renormalized pass the result of ``normalize_weights``.

    Returns:
        A list of ``{"supplier", "supplier_id", "score", "breakdown", "raw",
        "weighted"}`` dicts sorted by descending score; ties are broken by
        lower unit price, then supplier_id.

    Raises:
        ValueError: If a weight is not finite.
    """
    weights = weights or DEFAULT_WEIGHTS
    for dim, value in weights.items():
        if not math.isfinite(value):
            raise ValueError(f"weight for {dim!r} must be finite, got {value!r}")
    if not quotes:
        return []

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
        weighted = {dim: weights.get(dim, 0.0) * val for dim, val in breakdown.items()}
        score = sum(weighted.values())
        ranked.append({
            "supplier": quote.get("name", quote.get("supplier_id", "unknown")),
            "supplier_id": quote.get("supplier_id", "unknown"),
            "score": round(score, 4),
            "breakdown": {k: round(v, 4) for k, v in breakdown.items()},
            "raw": {
                "unit_price": quote["unit_price"],
                "lead_time_days": quote["lead_time_days"],
                "net_days": _parse_net_days(quote["payment_terms"]),
                "on_time_delivery_rate": quote["on_time_delivery_rate"],
                "quality_rating": quote["quality_rating"],
                "moq": quote.get("moq"),
                "payment_terms": quote["payment_terms"],
            },
            "weighted": {k: round(v, 4) for k, v in weighted.items()},
        })

    # Deterministic order: score desc, then cheaper, then supplier_id.
    ranked.sort(key=lambda r: (-r["score"], r["raw"]["unit_price"], str(r["supplier_id"])))
    return ranked

# -*- coding: utf-8 -*-
"""Read-only agent tools (Rubric #5.3).

These tools ONLY read from ``mock_data``. There are deliberately no write,
exec, or network tools - the agent cannot take side-effecting actions.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from mock_data import SUPPLIERS


def get_quotes(sku: str) -> List[Dict]:
    """Return every supplier quote for a given SKU.

    Args:
        sku: The SKU to look up (e.g. "BRK-100").

    Returns:
        A list of quote dicts, each augmented with ``supplier_id``, ``name``,
        ``region`` and the supplier's ``product_description`` (untrusted text).
    """
    results: List[Dict] = []
    for supplier in SUPPLIERS:
        for quote in supplier["quotes"]:
            if quote["sku"] == sku:
                enriched = dict(quote)
                enriched["supplier_id"] = supplier["supplier_id"]
                enriched["name"] = supplier["name"]
                enriched["region"] = supplier["region"]
                enriched["product_description"] = supplier["product_description"]
                results.append(enriched)
    return results


def get_supplier_profile(supplier_id: str) -> Optional[Dict]:
    """Return a supplier's profile (without its quotes list).

    Args:
        supplier_id: The supplier id to look up (e.g. "SUP-001").

    Returns:
        A profile dict, or None if no supplier matches.
    """
    for supplier in SUPPLIERS:
        if supplier["supplier_id"] == supplier_id:
            return {
                "supplier_id": supplier["supplier_id"],
                "name": supplier["name"],
                "region": supplier["region"],
                "product_description": supplier["product_description"],
            }
    return None


# Tool registry: name -> callable. Only read-only tools are registered.
TOOLS: Dict[str, Callable] = {
    "get_quotes": get_quotes,
    "get_supplier_profile": get_supplier_profile,
}

# -*- coding: utf-8 -*-
"""Mock supplier dataset loaded from CSV files under ``data/``.

This module is the single source of truth for the read-only tools in
``tools.py``. It loads ``data/products.csv`` (one row per SKU) and
``data/quotes.csv`` (long format, one row per supplier quote) with the
stdlib ``csv`` module and exposes the same structures as before:

  * ``SUPPLIERS`` - supplier profiles with nested ``quotes`` lists, in
    first-appearance order.
  * ``SKUS`` - known SKUs in ``products.csv`` order.
  * ``PRODUCTS`` / ``PRODUCTS_BY_SKU`` - product master data.

Two suppliers embed prompt-injection text in ``product_description`` so the
adversarial eval has something to catch (SUP-004 English, SUP-007 Chinese).
Rows are validated on load; bad data raises ``ValueError`` with the row number.
"""
from __future__ import annotations

import csv
import math
import os
from typing import Dict, List, Tuple

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PRODUCTS_CSV = os.path.join(DATA_DIR, "products.csv")
QUOTES_CSV = os.path.join(DATA_DIR, "quotes.csv")

_PRODUCT_FIELDS = ("sku", "name", "category", "unit")
_QUOTE_FIELDS = (
    "supplier_id", "supplier_name", "region", "product_description", "sku",
    "unit_price", "currency", "lead_time_days", "payment_terms", "moq",
    "on_time_delivery_rate", "quality_rating",
)


def _read_rows(path: str, required: Tuple[str, ...]) -> List[Tuple[int, Dict[str, str]]]:
    """Read a CSV file and return ``(row_number, row)`` pairs.

    Row numbers are 1-based file lines (the header is line 1). Raises
    ``ValueError`` if any required column is missing or a row has more fields
    than the header.
    """
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{os.path.basename(path)}: missing columns {missing}")
        rows: List[Tuple[int, Dict[str, str]]] = []
        for i, row in enumerate(reader, start=2):
            # DictReader stores surplus values in a list under the key None.
            if None in row:
                raise ValueError(f"{os.path.basename(path)} row {i}: too many fields")
            rows.append((i, {k: (v or "").strip() for k, v in row.items()}))
        return rows


def _to_number(raw: str, cast, field: str, where: str):
    """Convert ``raw`` with ``cast`` or raise ``ValueError`` naming the row.

    Non-finite results (``nan``, ``inf``) are rejected as invalid too.
    """
    try:
        value = cast(raw)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{where}: invalid {field} {raw!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"{where}: invalid {field} {raw!r}")
    return value


def load_products(path: str = PRODUCTS_CSV) -> List[Dict]:
    """Load and validate product master data.

    Args:
        path: Path to ``products.csv``.

    Returns:
        A list of ``{sku, name, category, unit}`` dicts in file order.
    """
    products: List[Dict] = []
    seen = set()
    for row_no, row in _read_rows(path, _PRODUCT_FIELDS):
        where = f"products.csv row {row_no}"
        sku = row["sku"]
        if not sku:
            raise ValueError(f"{where}: empty sku")
        if sku in seen:
            raise ValueError(f"{where}: duplicate sku {sku!r}")
        seen.add(sku)
        products.append({k: row[k] for k in _PRODUCT_FIELDS})
    return products


def load_suppliers(path: str = QUOTES_CSV, known_skus: List[str] = ()) -> List[Dict]:
    """Load and validate quotes, grouped into supplier profiles.

    Args:
        path: Path to ``quotes.csv``.
        known_skus: Valid SKUs; a quote for any other SKU is rejected.

    Returns:
        Supplier dicts ``{supplier_id, name, region, product_description,
        quotes}`` in first-appearance order.

    Raises:
        ValueError: On a malformed row or a duplicate (supplier_id, sku) pair.
    """
    by_id: Dict[str, Dict] = {}
    known = set(known_skus)
    seen_pairs = set()
    for row_no, row in _read_rows(path, _QUOTE_FIELDS):
        where = f"quotes.csv row {row_no}"
        if not row["supplier_id"]:
            raise ValueError(f"{where}: empty supplier_id")
        if row["sku"] not in known:
            raise ValueError(f"{where}: unknown sku {row['sku']!r}")
        pair = (row["supplier_id"], row["sku"])
        if pair in seen_pairs:
            raise ValueError(f"{where}: duplicate quote for supplier "
                             f"{pair[0]!r} and sku {pair[1]!r}")
        seen_pairs.add(pair)

        unit_price = _to_number(row["unit_price"], float, "unit_price", where)
        lead_time = _to_number(row["lead_time_days"], int, "lead_time_days", where)
        moq = _to_number(row["moq"], int, "moq", where)
        otd = _to_number(row["on_time_delivery_rate"], float, "on_time_delivery_rate", where)
        quality = _to_number(row["quality_rating"], float, "quality_rating", where)
        if unit_price <= 0:
            raise ValueError(f"{where}: unit_price must be > 0, got {unit_price}")
        if lead_time < 0 or moq < 0:
            raise ValueError(f"{where}: lead_time_days and moq must be >= 0")
        if not 0.0 <= otd <= 1.0:
            raise ValueError(f"{where}: on_time_delivery_rate {otd} outside 0-1")
        if not 0.0 <= quality <= 5.0:
            raise ValueError(f"{where}: quality_rating {quality} outside 0-5")

        supplier = by_id.get(row["supplier_id"])
        if supplier is None:
            supplier = {
                "supplier_id": row["supplier_id"],
                "name": row["supplier_name"],
                "region": row["region"],
                "product_description": row["product_description"],
                "quotes": [],
            }
            by_id[row["supplier_id"]] = supplier
        supplier["quotes"].append({
            "sku": row["sku"],
            "unit_price": unit_price,
            "currency": row["currency"],
            "lead_time_days": lead_time,
            "payment_terms": row["payment_terms"],
            "moq": moq,
            "on_time_delivery_rate": otd,
            "quality_rating": quality,
        })
    # dicts preserve insertion order, i.e. first appearance in the CSV.
    return list(by_id.values())


PRODUCTS: List[Dict] = load_products()
PRODUCTS_BY_SKU: Dict[str, Dict] = {p["sku"]: p for p in PRODUCTS}
# Known SKUs for convenience / validation.
SKUS: List[str] = [p["sku"] for p in PRODUCTS]
SUPPLIERS: List[Dict] = load_suppliers(known_skus=SKUS)

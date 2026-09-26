# -*- coding: utf-8 -*-
"""Minimal Flask web UI + JSON API for the supplier-comparison agent (#6.A display).

Run with:  python -m app     (binds 0.0.0.0:8080 for the Lightsail live site)

Presents a form to pick an SKU, calls ``agent.compare``, and renders the ranked
suppliers with a "Why this supplier?" expandable panel showing the per-dimension
breakdown and the rationale.

JSON API (contract in ``docs/API.md``):
    GET  /api/health     liveness + whether a gateway key is configured
    GET  /api/products   product master data with quote counts
    GET  /api/quotes     raw quotes for one SKU (``?sku=``)
    POST /api/compare    deterministic comparison (``compare.compare_quotes``)
    POST /api/recommend  comparison + LLM narration (``agent.compare``)
Every ``/api/*`` error is ``{"error": "<message>"}`` with a 4xx/5xx status; no
stack traces are returned.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

import agent
import gateway_client
import security
import tools
from compare import UnknownSkuError, compare_quotes
from mock_data import PRODUCTS, PRODUCTS_BY_SKU, SKUS

app = Flask(__name__)

API_PREFIX = "/api/"


@app.route("/", methods=["GET", "POST"])
def index():
    """Render the SKU picker and, on submit, the ranked comparison."""
    selected_sku = request.form.get("sku") or (SKUS[0] if SKUS else "")
    result = None
    if request.method == "POST" and selected_sku:
        result = agent.compare(selected_sku)
    return render_template(
        "index.html",
        skus=SKUS,
        selected_sku=selected_sku,
        result=result,
    )


# --------------------------------------------------------------------------- #
# JSON API
# --------------------------------------------------------------------------- #

def _error(message: str, status: int, **extra: Any):
    """Build a JSON error response ``{"error": message, **extra}``."""
    body: Dict[str, Any] = {"error": message}
    body.update(extra)
    return jsonify(body), status


def _parse_compare_body() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse and shape-check the /api/compare and /api/recommend JSON body.

    Value checks (weights, positive numbers) are left to ``compare_quotes``,
    which raises ``ValueError``.

    Returns:
        ``(kwargs, None)`` on success, or ``(None, error_message)``.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, "request body must be a JSON object"
    sku = body.get("sku")
    if not isinstance(sku, str) or not sku.strip():
        return None, "'sku' is required and must be a non-empty string"
    weights = body.get("weights")
    if weights is not None and not isinstance(weights, dict):
        return None, "'weights' must be an object like {\"price\": 0.5}"
    return {
        "sku": sku.strip(),
        "weights": weights,
        "quantity": body.get("quantity"),
        "max_lead_time_days": body.get("max_lead_time_days"),
    }, None


def _run_compare(kwargs: Dict[str, Any]):
    """Run ``compare_quotes`` and map its exceptions to ``(result, error)``.

    Only ``UnknownSkuError`` maps to 404; any other ``KeyError`` is a bug and
    falls through to the generic JSON 500 handler.
    """
    try:
        return compare_quotes(**kwargs), None
    except UnknownSkuError:
        return None, _error(f"unknown sku {kwargs['sku']!r}", 404)
    except ValueError as exc:
        return None, _error(str(exc), 400)


@app.get("/api/health")
def api_health():
    """Liveness probe; also reports whether LLM narration is available."""
    return jsonify({
        "status": "ok",
        "skus": len(SKUS),
        "gateway_configured": gateway_client.has_gateway_key(),
    })


@app.get("/api/products")
def api_products():
    """List product master data, each with its number of quotes."""
    products = [
        dict(p, num_quotes=len(tools.get_quotes(p["sku"]))) for p in PRODUCTS
    ]
    return jsonify({"products": products})


@app.get("/api/quotes")
def api_quotes():
    """Return every raw supplier quote for ``?sku=``."""
    sku = (request.args.get("sku") or "").strip()
    if not sku:
        return _error("query parameter 'sku' is required", 400)
    if sku not in PRODUCTS_BY_SKU:
        return _error(f"unknown sku {sku!r}", 404)
    return jsonify({
        "sku": sku,
        "product": PRODUCTS_BY_SKU[sku],
        "quotes": tools.get_quotes(sku),
    })


@app.post("/api/compare")
def api_compare():
    """Deterministic comparison (no LLM) for one SKU under optional constraints."""
    kwargs, message = _parse_compare_body()
    if message:
        return _error(message, 400)
    result, err = _run_compare(kwargs)
    if err:
        return err
    return jsonify(result)


@app.post("/api/recommend")
def api_recommend():
    """Deterministic comparison plus the agent's narrated Top-3.

    The comparison validates the request first; the agent then runs with the
    normalized weights on the eligible quotes only, so its Top-3 matches
    ``compare.ranked``. If the agent fails, the numbers are still returned
    with status 502.
    """
    kwargs, message = _parse_compare_body()
    if message:
        return _error(message, 400)
    result, err = _run_compare(kwargs)
    if err:
        return err
    eligible_ids = {row["supplier_id"] for row in result["ranked"]}
    eligible = [q for q in tools.get_quotes(kwargs["sku"])
                if q.get("supplier_id") in eligible_ids]
    try:
        # An empty list is passed through as is: agent.compare only reloads
        # quotes when the argument is None.
        agent_result = agent.compare(kwargs["sku"], quotes=eligible,
                                     weights=result["weights"],
                                     quantity=kwargs["quantity"])
    except Exception as exc:  # noqa: BLE001 - never leak a stack trace
        app.logger.exception("agent.compare failed for %s", kwargs["sku"])
        return _error(f"agent narration failed ({type(exc).__name__})", 502,
                      compare=result)
    # The agent only scans eligible quotes; also report injections from
    # excluded ones, so agent.injection_flag agrees with
    # compare.injection_suppliers and the page can show every matched snippet.
    agent_result["injection_flag"] = (bool(agent_result.get("injection_flag"))
                                      or bool(result["injection_suppliers"]))
    details = dict(agent_result.get("injection_details") or {})
    descriptions = {q.get("supplier_id"): q.get("product_description", "")
                    for q in tools.get_quotes(kwargs["sku"])}
    for sid in result["injection_suppliers"]:
        if sid not in details:
            details[sid] = security.find_injections(descriptions.get(sid, ""))
    agent_result["injection_details"] = details
    return jsonify({"compare": result, "agent": agent_result})


@app.errorhandler(HTTPException)
def _handle_http_error(exc: HTTPException):
    """Return JSON errors (404, 405, 500, ...) under /api/; default HTML elsewhere.

    Unhandled exceptions reach this handler as ``InternalServerError`` (outside
    debug/testing), so /api/ clients get a generic JSON 500 and the traceback
    only goes to the server log.
    """
    if request.path.startswith(API_PREFIX):
        return _error(exc.description or exc.name, exc.code or 500)
    return exc


if __name__ == "__main__":
    # 0.0.0.0:8080 so the Lightsail box can serve the live site to judges.
    app.run(host="0.0.0.0", port=8080, debug=False)

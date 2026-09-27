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
    GET  /api/decisions/<request_id>  the audit record: agent + buyer decision
    POST /api/decisions  the buyer approves or overrides a recommendation
                         (appended to the decision log; nothing is ordered)
Every ``/api/*`` error is ``{"error": "<message>"}`` with a 4xx/5xx status; no
stack traces are returned.
"""
from __future__ import annotations

import json
import os
import queue
import random
import threading
import time
from typing import Any, Dict, Optional, Tuple

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

import agent
import gateway_client
import observability
import security
import tools
from compare import UnknownSkuError, compare_quotes
from mock_data import PRODUCTS, PRODUCTS_BY_SKU, SKUS

app = Flask(__name__)

API_PREFIX = "/api/"


def _asset_version() -> str:
    """Cache-busting token for the static assets (based on their mtime)."""
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    try:
        return str(int(max(os.path.getmtime(os.path.join(static_dir, name))
                           for name in ("workspace.css", "workspace.js"))))
    except OSError:
        return "0"

# The deterministic steps (tool load, scan, scoring, levers) happen within a
# few milliseconds. They are spaced out on the streaming endpoint so the UI
# reveals the agent trace one step at a time instead of all at once.
STEP_PACING_SECONDS = 0.32

# While the model is thinking (20-40 s), a new "thinking" line is emitted every
# few seconds so the trace keeps moving instead of sitting still.
THINKING_NOTES = [
    "Reading the quotes",
    "Comparing price against the benchmark",
    "Weighing lead time against reliability",
    "Reviewing the supplier descriptions",
    "Checking the security scan",
    "Applying your priorities to the trade-offs",
    "Drafting the recommendation",
]
THINKING_NOTE_MIN_SECONDS = 2.0
THINKING_NOTE_MAX_SECONDS = 7.0


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
        asset_version=_asset_version(),
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
    """Deterministic comparison plus the agent's recommendation.

    The comparison validates the request first; the agent then runs on the
    eligible quotes with the normalized weights. The agent's ``top`` (its
    reference ranking) matches ``compare.ranked``, but its final
    ``recommendation`` is the model's own choice and may deviate from the
    code's #1. If the agent fails, the numbers are still returned with
    status 502.
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


@app.post("/api/recommend/stream")
def api_recommend_stream():
    """Stream the agent's reasoning, tool calls and final recommendation.

    Same validation and reference ranking as ``/api/recommend``, but the
    response is newline-delimited JSON (NDJSON): one event per line as the
    agent works, ending with ``{"type": "result", ...}`` carrying the full
    ``{"compare", "agent"}`` payload. A ``{"type": "error", ...}`` event is
    sent instead if the agent narration fails (the ``compare`` numbers are
    still attached to it).
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

    events: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()

    def _merge_injections(agent_result: Dict[str, Any]) -> Dict[str, Any]:
        # Match compare.injection_suppliers (covers flagged quotes that were
        # excluded by constraints, so the page can show every matched snippet).
        agent_result["injection_flag"] = (bool(agent_result.get("injection_flag"))
                                          or bool(result["injection_suppliers"]))
        details = dict(agent_result.get("injection_details") or {})
        descriptions = {q.get("supplier_id"): q.get("product_description", "")
                        for q in tools.get_quotes(kwargs["sku"])}
        for sid in result["injection_suppliers"]:
            if sid not in details:
                details[sid] = security.find_injections(descriptions.get(sid, ""))
        agent_result["injection_details"] = details
        return agent_result

    def run() -> None:
        last_emit = [0.0]

        def emit(event: Dict[str, Any]) -> None:
            # Pace the near-instant steps so the trace reveals one at a time.
            now = time.monotonic()
            wait = STEP_PACING_SECONDS - (now - last_emit[0])
            if wait > 0:
                time.sleep(wait)
            last_emit[0] = time.monotonic()
            events.put(event)

        # A ticker keeps the trace moving while the model thinks: a fresh
        # "thinking" line every 2-7 s, stopped as soon as the run finishes.
        stop_ticker = threading.Event()

        def ticker() -> None:
            index = 0
            while not stop_ticker.wait(random.uniform(THINKING_NOTE_MIN_SECONDS,
                                                     THINKING_NOTE_MAX_SECONDS)):
                events.put({"type": "note", "text": THINKING_NOTES[index % len(THINKING_NOTES)]})
                index += 1

        threading.Thread(target=ticker, daemon=True).start()
        # The read-only data load is shown as the agent's first tool step.
        emit({"type": "tool_call", "tool": "get_quotes", "args": {"sku": kwargs["sku"]}})
        emit({"type": "tool_result", "tool": "get_quotes",
              "detail": f"{len(eligible)} eligible quotes"})
        try:
            agent_result = agent.compare(kwargs["sku"], quotes=eligible,
                                         weights=result["weights"],
                                         quantity=kwargs["quantity"], on_event=emit)
            agent_result = _merge_injections(agent_result)
            events.put({"type": "result", "compare": result, "agent": agent_result})
        except Exception as exc:  # noqa: BLE001 - never leak a stack trace
            app.logger.exception("agent.compare failed for %s", kwargs["sku"])
            events.put({"type": "error",
                        "error": f"agent narration failed ({type(exc).__name__})",
                        "compare": result})
        finally:
            stop_ticker.set()
            events.put(None)  # sentinel: end of stream

    threading.Thread(target=run, daemon=True).start()

    def generate():
        while True:
            event = events.get()
            if event is None:
                break
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return Response(generate(), mimetype="application/x-ndjson",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@app.get("/api/decisions/<request_id>")
def api_decision_record(request_id: str):
    """Read-only audit record for one request (Rubric #6.A).

    Everything the decision log holds for ``request_id``: the agent's record
    (inputs, scores, tool calls, rationale, token usage, errors) and the
    buyer's decision, if any. Pretty-printed so it reads well in a browser.
    """
    if not observability.REQUEST_ID_RE.match(request_id):
        return _error("request_id must look like 'req-' followed by 8 hex characters", 400)
    record = observability.audit_record(request_id)
    if record is None:
        return _error(f"unknown request_id {request_id!r}", 404)
    return app.response_class(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                              mimetype="application/json")


@app.post("/api/decisions")
def api_decisions():
    """Record the human buyer's decision on a logged recommendation (Rubric #4).

    Body: ``{"request_id", "action": "approve"|"override", "supplier_id",
    "reason"?}``. The decision is appended to ``decisions.jsonl`` next to the
    agent's record with the same ``request_id``. No order is placed.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error("request body must be a JSON object", 400)
    fields = {k: body.get(k) for k in ("request_id", "action", "supplier_id", "reason")}
    for key in ("request_id", "action", "supplier_id"):
        if not isinstance(fields[key], str) or not fields[key].strip():
            return _error(f"'{key}' is required and must be a non-empty string", 400)
    if fields["reason"] is not None and not isinstance(fields["reason"], str):
        return _error("'reason' must be a string", 400)
    try:
        record = observability.record_human_decision(
            fields["request_id"].strip(), fields["action"].strip(),
            fields["supplier_id"].strip(), fields["reason"])
    except observability.DecisionError as exc:
        return _error(str(exc), exc.status)
    return jsonify({"decision": record}), 201


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

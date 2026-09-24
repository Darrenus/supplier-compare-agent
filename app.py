# -*- coding: utf-8 -*-
"""Minimal Flask web UI for the supplier-comparison agent (#6.A display).

Run with:  python -m app     (binds 0.0.0.0:8080 for the Lightsail live site)

Presents a form to pick an SKU, calls ``agent.compare``, and renders the ranked
suppliers with a "Why this supplier?" expandable panel showing the per-dimension
breakdown and the rationale.
"""
from __future__ import annotations

from flask import Flask, render_template, request

import agent
from mock_data import SKUS

app = Flask(__name__)


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


if __name__ == "__main__":
    # 0.0.0.0:8080 so the Lightsail box can serve the live site to judges.
    app.run(host="0.0.0.0", port=8080, debug=False)

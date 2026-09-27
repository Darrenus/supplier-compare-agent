# -*- coding: utf-8 -*-
"""Live-site stability checker for the supplier-comparison agent (stdlib only).

Usage:
    python scripts/check_live.py [--base-url http://56.10.70.203]
                                 [--recommend] [--timeout S]
                                 [--skip-gateway-check]

Runs a fixed set of read-only smoke checks against a running deployment and
prints one ``PASS``/``FAIL`` line per check with its latency. Exits 1 if any
check fails, so it can be dropped into cron or an uptime monitor, e.g.::

    */5 * * * * python3 /path/scripts/check_live.py >> /tmp/check_live.log 2>&1

Checks (no LLM tokens are spent unless ``--recommend`` is given):
    * ``GET /``                  200 and the page title is present
    * ``GET /api/health``        status ok, gateway_configured true, skus == 5
    * ``GET /api/products``      5 products, each with quotes
    * ``GET /api/quotes``        quotes for BRK-100
    * ``POST /api/compare``      200 and a winner for every SKU
    * ``POST /api/compare``      400 on a bad body
    * ``POST /api/compare``      400 on a 401-digit quantity (was 500 before
                                 the Sprint 3 fixes, so it also checks the
                                 deployed build is current)
    * ``POST /api/compare``      404 on an unknown SKU
    * ``GET /api/nope``          JSON 404 for an unknown API path
    * ``POST /api/recommend``    (only with ``--recommend``) one real LLM call

``GET /`` never triggers the agent; only ``POST /`` and ``/api/recommend`` do,
and those are the only paths that append to ``decisions.jsonl``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

DEFAULT_BASE_URL = "http://56.10.70.203"
# Any <title> naming the app; the #18 redesign renamed it to
# "Supplier Compare · Decision workspace".
TITLE_RE = re.compile(r"<title>[^<]*Supplier Compar[^<]*</title>")
EXPECTED_SKUS = ["BRK-100", "GSK-200", "CBL-300", "BRG-400", "PCB-500"]
# Deterministic endpoints should answer well under this; slower is a warning
# sign (overloaded workers) even if the body is correct.
SLOW_MS = 2000.0
USER_AGENT = "supplier-agent-check-live/1.0"


class Response:
    """A minimal HTTP response: status, headers, raw body and latency."""

    def __init__(self, status: int, headers: Dict[str, str], body: bytes,
                 elapsed_ms: float) -> None:
        self.status = status
        self.headers = headers
        self.body = body
        self.elapsed_ms = elapsed_ms

    def json(self) -> Any:
        """Decode the body as JSON (raises ``ValueError`` if it is not)."""
        return json.loads(self.body.decode("utf-8"))

    def text(self) -> str:
        """Decode the body as UTF-8 text, replacing bad bytes."""
        return self.body.decode("utf-8", errors="replace")


def http(base_url: str, method: str, path: str, timeout: float,
         payload: Optional[Any] = None, raw_body: Optional[bytes] = None) -> Response:
    """Send one request and return a ``Response`` even for 4xx/5xx statuses.

    Network errors (refused, DNS, timeout) propagate as exceptions.
    """
    data = raw_body
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base_url.rstrip("/") + path, data=data,
                                 headers=headers, method=method)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = resp.status
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
        hdrs = {k.lower(): v for k, v in (exc.headers or {}).items()}
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return Response(status, hdrs, body, elapsed_ms)


CheckFn = Callable[[], Tuple[bool, str, Optional[Response]]]


def _expect_status(resp: Response, status: int) -> Optional[str]:
    """Return an error message if ``resp.status`` differs from ``status``."""
    if resp.status != status:
        return f"expected HTTP {status}, got {resp.status}: {resp.text()[:120]!r}"
    return None


def build_checks(base_url: str, timeout: float, recommend: bool,
                 gateway_check: bool) -> List[Tuple[str, CheckFn]]:
    """Return the ordered list of ``(name, check)`` pairs to run."""

    def get(path: str) -> Response:
        return http(base_url, "GET", path, timeout)

    def post(path: str, payload: Any = None, raw: Optional[bytes] = None,
             t: Optional[float] = None) -> Response:
        return http(base_url, "POST", path, t or timeout, payload=payload, raw_body=raw)

    def check_index():
        r = get("/")
        err = _expect_status(r, 200)
        if err:
            return False, err, r
        title = TITLE_RE.search(r.text())
        if not title:
            return False, "page title missing", r
        return True, f"{title.group(0)[7:-8]} · {len(r.body)} bytes", r

    def check_health():
        r = get("/api/health")
        err = _expect_status(r, 200)
        if err:
            return False, err, r
        body = r.json()
        problems = []
        if body.get("status") != "ok":
            problems.append(f"status={body.get('status')!r}")
        if body.get("skus") != len(EXPECTED_SKUS):
            problems.append(f"skus={body.get('skus')!r}")
        if gateway_check and body.get("gateway_configured") is not True:
            problems.append("gateway_configured is not true (LLM narration off)")
        if problems:
            return False, "; ".join(problems), r
        return True, json.dumps(body, sort_keys=True), r

    def check_products():
        r = get("/api/products")
        err = _expect_status(r, 200)
        if err:
            return False, err, r
        products = r.json().get("products") or []
        skus = [p.get("sku") for p in products]
        if sorted(skus) != sorted(EXPECTED_SKUS):
            return False, f"unexpected SKUs {skus}", r
        empty = [p["sku"] for p in products if not p.get("num_quotes")]
        if empty:
            return False, f"SKUs without quotes: {empty}", r
        return True, f"{len(products)} products", r

    def check_quotes():
        r = get("/api/quotes?sku=BRK-100")
        err = _expect_status(r, 200)
        if err:
            return False, err, r
        quotes = r.json().get("quotes") or []
        if not quotes:
            return False, "no quotes for BRK-100", r
        return True, f"{len(quotes)} quotes", r

    def make_compare(sku: str) -> CheckFn:
        def check_compare():
            r = post("/api/compare", {"sku": sku})
            err = _expect_status(r, 200)
            if err:
                return False, err, r
            body = r.json()
            winner = (body.get("summary") or {}).get("winner_supplier_id")
            ranked = body.get("ranked") or []
            if not winner or not ranked or ranked[0].get("supplier_id") != winner:
                return False, f"no consistent winner (winner={winner!r})", r
            detail = f"winner={winner} ranked={len(ranked)}"
            if r.elapsed_ms > SLOW_MS:
                return False, f"slow ({r.elapsed_ms:.0f} ms > {SLOW_MS:.0f}); {detail}", r
            return True, detail, r
        return check_compare

    def check_bad_body():
        r = post("/api/compare", raw=b"not json")
        err = _expect_status(r, 400)
        if err:
            return False, err, r
        if "error" not in r.json():
            return False, "400 body has no 'error' key", r
        return True, r.json()["error"][:60], r

    def check_huge_quantity():
        # A 401-digit integer overflows float(); older builds answered 500.
        # A 400 here also shows the deployed build includes the Sprint 3
        # validation fixes, without spending any LLM tokens.
        r = post("/api/compare", raw=b'{"sku": "BRK-100", "quantity": 1' + b"0" * 400 + b"}")
        err = _expect_status(r, 400)
        if err:
            return False, err + " (deployed build predates the Sprint 3 fixes?)", r
        try:
            body = r.json()
        except ValueError:
            return False, "400 is not JSON", r
        if "error" not in body:
            return False, "400 body has no 'error' key", r
        return True, str(body["error"])[:60], r

    def check_unknown_sku():
        r = post("/api/compare", {"sku": "NOPE-999"})
        err = _expect_status(r, 404)
        if err:
            return False, err, r
        return True, r.json().get("error", "")[:60], r

    def check_unknown_path():
        r = get("/api/nope")
        err = _expect_status(r, 404)
        if err:
            return False, err, r
        try:
            body = r.json()
        except ValueError:
            return False, "404 is not JSON", r
        return True, str(body.get("error", ""))[:60], r

    def check_recommend():
        # The LLM call takes 20-40 s; allow the full gunicorn/nginx budget.
        r = post("/api/recommend", {"sku": "BRK-100"}, t=max(timeout, 180.0))
        err = _expect_status(r, 200)
        if err:
            return False, err, r
        body = r.json()
        agent = body.get("agent") or {}
        compare_winner = ((body.get("compare") or {}).get("summary") or {}).get(
            "winner_supplier_id")
        top = agent.get("top") or []
        top_id = top[0].get("supplier_id") if top and isinstance(top[0], dict) else None
        if not agent.get("rationale"):
            return False, "agent returned no rationale", r
        if top_id != compare_winner:
            return False, f"agent top {top_id!r} != compare winner {compare_winner!r}", r
        if agent.get("source") != "llm":
            # Numbers are still right, but the LLM narration fell back.
            return False, f"source={agent.get('source')!r} errors={agent.get('errors')}", r
        return True, f"winner={compare_winner} source=llm validated={agent.get('validated')}", r

    checks: List[Tuple[str, CheckFn]] = [
        ("GET / (title)", check_index),
        ("GET /api/health", check_health),
        ("GET /api/products", check_products),
        ("GET /api/quotes?sku=BRK-100", check_quotes),
    ]
    checks += [(f"POST /api/compare {sku}", make_compare(sku)) for sku in EXPECTED_SKUS]
    checks += [
        ("POST /api/compare bad body -> 400", check_bad_body),
        ("POST /api/compare huge quantity -> 400", check_huge_quantity),
        ("POST /api/compare NOPE-999 -> 404", check_unknown_sku),
        ("GET /api/nope -> 404 JSON", check_unknown_path),
    ]
    if recommend:
        checks.append(("POST /api/recommend BRK-100 (LLM)", check_recommend))
    return checks


def run(base_url: str, timeout: float, recommend: bool, gateway_check: bool,
        show_headers: bool) -> int:
    """Run every check, print a line per check, and return the exit code."""
    print(f"check_live: {base_url}  (timeout {timeout:.0f}s, "
          f"recommend={'on' if recommend else 'off'})")
    failures = 0
    first_headers: Optional[Dict[str, str]] = None
    for name, fn in build_checks(base_url, timeout, recommend, gateway_check):
        start = time.perf_counter()
        try:
            ok, detail, resp = fn()
            ms = resp.elapsed_ms if resp else (time.perf_counter() - start) * 1000.0
            if resp is not None and name == "GET /api/health":
                first_headers = resp.headers
        except Exception as exc:  # noqa: BLE001 - report, never crash the probe
            ok, detail = False, f"{type(exc).__name__}: {exc}"
            ms = (time.perf_counter() - start) * 1000.0
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {ms:8.1f} ms  {name:<38} {detail}")
    if show_headers and first_headers:
        keep = ("server", "cache-control", "expires", "etag", "x-content-type-options",
                "x-frame-options", "content-security-policy", "strict-transport-security")
        found = ", ".join(f"{k}={first_headers[k]}" for k in keep if k in first_headers)
        print(f"headers (/api/health): {found or 'none'}")
    print(f"{'OK' if not failures else 'FAILED'}: {failures} failure(s)")
    return 1 if failures else 0


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments and run the checks."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"site to check (default {DEFAULT_BASE_URL})")
    parser.add_argument("--recommend", action="store_true",
                        help="also call /api/recommend once (spends LLM tokens)")
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="per-request timeout in seconds (default 15)")
    parser.add_argument("--skip-gateway-check", action="store_true",
                        help="do not require gateway_configured=true (local runs)")
    parser.add_argument("--headers", action="store_true",
                        help="print notable response headers from /api/health")
    args = parser.parse_args(argv)
    return run(args.base_url, args.timeout, args.recommend,
               not args.skip_gateway_check, args.headers)


if __name__ == "__main__":
    sys.exit(main())

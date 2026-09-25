# JSON API

Contract between the backend (LIU), the agent (WANG) and the frontend (ZHOU).
Served by `app.py` next to the existing HTML page at `/`, which is unchanged.

- Base URL: `http://localhost:8080` (`python -m app`)
- All bodies are JSON (`Content-Type: application/json`), UTF-8.
- All numbers are computed by code (`compare.py`, `scoring.py`). Only
  `/api/recommend` calls the LLM, and only to write `agent.rationale`.
- The examples below are real output from the current data, trimmed where
  marked `...`.

## Endpoints

| Method | Path | Purpose | LLM |
|---|---|---|---|
| GET | `/api/health` | Liveness check; reports whether a gateway key is set | no |
| GET | `/api/products` | Product master data with the quote count per SKU | no |
| GET | `/api/quotes?sku=` | Raw supplier quotes for one SKU | no |
| POST | `/api/compare` | Deterministic ranking, exclusions, benchmarks, levers | no |
| POST | `/api/recommend` | `/api/compare` result plus `agent.compare` narration | yes, if a key is set |

## Errors

Every `/api/*` error uses the same shape. No stack traces are returned.

```json
{"error": "unknown sku 'NOPE'"}
```

| Status | When |
|---|---|
| 400 | Body is not a JSON object; `sku` is missing or empty; `weights` is not an object; an unknown weight dimension; a negative or non-finite (`Infinity`, `NaN`, `1e400`) weight, weights whose sum overflows, or all weights zero; `quantity` / `max_lead_time_days` is not a finite positive number; `?sku=` is missing |
| 404 | Unknown SKU, or an unknown `/api/...` path |
| 405 | Wrong method, e.g. `GET /api/compare` |
| 500 | Unexpected server error (generic message; the details go to the server log) |
| 502 | `/api/recommend` only: the agent failed. The body also contains `compare`, so the UI can still show the numbers |

## GET /api/health

```bash
curl -s localhost:8080/api/health
```
```json
{"gateway_configured": false, "skus": 5, "status": "ok"}
```

## GET /api/products

```bash
curl -s localhost:8080/api/products
```
```json
{"products": [
  {"sku": "BRK-100", "name": "Zinc-plated steel mounting bracket, 90 degree, 4-hole",
   "category": "Fabricated metal", "unit": "pcs", "num_quotes": 8},
  {"sku": "GSK-200", "name": "NBR rubber flange gasket, DN50, oil-resistant",
   "category": "Seals & gaskets", "unit": "pcs", "num_quotes": 5},
  ...
]}
```

## GET /api/quotes?sku=BRK-100

Returns the output of `tools.get_quotes`. `product_description` is
**untrusted supplier text** (it can contain prompt injection). Render it as
plain text and never pass it to an LLM outside `security.wrap_supplier_data`.

```bash
curl -s "localhost:8080/api/quotes?sku=BRK-100"
```
```json
{"sku": "BRK-100",
 "product": {"sku": "BRK-100", "name": "Zinc-plated steel mounting bracket, 90 degree, 4-hole",
             "category": "Fabricated metal", "unit": "pcs"},
 "quotes": [
  {"supplier_id": "SUP-001", "name": "Acme Precision Parts", "region": "Singapore",
   "sku": "BRK-100", "unit_price": 12.5, "currency": "SGD", "lead_time_days": 14,
   "payment_terms": "Net 30", "moq": 500, "on_time_delivery_rate": 0.97,
   "quality_rating": 4.6,
   "product_description": "ISO-9001 certified precision components with strong QA processes."},
  ...
 ]}
```

Errors: no `sku` gives 400, and an unknown SKU gives 404.

## POST /api/compare

Request body (only `sku` is required):

| Field | Type | Meaning |
|---|---|---|
| `sku` | string | e.g. `"BRK-100"` |
| `weights` | object, optional | Partial overrides of `price`, `lead_time`, `payment_terms`, `on_time_delivery_rate`, `quality_rating`. They are merged onto the defaults (0.3/0.2/0.1/0.2/0.2) and rescaled to sum to 1. The weights actually used are returned in `weights` |
| `quantity` | number > 0, optional | Order quantity. Quotes whose MOQ is above it are excluded |
| `max_lead_time_days` | number > 0, optional | Quotes with a longer lead time are excluded |

```bash
curl -s -X POST localhost:8080/api/compare -H 'Content-Type: application/json' \
  -d '{"sku":"BRK-100","quantity":600,"max_lead_time_days":20}'
```
```json
{
  "sku": "BRK-100",
  "product": {"sku": "BRK-100", "name": "...", "category": "Fabricated metal", "unit": "pcs"},
  "weights": {"price": 0.3, "lead_time": 0.2, "payment_terms": 0.1,
              "on_time_delivery_rate": 0.2, "quality_rating": 0.2},
  "constraints": {"quantity": 600, "max_lead_time_days": 20},
  "ranked": [
    {"supplier_id": "SUP-001", "supplier": "Acme Precision Parts", "score": 0.8,
     "breakdown": {"price": 1.0, "lead_time": 0.0, "payment_terms": 1.0,
                   "on_time_delivery_rate": 1.0, "quality_rating": 1.0},
     "weighted":  {"price": 0.3, "lead_time": 0.0, "payment_terms": 0.1,
                   "on_time_delivery_rate": 0.2, "quality_rating": 0.2},
     "raw": {"unit_price": 12.5, "lead_time_days": 14, "payment_terms": "Net 30",
             "net_days": 30, "moq": 500, "on_time_delivery_rate": 0.97, "quality_rating": 4.6},
     "injection_flag": false},
    {"supplier_id": "SUP-006", "supplier": "Harbourfront Engineering", "score": 0.3, ...}
  ],
  "excluded": [
    {"supplier_id": "SUP-002", "supplier": "Meridian Industrial Supply",
     "reasons": ["MOQ 1000 exceeds order quantity 600", "lead time 21d exceeds max 20d"]},
    {"supplier_id": "SUP-005", "supplier": "Pacific Rim Trading",
     "reasons": ["MOQ 750 exceeds order quantity 600"]},
    ...
  ],
  "best_in_class": {
    "price":     {"supplier_id": "SUP-001", "supplier": "Acme Precision Parts", "value": 12.5},
    "lead_time": {"supplier_id": "SUP-006", "supplier": "Harbourfront Engineering", "value": 5},
    ...
  },
  "negotiation_levers": [
    {"supplier_id": "SUP-001", "supplier": "Acme Precision Parts", "levers": [
      {"dimension": "lead_time", "gap": 9, "unit": "days", "benchmark_supplier_id": "SUP-006",
       "text": "Lead time is 9d longer than Harbourfront Engineering (5d) — ask for expedited slots or buffer stock"},
      {"dimension": "moq", "gap": 100, "unit": "units", "benchmark_supplier_id": null,
       "text": "Order quantity 600 is only 100 units above MOQ 500 — ask for a lower MOQ to keep flexibility"}]},
    ...
  ],
  "injection_suppliers": ["SUP-004"],
  "summary": {"num_quotes": 8, "num_eligible": 2, "winner_supplier_id": "SUP-001"}
}
```

Notes for consumers:
- `ranked` is sorted best first. `breakdown` values are 0..1 per dimension,
  and `score` is the sum of `weighted`. Scores are min-max normalized over the
  **eligible** set only, so with 2 eligible suppliers every dimension is 0 or 1.
- `negotiation_levers` covers the Top 3 of `ranked`. The `text` is ready to
  display, and `gap` / `unit` / `benchmark_supplier_id` let the UI render it
  in its own way.
- `injection_suppliers` covers **all** quotes, including excluded ones.
  `ranked[i].injection_flag` is true when that supplier is in the list. The
  detector only has English patterns; the Chinese injection from SUP-007 is
  not caught yet (tracked in `tests/test_compare.py`).
- If every quote is excluded, `ranked` is `[]` and `winner_supplier_id` is
  `null` (status 200).
- Changing the weights can change the winner: `{"weights": {"price": 0.8}}`
  on BRK-100 moves it from SUP-001 Acme to SUP-002 Meridian.

## POST /api/recommend

Takes the same body as `/api/compare`. The server first runs `compare_quotes`,
so any validation error returns 400 or 404 before the LLM is called. It then
calls `agent.compare(sku, quotes=<eligible quotes>, weights=<compare.weights>)`,
where the eligible quotes are the ones in `compare.ranked`.

```bash
curl -s -X POST localhost:8080/api/recommend -H 'Content-Type: application/json' \
  -d '{"sku":"GSK-200"}'
```
```json
{
  "compare": { ...same object as /api/compare... },
  "agent": {
    "request_id": "req-c445b649",
    "top": [ ...first 3 of scores... ],
    "scores": [ ...same row shape as compare.ranked, without injection_flag... ],
    "rationale": "Top 3 by objective weighted score:\n1. Acme Precision Parts (SUP-001) - score 0.675\n2. Pacific Rim Trading (SUP-005) - score 0.5167\n3. Harbourfront Engineering (SUP-006) - score 0.45",
    "injection_flag": false,
    "validated": true
  }
}
```

- When no gateway key is set, `agent.rationale` is the fixed offline text
  shown above. When a key is set, it is the LLM narration, and `validated`
  says whether that narration names a real supplier.
- The agent only sees the quotes that pass `quantity` / `max_lead_time_days`,
  with the same normalized weights. `agent.top` / `agent.scores` therefore
  match `compare.ranked` (same suppliers, order and scores), and the
  narration never names an excluded supplier. If every quote is excluded,
  the agent gets an empty list, and `agent.top` is `[]`.
- If the agent fails, the response is 502 and still includes the numbers:
  `{"error": "agent narration failed (RuntimeError)", "compare": {...}}`.

## Python API: `agent.compare(sku, quotes=None, weights=None)`

The signature and the `weights=None` behaviour are unchanged. `weights` passed
to `agent.compare` (and `scoring.score_suppliers`) is used **as given**, as
before: missing dimensions count as 0, unknown keys are ignored, and nothing
is merged or rescaled. For example, `{"price": 1.0}` ranks by price alone. The
only new check is that a non-finite weight (`inf`, `nan`) raises `ValueError`.

To get the HTTP behaviour (merge onto the defaults, validate, rescale to 1),
pass `scoring.normalize_weights(overrides)`, which is what `/api/recommend`
does.

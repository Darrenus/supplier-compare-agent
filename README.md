# Supplier Comparison Agent

An AI agent that compares suppliers for a given SKU and recommends the Top 3 —
objectively, safely, and auditably. Built for the NUS-ISS "Show Me Your Agent"
hackathon by team **Show Me Your Token** (Public track, Team Code `DAG1YLPM`).

The agent **recommends only — it never places orders** (human-in-the-loop stays
in control).

## Architecture

```
        SKU + candidate quotes
                 |
                 v
   [ injection detection ]  security.detect_injection() over supplier free-text
                 |
                 v
   [ deterministic scoring ] scoring.score_suppliers()  <-- no LLM needed
                 |            price / lead time / payment terms / OTD / quality
                 v            each dimension normalized 0-1, weighted
   [ LLM narration ] agent -> gateway_client.chat() -> AWS LLM Gateway
                 |            hardened system prompt (security.build_system_prompt)
                 |            manual JSON tool-call: model asks for get_quotes /
                 |            get_supplier_profile (read-only tools.py)
                 v
   [ output validation ]     chosen suppliers must exist in the input (#5.4)
                 |
                 v
   [ decision log ]          observability.log_decision() -> decisions.jsonl
                 |
                 v
   [ web UI ]                app.py + templates/index.html
                             ranked list + "Why this supplier?" breakdown panel
```

The gateway is **Ollama-compatible** and fronts **Claude Sonnet 4.5**. It
authenticates via the `X-API-Key` header and has **no native tool-calling**, so
the model is instructed to reply with ONLY a JSON tool request which we parse
and execute manually (mirroring the starter kit's `test_llm_gateway.py`). Rapid
requests can return `403` (rate limit); `gateway_client.invoke_with_retry()`
retries with linear backoff (3s, 6s, 9s).

## Guardrails & observability (judging rubric)

- **#4 Human-in-the-loop** — the agent only recommends; there are no
  order-placing or side-effecting tools.
- **#5 Security** — untrusted supplier text is wrapped in
  `<supplier_data>...</supplier_data>` and the system prompt forbids treating it
  as instructions; `detect_injection()` flags known attacks; tools are
  read-only (`tools.py`); the model's output is validated against the input set.
- **#6 Observability / eval** — every decision is logged as a JSON line in
  `decisions.jsonl`; `eval/` holds golden and adversarial cases with a runner.

## Setup

```bash
cd supplier-compare-agent
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then fill in the team gateway key
```

Edit `.env` and set `LLM_GATEWAY_API_KEY` to the shared team key (the URL and
model are pre-filled).

## Run

Web UI (binds `0.0.0.0:8080`; this is what deploys to the team's Lightsail box
as the live site):

```bash
python -m app
# open http://localhost:8080
```

Evaluation (golden + adversarial cases):

```bash
python eval/run_eval.py
```

Unit tests:

```bash
python -m pytest            # or: python tests/test_scoring.py
```

The deterministic parts — scoring, injection detection, decision log, and their
eval assertions — run **without a gateway key**. Only the LLM narration step
needs the key; when no key is set the eval skips that case with a clear
`SKIPPED (no gateway key)` message and the app falls back to an objective
ranking summary.

## Deployment

The live site for the judges deploys to the team's AWS Lightsail box
(`56.10.70.203`, Static IP). See `COLLABORATION.md` for the ownership and sprint
plan.

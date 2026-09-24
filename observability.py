# -*- coding: utf-8 -*-
"""Decision logging / observability (Rubric #6.A).

Every comparison appends one JSON line to ``decisions.jsonl`` capturing the
inputs, tool calls, scores, decision, rationale and injection flag, so judges
can audit exactly why the agent recommended what it did.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Dict

# Log file lives beside this module (and is gitignored).
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decisions.jsonl")


def new_request_id() -> str:
    """Return a short unique request id (e.g. 'req-1a2b3c4d')."""
    return f"req-{uuid.uuid4().hex[:8]}"


def log_decision(record: Dict) -> Dict:
    """Append a decision record as one JSON line to ``decisions.jsonl``.

    A ``timestamp`` (UTC ISO-8601) is added if not already present. Expected
    keys include: request_id, inputs, tool_calls, scores, decision, rationale,
    injection_flag.

    Args:
        record: The decision record to persist.

    Returns:
        The record actually written (with timestamp filled in).
    """
    enriched = dict(record)
    enriched.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(enriched, ensure_ascii=False) + "\n")
    return enriched

# -*- coding: utf-8 -*-
"""Decision logging / observability (Rubric #6.A).

Every comparison appends one JSON line to ``decisions.jsonl`` capturing the
inputs, tool calls, scores, decision, rationale and injection flag, so judges
can audit exactly why the agent recommended what it did.

The human buyer's decision on a recommendation (approve it, or override it
with another supplier and a reason) is appended as a second line with the
same ``request_id`` and ``"type": "human_decision"`` (Rubric #4).
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Log file lives beside this module (and is gitignored).
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decisions.jsonl")


REQUEST_ID_RE = re.compile(r"^req-[0-9a-f]{8}$")


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


HUMAN_DECISION = "human_decision"
HUMAN_ACTIONS = ("approve", "override")
REASON_MAX_CHARS = 500


class DecisionError(ValueError):
    """A human decision was rejected; ``status`` is the HTTP status to return."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _records(fh) -> List[Dict]:
    fh.seek(0)
    out = []
    for line in fh:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # a torn or foreign line must not break the audit trail
    return out


def read_records(request_id: str) -> List[Dict]:
    """Return every log record for ``request_id`` (agent record first), oldest first."""
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, "r", encoding="utf-8") as fh:
        return [r for r in _records(fh) if r.get("request_id") == request_id]


def record_human_decision(request_id: str, action: str, supplier_id: str,
                          reason: Optional[str] = None) -> Dict:
    """Append the buyer's decision on a logged recommendation.

    Rules: the ``request_id`` must have an agent record with a recommendation;
    ``approve`` must name the recommended supplier; ``override`` must name
    another supplier from that comparison and give a reason; each request can
    be decided once (the audit trail is append-only). Nothing is ordered.

    Raises:
        DecisionError: with status 400 (invalid input), 404 (unknown
            request_id) or 409 (already decided / nothing to decide).
    """
    if action not in HUMAN_ACTIONS:
        raise DecisionError(f"action must be one of {list(HUMAN_ACTIONS)}")
    reason = (reason or "").strip()
    if len(reason) > REASON_MAX_CHARS:
        raise DecisionError(f"reason must be at most {REASON_MAX_CHARS} characters")
    if action == "override" and len(reason) < 5:
        raise DecisionError("an override needs a reason (at least 5 characters)")

    # One lock around read + append, so two workers cannot both record a
    # decision for the same request.
    with open(LOG_PATH, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            records = [r for r in _records(fh) if r.get("request_id") == request_id]
            agent_rec = next((r for r in records if r.get("type") != HUMAN_DECISION), None)
            if agent_rec is None:
                raise DecisionError(f"unknown request_id {request_id!r}", 404)
            if any(r.get("type") == HUMAN_DECISION for r in records):
                raise DecisionError(f"request {request_id} already has a decision", 409)
            recommended = agent_rec.get("recommended_supplier_id")
            compared = list((agent_rec.get("scores") or {}).keys())
            if not recommended:
                raise DecisionError("this request had no eligible supplier to decide on", 409)
            if action == "approve" and supplier_id != recommended:
                raise DecisionError(f"approve must name the recommended supplier {recommended!r}")
            if action == "override":
                if supplier_id not in compared:
                    raise DecisionError(f"supplier_id must be one of {compared}")
                if supplier_id == recommended:
                    raise DecisionError("to accept the recommended supplier, use action 'approve'")

            record = {
                "type": HUMAN_DECISION,
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": action,
                "supplier_id": supplier_id,
                "recommended_supplier_id": recommended,
                "agrees_with_agent": supplier_id == recommended,
                "reason": reason,
                "order_placed": False,  # the tool only records the decision
            }
            fh.seek(0, os.SEEK_END)
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            return record
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def audit_record(request_id: str) -> Optional[Dict]:
    """The full audit trail for one request, or None if it was never logged.

    Returns ``{"request_id", "agent", "human_decision"}``: the agent's record
    (inputs, scores, tool calls, rationale, usage, errors) and the buyer's
    decision, or None while it is undecided.
    """
    records = read_records(request_id)
    agent_rec = next((r for r in records if r.get("type") != HUMAN_DECISION), None)
    if agent_rec is None:
        return None
    human = next((r for r in records if r.get("type") == HUMAN_DECISION), None)
    return {"request_id": request_id, "agent": agent_rec, "human_decision": human}

"""Validate transitions and hash-linked governance events."""

from __future__ import annotations

from pathlib import Path
from governance.layout import metadata_path
from governance.read_session import ReadSession
from governance.read_session import SessionError
import json
import math
import re
from governance.workflow_common import (
    WorkflowError,
    _sha,
)


_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


TRANSITIONS = {
    "project": {None: {"initialized"}, "initialized": {"initialized"}},
    "consultation": {
        "awaiting_decision": {"ready_for_review", "agreed", "rejected"},
        "awaiting_discussion": {"ready_for_review", "agreed", "rejected"},
        "ready_for_review": {"agreed", "rejected"},
        "not_started": {"agreed"},
        "agreed": set(),
        "rejected": set(),
    },
    "task_execution": {
        "draft": {"ready_for_review", "approved", "superseded", "blocked"},
        "ready_for_review": {"approved", "draft", "superseded"},
        "approved": {"implementation_in_progress", "implementation_submitted", "blocked", "superseded"},
        "implementation_in_progress": {"implementation_submitted", "blocked", "superseded"},
        "implementation_submitted": {"verified", "verification_failed", "blocked"},
        "verification_failed": {"approved", "blocked", "superseded"},
        "verified": set(),
    },
    "task_dispatch": {None: {"dispatched"}, "not_dispatched": {"dispatched"}, "dispatched": set()},
}


DOMAIN_INITIAL = {"task_execution": "draft", "consultation": "awaiting_decision", "task_dispatch": "not_dispatched"}


BASELINE_PREFIX = "artifacts/events/baselines/"


EVENT_DOMAINS = {
    "project_initialized": "project",
    "governance_metadata_updated": "project",
    "consultation_agreed": "consultation",
    "task_approved": "task_execution",
    "implementation_started": "task_execution",
    "implementation_submitted": "task_execution",
    "verification_failed": "task_execution",
    "task_verified": "task_execution",
    "task_superseded": "task_execution",
    "task_blocked": "task_execution",
    "task_dispatched": "task_dispatch",
}


def _jcs_string(value: str) -> str:
    escapes = {"\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    out = ['"']
    for char in value:
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char in escapes:
            out.append(escapes[char])
        elif ord(char) < 0x20:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _jcs(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _jcs_string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise WorkflowError("non-finite JSON number in event")
        if value == int(value) and abs(value) < 1e21:
            return str(int(value))
        return repr(value)
    if isinstance(value, list):
        return "[" + ",".join(_jcs(item) for item in value) + "]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda pair: pair[0].encode("utf-16-be"))
        return "{" + ",".join(_jcs_string(key) + ":" + _jcs(item) for key, item in items) + "}"
    raise WorkflowError(f"unsupported JSON value in event: {type(value).__name__}")


def _event_hash(event: dict) -> str:
    payload = {key: value for key, value in event.items() if key != "event_hash"}
    return _sha(_jcs(payload).encode("utf-8"))


def _state_key(domain: str, task_id: object, round_id: object) -> tuple[str, ...]:
    """Execution and consultation state is continuous per task; dispatch is per round."""
    if domain in ("task_execution", "consultation"):
        return (str(task_id), domain)
    return (str(task_id), str(round_id), domain)


def _baseline_consultation_witnesses(session: ReadSession, artifacts: object) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    if not isinstance(artifacts, dict):
        return found
    for rel, expected in sorted(artifacts.items()):
        if not isinstance(rel, str) or not rel.startswith(metadata_path(session.root, BASELINE_PREFIX)):
            continue
        if Path(rel).name != "DEVELOPMENT_STATUS.json":
            continue
        try:
            raw = session.read_bytes(rel)
        except SessionError:
            continue
        if not isinstance(expected, str) or _sha(raw) != expected:
            continue
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        for phase in doc.get("phases", []) or []:
            if not isinstance(phase, dict):
                continue
            for section in ("completed_details", "pending_items"):
                for task in phase.get(section, []) or []:
                    if not isinstance(task, dict):
                        continue
                    task_key = task.get("id") or task.get("task_id")
                    state = task.get("consultation_status")
                    if task_key and isinstance(state, str) and state:
                        found.setdefault(str(task_key), []).append(state)
    return found


def _consultation_start(task_id: str, declared: object, history: list, position: int) -> tuple[str, object]:
    eligible = [recorded for recorded, _ in history if recorded < position]
    if not eligible:
        if declared == DOMAIN_INITIAL["consultation"]:
            return DOMAIN_INITIAL["consultation"], None
        return "<unproved>", None
    latest = max(eligible)
    proved = sorted({state for recorded, state in history if recorded == latest})
    if len(proved) == 1:
        return proved[0], None
    return "|".join(proved), None


def _event_chain_valid(session: ReadSession, text: str) -> bool:
    previous = None
    seen_ids: set[str] = set()
    position = 0
    tracked: dict[tuple[str, ...], str] = {}
    state_round: dict[tuple[str, ...], str] = {}
    witness_history: dict[str, list] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        position += 1
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(event, dict):
            return False
        event_type = event.get("event_type")
        if event.get("sequence") != position:
            return False
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id or event_id in seen_ids:
            return False
        seen_ids.add(event_id)
        occurred = event.get("occurred_at")
        if not isinstance(occurred, str) or not _RFC3339.match(occurred):
            return False
        if event_type not in EVENT_DOMAINS:
            return False
        domain = event.get("state_domain")
        if domain != EVENT_DOMAINS[event_type]:
            return False
        if event.get("previous_event_hash") != previous:
            return False
        stored = event.get("event_hash")
        try:
            calculated = _event_hash(event)
        except (WorkflowError, TypeError):
            calculated = None
        if not isinstance(stored, str) or stored != calculated:
            return False
        artifacts = event.get("artifact_hashes")
        if not isinstance(artifacts, dict) or not artifacts:
            return False
        for rel, expected in sorted(artifacts.items()):
            if not isinstance(rel, str) or not isinstance(expected, str):
                return False
            try:
                raw = session.read_bytes(rel)
            except SessionError:
                return False
            if _sha(raw) != expected:
                return False
        task_id = event.get("task_id")
        round_id = event.get("round_id")
        key = _state_key(domain, task_id, round_id)
        if not task_id and domain != "project":
            return False
        start = tracked.get(key)
        if start is None:
            if domain == "consultation":
                start = _consultation_start(str(task_id), event.get("previous_state"),
                                           witness_history.get(str(task_id), []), position)[0]
            else:
                start = DOMAIN_INITIAL.get(domain)
        if event.get("previous_state") != start:
            return False
        new_state = event.get("new_state")
        allowed = TRANSITIONS.get(domain, {}).get(start, set())
        if new_state not in allowed:
            return False
        if domain == "task_execution" and start == "verification_failed" and new_state == "approved":
            failed_round = state_round.get(key)
            if not isinstance(round_id, str) or not round_id or round_id == failed_round:
                return False
        if isinstance(new_state, str):
            tracked[key] = new_state
            if isinstance(round_id, str) and round_id:
                state_round[key] = round_id
        for witness_task, states in _baseline_consultation_witnesses(session, artifacts).items():
            for state in states:
                witness_history.setdefault(witness_task, []).append((position, state))
        previous = stored
    return bool(text.strip())

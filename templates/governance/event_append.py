"""Append a separately recorded Steward event; never change the project's decision or grants."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
import time
import uuid

from governance import implementation_scope as scope
from governance.layout import metadata_prefix
from governance.read_session import ReadSession, _rel_reason, _parse_strict
from governance.round_draft import ordinary_directory
from governance.round_package import _ids, verify_package
from governance.workflow_common import _sha
from governance.workflow_events import (
    DOMAIN_INITIAL, EVENT_DOMAINS, _baseline_consultation_witnesses, _event_chain_valid,
    _event_hash, _state_key,
)

META = ".dual-agent/"
STATUS = META + "DEVELOPMENT_STATUS.json"
LOG = META + "artifacts/events/governance-events.jsonl"
STATES = dict(project_initialized="initialized", governance_metadata_updated="initialized",
              consultation_agreed="agreed", task_approved="approved", task_dispatched="dispatched",
              implementation_started="implementation_in_progress", implementation_submitted="implementation_submitted",
              verification_failed="verification_failed", task_verified="verified", task_superseded="superseded",
              task_blocked="blocked")


def enabled(session):
    doc = session.read_json_object(STATUS)
    config = doc.get("governance_event_log")
    if not isinstance(config, dict) or config.get("status") != "enabled" or config.get("path") != LOG:
        raise ValueError("ledger is not explicitly enabled at the canonical path")
    return doc


@contextmanager
def writer_lock(path):
    deadline = time.monotonic() + 10
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ValueError("ledger writer lock busy; inspect the owner before recovery") from None
            time.sleep(0.04)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        path.unlink()


def nonempty(doc, key):
    value = doc.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing {key}")
    return value


def decision_fields(session, status, kind, task, rnd, ref):
    raw = session.read_bytes(ref)
    doc = _parse_strict(raw.decode("utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("decision record must be a JSON object")
    domain = EVENT_DOMAINS[kind]
    if domain == "project":
        if task is not None or rnd is not None:
            raise ValueError("project events have no task or round")
    else:
        _ids(task, rnd)
    role = "user" if kind in ("consultation_agreed", "task_approved") else "task_steward"
    for key, expected in (("project", nonempty(status, "project")), ("task_id", task), ("round_id", rnd),
                          ("event_type", kind), ("decision", STATES[kind]), ("role", role)):
        if key not in doc or doc[key] != expected:
            raise ValueError(f"record {key} does not match this event")
    if doc.get("template_only") or doc.get("status") == "draft":
        raise ValueError("draft or sample record")
    decision_id = nonempty(doc, "decision_id")
    nonempty(doc, "decided_by")
    nonempty(doc, "basis")
    refs = doc.get("evidence", [])
    if not isinstance(refs, list) or not all(isinstance(x, str) for x in refs):
        raise ValueError("evidence must list repository-relative paths")
    if kind == "task_approved":
        scope = doc.get("approved_paths")
        if not isinstance(scope, list) or not scope or any(_rel_reason(x) for x in scope) or len(set(scope)) != len(scope):
            raise ValueError("approval record lacks exact approved_paths")
    if kind in ("task_verified", "verification_failed"):
        checks = doc.get("checks")
        if not isinstance(checks, list) or not checks or not all(
            isinstance(c, dict) and c.get("result") in ("passed", "failed", "blocked")
            and isinstance(c.get("observation"), str) and c["observation"].strip() for c in checks
        ):
            raise ValueError("verification record lacks checks and observations")
        if (kind == "task_verified") != all(c["result"] == "passed" for c in checks):
            raise ValueError("verification decision disagrees with checks")
    if kind == "task_dispatched":
        manifest = f"{META}task_interactions/{task}/{rnd}/package/round-manifest.json"
        nonempty(doc, "release_channel")
        if manifest not in refs or doc.get("manifest_sha256") != session.sha256(manifest):
            raise ValueError("dispatch receipt does not bind the exact manifest")
        if not isinstance(doc.get("route_sha256"), str) or len(doc["route_sha256"]) != 64:
            raise ValueError("dispatch receipt lacks route digest")
    hashes = {}
    for rel in [ref, *refs]:
        if rel in (LOG, STATUS, META + "tasks/CURRENT_TASK.md", LOG + ".lock"):
            raise ValueError("mutable authority or ledger cannot be evidence")
        if rel.startswith(META + "task_interactions/") and rel.split("/")[2:4] != [task, rnd]:
            raise ValueError("evidence belongs to another task or round")
        hashes[rel] = _sha(raw) if rel == ref else session.sha256(rel)
    return doc, {"event_type": kind, "task_id": task, "round_id": rnd, "decision_id": decision_id,
                 "state_domain": domain, "new_state": STATES[kind], "artifact_hashes": hashes}


def live_binding(session, status, fields, doc):
    task, rnd, kind = fields["task_id"], fields["round_id"], fields["event_type"]
    if task is None:
        return
    entries = [row for phase in status["phases"] for group in ("pending_items", "completed_details")
               for row in phase.get(group, []) if row.get("task_id", row.get("id")) == task]
    if len(entries) != 1 or entries[0].get("round_id") != rnd:
        raise ValueError("current project state does not uniquely bind task and round")
    row = entries[0]
    if kind == "task_approved" and (row.get("consultation_status") != "agreed"
                                   or row.get("authorization_status") != "authorized"
                                   or sorted(doc["approved_paths"]) != sorted(row.get("authorized_write_paths", []))):
        raise ValueError("approval differs from current grant")
    if kind == "task_dispatched":
        manifest = f"{META}task_interactions/{task}/{rnd}/package/round-manifest.json"
        if (row.get("authorization_status") != "authorized" or row.get("round_manifest_path") != manifest
                or session.sha256(META + "tasks/CURRENT_TASK.md") != doc["route_sha256"]):
            raise ValueError("dispatch route or current grant differs")
        verify_package(session.root, manifest, check_route=True, session=session)
    else:
        key = "consultation_status" if kind == "consultation_agreed" else "execution_status"
        if row.get(key) != fields["new_state"]:
            raise ValueError("event differs from the recorded live state")


def append(root, kind, task, rnd, ref):
    if metadata_prefix(root) != META:
        raise ValueError("installed .dual-agent directory required")
    enabled(ReadSession(root))
    path = root / LOG
    for part in (root / META / "artifacts", path.parent):
        ordinary_directory(part)
    with writer_lock(path.with_suffix(".jsonl.lock")):
        session = ReadSession(root)
        status = enabled(session)
        doc, fields = decision_fields(session, status, kind, task, rnd, ref)
        event, data = prepare_append(session, status, doc, fields, ref)
        if data is None:
            return "already_recorded", event
        publish_ledger(path, data)
        return "appended", event


def prepare_append(session, status, doc, fields, ref):
    """Validate a proposed revision while the caller holds the shared writer lock."""
    kind, task, rnd = fields["event_type"], fields["task_id"], fields["round_id"]
    path = session.root / LOG
    raw = session.read_bytes(LOG) if os.path.lexists(path) else b""
    text = raw.decode("utf-8")
    lines = [_parse_strict(line) for line in text.splitlines() if line.strip()]
    if raw and (not lines or not _event_chain_valid(session, text) or any(
        STATES[e["event_type"]] != e["new_state"] for e in lines
    )):
        raise ValueError("existing event chain or evidence is invalid")
    for event in lines:
        if event.get("decision_id") == fields["decision_id"]:
            if any(event.get(k) != v for k, v in fields.items()):
                raise ValueError("decision_id already bound to different content")
            return event, None
    live_binding(session, status, fields, doc)
    if (not lines) != (kind == "project_initialized"):
        raise ValueError("project_initialized must be the first event and occur only once")
    domain = fields["state_domain"]
    key = _state_key(domain, task, rnd)
    last = next((e for e in reversed(lines)
                 if _state_key(e["state_domain"], e.get("task_id"), e.get("round_id")) == key), None)
    previous = last["new_state"] if last else DOMAIN_INITIAL.get(domain)
    if (domain == "task_execution" and last and last.get("round_id") != rnd
            and (previous, kind) != ("verification_failed", "task_approved")):
        raise ValueError("execution transition crosses rounds without a new approval")
    if domain == "consultation" and not last:
        for e in reversed(lines):
            states = set(_baseline_consultation_witnesses(session, e["artifact_hashes"]).get(task, []))
            if states:
                if len(states) != 1:
                    raise ValueError("ambiguous consultation witness")
                previous = states.pop()
                break
    if kind == "task_dispatched":
        latest_execution = next((e for e in reversed(lines) if e.get("task_id") == task and
                                 e.get("state_domain") == "task_execution"), {})
        if latest_execution.get("round_id") != rnd or latest_execution.get("new_state") != "approved":
            raise ValueError("dispatch requires approval for this round")
    candidate = dict(fields, schema_version="0.1.0", actor_role="task_steward",
                     event_id=str(uuid.uuid4()), sequence=len(lines)+1, previous_state=previous,
                     previous_event_hash=lines[-1]["event_hash"] if lines else None,
                     occurred_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    candidate["event_hash"] = _event_hash(candidate)
    line = json.dumps(candidate, ensure_ascii=False, separators=(",", ":")) + "\n"
    suffix = ("" if not raw or raw.endswith(b"\n") else "\n") + line
    if not _event_chain_valid(session, text + suffix):
        raise ValueError("proposed transition or evidence rejected by event validator")
    same_round = [e for e in lines if e.get("task_id") == task and e.get("round_id") == rnd]
    dispatch = next((e for e in same_round if e["event_type"] == "task_dispatched"), {})
    if kind == "task_dispatched":
        manifest = f"{META}task_interactions/{task}/{rnd}/package/round-manifest.json"
        if session.read_json_object(manifest).get("implementation_scope_check") is True:
            candidate["scope_baseline_sha256"] = scope.capture(session, candidate, manifest)
    if dispatch.get("scope_baseline_sha256"):
        if kind in ("implementation_started", "implementation_submitted"):
            candidate["decision_record"] = ref
        if kind == "implementation_submitted":
            frozen_task = (f"{META}task_interactions/{task}/{rnd}/package/inputs/"
                           f"{META}tasks/active/{task}.task.json")
            allowed = session.read_json_object(frozen_task)["allowed_write_paths"]
            submitted_map = scope.submitted_files(session, allowed)
            if "submitted_files" in doc and doc["submitted_files"] != submitted_map:
                raise ValueError("submitted_files record disagrees with current files")
            candidate["submitted_files"] = submitted_map
            cleanup = None
            if "cleanup_receipt" in doc:
                manifest = f"{META}task_interactions/{task}/{rnd}/package/round-manifest.json"
                cleanup = scope.cleanup_binding(session, manifest, doc["cleanup_receipt"], task, rnd)
                if not {cleanup["receipt"], cleanup["approval"]} <= fields["artifact_hashes"].keys():
                    raise ValueError("submission does not bind cleanup evidence")
                candidate["steward_cleanup"] = cleanup
            candidate["scope_check"] = scope.compare(
                session, dispatch, allowed, scope.inventory(session),
                [e.get("decision_record") for e in same_round] + [ref], cleanup)
        if kind == "task_verified" and not any(
            e["event_type"] == "implementation_submitted" and e.get("scope_check", {}).get("passed") is True
            for e in same_round
        ):
            raise ValueError("cannot verify: submitted changes exceeded the frozen grant")
    candidate["event_hash"] = _event_hash(candidate)
    line = json.dumps(candidate, ensure_ascii=False, separators=(",", ":")) + "\n"
    return candidate, raw + (("" if not raw or raw.endswith(b"\n") else "\n") + line).encode()


def publish_ledger(path, data):
    # Preserve every published byte; readers see either complete revision.
    scratch = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".append-", delete=False) as stream:
            scratch = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(scratch, path)
    finally:
        if scratch is not None:
            scratch.unlink(missing_ok=True)



def main(directory, argv):
    parser = argparse.ArgumentParser(prog="tools.py append-event", description=__doc__)
    parser.add_argument("event_type", choices=EVENT_DOMAINS)
    parser.add_argument("--record", required=True)
    parser.add_argument("--task")
    parser.add_argument("--round", dest="round_id")
    args = parser.parse_args(argv)
    try:
        if args.task is None and args.round_id is None and EVENT_DOMAINS[args.event_type] != "project":
            record = ReadSession(directory.parent).read_json_object(args.record)
            args.task, args.round_id = record.get("task_id"), record.get("round_id")
        result, event = append(directory.parent, args.event_type, args.task, args.round_id, args.record)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"append-event: {exc}", file=sys.stderr)
        return 1
    reply = {"result": result, "sequence": event["sequence"], "event_hash": event["event_hash"]}
    if "scope_check" in event:
        reply["scope_check"] = event["scope_check"]
    print(json.dumps(reply, ensure_ascii=False))
    if "scope_check" in event and not event["scope_check"]["passed"]:
        return 1
    return 0


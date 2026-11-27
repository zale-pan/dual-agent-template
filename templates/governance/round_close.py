"""Close the current grant using an independent Steward decision already on disk."""
import argparse
import json
import os
import re

from governance.event_append import (
    LOG, META, STATUS, decision_fields, enabled, prepare_append, publish_ledger, writer_lock,
)
from governance.read_session import ReadSession
from governance.round_package import _file, _front_from_text, _ids
from governance.round_promote import encode, replace


def close(root, task, rnd, ref):
    _ids(task, rnd)
    route = META + "tasks/CURRENT_TASK.md"
    manifest = f"{META}task_interactions/{task}/{rnd}/package/round-manifest.json"
    with writer_lock(_file(root, LOG + ".lock", required=False)):
        session = ReadSession(root)
        status = session.read_json_object(STATUS)
        kind = session.read_json_object(ref).get("event_type")
        if kind not in ("task_verified", "verification_failed"):
            raise ValueError("expected a recorded Steward acceptance decision")
        doc, fields = decision_fields(session, status, kind, task, rnd, ref)
        terminal = fields["new_state"]
        binding = {"path": ref, "artifact_hashes": fields["artifact_hashes"]}
        entries = [(phase, group, row) for phase in status["phases"]
                   for group in ("pending_items", "completed_details")
                   for row in phase.get(group, []) if row.get("task_id", row.get("id")) == task]
        if len(entries) != 1:
            raise ValueError("task row missing or ambiguous")
        phase, group, row = entries[0]
        source = session.read_bytes(route)
        front = _front_from_text(source.decode("utf-8").replace("\r\n", "\n"))
        if ((front.get("task_id"), front.get("round_id"), front.get("round_manifest"))
                != (task, rnd, manifest) or row.get("round_id") != rnd
                or row.get("round_manifest_path") != manifest):
            raise ValueError("route, task row and requested round must agree")
        if row.get("execution_status") not in ("implementation_submitted", terminal):
            raise ValueError("round must be submitted or already closed with this outcome")
        if group == "completed_details" and terminal != "verified":
            raise ValueError("completed task cannot be closed as failed")
        if row.get("closure_record", binding) != binding:
            raise ValueError("round already bound to different closure evidence")
        if front.get("execution_status") not in (
            "approved", "implementation_in_progress", "implementation_submitted", terminal
        ):
            raise ValueError("current route conflicts with decision")
        # Only replace the status line in the front matter, retaining body and line endings.
        _, first_newline, rest = source.partition(b"\n")
        header_end = re.search(rb"(?m)^---\r?$", rest)
        if not first_newline or not header_end:
            raise ValueError("route header terminator missing")
        boundary = len(source) - len(rest) + header_end.start()
        header, count = re.subn(rb"(?m)^execution_status:[^\r\n]*",
                                b"execution_status: " + terminal.encode(), source[:boundary])
        if count != 1:
            raise ValueError("expected exactly one route state")
        new_route = header + source[boundary:]
        config = status.get("governance_event_log", {}).get("status")
        data = None
        if config == "enabled":
            enabled(session)
        elif config != "unused" or os.path.lexists(root / LOG) and (root / LOG).stat().st_size:
            raise ValueError("inconsistent event ledger configuration")
        row.update(execution_status=terminal, authorized_write_paths=[],
                   authorization_status="closed", closure_record=binding)
        if terminal == "verified" and group == "pending_items":
            phase[group].remove(row)
            phase.setdefault("completed_details", []).append(row)
        if config == "enabled":
            _, data = prepare_append(session, status, doc, fields, ref)
        summary = f"{META}docs/round-summaries/{task}/{rnd}.md"
        dest = _file(root, summary, required=False)
        lines = [f"# {task} / {rnd}", "", f"- 独立验收：{terminal}；本轮写权限关闭。",
                 f"- 裁决人：{doc['decided_by']}", f"- 依据：{doc['basis']}"]
        lines.extend(f"- {c['result']}：{c['observation']}" for c in doc["checks"])
        for key, label in (("changes", "改动"), ("limitations", "已知限制"), ("retention", "留存")):
            if doc.get(key):
                lines.append(f"- {label}：{doc[key]}")
        content = ("\n".join(lines) + "\n").encode()
        if dest.exists() and dest.read_bytes() != content:
            raise ValueError(f"existing round summary differs: {summary}")
        # Revoke first. The old route blocks a new promotion until the ledger is durable.
        updated = encode(status)
        if session.read_bytes(STATUS) != updated:
            replace(root / STATUS, updated)
        if data is not None:
            publish_ledger(root / LOG, data)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            replace(dest, content)
        if source != new_route:
            replace(root / route, new_route)
        return {"task_id": task, "round_id": rnd, "execution_status": terminal,
                "authorization_status": "closed", "record": ref, "summary": summary}


def main(directory, argv):
    parser = argparse.ArgumentParser(prog="tools.py close-round", description=__doc__)
    parser.add_argument("task")
    parser.add_argument("round")
    parser.add_argument("--record", required=True)
    args = parser.parse_args(argv)
    try:
        if directory.name != ".dual-agent":
            raise ValueError("use the installed project launcher")
        result = close(directory.parent, args.task, args.round, args.record)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"result": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0

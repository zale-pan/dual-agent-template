"""Bind file contents at handoff and compare submissions with the frozen write grant."""
import json
import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile

from governance.round_package import _file, _exact
from governance.workflow_common import _is_reparse, _sha

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache"}
META = ".dual-agent/"
STATUS = META + "DEVELOPMENT_STATUS.json"
LOG = META + "artifacts/events/governance-events.jsonl"


def inventory_error(error):
    raise error


def inventory(session):
    entries = {}
    for base, dirs, files in os.walk(session.root, onerror=inventory_error):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        file_names = set(files)
        for name in dirs + files:
            path = Path(base) / name
            mode = path.lstat()
            if stat.S_ISLNK(mode.st_mode) or _is_reparse(mode):
                raise ValueError(f"scope inventory refuses link: {path}")
            if name in file_names:
                if not stat.S_ISREG(mode.st_mode):
                    raise ValueError(f"scope inventory requires ordinary file: {path}")
                rel = path.relative_to(session.root).as_posix()
                entries[rel] = session.sha256(rel)
    return entries


def baseline_path(task, rnd):
    return f"{META}task_interactions/{task}/{rnd}/implementation-baseline.json"


def submitted_files(session, allowed_paths):
    return {
        rel: session.sha256(rel) if (session.root / rel).is_file() else None
        for rel in allowed_paths
    }


def comparable(session, task, rnd, entries):
    entries = dict(entries)
    for rel in (LOG, LOG + ".lock", baseline_path(task, rnd)):
        entries.pop(rel, None)
    # Only the active row's lifecycle state may be advanced by submission.
    status = json.loads(session.read_bytes(STATUS))
    rows = [row for phase in status["phases"] for row in phase.get("pending_items", [])
            if row.get("task_id", row.get("id")) == task and row.get("round_id") == rnd]
    if len(rows) != 1 or rows[0].get("execution_status") not in (
        "approved", "implementation_in_progress", "implementation_submitted"
    ):
        raise ValueError("scope requires an active grant for the exact round")
    rows[0]["execution_status"] = "approved"
    entries[STATUS] = _sha(json.dumps(status, sort_keys=True).encode())
    return entries


def capture(session, event, manifest):
    task, rnd = event["task_id"], event["round_id"]
    entries = comparable(session, task, rnd, inventory(session))
    raw = json.dumps(dict(manifest_sha256=session.sha256(manifest), entries=entries),
                     sort_keys=True, ensure_ascii=False).encode()
    path = _file(session.root, baseline_path(task, rnd), required=False)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("existing scope baseline differs; do not replace a handoff baseline")
    else:
        scratch = None
        try:
            with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                scratch = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(scratch, path)
        finally:
            if scratch is not None:
                scratch.unlink(missing_ok=True)
    return _sha(raw)


def cleanup_binding(session, manifest, receipt_ref, task, rnd):
    """Validate an exceptional Steward deletion against the approval frozen before handoff."""
    prefix = META + "artifacts/records/"
    if not receipt_ref.startswith(prefix):
        raise ValueError("cleanup receipt must be a Steward record")
    receipt = session.read_json_object(receipt_ref)
    approval_ref = receipt.get("approval")
    if not isinstance(approval_ref, str) or not approval_ref.startswith(prefix):
        raise ValueError("missing cleanup approval record path")
    manifest_doc = session.read_json_object(manifest)
    inputs = {e["source_path"]: e for e in manifest_doc["inputs"]}
    entry = inputs.get(approval_ref)
    if not entry or receipt.get("approval_sha256") != entry["sha256"]:
        raise ValueError("cleanup approval was not bound by this frozen contract")
    approval = session.read_json_object(entry["snapshot_path"])
    project = session.read_json_object(STATUS)["project"]
    for record, role, decision in ((approval, "user", "approve_cleanup"), (receipt, "task_steward", "cleanup_performed")):
        if any(record.get(k) != v for k, v in dict(task_id=task, round_id=rnd, project=project,
                                                   role=role, decision=decision).items()):
            raise ValueError("cleanup record identity or decision differs")
        if not isinstance(record.get("decided_by"), str) or not record["decided_by"].strip():
            raise ValueError("cleanup record requires an identified signer")
    deleted = receipt.get("deleted")
    approved = approval.get("approved_deletions")
    if not isinstance(deleted, dict) or not deleted or not isinstance(approved, dict):
        raise ValueError("cleanup requires exact path and digest maps")
    for path, digest in deleted.items():
        _exact(path)
        if (path.casefold().startswith((".dual-agent/", ".git/")) or path.casefold() == "agents.md"
                or path in inputs or not isinstance(digest, str) or len(digest) != 64
                or approved.get(path) != digest):
            raise ValueError(f"unapproved or governed cleanup object: {path}")
    return {"receipt": receipt_ref, "approval": approval_ref, "deleted": deleted}


def compare(session, dispatch, allowed, entries, records, cleanup=None):
    task, rnd = dispatch["task_id"], dispatch["round_id"]
    raw = session.read_bytes(baseline_path(task, rnd))
    if _sha(raw) != dispatch.get("scope_baseline_sha256"):
        raise ValueError("scope baseline disagrees with dispatch")
    baseline = json.loads(raw)
    before = baseline["entries"]
    after = comparable(session, task, rnd, entries)
    # Exempt only new, individually bound Steward decision records, never their attachments.
    for rel in records:
        if rel and rel.startswith(META + "artifacts/records/") and rel not in before:
            after.pop(rel, None)
    confirmed = []
    if cleanup is not None:
        receipt = cleanup["receipt"]
        if receipt not in before:
            after.pop(receipt, None)
        for path, digest in cleanup["deleted"].items():
            if path in allowed or before.get(path) != digest or path in after:
                raise ValueError(f"Steward removal disagrees with baseline or readback: {path}")
            confirmed.append(path)
    changes = {"added": sorted(after.keys() - before.keys()),
               "removed": sorted(before.keys() - after.keys()),
               "modified": sorted(k for k in before.keys() & after.keys() if before[k] != after[k])}
    outside = {kind: [p for p in paths if p not in allowed and p not in confirmed] for kind, paths in changes.items()}
    return {"changes": changes, "steward_removals": sorted(confirmed), "outside_grant": outside, "attribution": "undetermined" if any(outside.values()) else None, "passed": not any(outside.values())}


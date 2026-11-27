"""Exercise append-event through an installed launcher and independent chain readback."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys

import pytest

from init_project import initialize
from governance.event_append import LOG, META, STATUS, STATES
from governance.read_session import ReadSession
from governance.workflow_events import _event_chain_valid


def put(root, rel, obj):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf8")
    return rel


def status(root, enabled=True, state="draft", rnd="round-001"):
    d = json.loads((root / STATUS).read_bytes())
    d["governance_event_log"]["status"] = "enabled" if enabled else "unused"
    d["phases"] = [{"pending_items": [{"id": "APP", "round_id": rnd, "execution_status": state,
                                      "consultation_status": "agreed", "authorization_status": "authorized",
                                      "authorized_write_paths": ["src/app.py"]}]}]
    put(root, STATUS, d)


def record(root, kind, key=None, **overrides):
    project = kind in ("project_initialized", "governance_metadata_updated")
    d = dict(project="Demo", decision_id=key or kind, event_type=kind, decision=STATES[kind],
             task_id=None if project else "APP", round_id=None if project else "round-001",
             role="user" if kind in ("consultation_agreed", "task_approved") else "task_steward",
             decided_by="fixture owner", basis="Synthetic observed decision.")
    d.update(overrides)
    return put(root, META + "artifacts/records/" + (key or kind) + ".json", d)


def call(root, kind, ref, *args, ok=True):
    result = subprocess.run([sys.executable, "-B", str(root / META / "tools.py"), "append-event", kind,
                             "--record", ref, *args], cwd=root.parent, capture_output=True, stdin=subprocess.DEVNULL)
    assert (result.returncode == 0) == ok, (result.stdout, result.stderr)
    return json.loads(result.stdout) if ok else result.stderr


TASK = ["--task", "APP", "--round", "round-001"]


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    initialize(root, "Demo", False)
    return root


def start(root):
    status(root)
    ref = record(root, "project_initialized")
    call(root, "project_initialized", ref)


def test_disabled_and_wrong_path_do_not_initialize(project):
    ref = record(project, "project_initialized")
    call(project, "project_initialized", ref, ok=False)
    d = json.loads((project / STATUS).read_bytes())
    d["governance_event_log"] = {"status": "enabled", "path": "../other"}
    put(project, STATUS, d)
    call(project, "project_initialized", ref, ok=False)
    assert not (project / LOG).exists()
    assert not (project / (LOG + ".lock")).exists()


def test_recorded_decisions_and_exact_idempotency(project):
    start(project)
    status(project, state="approved")
    approval = record(project, "task_approved", approved_paths=["src/app.py"])
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file() and p != project / LOG}
    a = call(project, "task_approved", approval, *TASK)
    assert before == {p: p.read_bytes() for p in project.rglob("*") if p.is_file() and p != project / LOG}
    status(project, state="implementation_submitted")
    call(project, "implementation_submitted", record(project, "implementation_submitted"), *TASK)
    status(project, state="verified")
    verified = record(project, "task_verified", checks=[{"result": "passed", "observation": "2 cases passed"}])
    v = call(project, "task_verified", verified, *TASK)
    raw = (project / LOG).read_bytes()
    retry = call(project, "task_approved", approval, *TASK)
    assert retry["result"] == "already_recorded" and retry["event_hash"] == a["event_hash"]
    assert (project / LOG).read_bytes() == raw
    assert _event_chain_valid(ReadSession(project), raw.decode())
    lines = [json.loads(line) for line in raw.splitlines()]
    assert v["sequence"] == 4 and verified in lines[-1]["artifact_hashes"]
    assert lines[-1]["previous_event_hash"] == lines[-2]["event_hash"]


@pytest.mark.parametrize("change", [
    {"project": "Other"}, {"task_id": "Other"}, {"round_id": "round-002"},
    {"role": "implementation_agent"}, {"status": "draft"}, {"decision": "verified"},
    {"approved_paths": ["src/other.py"]}, {"evidence": ["missing.json"]},
    {"evidence": [META + "task_interactions/OTHER/round-001/x"]},
])
def test_bad_binding_does_not_append(project, change):
    start(project)
    status(project, state="approved")
    doc = {"approved_paths": ["src/app.py"], **change}
    ref = record(project, "task_approved", **doc)
    old = (project / LOG).read_bytes()
    call(project, "task_approved", ref, *TASK, ok=False)
    assert (project / LOG).read_bytes() == old


def test_rejects_invalid_transition_and_unsubstantiated_verdict(project):
    start(project)
    status(project, state="verified")
    old = (project / LOG).read_bytes()
    for checks in ([], [{"result": "failed", "observation": "case failed"}],
                   [{"result": "passed", "observation": "case passed"}]):
        ref = record(project, "task_verified", checks=checks)
        call(project, "task_verified", ref, *TASK, ok=False)
        assert (project / LOG).read_bytes() == old


def test_refuses_tamper_and_conflicting_retry(project):
    start(project)
    one = record(project, "governance_metadata_updated", "one")
    call(project, "governance_metadata_updated", one)
    original = (project / LOG).read_bytes()
    two = record(project, "governance_metadata_updated", "two", decision_id="one")
    call(project, "governance_metadata_updated", two, ok=False)
    assert (project / LOG).read_bytes() == original
    (project / one).write_bytes(b"{}")
    call(project, "governance_metadata_updated", two, ok=False)
    assert (project / LOG).read_bytes() == original
    damaged = original + b'{"truncated":'
    (project / LOG).write_bytes(damaged)
    call(project, "governance_metadata_updated", two, ok=False)
    assert (project / LOG).read_bytes() == damaged


def test_concurrent_processes_preserve_chain_and_deduplicate(project):
    start(project)
    prefix = (project / LOG).read_bytes()
    kind = "governance_metadata_updated"
    refs = [record(project, kind, str(i)) for i in range(4)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda r: call(project, kind, r), refs + refs[:2]))
    assert sorted(r["result"] for r in results) == ["already_recorded"] * 2 + ["appended"] * 4
    raw = (project / LOG).read_bytes()
    assert raw.startswith(prefix) and len(raw.splitlines()) == 5
    assert _event_chain_valid(ReadSession(project), raw.decode())
    assert not (project / (LOG + ".lock")).exists()


def test_dispatch_receipt_binds_verified_frozen_package(project):
    import runpy
    from hashlib import sha256
    from init_project import BASE

    fixture = runpy.run_path(str(BASE / "templates/tests/governance/test_steward_workflow.py"))
    fixture["make_project"](project, dispatched=False)
    d = json.loads((project / STATUS).read_bytes())
    d["governance_event_log"] = {"status": "enabled", "path": LOG}
    row = d["phases"][0]["pending_items"][0]
    row["consultation_status"] = "agreed"
    put(project, STATUS, d)
    task = row["task_id"]
    args = ["--task", task, "--round", row["round_id"]]
    subject = dict(project=d["project"], task_id=task, round_id=row["round_id"])
    approval = record(project, "task_approved", approved_paths=row["authorized_write_paths"], **subject)
    call(project, "task_approved", approval, *args)
    manifest = row["round_manifest_path"]
    digest = sha256((project / manifest).read_bytes()).hexdigest()
    route = sha256((project / META / "tasks/CURRENT_TASK.md").read_bytes()).hexdigest()
    valid = dict(release_channel="fixture workspace", manifest_sha256=digest, route_sha256=route,
                 evidence=[manifest], **subject)
    old = (project / LOG).read_bytes()
    for change in ({"route_sha256": "0" * 64}, {"manifest_sha256": "0" * 64}, {"evidence": []}):
        ref = record(project, "task_dispatched", **(valid | change))
        call(project, "task_dispatched", ref, *args, ok=False)
        assert (project / LOG).read_bytes() == old
    ref = record(project, "task_dispatched", **valid)
    call(project, "task_dispatched", ref, *args)
    event = json.loads((project / LOG).read_bytes().splitlines()[-1])
    assert event["artifact_hashes"][manifest] == digest


def test_failed_publish_leaves_original_chain_and_allows_retry(project, monkeypatch):
    from governance import event_append
    start(project)
    old = (project / LOG).read_bytes()
    ref = record(project, "governance_metadata_updated")
    def fail_replace(source, dest):
        raise PermissionError("simulated publication failure")
    with monkeypatch.context() as patch:
        patch.setattr(event_append.os, "replace", fail_replace)
        with pytest.raises(PermissionError):
            event_append.append(project, "governance_metadata_updated", None, None, ref)
    assert (project / LOG).read_bytes() == old
    assert not list((project / LOG).parent.glob(".append-*"))
    call(project, "governance_metadata_updated", ref)

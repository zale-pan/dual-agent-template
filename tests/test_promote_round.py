"""Exercise the installed CLIs from draft through user-approved package and independent run."""

import json
import os
import subprocess
import sys

import pytest

from governance import round_close
from init_project import initialize


def test_structured_round_from_fresh_project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    initialize(root, "Demo", False)
    meta = root / ".dual-agent"

    def load(p):
        return json.loads(p.read_bytes())

    def save(p, d):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf8")

    def cli(*args, ok=True):
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", ".dual-agent/tools.py", *args],
            cwd=root,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        )
        assert (proc.returncode == 0) == ok, (proc.stdout, proc.stderr)
        return (proc.stdout if ok else proc.stdout + proc.stderr).decode("utf8")

    rnd = "round-001"

    def record(kind, decision, role, **kwargs):
        ref = f".dual-agent/artifacts/records/{rnd}-{kind}.json"
        scoped = kind != "project_initialized"
        save(
            root / ref,
            dict(
                project="Demo",
                decision_id=rnd + "-" + kind,
                event_type=kind,
                decision=decision,
                role=role,
                decided_by="fixture owner",
                basis="synthetic completed observation",
                task_id="APP" if scoped else None,
                round_id=rnd if scoped else None,
                **kwargs,
            ),
        )
        return ref

    cli("new-round", "APP", "round-001", "--structured")
    base = meta / "task_interactions/APP/round-001"
    draft = base / "acceptance-plan.draft.json"
    d = load(draft)
    cmd = f"""& "{sys.executable}" -B -c "from pathlib import Path;assert Path('app.txt').read_text()=='fixture'" """
    (root / "app.txt").write_text("fixture")
    d.update(
        authorized_write_paths=["app.txt"],
        required_commands=[cmd],
        extra_inputs=["app.txt"],
    )
    save(draft, d)
    sheet = base / "轮次工作单.md"
    sheet.write_text("# APP / round-001\nVerify fixture file.", encoding="utf8")
    promote = [
        "promote-round",
        "APP",
        "round-001",
        "--phase",
        "P1",
        "--agent",
        "example-agent",
    ]
    assert "P0" in cli(*promote, ok=False)
    status = meta / "DEVELOPMENT_STATUS.json"
    d = load(status)
    d["project_startup"]["status"] = "ready"
    d["phases"] = [{"id": "P1"}]
    d["governance_event_log"]["status"] = "enabled"
    save(status, d)
    assert "policy" in cli(*promote, ok=False)
    policy = meta / "COLLABORATION_POLICY.json"
    d = load(policy)
    d["policy_status"] = "active"
    save(policy, d)
    genesis = record("project_initialized", "initialized", "task_steward")
    cli("append-event", "project_initialized", "--record", genesis)
    preview = json.loads(cli(*promote))
    assert preview["approved_paths"] == [
        "app.txt",
        ".dual-agent/task_interactions/APP/round-001/completion-report.yaml",
    ]
    agree = record("consultation_agreed", "agreed", "user")
    approve = record(
        "task_approved",
        "approved",
        "user",
        approved_paths=preview["approved_paths"],
        review_sha256=preview["review_sha256"],
    )
    args = promote + ["--agreement", agree, "--approval", approve]
    route = meta / "tasks/CURRENT_TASK.md"
    before = route.read_bytes(), status.read_bytes()
    original = sheet.read_bytes()
    sheet.write_bytes(original + b"changed")
    assert "preview" in cli(*args, ok=False)
    assert (route.read_bytes(), status.read_bytes()) == before and not (
        base / "instruction.md"
    ).exists()
    sheet.write_bytes(original)
    record_data = load(root / approve)
    save(root / approve, {**record_data, "approved_paths": ["app.txt"]})
    assert "preview" in cli(*args, ok=False)
    assert (route.read_bytes(), status.read_bytes()) == before
    save(root / approve, record_data)
    cli(*args)
    assert not (base / "package").exists()
    promoted = (route.read_bytes(), status.read_bytes())
    cli(*args)
    assert (route.read_bytes(), status.read_bytes()) == promoted
    for kind, ref in [("consultation_agreed", agree), ("task_approved", approve)]:
        cli("append-event", kind, "--record", ref)
    (root / "app.txt").write_text("changed")
    assert "changed" in cli("round_package", "--build", ok=False)
    assert not (base / "package").exists()
    (root / "app.txt").write_text("fixture")
    pkg = json.loads(cli("round_package", "--build"))
    receipt = record(
        "task_dispatched",
        "dispatched",
        "task_steward",
        release_channel="fixture workspace",
        **pkg["receipt_fields"],
    )
    (root / "dirty.txt").write_text("preexisting untracked bytes")
    (root / "removed.txt").write_text("preexisting")
    cli("append-event", "task_dispatched", "--record", receipt)
    baseline = base / "implementation-baseline.json"
    baseline_raw = baseline.read_bytes()
    baseline.write_bytes(b"{}")
    assert "baseline" in cli("steward_workflow", "run", ok=False)
    baseline.write_bytes(baseline_raw)
    out = str(tmp_path / "outputs")
    plan = json.loads(cli("steward_workflow", "plan", "--mode", "acceptance"))
    assert plan["stages"][0]["argv"][0] == sys.executable
    cli("steward_workflow", "run", "--mode", "acceptance", "--output", out)
    assert (
        json.loads(cli("steward_workflow", "summarize", "--output", out))["verdict"]
        == "pass"
    )
    def submit(ok=True):
        d = load(status)
        d["phases"][0]["pending_items"][0]["execution_status"] = "implementation_submitted"
        save(status, d)
        ref = record("implementation_submitted", "implementation_submitted", "task_steward")
        return json.loads(cli("append-event", "implementation_submitted", "--record", ref, ok=ok))

    def terminal(kind, state, result, observation):
        return record(kind, state, "task_steward", checks=[dict(result=result, observation=observation)])

    log = meta / "artifacts/events/governance-events.jsonl"
    failed = terminal("verification_failed", "verification_failed", "failed", "implementation report missing")
    assert not (base / "completion-report.yaml").exists()
    before = (route.read_bytes(), status.read_bytes(), log.read_bytes())
    cli("close-round", "APP", rnd, "--record", failed, ok=False)
    assert before == (route.read_bytes(), status.read_bytes(), log.read_bytes())
    (root / failed).unlink()
    (root / "dirty.txt").write_text("changed")
    (root / "removed.txt").unlink()
    (root / "extra.txt").write_text("new")
    (root / "app.txt").write_text("authorized edit")
    submitted = submit(ok=False)["scope_check"]
    assert submitted["attribution"] == "undetermined"
    assert submitted["outside_grant"] == {"added": ["extra.txt"], "modified": ["dirty.txt"], "removed": ["removed.txt"]}
    assert "app.txt" in submitted["changes"]["modified"]
    assert "outside grant" in cli("steward_workflow", "run", ok=False)
    (root / "app.txt").write_text("fixture")
    false_pass = terminal("task_verified", "verified", "passed", "stage passed")
    assert "exceeded" in cli("close-round", "APP", rnd, "--record", false_pass, ok=False)
    failed = terminal("verification_failed", "verification_failed", "failed", "out-of-scope paths and missing report")
    def interrupted(*args):
        raise OSError("injected ledger I/O failure")
    with monkeypatch.context() as patch:
        patch.setattr(round_close, "publish_ledger", interrupted)
        with pytest.raises(OSError, match="injected"):
            round_close.close(root, "APP", rnd, failed)
    row = load(status)["phases"][0]["pending_items"][0]
    assert row["authorization_status"] == "closed" and row["authorized_write_paths"] == []
    assert "execution_status: approved" in route.read_text("utf8")
    cli("close-round", "APP", rnd, "--record", failed)
    closed = (route.read_bytes(), status.read_bytes(), log.read_bytes())
    cli("close-round", "APP", rnd, "--record", failed)
    assert closed == (route.read_bytes(), status.read_bytes(), log.read_bytes())
    assert "execution_status: verification_failed" in route.read_text("utf8")

    rnd = "round-002"
    cli("new-round", "APP", rnd, "--structured")
    correction = meta / "task_interactions/APP" / rnd
    d = load(correction / draft.name)
    d.update(authorized_write_paths=["app.txt"], required_commands=[cmd], extra_inputs=["app.txt"])
    save(correction / draft.name, d)
    promote[2] = rnd
    preview = json.loads(cli(*promote))
    agree = record("consultation_agreed", "agreed", "user")
    approve = record("task_approved", "approved", "user", approved_paths=preview["approved_paths"],
                     review_sha256=preview["review_sha256"])
    cli(*promote, "--agreement", agree, "--approval", approve)
    before = (route.read_bytes(), status.read_bytes(), log.read_bytes())
    cli("close-round", "APP", "round-001", "--record", failed, ok=False)
    assert before == (route.read_bytes(), status.read_bytes(), log.read_bytes())
    cli("append-event", "task_approved", "--record", approve)
    pkg = json.loads(cli("round_package", "--build"))
    ref = record("task_dispatched", "dispatched", "task_steward",
                 release_channel="fixture workspace", **pkg["receipt_fields"])
    cli("append-event", "task_dispatched", "--record", ref)
    (correction / "completion-report.yaml").write_text("result: submitted")
    assert submit()["scope_check"]["passed"]
    (root / "post-submit.txt").write_text("too late")
    assert "post-submit.txt" in cli("steward_workflow", "run", ok=False)
    (root / "post-submit.txt").unlink()
    assert json.loads(cli("steward_workflow", "run"))["verdict"] == "pass"
    passed = terminal("task_verified", "verified", "passed", "app.txt check passed; report exists")
    # Legacy flow already closed the grant and appended the verdict, but left the approved route.
    d = load(status)
    d["phases"][0]["pending_items"][0].update(
        execution_status="verified", authorization_status="closed", authorized_write_paths=[])
    save(status, d)
    cli("append-event", "task_verified", "--record", passed)
    old_ledger = log.read_bytes()
    cli("close-round", "APP", rnd, "--record", passed)
    assert log.read_bytes() == old_ledger
    phase = load(status)["phases"][0]
    assert not phase["pending_items"]
    row, = phase["completed_details"]
    assert row["execution_status"] == "verified" and row["authorized_write_paths"] == []
    assert row["authorization_status"] == "closed"
    assert "execution_status: verified" in route.read_text("utf8")
    assert log.read_bytes().startswith(closed[2])
    assert load(root / failed)["checks"][0]["result"] == "failed"
    cli("review_views", "--write")
    cli("review_views", "--check")


def test_close_without_ledger_preserves_route_body(tmp_path):
    root = tmp_path / "project"
    initialize(root, "Demo", False)
    meta = root / ".dual-agent"
    status_path = meta / "DEVELOPMENT_STATUS.json"
    status = json.loads(status_path.read_bytes())
    manifest = ".dual-agent/task_interactions/APP/round-001/package/round-manifest.json"
    status["phases"] = [{"pending_items": [dict(task_id="APP", round_id="round-001",
        execution_status="implementation_submitted", authorization_status="authorized",
        authorized_write_paths=["app.txt"], round_manifest_path=manifest)]}]
    status_path.write_text(json.dumps(status))
    route = meta / "tasks/CURRENT_TASK.md"
    original = f"---\r\ntask_id: APP\r\nround_id: round-001\r\nexecution_status: approved\r\nround_manifest: {manifest}\r\n---\r\nexecution_status: approved\r\n".encode()
    route.write_bytes(original)
    ref = ".dual-agent/decision.json"
    (root / ref).write_text(json.dumps(dict(project="Demo", task_id="APP", round_id="round-001",
        event_type="task_verified", decision="verified", role="task_steward",
        decision_id="accepted", decided_by="tester", basis="fixture",
        checks=[dict(result="passed", observation="fixture matched")])))
    log = root / round_close.LOG
    previous = log.read_bytes() if log.exists() else None
    round_close.close(root, "APP", "round-001", ref)
    assert route.read_bytes() == original.replace(b"approved", b"verified", 1)
    assert (log.read_bytes() if log.exists() else None) == previous
    row, = json.loads(status_path.read_bytes())["phases"][0]["completed_details"]
    assert row["authorization_status"] == "closed" and row["authorized_write_paths"] == []

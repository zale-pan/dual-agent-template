import hashlib
import json
import subprocess
import sys


from governance import daily_round, round_close, round_draft, round_promote, steward_workflow
from governance.event_append import META, STATUS, LOG
from init_project import initialize


def test_daily_round_without_duplicate_decisions(tmp_path, monkeypatch, capsys):
    root = tmp_path / "demo"
    initialize(root, "Demo", False)
    meta = root / META
    def call(*args, ok=True, in_process=False):
        if in_process:
            rc = daily_round.main(meta, list(args))
            stdout = capsys.readouterr().out
        else:
            proc = subprocess.run([sys.executable, "-B", str(meta / "tools.py"), *args],
                                  cwd=root, stdin=subprocess.DEVNULL, capture_output=True)
            rc, stdout = proc.returncode, proc.stdout
        doc = json.loads(stdout.strip().splitlines()[-1])
        assert (rc == 0) == ok, doc
        return doc

    def read(rel):
        return json.loads((root / rel).read_bytes())

    activate = ["activate-project", "--ledger", "enabled", "--by", "Steward", "--basis", "P0 reviewed"]
    assert "P0" in call(*activate, ok=False)["message"]
    status = read(STATUS)
    status["project_startup"]["status"] = "ready"
    status["project_startup"]["decision_basis"] = "P0 verified"
    status["phases"] = [{"id": "P1"}]
    (root / STATUS).write_text(json.dumps(status))
    call(*activate)
    call(*activate)
    task, rnd = "T1", "round-001"
    assert round_draft.main(meta, [task, rnd, "--structured"]) == 0
    base = meta / "task_interactions" / task / rnd
    draft = base / "acceptance-plan.draft.json"
    doc = json.loads(draft.read_bytes())
    retired = root / "expired.log"
    retired.write_bytes(b"expired")
    digest = hashlib.sha256(retired.read_bytes()).hexdigest()
    approval = META + "artifacts/records/retire.json"
    deletion = dict(project="Demo", task_id=task, round_id=rnd, role="user", decision="approve_cleanup",
                    decided_by="Owner", approved_deletions={"expired.log": digest})
    (root / approval).write_text(json.dumps(deletion))
    doc["extra_inputs"] = [approval]
    doc.update(authorized_write_paths=["feature.txt"], required_commands=[f'"{sys.executable}" -B -c "print(1)"'])
    draft.write_text(json.dumps(doc))
    review = round_promote.promote(root, task, rnd, "P1", "Implementer", None, None)
    argv = ["dispatch-round", task, rnd, "--phase", "P1", "--agent", "Implementer",
            "--by", "Owner", "--basis", "approved this exact preview", "--approved-sha256"]
    call(*argv, "wrong", ok=False)
    assert not (base / "package").exists()
    with monkeypatch.context() as m:
        def fail(*args, **kwargs):
            raise ValueError("injected delivery readback failure")
        m.setattr(daily_round.round_package, "verify_package", fail)
        assert "readback" in call(*argv, review["review_sha256"], ok=False, in_process=True)["message"]
    lines = lambda: [json.loads(line) for line in (root / LOG).read_bytes().splitlines()]
    assert [e["event_type"] for e in lines()] == ["project_initialized", "consultation_agreed", "task_approved"]
    assert (base / "package/round-manifest.json").exists()
    call(*argv, review["review_sha256"])
    call(*argv, review["review_sha256"])
    assert [e["event_type"] for e in lines()].count("task_dispatched") == 1
    call("submit-round", task, rnd, ok=False)
    (root / "feature.txt").write_text("implemented")
    (base / "completion-report.yaml").write_text("changes: feature.txt\ntests: synthetic smoke\n")
    retired.unlink()
    receipt = META + "artifacts/records/deleted.json"
    attestation = dict(project="Demo", task_id=task, round_id=rnd, role="task_steward",
                       decided_by="Steward", decision="cleanup_performed", approval=approval,
                       approval_sha256=hashlib.sha256((root / approval).read_bytes()).hexdigest(),
                       deleted={"expired.log": "0" * 64})
    (root / receipt).write_text(json.dumps(attestation))
    sub = ["submit-round", task, rnd, "--cleanup-receipt", receipt]
    assert "unapproved" in call(*sub, ok=False)["message"]
    attestation["deleted"]["expired.log"] = digest
    (root / receipt).write_text(json.dumps(attestation))
    submitted = call(*sub)
    assert submitted["scope_check"]["steward_removals"] == ["expired.log"]
    assert submitted["scope_check"]["passed"]
    assert call(*sub)["sequence"] == submitted["sequence"]
    verdict = steward_workflow.run(root, (base / "package/round-manifest.json").relative_to(root).as_posix(),
                                   "acceptance", tmp_path / "outputs")
    assert verdict["publication_eligible"]
    ref = META + "artifacts/records/independent.json"
    (root / ref).write_text(json.dumps(dict(task_id=task, round_id=rnd, project="Demo",
        event_type="task_verified", decision="verified", role="task_steward", decision_id="independent",
        decided_by="Steward", basis="independent fixture run",
        checks=[dict(result="passed", observation="smoke passed")])))
    closed = round_close.close(root, task, rnd, ref)
    assert "smoke passed" in (root / closed["summary"]).read_text("utf8")
    assert round_close.close(root, task, rnd, ref) == closed
    assert [e["event_type"] for e in lines()] == ["project_initialized", "consultation_agreed", "task_approved",
        "task_dispatched", "implementation_submitted", "task_verified"]


def test_daily_round_fix_round_skips_duplicate_consultation(tmp_path):
    root = tmp_path / "demo"
    initialize(root, "Demo", False)
    meta = root / META
    status = json.loads((root / STATUS).read_bytes())
    status["project_startup"]["status"] = "ready"
    status["project_startup"]["decision_basis"] = "P0 verified"
    status["phases"] = [{"id": "P1"}]
    (root / STATUS).write_text(json.dumps(status))
    assert daily_round.main(meta, ["activate-project", "--ledger", "enabled", "--by", "Steward", "--basis", "P0"]) == 0
    task = "T1"
    for rnd, verdict_type, decision in (("round-001", "verification_failed", "verification_failed"),
                                        ("round-002", "task_verified", "verified")):
        assert round_draft.main(meta, [task, rnd, "--structured"]) == 0
        base = meta / "task_interactions" / task / rnd
        draft = base / "acceptance-plan.draft.json"
        doc = json.loads(draft.read_bytes())
        doc.update(authorized_write_paths=["feature.txt"], required_commands=[f'"{sys.executable}" -B -c "print(1)"'])
        draft.write_text(json.dumps(doc))
        review = round_promote.promote(root, task, rnd, "P1", "Implementer", None, None)
        assert daily_round.main(meta, [
            "dispatch-round", task, rnd, "--phase", "P1", "--agent", "Implementer",
            "--by", "Owner", "--basis", f"approved {rnd}", "--approved-sha256", review["review_sha256"],
        ]) == 0
        (root / "feature.txt").write_text(rnd)
        (base / "completion-report.yaml").write_text(f"round: {rnd}\n")
        assert daily_round.main(meta, ["submit-round", task, rnd]) == 0
        ref = META + f"artifacts/records/{task}-{rnd}-verdict.json"
        (root / ref).write_text(json.dumps(dict(
            task_id=task, round_id=rnd, project="Demo", event_type=verdict_type, decision=decision,
            role="task_steward", decision_id=f"{task}/{rnd}/verdict", decided_by="Steward",
            basis=f"outcome {rnd}", checks=[dict(result="failed" if decision == "verification_failed" else "passed",
                                                 observation=rnd)])))
        round_close.close(root, task, rnd, ref)
    events = [json.loads(line)["event_type"] for line in (root / LOG).read_bytes().splitlines()]
    assert events == [
        "project_initialized",
        "consultation_agreed", "task_approved", "task_dispatched", "implementation_submitted", "verification_failed",
        "task_approved", "task_dispatched", "implementation_submitted", "task_verified",
    ]


def test_check_setup_and_dispatch_channel_options(tmp_path):
    root = tmp_path / "demo"
    initialize(root, "Demo", False)
    meta = root / META
    assert daily_round.main(meta, ["check-setup"]) == 0
    status = json.loads((root / STATUS).read_bytes())
    status["project_startup"]["status"] = "ready"
    (root / STATUS).write_text(json.dumps(status))
    # Missing decision_basis while ready must be caught immediately by both check-setup and activate-project.
    assert daily_round.main(meta, ["check-setup"]) == 1
    assert daily_round.main(meta, ["activate-project", "--ledger", "enabled", "--by", "Alice", "--basis", "P0"]) == 1
    status["project_startup"]["decision_basis"] = "Verified locally"
    status["phases"] = [{"id": "P1"}, {"id": "P1"}]
    (root / STATUS).write_text(json.dumps(status))
    # Duplicate phase id must be caught immediately by check-setup.
    assert daily_round.main(meta, ["check-setup"]) == 1
    status["phases"] = [{"id": "P1"}]
    (root / STATUS).write_text(json.dumps(status))
    assert daily_round.main(meta, ["check-setup"]) == 0
    assert daily_round.main(meta, ["activate-project", "--ledger", "enabled", "--by", "Alice", "--basis", "P0"]) == 0
    assert round_draft.main(meta, ["T1", "round-001", "--structured"]) == 0
    draft = meta / "task_interactions" / "T1" / "round-001" / "acceptance-plan.draft.json"
    doc = json.loads(draft.read_bytes())
    doc.update(authorized_write_paths=["a.py"], required_commands=[f'"{sys.executable}" -B -c "print(1)"'])
    draft.write_text(json.dumps(doc))
    review = round_promote.promote(root, "T1", "round-001", "P1", "Impl", None, None)
    assert daily_round.main(meta, [
        "dispatch-round", "T1", "round-001", "--phase", "P1", "--agent", "Impl",
        "--by", "Owner", "--basis", "ok", "--approved-sha256", review["review_sha256"],
        "--channel", "git-pr", "--steward", "Alice",
    ]) == 0
    receipt = json.loads((meta / "artifacts" / "records" / "T1-round-001-dispatch.json").read_bytes())
    assert receipt["release_channel"] == "shared workspace"
    assert receipt["declared_channel"] == "git-pr"
    assert receipt["decided_by"] == "Alice"


def test_submitted_file_changed_before_acceptance_is_rejected(tmp_path):
    root = tmp_path / "demo"
    initialize(root, "Demo", False)
    meta = root / META
    status = json.loads((root / STATUS).read_bytes())
    status["project_startup"].update(status="ready", decision_basis="Verified locally")
    status["phases"] = [{"id": "P1"}]
    (root / STATUS).write_text(json.dumps(status))
    assert daily_round.main(meta, ["activate-project", "--ledger", "enabled", "--by", "Steward", "--basis", "P0"]) == 0
    assert round_draft.main(meta, ["T1", "round-001", "--structured"]) == 0
    base = meta / "task_interactions" / "T1" / "round-001"
    draft = base / "acceptance-plan.draft.json"
    doc = json.loads(draft.read_bytes())
    doc.update(authorized_write_paths=["feature.txt"], required_commands=[f'"{sys.executable}" -B -c "print(1)"'])
    draft.write_text(json.dumps(doc))
    review = round_promote.promote(root, "T1", "round-001", "P1", "Impl", None, None)
    assert daily_round.main(meta, [
        "dispatch-round", "T1", "round-001", "--phase", "P1", "--agent", "Impl",
        "--by", "Owner", "--basis", "ok", "--approved-sha256", review["review_sha256"],
    ]) == 0
    (root / "feature.txt").write_text("submitted A", encoding="utf-8")
    (base / "completion-report.yaml").write_text("changes: feature.txt\n", encoding="utf-8")
    assert daily_round.main(meta, ["submit-round", "T1", "round-001"]) == 0
    # Mutate authorized file after submit-round; acceptance must refuse stale submission.
    (root / "feature.txt").write_text("changed B", encoding="utf-8")
    manifest = (base / "package/round-manifest.json").relative_to(root).as_posix()
    try:
        steward_workflow.run(root, manifest, "acceptance", tmp_path / "outputs")
    except steward_workflow.WorkflowError as exc:
        assert "restore the submitted bytes or close as failed" in str(exc)
    else:
        raise AssertionError("expected acceptance to reject post-submission edit")
    # Restoring the exact submitted bytes allows acceptance to proceed.
    (root / "feature.txt").write_text("submitted A", encoding="utf-8")
    out_dir = tmp_path / "outputs"
    verdict = steward_workflow.run(root, manifest, "acceptance", out_dir)
    assert verdict["publication_eligible"]
    # Close T1 and switch live route to T2; summarize on T1's old output must still succeed.
    ver_ref = META + "artifacts/records/T1-round-001-verdict.json"
    (root / ver_ref).write_text(json.dumps(dict(
        project="Demo", task_id="T1", round_id="round-001", event_type="task_verified",
        decision="verified", role="task_steward", decision_id="T1/round-001/verdict",
        decided_by="Steward", basis="passed", checks=[dict(result="passed", observation="ok")])))
    round_close.close(root, "T1", "round-001", ver_ref)
    assert round_draft.main(meta, ["T2", "round-001", "--structured"]) == 0
    d2 = meta / "task_interactions" / "T2" / "round-001" / "acceptance-plan.draft.json"
    doc2 = json.loads(d2.read_bytes())
    doc2.update(authorized_write_paths=["other.txt"], required_commands=[f'"{sys.executable}" -B -c "print(2)"'])
    d2.write_text(json.dumps(doc2))
    r2 = round_promote.promote(root, "T2", "round-001", "P1", "Impl", None, None)
    assert daily_round.main(meta, [
        "dispatch-round", "T2", "round-001", "--phase", "P1", "--agent", "Impl",
        "--by", "Owner", "--basis", "ok", "--approved-sha256", r2["review_sha256"],
    ]) == 0
    summary = steward_workflow.summarize(out_dir)
    assert summary["verdict"] == "pass"
    assert summary["problems"] == []


def test_step_by_step_append_event_records_submitted_files_and_blocks_mutation(tmp_path):
    from governance import event_append, round_package
    root = tmp_path / "demo"
    initialize(root, "Demo", False)
    meta = root / META
    status = json.loads((root / STATUS).read_bytes())
    status["project_startup"].update(status="ready", decision_basis="Verified locally")
    status["phases"] = [{"id": "P1"}]
    (root / STATUS).write_text(json.dumps(status))
    assert daily_round.main(meta, ["activate-project", "--ledger", "enabled", "--by", "Steward", "--basis", "P0"]) == 0
    assert round_draft.main(meta, ["T1", "round-001", "--structured"]) == 0
    base = meta / "task_interactions" / "T1" / "round-001"
    draft = base / "acceptance-plan.draft.json"
    doc = json.loads(draft.read_bytes())
    doc.update(authorized_write_paths=["feature.txt"], required_commands=[f'"{sys.executable}" -B -c "print(1)"'])
    draft.write_text(json.dumps(doc))
    review = round_promote.promote(root, "T1", "round-001", "P1", "Impl", None, None)
    agree = META + "artifacts/records/agree.json"
    appr = META + "artifacts/records/appr.json"
    (root / agree).write_text(json.dumps(dict(
        project="Demo", task_id="T1", round_id="round-001", event_type="consultation_agreed",
        decision="agreed", role="user", decision_id="d-agree", decided_by="Owner", basis="ok")))
    (root / appr).write_text(json.dumps(dict(
        project="Demo", task_id="T1", round_id="round-001", event_type="task_approved",
        decision="approved", role="user", decision_id="d-appr", decided_by="Owner", basis="ok",
        approved_paths=review["approved_paths"], review_sha256=review["review_sha256"])))
    round_promote.promote(root, "T1", "round-001", "P1", "Impl", agree, appr)
    assert event_append.main(meta, ["consultation_agreed", "--record", agree]) == 0
    assert event_append.main(meta, ["task_approved", "--record", appr]) == 0
    assert round_package.main(["--root", str(root), "--build"]) == 0
    manifest = (base / "package/round-manifest.json").relative_to(root).as_posix()
    disp = META + "artifacts/records/disp.json"
    (root / disp).write_text(json.dumps(dict(
        project="Demo", task_id="T1", round_id="round-001", event_type="task_dispatched",
        decision="dispatched", role="task_steward", decision_id="d-disp", decided_by="Steward",
        basis="ok", release_channel="shared workspace",
        manifest_sha256=hashlib.sha256((root / manifest).read_bytes()).hexdigest(),
        route_sha256=hashlib.sha256((meta / "tasks/CURRENT_TASK.md").read_bytes()).hexdigest(),
        evidence=[manifest])))
    assert event_append.main(meta, ["task_dispatched", "--record", disp]) == 0
    (root / "feature.txt").write_text("submitted A", encoding="utf-8")
    (base / "completion-report.yaml").write_text("changes: feature.txt\n", encoding="utf-8")
    status = json.loads((root / STATUS).read_bytes())
    status["phases"][0]["pending_items"][0]["execution_status"] = "implementation_submitted"
    (root / STATUS).write_text(json.dumps(status))
    sub = META + "artifacts/records/sub.json"
    (root / sub).write_text(json.dumps(dict(
        project="Demo", task_id="T1", round_id="round-001", event_type="implementation_submitted",
        decision="implementation_submitted", role="task_steward", decision_id="d-sub",
        decided_by="Steward", basis="ok")))
    assert event_append.main(meta, ["implementation_submitted", "--record", sub]) == 0
    (root / "feature.txt").write_text("changed B", encoding="utf-8")
    try:
        steward_workflow.run(root, manifest, "acceptance", tmp_path / "outputs")
    except steward_workflow.WorkflowError as exc:
        assert "restore the submitted bytes or close as failed" in str(exc)
    else:
        raise AssertionError("expected step-by-step acceptance to reject post-submission edit")


def test_upgrade_preview_is_readonly(tmp_path):
    root = tmp_path / "demo"
    initialize(root, "Demo", False)
    tool = root / META / "tools.py"
    tool.write_text("# local")
    (root / STATUS).write_text('{"project":"custom"}')
    missing = root / META / "governance/daily_round.py"
    missing.unlink()
    preview = initialize(root, "Demo", False, upgrade_preview=True)
    assert META + "tools.py" in preview["different_tools"]
    assert STATUS in preview["manual_comparison"]
    assert missing.relative_to(root).as_posix() in preview["missing"]
    assert tool.read_bytes() == b"# local" and not missing.exists()
    assert (root / STATUS).read_bytes() == b'{"project":"custom"}'


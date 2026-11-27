"""Steward's shared-workspace dispatch and registration of an actual delivery."""
import argparse
import json
import os
from pathlib import Path

from governance import event_append as events
from governance import round_package
from governance.event_append import META, LOG, STATUS
from governance.read_session import ReadSession
from governance.round_promote import promote, encode, replace
from governance.workflow_common import _utcnow


def save_record(root, ref, doc):
    path = round_package._file(root, ref, required=False)
    data = encode(doc)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"existing record differs: {ref}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        replace(path, data)


def record_event(root, status, doc, ref):
    session = ReadSession(root)
    kind, task, rnd = doc["event_type"], doc["task_id"], doc["round_id"]
    checked, fields = events.decision_fields(session, status, kind, task, rnd, ref)
    return events.prepare_append(session, status, checked, fields, ref)


def publish(root, data):
    if data is not None:
        events.publish_ledger(root / LOG, data)


def dispatch(root, args, status):
    task, rnd = args.task, args.round
    if not (root / LOG).is_file() or not (root / LOG).stat().st_size:
        raise ValueError("initialize the enabled ledger after P0 before dispatch")
    history = [json.loads(line) for line in ReadSession(root).read_text(LOG).splitlines() if line.strip()]
    prior_agreed = any(e.get("task_id") == task and e.get("event_type") == "consultation_agreed"
                       and e.get("round_id") != rnd for e in history)
    preview = promote(root, task, rnd, args.phase, args.agent, None, None)
    if preview["review_sha256"] != args.approved_sha256:
        raise ValueError("approval differs from the reviewed preview")
    stem = f"{META}artifacts/records/{task}-{rnd}"
    agreement_ref = None if prior_agreed else stem + "-agreement.json"
    approval_ref = stem + "-approval.json"
    shared = dict(project=status["project"], task_id=task, round_id=rnd)
    agreement = None if prior_agreed else dict(
        shared, decision_id=f"{task}/{rnd}/agreement", role="user",
        event_type="consultation_agreed", decision="agreed", decided_by=args.by, basis=args.basis)
    approval = dict(shared, decision_id=f"{task}/{rnd}/approval", role="user",
                    event_type="task_approved", decision="approved", decided_by=args.by,
                    basis=args.basis, agrees_to_contract=True, review_sha256=args.approved_sha256,
                    approved_paths=preview["approved_paths"])
    events.nonempty(approval, "basis")
    events.nonempty(approval, "decided_by")
    manifest = f"{META}task_interactions/{task}/{rnd}/package/round-manifest.json"
    package_exists = os.path.lexists((root / manifest).parent)
    if package_exists:
        front = round_package._front(root)
        if front.get("round_manifest") != manifest or front.get("execution_status") != "approved":
            raise ValueError("cannot resume a different or closed round")
        frozen_task = ReadSession(root).read_json_object(
            manifest.rsplit("/", 1)[0] + "/inputs/" + front["task_manifest"])
        bound = frozen_task.get("bound_inputs_sha256", {})
        if bound.get(approval_ref) != round_package._sha(encode(approval)):
            raise ValueError("round already frozen with different approval arguments; use a new round if the grant changed")
        if agreement is not None and agreement_ref in bound and bound.get(agreement_ref) != round_package._sha(encode(agreement)):
            raise ValueError("round already frozen with different agreement arguments; use a new round if the grant changed")
    if agreement is not None:
        save_record(root, agreement_ref, agreement)
    save_record(root, approval_ref, approval)
    if not package_exists:
        promote(root, task, rnd, args.phase, args.agent, agreement_ref, approval_ref)
    session = ReadSession(root)
    status = events.enabled(session)
    if agreement is not None:
        agree_fields = events.decision_fields(session, status, "consultation_agreed", task, rnd, agreement_ref)[1]
        events.live_binding(session, status, agree_fields, agreement)
        _, agree_data = record_event(root, status, agreement, agreement_ref)
        publish(root, agree_data)
    fields = events.decision_fields(session, status, "task_approved", task, rnd, approval_ref)[1]
    events.live_binding(session, status, fields, approval)
    _, data = record_event(root, status, approval, approval_ref)
    publish(root, data)
    front = round_package._front(root)
    if not package_exists:
        task_doc = session.read_json_object(front["task_manifest"])
        round_package.build_package(root, task, rnd, task_doc["governed_input_paths"],
                                    [front[k] for k in round_package.READ_SET_KEYS],
                                    runtime=args.agent, prepared_at=_utcnow())
    verified = round_package.verify_package(root, manifest, check_route=True, check_sources=True)
    receipt = dict(shared, decision_id=f"{task}/{rnd}/dispatch", role="task_steward",
                   event_type="task_dispatched", decision="dispatched", decided_by=args.steward,
                   basis="Frozen package read back in the shared workspace",
                   release_channel="shared workspace", evidence=[manifest],
                   manifest_sha256=verified["manifest_sha256"],
                   route_sha256=session.sha256(META + "tasks/CURRENT_TASK.md"))
    if args.channel != "shared workspace":
        receipt["declared_channel"] = args.channel
        events.nonempty(receipt, "declared_channel")
    events.nonempty(receipt, "decided_by")
    ref = stem + "-dispatch.json"
    save_record(root, ref, receipt)
    event, data = record_event(root, status, receipt, ref)
    publish(root, data)
    return dict(result="dispatched", manifest=manifest, receipt=ref, sequence=event["sequence"])


def submit(root, args, status):
    task, rnd = args.task, args.round
    manifest = f"{META}task_interactions/{task}/{rnd}/package/round-manifest.json"
    session = ReadSession(root)
    verified = round_package.verify_package(root, manifest, check_route=True, require_dispatch=True)
    task_doc = session.read_json_object(manifest.rsplit("/", 1)[0] +
                                        f"/inputs/{META}tasks/active/{task}.task.json")
    report = task_doc["completion_report_path"]
    if not session.read_text(report).strip():
        raise ValueError("delivery report is empty")
    rows = [r for phase in status["phases"] for r in phase.get("pending_items", [])
            if r.get("task_id", r.get("id")) == task]
    if len(rows) != 1 or rows[0].get("round_id") != rnd:
        raise ValueError("submission belongs to another task or round")
    row = rows[0]
    if (row.get("execution_status") not in ("approved", "implementation_in_progress", "implementation_submitted")
            or row.get("authorization_status") != "authorized" or row.get("round_manifest_path") != manifest
            or row.get("authorized_write_paths") != task_doc["allowed_write_paths"]):
        raise ValueError("submission requires the current frozen grant")
    submitted_files = events.scope.submitted_files(session, task_doc["allowed_write_paths"])
    ref = f"{META}artifacts/records/{task}-{rnd}-submission.json"
    doc = dict(project=status["project"], task_id=task, round_id=rnd, role="task_steward",
               decision_id=f"{task}/{rnd}/submission", event_type="implementation_submitted",
               decision="implementation_submitted", decided_by="Task Steward",
               basis="Read back the implementation delivery at the frozen report path",
               evidence=[report, manifest], report_sha256=session.sha256(report),
               manifest_sha256=verified["manifest_sha256"], submitted_files=submitted_files)
    if args.cleanup_receipt:
        cleanup = events.scope.cleanup_binding(session, manifest, args.cleanup_receipt, task, rnd)
        doc["cleanup_receipt"] = args.cleanup_receipt
        doc["evidence"].extend([cleanup["receipt"], cleanup["approval"]])
    save_record(root, ref, doc)
    row["execution_status"] = "implementation_submitted"
    event, data = record_event(root, status, doc, ref)
    updated = encode(status)
    if session.read_bytes(STATUS) != updated:
        replace(root / STATUS, updated)
    publish(root, data)
    return dict(result="submitted", report=report, sequence=event["sequence"],
                scope_check=event.get("scope_check"))


def activate(root, args):
    check_setup(root)
    session = ReadSession(root)
    status = session.read_json_object(STATUS)
    startup = status.get("project_startup", {})
    if startup.get("status") != "ready" or startup.get("blockers"):
        raise ValueError("complete P0 and resolve its blockers first")
    if not args.basis.strip() or not args.by.strip():
        raise ValueError("identify the actual Steward and basis")
    config = status["governance_event_log"]
    if config.get("path") != LOG or config.get("status") not in ("unused", "enabled"):
        raise ValueError("unexpected ledger configuration")
    if args.ledger == "unused" and (config["status"] == "enabled" or
            ((root / LOG).exists() and (root / LOG).stat().st_size)):
        raise ValueError("activation cannot retire an existing ledger")
    policy_path = META + "COLLABORATION_POLICY.json"
    policy = session.read_json_object(policy_path)
    if policy.get("policy_status") not in ("configured", "active"):
        raise ValueError("unexpected policy state")
    config["status"] = args.ledger
    policy["policy_status"] = "active"
    ledger_data = None
    if args.ledger == "enabled":
        history = session.read_text(LOG) if (root / LOG).exists() else ""
        if history.strip():
            if not events._event_chain_valid(session, history):
                raise ValueError("existing ledger invalid")
        else:
            ref = META + "artifacts/records/project-initialized.json"
            doc = dict(project=status["project"], task_id=None, round_id=None, role="task_steward",
                       decided_by=args.by, basis=args.basis, decision_id="project/initialized",
                       event_type="project_initialized", decision="initialized")
            save_record(root, ref, doc)
            _, ledger_data = record_event(root, status, doc, ref)
    for path, data in ((STATUS, status), (policy_path, policy)):
        if json.loads(session.read_bytes(path)) != data:
            replace(root / path, encode(data))
    publish(root, ledger_data)
    return dict(result="active", ledger=args.ledger)


def check_setup(root):
    session = ReadSession(root)
    for required_text in ("AGENTS.md", META + "IMPLEMENTATION_AGENT_PROMPT.en.md"):
        if not session.read_text(required_text).strip():
            raise ValueError(f"empty required file: {required_text}")
    goals = session.read_json_object(META + "PROJECT_GOALS.json")
    status = session.read_json_object(STATUS)
    registry = session.read_json_object(META + "COMPONENT_REGISTRY.json")
    policy = session.read_json_object(META + "COLLABORATION_POLICY.json")
    projects = []
    for name, doc in (("PROJECT_GOALS.json", goals), ("DEVELOPMENT_STATUS.json", status),
                      ("COMPONENT_REGISTRY.json", registry), ("COLLABORATION_POLICY.json", policy)):
        project = doc.get("project")
        if not isinstance(project, str) or not project.strip() or project == "PROJECT_NAME":
            raise ValueError(f"{name}: missing or uninitialized project name")
        projects.append(project)
    if len(set(projects)) != 1:
        raise ValueError("project name differs across the four governance files")
    if not isinstance(goals.get("goals"), list):
        raise ValueError("PROJECT_GOALS.json.goals must be a list")
    if not isinstance(registry.get("components"), list):
        raise ValueError("COMPONENT_REGISTRY.json.components must be a list")
    if policy.get("policy_status") not in ("configured", "active"):
        raise ValueError("unexpected policy_status in COLLABORATION_POLICY.json")
    startup = status.get("project_startup")
    if not isinstance(startup, dict) or startup.get("status") not in ("pending", "ready", "blocked"):
        raise ValueError("invalid project_startup.status in DEVELOPMENT_STATUS.json")
    blockers = startup.get("blockers")
    if not isinstance(blockers, list):
        raise ValueError("project_startup.blockers must be a list")
    if startup["status"] == "ready":
        if blockers:
            raise ValueError("project_startup cannot be ready while blockers remain")
        basis = startup.get("decision_basis")
        if not isinstance(basis, str) or not basis.strip():
            raise ValueError("ready project_startup requires decision_basis")
    phases = status.get("phases")
    if not isinstance(phases, list):
        raise ValueError("DEVELOPMENT_STATUS.json.phases must be a list")
    phase_ids = []
    for phase in phases:
        if not isinstance(phase, dict) or not isinstance(phase.get("id"), str) or not phase["id"].strip():
            raise ValueError("every phase entry requires a non-empty string id")
        phase_ids.append(phase["id"])
    if len(set(phase_ids)) != len(phase_ids):
        raise ValueError("duplicate phase id in DEVELOPMENT_STATUS.json")
    config = status.get("governance_event_log")
    if not isinstance(config, dict) or config.get("path") != LOG or config.get("status") not in ("unused", "enabled"):
        raise ValueError("unexpected governance_event_log configuration")
    if config["status"] == "enabled" and (root / LOG).exists():
        history = session.read_text(LOG)
        if history.strip() and not events._event_chain_valid(session, history):
            raise ValueError("existing ledger invalid")
    return dict(result="ok", project=projects[0], startup_status=startup["status"],
                policy_status=policy["policy_status"], ledger_status=config["status"], phases=phase_ids)


def main(directory: Path, argv):
    command = argv[0]
    parser = argparse.ArgumentParser(prog="tools.py " + command)
    if command == "check-setup":
        pass
    elif command == "activate-project":
        parser.add_argument("--ledger", choices=["enabled", "unused"], required=True)
        parser.add_argument("--by", required=True)
        parser.add_argument("--basis", required=True)
    else:
        parser.add_argument("task")
        parser.add_argument("round")
    if command == "submit-round":
        parser.add_argument("--cleanup-receipt")
    if command == "dispatch-round":
        for option in ("phase", "agent", "approved-sha256", "by", "basis"):
            parser.add_argument("--" + option, required=True)
        parser.add_argument("--channel", default="shared workspace")
        parser.add_argument("--steward", default="Task Steward")
    args = parser.parse_args(argv[1:])
    try:
        root = directory.parent
        if directory.name != ".dual-agent":
            raise ValueError("use the installed project launcher")
        if command == "check-setup":
            result = check_setup(root)
        else:
            with events.writer_lock(round_package._file(root, LOG + ".lock", required=False)):
                if command == "activate-project":
                    result = activate(root, args)
                else:
                    round_package._ids(args.task, args.round)
                    status = events.enabled(ReadSession(root))
                    result = dispatch(root, args, status) if command == "dispatch-round" else submit(root, args, status)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if (result.get("scope_check") or {}).get("passed") is False else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(result="error", message=str(exc)), ensure_ascii=False))
        return 1

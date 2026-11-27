"""Project reviewed round drafts into the existing structured file contracts."""

import argparse
from fnmatch import fnmatchcase
from contextlib import ExitStack
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from governance.event_append import META, STATUS, decision_fields, writer_lock
from governance.read_session import ReadSession, _rel_reason
from governance.round_package import (
    MINIMUM_ROOT_INPUTS,
    _check_input_source,
    _file,
    _front,
    _ids,
    _sha,
    _unique,
)
from governance.workflow_commands import _normalize_commands


def encode(doc):
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode()


def replace(path, data):
    name = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            name = Path(stream.name)
            stream.write(data)
        os.replace(name, path)
    finally:
        if name is not None:
            name.unlink(missing_ok=True)


def promote(root, task, rnd, phase, agent, agreement, approval):
    _ids(task, rnd)
    if (
        _rel_reason(task)
        or not rnd.isascii()
        or not agent.strip()
        or any(ord(c) < 32 for c in agent)
    ):
        raise ValueError("invalid identity")
    session = ReadSession(root)
    status = session.read_json_object(STATUS)
    if status.get("project_startup", {}).get("status") != "ready":
        raise ValueError("business promotion requires a ready P0 decision")
    policy = session.read_json_object(META + "COLLABORATION_POLICY.json")
    if policy.get("policy_status") != "active":
        raise ValueError(
            "policy is configured only; Steward must explicitly activate after P0 checks"
        )
    base = f"{META}task_interactions/{task}/{rnd}"
    sheet, draft, inst = (
        base + "/" + n
        for n in ("轮次工作单.md", "acceptance-plan.draft.json", "instruction.draft.md")
    )
    plan = session.read_json_object(draft)
    if (
        plan.get("task_id"),
        plan.get("round_id"),
        plan.get("status"),
        plan.get("worksheet"),
        plan.get("instruction_draft"),
    ) != (task, rnd, "draft", sheet, inst):
        raise ValueError("draft identity or pointers disagree")
    _normalize_commands(plan["required_commands"])
    scope = plan["authorized_write_paths"]
    report = base + "/completion-report.yaml"
    if not isinstance(scope, list) or any(_rel_reason(p) for p in scope):
        raise ValueError("expected exact project-relative write paths")
    scope = [*scope] if report in scope else [*scope, report]
    _unique(scope)
    forbidden = policy["roles"]["implementation_agent"]["forbidden_write_scopes"]
    for path in scope:
        if any(
            fnmatchcase(path.casefold(), pattern.casefold()) for pattern in forbidden
        ):
            raise ValueError(f"project policy protects {path}")
        _file(root, path, required=False)
        if path != report and (
            path.casefold().startswith((".dual-agent/", ".git/"))
            or path.casefold() in (".git", ".dual-agent", "agents.md")
        ):
            raise ValueError("business draft cannot grant governance writes")
    extras = plan.get("extra_inputs", [])
    if not isinstance(extras, list):
        raise ValueError(
            "extra_inputs must be a list of original project-relative paths"
        )
    originals = [sheet, draft, inst, *extras]
    _unique(originals)
    for path in originals:
        _check_input_source(path)
    pins = {p: session.sha256(p) for p in originals}
    review = {
        "project": status["project"],
        "task_id": task,
        "round_id": rnd,
        "phase": phase,
        "assigned_agent": agent,
        "approved_paths": scope,
        "required_commands": plan["required_commands"],
        "input_sha256": pins,
    }
    digest = _sha(encode(review))
    if not approval and not agreement:
        return dict(review, review_sha256=digest)
    if not approval:
        raise ValueError("an exact user approval is required")
    records = [("task_approved", approval)]
    if agreement:
        records.insert(0, ("consultation_agreed", agreement))
    for kind, ref in records:
        doc, fields = decision_fields(session, status, kind, task, rnd, ref)
        if agreement is None and doc.get("agrees_to_contract") is not True:
            raise ValueError("combined approval must explicitly agree to this contract")
        pins = {**pins, **fields["artifact_hashes"]}
        if kind == "task_approved" and (
            doc.get("review_sha256") != digest or doc["approved_paths"] != scope
        ):
            raise ValueError("approval does not bind this exact preview")
    rows = [
        (p, r)
        for p in status["phases"]
        for group in ("pending_items", "completed_details")
        for r in p.get(group, [])
        if r.get("task_id", r.get("id")) == task
    ]
    phases = [p for p in status["phases"] if p.get("id") == phase]
    if len(phases) != 1 or len(rows) > 1:
        raise ValueError(
            "expected a single named project phase and an unambiguous task"
        )
    resumed = bool(
        rows
        and rows[0][1].get("execution_status") == "approved"
        and rows[0][1].get("round_id") == rnd
        and rows[0][1].get("promotion_review_sha256") == digest
        and rows[0][1].get("authorization_status") == "authorized"
        and rows[0][1].get("authorized_write_paths") == scope
    )
    if rows:
        parent, row = rows[0]
        if (
            parent is not phases[0]
            or row not in parent.get("pending_items", [])
            or not resumed
            and row.get("execution_status")
            not in ("draft", "ready_for_review", "verification_failed")
        ):
            raise ValueError("task is active, completed or belongs to another phase")
        if (
            row.get("execution_status") == "verification_failed"
            and row.get("round_id") == rnd
        ):
            raise ValueError("failed round cannot be reopened")
    else:
        row = {"id": task}
        phases[0].setdefault("pending_items", []).append(row)
    route = META + "tasks/CURRENT_TASK.md"
    current = _front(root)
    route_matches = (
        resumed and current.get("task_id") == task and current.get("round_id") == rnd
    )
    if not route_matches and current.get("execution_status") not in (
        "draft",
        "ready_for_review",
        "verified",
        "verification_failed",
        "superseded",
        "cancelled",
    ):
        raise ValueError("an active route cannot be replaced")
    if current.get("execution_status") in ("draft", "ready_for_review") and current.get(
        "task_id"
    ) not in ("NONE", task):
        raise ValueError("another draft is the current task")
    manifest = base + "/package/round-manifest.json"
    if os.path.lexists(root / base / "package"):
        raise ValueError("round already frozen")
    consultation, instruction, final_plan = (
        base + "/" + n
        for n in ("consultation.md", "instruction.md", "acceptance-plan.json")
    )
    tm = META + f"tasks/active/{task}.task.json"
    row.pop("closure_record", None)
    row.update(
        task_id=task,
        round_id=rnd,
        consultation_status="agreed",
        execution_status="approved",
        authorization_status="authorized",
        promotion_review_sha256=digest,
        authorized_write_paths=scope,
        round_manifest_path=manifest,
    )
    inputs = list(
        dict.fromkeys(
            [
                *sorted(
                    p if p == "AGENTS.md" else META + p for p in MINIMUM_ROOT_INPUTS
                ),
                consultation,
                sheet,
                tm,
                instruction,
                final_plan,
                *pins,
            ]
        )
    )
    front = dict(
        task_id=task,
        round_id=rnd,
        execution_status="approved",
        contract=sheet,
        assigned_agent=agent,
        dispatch="NONE",
        consultation_ref=consultation,
        task_doc=sheet,
        task_manifest=tm,
        instruction=instruction,
        round_manifest=manifest,
    )
    final = dict(
        plan,
        document_type="acceptance_plan",
        status="approved",
        authorized_write_paths=scope,
    )
    task_doc = dict(
        task_id=task,
        round_id=rnd,
        status="approved",
        implementation_scope_check=True,
        acceptance_execution_plan=final_plan,
        allowed_write_paths=scope,
        completion_report_path=report,
        round_manifest_path=manifest,
        governed_input_paths=inputs,
    )
    outputs = {
        consultation: f"商讨状态：agreed\n用户记录：{agreement or approval}\n".encode(),
        instruction: f"# {task} / {rnd}\n实施者：{agent}\n合同：{sheet}\n验收计划：{final_plan}\n已审阅指令原文：{inst}\n批准记录：{approval}\n实际下发前不得实施。\n".encode(),
        final_plan: encode(final),
    }
    task_doc["bound_inputs_sha256"] = {
        **pins,
        **{p: _sha(data) for p, data in outputs.items()},
    }
    outputs.update(
        {
            tm: encode(task_doc),
            STATUS: encode(status),
            route: (
                "---\n" + "".join(f"{k}: {v}\n" for k, v in front.items()) + "---\n"
            ).encode(),
        }
    )
    prior = {}
    for rel, data in outputs.items():
        path = _file(root, rel, required=False)
        old = path.read_bytes() if path.exists() else None
        if old is not None and old != data and rel not in (route, STATUS):
            if (
                rel != tm
                or not rows
                or current.get("task_id") != task
                or current.get("execution_status") != "verification_failed"
            ):
                raise ValueError(f"conflicting existing file: {rel}")
        prior[rel] = old
    with ExitStack() as rollback:
        for rel, data in outputs.items():
            path = root / rel
            if prior[rel] == data:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            replace(path, data)
            if prior[rel] is None:
                rollback.callback(path.unlink)
            else:
                rollback.callback(replace, path, prior[rel])
        rollback.pop_all()
    return {"result": "promoted", "manifest": manifest, "dispatch": "not_dispatched"}


def main(directory, argv):
    parser = argparse.ArgumentParser(prog="tools.py promote-round", description=__doc__)
    parser.add_argument("task")
    parser.add_argument("round")
    parser.add_argument("--phase", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--agreement")
    parser.add_argument("--approval")
    args = parser.parse_args(argv)
    try:
        root = directory.parent
        if directory.name != ".dual-agent":
            raise ValueError("use the installed project launcher")
        with writer_lock(
            _file(
                root,
                META + "artifacts/events/governance-events.jsonl.lock",
                required=False,
            )
        ):
            result = promote(
                root,
                args.task,
                args.round,
                args.phase,
                args.agent,
                args.agreement,
                args.approval,
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"result": "error", "message": str(exc)}, ensure_ascii=False))
        return 1

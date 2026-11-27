"""Parameterized Steward workflow: plan, run, summarize.

Commands, identity and order come from the frozen, hash-verified round package. The
four live facts must be consistent, the policy active, the live ledger entry must
exist and authorize the current task/round, and the full event chain (present
hashes, valid linkage, real artifact evidence, manifest-bound dispatch) must verify
before any subprocess starts. The tool never hardcodes a project, round, event or
pass result, never approves, never edits ledgers/events/reports/packages/business,
and writes only outside the governed root.
"""

from __future__ import annotations

from pathlib import Path
from governance.read_session import ReadSession
from governance.read_session import SessionError
import argparse
import json
import os
from governance import read_session
from governance.linux_witness import LinuxWitness
from governance.macos_witness import MacOSWitness
from governance import implementation_scope as scope
from governance import round_package
from governance.layout import metadata_path, metadata_prefix
import stat
import sys
import time
import uuid
from governance.workflow_common import (
    FILE_ATTRIBUTE_REPARSE_POINT,
    WorkflowError,
    WorkflowInputError,
    _is_reparse,
    _sha,
)
from governance.workflow_events import (
    _event_chain_valid,
    _event_hash,
)
from governance.workflow_commands import (
    MODES,
    _find_reuse,
    _normalize_commands,
    _raw_sha,
    _verify_attempt,
    _write_attempt,
)
from governance.workflow_cleanup import (
    apply_cleanup,
    cleanup_plan,
)

TOOL = "steward_workflow"
FACTS = ("PROJECT_GOALS.json", "DEVELOPMENT_STATUS.json", "COMPONENT_REGISTRY.json", "COLLABORATION_POLICY.json")
EXECUTABLE_STATES = ("approved", "implementation_in_progress", "implementation_submitted")
RECURSION_MARKER = "governance.steward_workflow"
SKIP_DIRS = scope.SKIP_DIRS
_EXEC_STATES = set(EXECUTABLE_STATES)


def _load_json_object(session: ReadSession, rel: str) -> dict:
    try:
        value = session.read_json(rel)
    except SessionError as exc:
        raise WorkflowInputError(str(exc)) from exc
    if not isinstance(value, dict):
        raise WorkflowError(f"JSON root must be an object: {rel}")
    return value


def _route_front(session: ReadSession) -> dict[str, str]:
    text = session.read_text(metadata_path(session.root, "tasks/CURRENT_TASK.md"))
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise WorkflowError("CURRENT_TASK.md front matter is missing")
    block = text[4:].split("\n---\n", 1)[0]
    front = {}
    for line in block.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            front[key.strip()] = value.strip()
    return front


def _output_outside(root: Path, output: Path) -> None:
    root = root.resolve()
    output = output.resolve()
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise WorkflowError("output directory must be outside the governed root")
    current = output
    for _ in range(64):
        try:
            info = os.lstat(current)
        except OSError:
            return
        if stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT):
            raise WorkflowError("output path uses a symlink/reparse component")
        if current.parent == current:
            return
        current = current.parent


def _inventory_sha(session: ReadSession, root: Path, exclude: str) -> str:
    """One shared content inventory; refuses reparse/unsafe paths before any read."""
    root = root.resolve()
    entries: list[tuple[str, str]] = []
    exclude_resolved = str(Path(exclude).resolve()) if exclude else ""
    for base, dirs, files in os.walk(root):
        kept: list[str] = []
        for name in dirs:
            if name in SKIP_DIRS:
                continue
            path = Path(base) / name
            try:
                info = os.lstat(path)
            except OSError as exc:
                raise WorkflowError(f"inventory cannot inspect {path}: {exc}") from exc
            if getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
                raise WorkflowError(f"inventory refuses reparse directory: {path}")
            kept.append(name)
        dirs[:] = kept
        for name in files:
            path = Path(base) / name
            try:
                info = os.lstat(path)
            except OSError as exc:
                raise WorkflowError(f"inventory cannot inspect {path}: {exc}") from exc
            if getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
                raise WorkflowError(f"inventory refuses reparse file: {path}")
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                raise WorkflowError(f"inventory path escapes the root: {path}") from None
            if exclude_resolved and str(path.resolve()) == exclude_resolved:
                continue
            try:
                entries.append((rel, _sha(session.read_bytes(rel))))
            except SessionError as exc:
                raise WorkflowError(f"inventory refused unsafe path {rel}: {exc}") from exc
    return _sha(json.dumps(sorted(entries), ensure_ascii=False).encode("utf-8"))


def _stage_key(argv: list[str], env: dict[str, str], root: Path, manifest_sha: str,
               event_head: str, inventory_sha: str) -> str:
    try:
        from governance import steward_workflow as self_module
        tool_sha = _sha(Path(self_module.__file__).read_bytes())
    except (OSError, AttributeError):
        tool_sha = ""
    payload = json.dumps(
        {
            "argv": argv,
            "env": sorted(env.items()),
            "root": str(root.resolve()),
            "manifest_sha": manifest_sha,
            "event_head": event_head,
            "tool_sha": tool_sha,
            "inventory_sha": inventory_sha,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return _sha(payload.encode("utf-8"))


def _stage_inventory_hash(session, stage: dict) -> str:
    if stage.get("kind") == "main_regression" and getattr(session, "report_rel", ""):
        filtered = {rel: digest for rel, digest in session.inventory.items()
                    if rel != session.report_rel}
        return _sha(json.dumps(sorted(filtered.items()), ensure_ascii=False).encode("utf-8"))
    return session.inventory_id


def _live_task_entry(session: ReadSession, task_id: str) -> dict | None:
    status = _load_json_object(session, metadata_path(session.root, "DEVELOPMENT_STATUS.json"))
    for phase in status.get("phases", []) or []:
        if not isinstance(phase, dict):
            continue
        for section in ("completed_details", "pending_items"):
            for task in phase.get(section, []) or []:
                if not isinstance(task, dict):
                    continue
                if task.get("id") == task_id or task.get("task_id") == task_id:
                    return task
    return None


def _check_live_authorization(session: ReadSession, task_id: str, round_id: str,
                              manifest_rel: str, allowed_write_paths: object) -> None:
    entry = _live_task_entry(session, task_id)
    if entry is None:
        raise WorkflowError("live ledger has no entry for the current task")
    if entry.get("round_id") != round_id:
        raise WorkflowError("live task round disagrees with the manifest")
    if entry.get("round_manifest_path") != manifest_rel:
        raise WorkflowError("live task manifest binding disagrees with the given manifest")
    state = entry.get("execution_status")
    if state not in _EXEC_STATES:
        raise WorkflowError(f"live task state {state!r} is not executable")
    if entry.get("authorization_status") != "authorized":
        raise WorkflowError("live task authorization is not granted")
    entry_paths = entry.get("authorized_write_paths")
    if not isinstance(entry_paths, list) or not entry_paths:
        raise WorkflowError("live task has no authorized write paths")
    if not isinstance(allowed_write_paths, list) or set(entry_paths) != set(allowed_write_paths):
        raise WorkflowError("live authorized scope disagrees with the frozen manifest")


def _authorization_valid_now(session: ReadSession, task_id: str, round_id: str,
                             manifest_rel: str, allowed_write_paths: object) -> bool:
    """Re-check current policy/authorization before every execution or reuse decision."""
    try:
        policy = _load_json_object(session, metadata_path(session.root, "COLLABORATION_POLICY.json"))
        if policy.get("policy_status") != "active":
            return False
        _check_live_authorization(session, task_id, round_id, manifest_rel, allowed_write_paths)
    except (WorkflowError, WorkflowInputError):
        return False
    return True


def _load(root: Path, manifest_rel: str, *, require_dispatch: bool) -> tuple[ReadSession, str, str, dict, str, str, list[dict], bool, str]:
    session = ReadSession(root)
    front = _route_front(session)
    if front.get("execution_status") != "approved":
        raise WorkflowError(f"route task is not approved: {front.get('execution_status')!r}")
    if front.get("round_manifest") != manifest_rel:
        raise WorkflowError("live route does not point at the given round manifest")

    parts = manifest_rel.removeprefix(metadata_prefix(root)).split("/")
    if len(parts) != 5 or parts[0] != "task_interactions" or parts[4] != "round-manifest.json":
        raise WorkflowError("round manifest path is not canonical")
    task_id, round_id = parts[1], parts[2]
    if front.get("task_id") != task_id:
        raise WorkflowError("live route task identity disagrees with the manifest")
    if front.get("round_id") != round_id:
        raise WorkflowError("live route round identity disagrees with the manifest")

    policy = _load_json_object(session, metadata_path(session.root, "COLLABORATION_POLICY.json"))
    if policy.get("policy_status") != "active":
        raise WorkflowError("collaboration policy is not active")
    project_identity = None
    for rel in FACTS:
        doc = _load_json_object(session, metadata_path(root, rel))
        value = doc.get("project")
        if not isinstance(value, str) or not value:
            raise WorkflowError(f"fact source {rel} has no project identity")
        if project_identity is None:
            project_identity = value
        elif value != project_identity:
            raise WorkflowError(f"fact source {rel} project identity disagrees")

    try:
        event_text = session.read_text(metadata_path(session.root, "artifacts/events/governance-events.jsonl"))
    except SessionError:
        event_text = ""
    if not _event_chain_valid(session, event_text):
        raise WorkflowError("governance event chain is not hash-valid with real evidence")

    try:
        round_package.verify_package(root, manifest_rel, require_dispatch=require_dispatch, session=session)
    except SessionError as exc:
        raise WorkflowInputError(str(exc)) from exc
    except round_package.PackageError as exc:
        raise WorkflowError(str(exc)) from exc

    package_rel = manifest_rel.rsplit("/", 1)[0]
    frozen_task = _load_json_object(session, package_rel + "/inputs/" + front["task_manifest"])
    if frozen_task.get("task_id") != task_id or frozen_task.get("round_id") != round_id:
        raise WorkflowError("frozen task manifest identity disagrees with route")
    _check_live_authorization(session, task_id, round_id, manifest_rel, frozen_task.get("allowed_write_paths"))
    plan_rel = frozen_task.get("acceptance_execution_plan")
    if not isinstance(plan_rel, str) or not plan_rel:
        raise WorkflowError("frozen task manifest has no acceptance plan")
    frozen_plan = _load_json_object(session, package_rel + "/inputs/" + plan_rel)
    stages = _normalize_commands(frozen_plan.get("required_commands", frozen_plan.get("ordered_commands")))
    env = frozen_plan.get("environment", {}).get("env", {})
    if not isinstance(env, dict):
        env = {}
    manifest_sha = session.sha256(manifest_rel)
    event_head = _sha(event_text.encode("utf-8"))
    dispatched = True
    return (session, task_id, round_id, dict(env), manifest_sha, event_head, stages, dispatched,
            event_text, frozen_task.get("allowed_write_paths"))


def _frozen_binding(session: ReadSession, manifest_rel: str) -> dict[str, str]:
    package_rel = manifest_rel.rsplit("/", 1)[0]
    snapshot_route = package_rel + "/inputs/" + metadata_path(session.root, "tasks/CURRENT_TASK.md")
    try:
        front = round_package._front_from_text(session.read_text(snapshot_route))
    except (round_package.PackageError, SessionError) as exc:
        raise WorkflowError(f"cannot read frozen route snapshot: {exc}") from exc
    task_path = package_rel + "/inputs/" + front["task_manifest"]
    frozen_task = _load_json_object(session, task_path)
    plan_source = frozen_task.get("acceptance_execution_plan")
    if not isinstance(plan_source, str) or not plan_source:
        raise WorkflowError("frozen task manifest has no acceptance plan")
    plan_path = package_rel + "/inputs/" + plan_source
    manifest_doc = _load_json_object(session, manifest_rel)
    recorded = {entry.get("source_path"): entry
                for entry in manifest_doc.get("inputs", []) if isinstance(entry, dict)}
    task_raw = session.read_bytes(task_path)
    plan_raw = session.read_bytes(plan_path)
    task_sha = _sha(task_raw)
    plan_sha = _sha(plan_raw)
    task_entry = recorded.get(front["task_manifest"])
    plan_entry = recorded.get(plan_source)
    if task_sha != (task_entry or {}).get("sha256"):
        raise WorkflowError("frozen task manifest bytes do not match the round manifest")
    if plan_sha != (plan_entry or {}).get("sha256"):
        raise WorkflowError("frozen plan bytes do not match the round manifest")
    if task_entry is not None and len(task_raw) != task_entry.get("bytes"):
        raise WorkflowError("frozen task manifest length does not match the round manifest")
    if plan_entry is not None and len(plan_raw) != plan_entry.get("bytes"):
        raise WorkflowError("frozen plan length does not match the round manifest")
    return {"task_path": task_path, "task_sha256": task_sha,
            "plan_path": plan_path, "plan_sha256": plan_sha,
            "report_path": frozen_task.get("completion_report_path") or ""}


def _stages_for_mode(stages: list[dict], mode: str) -> list[tuple[int, dict]]:
    return [(i, s) for i, s in enumerate(stages, start=1) if mode in s["modes"]]


def _preview_load(root: Path, manifest_rel: str) -> tuple[ReadSession, str, str, dict, list[dict]]:
    """Cheap preview: read only the route and the manifest-bound frozen task/plan records."""
    session = ReadSession(root)
    front = _route_front(session)
    if front.get("round_manifest") != manifest_rel:
        raise WorkflowError("live route does not point at the given round manifest")
    parts = manifest_rel.removeprefix(metadata_prefix(root)).split("/")
    if len(parts) != 5 or parts[0] != "task_interactions" or parts[4] != "round-manifest.json":
        raise WorkflowError("round manifest path is not canonical")
    task_id, round_id = parts[1], parts[2]
    if front.get("task_id") != task_id or front.get("round_id") != round_id:
        raise WorkflowError("live route task/round identity disagrees with the manifest")
    binding = _frozen_binding(session, manifest_rel)
    frozen_plan = _load_json_object(session, binding["plan_path"])
    stages = _normalize_commands(frozen_plan.get("required_commands", frozen_plan.get("ordered_commands")))
    env = frozen_plan.get("environment", {}).get("env", {})
    if not isinstance(env, dict):
        env = {}
    return session, task_id, round_id, dict(env), stages


def plan(root: Path, manifest_rel: str, mode: str) -> dict:
    if mode not in MODES:
        raise WorkflowError(f"unknown mode: {mode!r}")
    _session, task_id, round_id, _env, stages = _preview_load(root, manifest_rel)
    out = []
    for original, stage in _stages_for_mode(stages, mode):
        if RECURSION_MARKER in " ".join(stage["argv"]):
            raise WorkflowError("a leaf stage must not invoke steward_workflow")
        out.append({
            "index": original,
            "stage_id": stage["stage_id"],
            "kind": stage["kind"],
            "modes": list(stage["modes"]),
            "depends_on": list(stage["depends_on"]),
            "argv": list(stage["argv"]),
        })
    return {
        "tool": TOOL,
        "subcommand": "plan",
        "task_id": task_id,
        "round_id": round_id,
        "mode": mode,
        "manifest": manifest_rel,
        "preview": True,
        "executable": False,
        "snapshot_only": True,
        "stage_count": len(out),
        "stages": out,
    }


def _recorded_run(output: Path) -> dict:
    """Bind the final decision to this generation of plan and index."""
    try:
        record = json.loads((output / "verdict.json").read_bytes())
        if (isinstance(record, dict) and record.get("subcommand") == "run"
                and record.get("output") == output.as_posix()
                and all(record.get(name + "_sha256") == _sha((output / (name + ".json")).read_bytes())
                        for name in ("plan", "index"))):
            return record
    except (OSError, ValueError, UnicodeError):
        pass
    return {}


def run(root: Path, manifest_rel: str, mode: str, output: Path) -> dict:
    if mode not in MODES:
        raise WorkflowError(f"unknown mode: {mode!r}")
    root = Path(root).resolve()
    output = output.resolve()
    _output_outside(root, output)
    _reject_wrong_identity(output, root, manifest_rel, mode)
    output.mkdir(parents=True, exist_ok=True)
    with audit_session(root, manifest_rel) as session:
        prior = _recorded_run(output)
        reusable = {}
        if prior.get("publication_eligible") is True and prior.get("verdict") == "pass":
            reusable = {row["stage_id"]: row for row in prior["results"]}
        env = session.env
        inventory_sha = session.inventory_id
        selected = _stages_for_mode(session.stages, mode)
        kept = {original for original, _ in selected}
        planned = []
        for original, stage in selected:
            planned.append({
                "index": original, "stage_id": stage["stage_id"], "kind": stage["kind"],
                "modes": list(stage["modes"]), "depends_on": list(stage["depends_on"]),
                "argv": list(stage["argv"]), "timeout_seconds": stage["timeout_seconds"],
                "key": _stage_key(stage["argv"], env, root, session.manifest_sha, session.event_head,
                                  _stage_inventory_hash(session, stage)),
            })
        frozen = _frozen_binding(session.session, manifest_rel)
        planned_by_id = {item["stage_id"]: item["argv"] for item in planned}
        planned_by_key = {item["stage_id"]: item["key"] for item in planned}
        canonical_cwd = str(root.resolve())
        canonical_env = dict(env)
        (output / "plan.json").write_text(json.dumps({
            "task_id": session.task_id, "round_id": session.round_id, "mode": mode,
            "manifest": manifest_rel, "root": str(root.resolve()),
            "run_id": uuid.uuid4().hex,
            "inventory_sha256": inventory_sha, "frozen_binding": frozen, "stages": planned,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        results = []
        blocked: set[int] = set()

        def _flush_index() -> None:
            (output / "index.json").write_text(
                json.dumps({"task_id": session.task_id, "round_id": session.round_id, "mode": mode,
                            "results": results}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        t_exec = time.monotonic()
        cleanup_unconfirmed = False
        for original, stage in selected:
            deps = stage["depends_on"]
            if cleanup_unconfirmed or any(dep not in kept or dep in blocked for dep in deps):
                blocked.add(original)
                results.append({"index": original, "stage_id": stage["stage_id"], "status": "blocked",
                                "reason": "timeout cleanup unconfirmed" if cleanup_unconfirmed else "dependency not executed or failed"})
                _flush_index()
                continue
            if RECURSION_MARKER in " ".join(stage["argv"]):
                raise WorkflowError("a leaf stage must not invoke steward_workflow")
            if not _authorization_valid_now(session.session, session.task_id, session.round_id,
                                            manifest_rel, session.allowed):
                blocked.add(original)
                results.append({"index": original, "stage_id": stage["stage_id"], "status": "blocked",
                                "reason": "authorization revoked before stage"})
                _flush_index()
                continue
            key = _stage_key(stage["argv"], env, root, session.manifest_sha, session.event_head,
                              _stage_inventory_hash(session, stage))
            reused = None
            if stage["reusable_readonly"] and stage["stage_id"] in reusable:
                reused = _find_reuse(output, reusable[stage["stage_id"]], stage["stage_id"], key, stage["argv"])
            if reused is not None:
                results.append({"index": original, "stage_id": stage["stage_id"], "status": "reused",
                                "key": key, "path": reused})
                _flush_index()
                continue
            record, path = _write_attempt(output, {**stage, "index": original}, key, root, env)
            if record["status"] == "timed_out":
                cleanup = record["timeout_cleanup"]
                cleanup_unconfirmed = not cleanup["direct_child_exited"] or cleanup["tree_stop"] == "unconfirmed"
            if record["status"] != "passed":
                blocked.add(original)
            results.append({"index": original, "stage_id": stage["stage_id"], "status": record["status"],
                            "key": key, "path": path})
            _flush_index()
        session.phase_seconds["execution"] = max(0.0, time.monotonic() - t_exec)
        _flush_index()
        t_summary = time.monotonic()
        problems = [f"{r.get('stage_id')}: {r.get('status')}" for r in results
                    if r.get("status") not in ("passed", "reused")]
        planned_order = [p["stage_id"] for p in planned]
        planned_full = {p["stage_id"]: p for p in planned}
        execution_by_stage = {r["stage_id"]: r for r in results}
        plan_bytes = b""
        try:
            plan_bytes = (output / "plan.json").read_bytes()
            persisted_plan = json.loads(plan_bytes.decode("utf-8"))
        except (OSError, json.JSONDecodeError):
            persisted_plan = None
            problems.append("persisted plan.json is unreadable after execution")
        if not isinstance(persisted_plan, dict):
            problems.append("persisted plan is not a valid object")
        else:
            if (persisted_plan.get("task_id") != session.task_id
                    or persisted_plan.get("round_id") != session.round_id
                    or persisted_plan.get("root") != str(root.resolve())
                    or persisted_plan.get("mode") != mode
                    or persisted_plan.get("manifest") != manifest_rel):
                problems.append("persisted plan identity/context disagrees with the admitted context")
            if persisted_plan.get("inventory_sha256") != inventory_sha:
                problems.append("persisted plan inventory_sha256 disagrees with the admitted snapshot")
            if persisted_plan.get("frozen_binding") != frozen:
                problems.append("persisted plan frozen binding disagrees with the admitted binding")
            pstages = persisted_plan.get("stages")
            if not isinstance(pstages, list) or not pstages:
                problems.append("persisted plan declares no stages")
            else:
                persisted_ids = [st.get("stage_id") for st in pstages]
                if persisted_ids != planned_order:
                    problems.append("persisted plan stage set/order disagrees with the approved plan")
                if len(set(persisted_ids)) != len(persisted_ids):
                    problems.append("persisted plan has duplicate stage ids")
                for st in pstages:
                    sid = st.get("stage_id")
                    canonical_st = planned_full.get(sid)
                    if canonical_st is None:
                        problems.append(f"{sid}: persisted plan stage is not in the approved plan")
                        continue
                    if st.get("index") != canonical_st["index"]:
                        problems.append(f"{sid}: persisted plan index disagrees with the approved index")
                    if st.get("argv") != canonical_st["argv"]:
                        problems.append(f"{sid}: persisted plan argv disagrees with the approved argv")
                    if st.get("key") != canonical_st["key"]:
                        problems.append(f"{sid}: persisted plan key disagrees with the approved key")
                    if st.get("timeout_seconds") != canonical_st["timeout_seconds"]:
                        problems.append(f"{sid}: persisted plan timeout disagrees with the approved plan")
                    if st.get("kind") != canonical_st["kind"]:
                        problems.append(f"{sid}: persisted plan kind disagrees with the approved kind")
                    if not isinstance(st.get("modes"), list) or list(st.get("modes")) != list(canonical_st["modes"]):
                        problems.append(f"{sid}: persisted plan modes disagree with the approved modes")
                    if not isinstance(st.get("depends_on"), list) or list(st.get("depends_on")) != list(canonical_st["depends_on"]):
                        problems.append(f"{sid}: persisted plan dependencies disagree with the approved plan")
        index_bytes = b""
        try:
            index_bytes = (output / "index.json").read_bytes()
            persisted_index = json.loads(index_bytes.decode("utf-8"))
        except (OSError, json.JSONDecodeError):
            persisted_index = None
            problems.append("persisted index.json is unreadable after execution")
        if persisted_index is not None:
            if (persisted_index.get("task_id") != session.task_id
                    or persisted_index.get("round_id") != session.round_id):
                problems.append("persisted index identity disagrees with the admitted context")
            if persisted_index.get("mode") != mode:
                problems.append("persisted index mode disagrees with the admitted mode")
        iresults = persisted_index.get("results") if persisted_index is not None else None
        if iresults is None:
            problems.append("persisted index lacks a results list")
            iresults = []
        if not isinstance(iresults, list) or not iresults:
            problems.append("persisted index declares no results")
        else:
            iids = [r.get("stage_id") for r in iresults]
            if iids != planned_order:
                problems.append("persisted index stage order disagrees with the approved plan")
            for r in iresults:
                sid = r.get("stage_id")
                canonical_st = planned_full.get(sid)
                execr = execution_by_stage.get(sid)
                if canonical_st is not None:
                    if r.get("key") != canonical_st["key"]:
                        problems.append(f"{sid}: persisted index key disagrees with the approved key")
                    if r.get("index") != canonical_st["index"]:
                        problems.append(f"{sid}: persisted index index disagrees with the approved index")
                if execr is not None:
                    if r.get("status") != execr.get("status"):
                        problems.append(f"{sid}: persisted index status disagrees with the actual execution")
                    if r.get("path") != execr.get("path"):
                        problems.append(f"{sid}: persisted index path disagrees with the actual attempt path")
        for r in iresults:
            sid = r.get("stage_id")
            canonical_st = planned_full.get(sid)
            path = r.get("path")
            if not (isinstance(path, str) and Path(path).is_file()):
                problems.append(f"{sid}: attempt evidence is missing after execution")
                continue
            try:
                raw = Path(path).read_bytes()
                rec = json.loads(raw.decode("utf-8"))
                if sid in execution_by_stage:
                    execution_by_stage[sid]["attempt_sha256"] = _sha(raw)
            except (OSError, json.JSONDecodeError):
                problems.append(f"{sid}: attempt is unreadable after execution")
                continue
            if not (_raw_sha(rec, "stdout") and _raw_sha(rec, "stderr")):
                problems.append(f"{sid}: attempt raw bytes were corrupted after execution")
            if rec.get("status") != "passed":
                problems.append(f"{sid}: persisted attempt status {rec.get('status')!r} is not a pass")
            if rec.get("exit_code") != 0:
                problems.append(f"{sid}: persisted attempt exit_code {rec.get('exit_code')!r} contradicts a pass")
            if canonical_st is not None and rec.get("index") != canonical_st["index"]:
                problems.append(f"{sid}: persisted attempt index disagrees with the approved index")
            if rec.get("stage_id") != sid:
                problems.append(f"{sid}: persisted attempt stage_id disagrees with the index")
            approved_argv = planned_by_id.get(sid)
            if approved_argv is not None and rec.get("argv") != approved_argv:
                problems.append(f"{sid}: persisted attempt argv disagrees with the approved argv")
            approved_key = planned_by_key.get(sid)
            if canonical_st is not None and rec.get("timeout_seconds") != canonical_st["timeout_seconds"]:
                problems.append(f"{sid}: persisted attempt timeout disagrees with the approved plan")
            if approved_key is not None and rec.get("key") != approved_key:
                problems.append(f"{sid}: persisted attempt key disagrees with the approved key")
            if rec.get("cwd") != canonical_cwd:
                problems.append(f"{sid}: persisted attempt cwd disagrees with the approved context")
            if rec.get("env") != canonical_env:
                problems.append(f"{sid}: persisted attempt environment disagrees with the approved context")
        command_verdict = "pass" if all(r["status"] in ("passed", "reused") for r in results) and results else "fail"
        session.phase_seconds["summary"] = max(0.0, time.monotonic() - t_summary)
        final = session.finalize()
        problems.extend(final["problems"])
        drift = {kind: final[kind] for kind in ("added", "removed", "modified")}
        root_metadata_changed = "." in drift["modified"]
        if root_metadata_changed:
            drift["modified"].remove(".")
            if not any(drift.values()):
                problems.append("workspace root metadata changed; no child path identified")
        problems.extend(f"{kind}: {rel}" for kind, paths in drift.items() for rel in paths)
        verdict = "fail" if problems else "pass"
        performance = {
            "whole_root_inventories": session.whole_root_inventories,
            "package_validations": session.package_validations,
            "phase_seconds": dict(session.phase_seconds),
        }
        payload = {
            "tool": TOOL, "subcommand": "run", "task_id": session.task_id, "round_id": session.round_id,
            "mode": mode, "manifest": manifest_rel, "output": output.as_posix(),
            "command_verdict": command_verdict, "verdict": verdict, "publication_eligible": verdict == "pass",
            "root_metadata_changed": root_metadata_changed, "workspace_drift": drift, "plan_sha256": _sha(plan_bytes),
            "index_sha256": _sha(index_bytes),
            "results": results, "problems": problems, "performance": performance,
        }
        _write_verdict(output, payload)
        return payload


def _relative_change(root: Path, rel: object) -> str:
    if not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/"):
        raise WorkflowError("change path must be a relative posix path")
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise WorkflowError("change path has an empty/dot segment")
    root = root.resolve()
    try:
        (root / rel).resolve().relative_to(root)
    except ValueError:
        raise WorkflowError("change path escapes the source root") from None
    current = root
    for part in parts:
        current = current / part
        try:
            info = os.lstat(current)
        except OSError:
            continue
        if _is_reparse(info):
            raise WorkflowError("source change path uses a reparse point")
    return rel


def _candidate_root(root: Path, candidate: Path) -> tuple[Path, Path]:
    root = root.resolve()
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return root, candidate
    raise WorkflowError("candidate must be an explicit external projection outside the root")


def _validate_candidate_change(root: Path, candidate: Path, rel: str) -> str:
    rel = _relative_change(root, rel)
    parts = rel.split("/")
    target = candidate
    for part in parts:
        target = target / part
        try:
            info = os.lstat(target)
        except OSError:
            continue
        if _is_reparse(info):
            raise WorkflowError("candidate change path uses a reparse point")
    resolved = (candidate / rel).resolve()
    try:
        resolved.relative_to(candidate)
    except ValueError:
        raise WorkflowError("candidate change path escapes the candidate projection") from None
    try:
        resolved.relative_to(root)
    except ValueError:
        pass
    else:
        raise WorkflowError("candidate change path routes back into the governed root")
    return rel


def _validate_candidate_changes(root: Path, candidate: Path, changes: object) -> list[str]:
    if not isinstance(changes, list):
        raise WorkflowError("changes must be a list")
    seen: set[str] = set()
    rels: list[str] = []
    for rel in changes:
        rel = _validate_candidate_change(root, candidate, rel)
        if rel in seen:
            raise WorkflowError(f"duplicate change path: {rel}")
        seen.add(rel)
        rels.append(rel)
    return rels


def candidate_plan(root: Path, candidate: Path, *, changes: list[str]) -> dict:
    """Read-only comparison of an external candidate projection with the root."""
    session = ReadSession(root)
    root, candidate = _candidate_root(root, candidate)
    rels = _validate_candidate_changes(root, candidate, changes)
    missing: list[str] = []
    changed: list[str] = []
    unchanged: list[str] = []
    for rel in rels:
        try:
            source = session.read_bytes(rel)
        except SessionError:
            missing.append(rel)
            continue
        target = candidate / rel
        if not target.is_file():
            missing.append(rel)
        elif target.read_bytes() == source:
            unchanged.append(rel)
        else:
            changed.append(rel)
    reusable = not missing and not changed
    return {
        "missing": missing, "changed": changed, "unchanged": unchanged,
        "reusable": reusable, "changes": rels,
        "reason": "" if reusable else "candidate projection differs from the root",
    }


def candidate_update(root: Path, candidate: Path, *, changes: list[str]) -> dict:
    """Write new objects into the external candidate, never touching the root."""
    session = ReadSession(root)
    root, candidate = _candidate_root(root, candidate)
    rels = _validate_candidate_changes(root, candidate, changes)
    updated: list[str] = []
    skipped: list[dict] = []
    for rel in rels:
        try:
            source = session.read_bytes(rel)
        except SessionError:
            skipped.append({"path": rel, "reason": "source unreadable"})
            continue
        target = candidate / rel
        staging = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = target.with_name(target.name + ".staged-" + uuid.uuid4().hex[:10])
            staging.write_bytes(source)
            os.replace(staging, target)
        except OSError as exc:
            skipped.append({"path": rel, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        finally:
            if staging is not None:
                try:
                    staging.unlink()
                except OSError:
                    pass
        updated.append(rel)
    return {"updated": sorted(updated), "skipped": skipped,
            "ok": not skipped and len(updated) == len(rels)}


def _write_verdict(output: Path, payload: dict) -> None:
    (output / "verdict.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    problems = payload.get("problems", [])
    lines = [f"# Steward workflow verdict: {payload.get('verdict')}", "",
             f"{len(problems)} problem(s).", ""]
    lines.extend(f"- {problem}" for problem in problems)
    (output / "review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize(output: Path) -> dict:
    output = output.resolve()
    if not output.is_dir():
        raise WorkflowError(f"output directory is missing: {output}")
    plan_path = output / "plan.json"
    index_path = output / "index.json"
    if not plan_path.is_file() or not index_path.is_file():
        raise WorkflowError("plan.json/index.json are required to bind the current run")
    try:
        plan_doc = json.loads(plan_path.read_text(encoding="utf-8"))
        index_doc = json.loads(index_path.read_text(encoding="utf-8"))
        planned = plan_doc.get("stages", [])
        index = index_doc.get("results", [])
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowInputError(str(exc)) from exc
    if not isinstance(planned, list) or not planned:
        raise WorkflowError("plan declares no expected stages")
    if not isinstance(index, list) or not index:
        raise WorkflowError("index declares no executed stages")
    problems: list[str] = []
    manifest_rel = plan_doc.get("manifest")
    canonical_task = canonical_round = None
    if isinstance(manifest_rel, str):
        parts = manifest_rel.removeprefix(".dual-agent/").split("/")
        if len(parts) == 5 and parts[0] == "task_interactions":
            canonical_task, canonical_round = parts[1], parts[2]
    if canonical_task is not None and plan_doc.get("task_id") != canonical_task:
        problems.append("plan task disagrees with the manifest identity")
    if canonical_round is not None and plan_doc.get("round_id") != canonical_round:
        problems.append("plan round disagrees with the manifest identity")
    if canonical_task is not None and index_doc.get("task_id") != canonical_task:
        problems.append("index task disagrees with the manifest identity")
    if canonical_round is not None and index_doc.get("round_id") != canonical_round:
        problems.append("index round disagrees with the manifest identity")
    canonical: dict[str, dict] = {}
    order: list[str] = []
    root_rel = plan_doc.get("root")
    plan_mode = plan_doc.get("mode")
    frozen = plan_doc.get("frozen_binding", {})
    canonical_env: dict[str, str] = {}
    if isinstance(root_rel, str) and isinstance(manifest_rel, str) and plan_mode in MODES:
        try:
            session = ReadSession(Path(root_rel))
            derived = _frozen_binding(session, manifest_rel)
            if frozen.get("plan_path") != derived["plan_path"] or frozen.get("plan_sha256") != derived["plan_sha256"]:
                problems.append("frozen plan binding was substituted or altered")
            if frozen.get("task_path") != derived["task_path"] or frozen.get("task_sha256") != derived["task_sha256"]:
                problems.append("frozen task binding was substituted or altered")
            raw = (Path(root_rel) / derived["plan_path"]).read_bytes()
            frozen_plan = json.loads(raw.decode("utf-8"))
            canonical_env = frozen_plan.get("environment", {}).get("env", {}) or {}
            for _, stage in _stages_for_mode(_normalize_commands(frozen_plan.get("required_commands", frozen_plan.get("ordered_commands"))), plan_mode):
                order.append(stage["stage_id"])
                canonical[stage["stage_id"]] = {
                    "argv": list(stage["argv"]), "kind": stage["kind"],
                    "modes": list(stage["modes"]), "depends_on": list(stage["depends_on"]),
                }
        except (OSError, json.JSONDecodeError, WorkflowError, KeyError, TypeError) as exc:
            problems.append(f"cannot read the frozen plan for evidence binding: {exc}")
    else:
        problems.append("plan lacks a valid frozen binding")
    expected = [item for item in planned if isinstance(item, dict)]
    current = {str(item.get("stage_id")): item for item in index if isinstance(item, dict)}
    if canonical:
        local_order = [str(item.get("stage_id")) for item in expected]
        if local_order != order:
            problems.append("plan stage order disagrees with the frozen approved order")
        if set(local_order) != set(order):
            problems.append("plan stage set disagrees with the frozen approved plan")
        index_order = [str(item.get("stage_id")) for item in index if isinstance(item, dict)]
        if index_order != order:
            problems.append("index stage order disagrees with the frozen approved order")
    if len(current) != len([item for item in index if isinstance(item, dict)]):
        problems.append("duplicate stage_id in the current index")
    missing = [str(item.get("stage_id")) for item in expected if str(item.get("stage_id")) not in current]
    if missing:
        problems.append("missing stages: " + ",".join(sorted(missing)))
    for plan_item in expected:
        sid = str(plan_item.get("stage_id"))
        run_item = current.get(sid)
        if run_item is None:
            continue
        if canonical.get(sid) is not None:
            if plan_item.get("argv") != canonical[sid]["argv"]:
                problems.append(f"{sid}: plan argv disagrees with the frozen approved argv")
            if run_item.get("key") != plan_item.get("key"):
                problems.append(f"{sid}: index key disagrees with the plan key")
        status = run_item.get("status")
        attempt_path = run_item.get("path")
        if status == "reused":
            if not isinstance(attempt_path, str) or not Path(attempt_path).is_file():
                problems.append(f"{sid}: reused attempt does not exist")
                continue
            try:
                record = json.loads(Path(attempt_path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                problems.append(f"{sid}: reused attempt is unreadable")
                continue
            problems.extend(_verify_attempt(record, plan_item))
            if canonical.get(sid) is not None and record.get("argv") != canonical[sid]["argv"]:
                problems.append(f"{sid}: attempt argv disagrees with the frozen approved argv")
            if canonical.get(sid) is not None:
                if record.get("cwd") != str(Path(root_rel).resolve()):
                    problems.append(f"{sid}: reused attempt cwd disagrees with the approved context")
                if record.get("env") != canonical_env:
                    problems.append(f"{sid}: reused attempt environment disagrees with the approved context")
            continue
        if not isinstance(attempt_path, str) or not Path(attempt_path).is_file():
            problems.append(f"{sid}: attempt path is missing")
            continue
        try:
            record = json.loads(Path(attempt_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            problems.append(f"{sid}: attempt is unreadable")
            continue
        problems.extend(_verify_attempt(record, plan_item))
        if canonical.get(sid) is not None and record.get("argv") != canonical[sid]["argv"]:
            problems.append(f"{sid}: attempt argv disagrees with the frozen approved argv")
        if canonical.get(sid) is not None:
            if record.get("cwd") != str(Path(root_rel).resolve()):
                problems.append(f"{sid}: attempt cwd disagrees with the approved context")
            if record.get("env") != canonical_env:
                problems.append(f"{sid}: attempt environment disagrees with the approved context")
        if status != record.get("status"):
            problems.append(f"{sid}: index status disagrees with the attempt")
        if status != "passed":
            problems.append(f"{sid}: status {status} is not a pass")
    original = _recorded_run(output)
    if not original:
        problems.append("no final run record bound to this plan and index")
    elif original.get("verdict") != "pass" or original.get("publication_eligible") is not True:
        problems.extend(original.get("problems") or ["original run evidence is ineligible"])
    else:
        for row in original["results"]:
            path = Path(row["path"])
            if not path.is_file() or _sha(path.read_bytes()) != row.get("attempt_sha256"):
                problems.append(f"{row['stage_id']}: recorded attempt bytes changed")
    verdict = "pass" if not problems else "fail"
    payload = {"tool": TOOL, "subcommand": "summarize", "output": output.as_posix(),
               "verdict": verdict, "problems": problems, "results": sorted(current),
               "evidence_only": True, "original_run_eligible": original.get("publication_eligible"),
               "publication_eligible": False}
    return payload


class _AuditSession:
    """One ReadSession + one shared whole-root content inventory for many consumers."""

    def __init__(self, root: Path, manifest_rel: str, witness=None) -> None:
        self.change_witness = witness
        self.root = Path(root).resolve()
        self.manifest_rel = manifest_rel
        self.package_validations = 1
        self.whole_root_inventories = 0
        self.phase_seconds: dict[str, float] = {}
        self.finalized = False
        self._t0 = time.monotonic()
        (self.session, self.task_id, self.round_id, self.env, self.manifest_sha, self.event_head,
         self.stages, self.dispatched, event_text, self.allowed) = _load(self.root, manifest_rel, require_dispatch=True)
        self.report_rel = _frozen_binding(self.session, manifest_rel).get("report_path", "")
        self.phase_seconds["admission"] = max(0.0, time.monotonic() - self._t0)
        self._t1 = time.monotonic()
        self.inventory, self.signatures, self.dirs, self._unavailable = self._walk_inventory()
        if self.session.read_json_object(manifest_rel).get("implementation_scope_check") is True:
            events = [json.loads(line) for line in event_text.splitlines() if line.strip()]
            events = [e for e in events if e.get("task_id") == self.task_id and e.get("round_id") == self.round_id]
            dispatch = next(e for e in events if e["event_type"] == "task_dispatched")
            current = scope.compare(self.session, dispatch, self.allowed, self.inventory,
                                    [e.get("decision_record") for e in events],
                                    next((e["steward_cleanup"] for e in events if "steward_cleanup" in e), None))
            for check in [e["scope_check"] for e in events if "scope_check" in e] + [current]:
                if not check["passed"]:
                    raise WorkflowError("changes outside grant; source unattributed: " +
                                        json.dumps(check["outside_grant"], ensure_ascii=False))
            submitted = next((e for e in reversed(events) if e.get("event_type") == "implementation_submitted"), None)
            if submitted is not None:
                sub_ref = submitted.get("decision_record")
                sub_doc = self.session.read_json_object(sub_ref) if isinstance(sub_ref, str) and sub_ref else {}
                submitted_files = (
                    sub_doc.get("submitted_files")
                    if isinstance(sub_doc.get("submitted_files"), dict)
                    else submitted.get("submitted_files")
                )
                if not isinstance(submitted_files, dict) or set(submitted_files) != set(self.allowed):
                    raise WorkflowError("submitted record lacks exact submitted_files fingerprints for all authorized paths")
                for rel, expected_sha in submitted_files.items():
                    if self.inventory.get(rel) != expected_sha:
                        raise WorkflowError(
                            f"submitted file changed after submission; restore the submitted bytes or close as failed and start a new round: {rel}"
                        )
        self.whole_root_inventories += 1
        self.inventory_id = _sha(json.dumps(sorted(self.inventory.items()), ensure_ascii=False).encode("utf-8"))
        self.phase_seconds["admission"] = max(0.0, time.monotonic() - self._t0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.change_witness is not None:
            self.change_witness.close()

    def _walk_inventory(self, *, final=False) -> tuple[dict[str, str], dict[str, object], dict[str, object], bool]:
        entries: dict[str, str] = {}
        signatures: dict[str, object] = {}
        dirs: dict[str, object] = {}
        unavailable = False
        root = self.root
        # Notification-backed audits rehash final bytes independently of the admission cache.
        reader = ReadSession(root) if final and self.change_witness is not None else self.session
        for base, dirs_names, files in os.walk(root, onerror=scope.inventory_error):
            try:
                base_rel = Path(base).relative_to(root).as_posix()
            except ValueError:
                base_rel = "."
            base_info = None
            try:
                base_info = os.lstat(Path(base))
                base_ino = base_info.st_ino
            except OSError:
                base_ino = None
            if self.change_witness is None:
                base_change = read_session._nt_change_time_ns(Path(base))
            else:
                base_change = base_info.st_ctime_ns if base_info else None
                if not final and base_info is not None:
                    self.change_witness.watch(base, base_rel, base_info.st_dev)
            if base_change is None:
                unavailable = True
            dirs[base_rel] = (base_ino, base_change)
            kept: list[str] = []
            for name in dirs_names:
                if name in SKIP_DIRS:
                    continue
                path = Path(base) / name
                try:
                    info = os.lstat(path)
                except OSError as exc:
                    raise WorkflowError(f"inventory cannot inspect {path}: {exc}") from exc
                if _is_reparse(info) or stat.S_ISLNK(info.st_mode):
                    raise WorkflowError(f"inventory refuses reparse directory: {path}")
                kept.append(name)
            dirs_names[:] = kept
            for name in files:
                path = Path(base) / name
                try:
                    info = os.lstat(path)
                except OSError as exc:
                    raise WorkflowError(f"inventory cannot inspect {path}: {exc}") from exc
                if _is_reparse(info) or stat.S_ISLNK(info.st_mode):
                    raise WorkflowError(f"inventory refuses reparse file: {path}")
                rel = path.relative_to(root).as_posix()
                try:
                    if self.change_witness is not None and base_info and info.st_dev != base_info.st_dev:
                        self.change_witness.error = f"stability witness cannot cover mounted file: {rel}"
                    if not final and isinstance(self.change_witness, MacOSWitness):
                        self.change_witness.watch(path, rel, info.st_dev)
                    entries[rel] = _sha(reader.read_bytes(rel))
                    signatures[rel] = reader._signature(path)
                    if signatures[rel] is None:
                        unavailable = True
                except SessionError as exc:
                    raise WorkflowError(f"inventory refused unsafe path {rel}: {exc}") from exc
        return entries, signatures, dirs, unavailable

    def _authority_still_valid(self) -> bool:
        return _authorization_valid_now(self.session, self.task_id, self.round_id,
                                        self.manifest_rel, self.allowed)

    def plan(self, mode: str) -> dict:
        if mode not in MODES:
            raise WorkflowError(f"unknown mode: {mode!r}")
        out: list[dict] = []
        for original, stage in _stages_for_mode(self.stages, mode):
            if RECURSION_MARKER in " ".join(stage["argv"]):
                raise WorkflowError("a leaf stage must not invoke steward_workflow")
            out.append({
                "index": original,
                "stage_id": stage["stage_id"],
                "kind": stage["kind"],
                "modes": list(stage["modes"]),
                "depends_on": list(stage["depends_on"]),
                "argv": list(stage["argv"]),
            })
        return {"tool": TOOL, "subcommand": "plan", "task_id": self.task_id, "round_id": self.round_id,
                "mode": mode, "manifest": self.manifest_rel, "executable": False, "preview": True,
                "snapshot_only": True, "inventory_id": self.inventory_id,
                "stage_count": len(out), "stages": out}

    def scope_diff(self, baseline_entries: dict[str, str]) -> dict:
        modified = [rel for rel, digest in self.inventory.items()
                    if rel in baseline_entries and baseline_entries[rel] != digest]
        added = [rel for rel in self.inventory if rel not in baseline_entries]
        removed = [rel for rel in baseline_entries if rel not in self.inventory]
        return {"inventory_id": self.inventory_id, "modified": sorted(modified),
                "added": sorted(added), "removed": sorted(removed), "snapshot_only": True}

    def candidate_plan(self, candidate: Path, *, changes: list[str]) -> dict:
        candidate = Path(candidate).resolve()
        rels = _validate_candidate_changes(self.root, candidate, changes)
        missing: list[str] = []
        changed: list[str] = []
        unchanged: list[str] = []
        for rel in rels:
            try:
                source = self.session.read_bytes(rel)
            except SessionError:
                missing.append(rel)
                continue
            target = candidate / rel
            if not target.is_file():
                missing.append(rel)
            elif target.read_bytes() == source:
                unchanged.append(rel)
            else:
                changed.append(rel)
        reusable = not missing and not changed
        return {"inventory_id": self.inventory_id, "missing": missing, "changed": changed,
                "unchanged": unchanged, "reusable": reusable, "changes": rels,
                "snapshot_only": True,
                "reason": "" if reusable else "candidate projection differs from the root"}

    def finalize(self) -> dict:
        """One final whole-root inventory + small authority check; idempotent."""
        if self.finalized:
            return self._final_result
        self._t1 = time.monotonic()
        current, current_sigs, current_dirs, unavailable = self._walk_inventory(final=True)
        self.whole_root_inventories += 1
        self.phase_seconds["final"] = max(0.0, time.monotonic() - self._t1)
        added = [rel for rel in current if rel not in self.inventory]
        removed = [rel for rel in self.inventory if rel not in current]
        modified = [rel for rel in current if rel in self.inventory and (
            current[rel] != self.inventory[rel]
            or current_sigs.get(rel) != self.signatures.get(rel)
        )]
        dirs_added = sorted(set(current_dirs) - set(self.dirs))
        dirs_removed = sorted(set(self.dirs) - set(current_dirs))
        dirs_modified = sorted(rel for rel in set(current_dirs) & set(self.dirs)
                               if current_dirs.get(rel) != self.dirs.get(rel))
        problems: list[str] = []
        if not self._authority_still_valid():
            problems.append("live authorization is no longer valid")
        if self.change_witness is not None:
            touched = self.change_witness.changes(SKIP_DIRS)
            classified = set(added + removed + dirs_added + dirs_removed + dirs_modified)
            modified = sorted(set(modified) | (touched - classified))
            if self.change_witness.error:
                problems.append(self.change_witness.error)
            self.change_witness.close()
        if unavailable or self._unavailable:
            problems.append("stability witness unavailable; publication eligibility cannot be proven")
        if added or removed or modified or dirs_added or dirs_removed or dirs_modified:
            problems.append("workspace drifted from the admission snapshot")
        eligible = not problems
        self._final_result = {
            "publication_eligible": eligible,
            "added": sorted(added + dirs_added), "removed": sorted(removed + dirs_removed),
            "modified": sorted(modified + dirs_modified),
            "problems": problems,
        }
        self.finalized = True
        return self._final_result


def audit_session(root: Path, manifest_rel: str):
    witness = None
    if sys.platform == "linux":
        witness = LinuxWitness()
    elif sys.platform == "darwin":
        witness = MacOSWitness()
    try:
        return _AuditSession(root, manifest_rel, witness)
    except BaseException:
        if witness is not None:
            witness.close()
        raise


def plan_all(root: Path, manifest_rel: str) -> dict:
    _session, task_id, round_id, _env, stages = _preview_load(root, manifest_rel)

    def _view(mode: str) -> dict:
        out = []
        for original, stage in _stages_for_mode(stages, mode):
            if RECURSION_MARKER in " ".join(stage["argv"]):
                raise WorkflowError("a leaf stage must not invoke steward_workflow")
            out.append({
                "index": original, "stage_id": stage["stage_id"], "kind": stage["kind"],
                "modes": list(stage["modes"]), "depends_on": list(stage["depends_on"]),
                "argv": list(stage["argv"]),
            })
        return {"tool": TOOL, "subcommand": "plan", "task_id": task_id, "round_id": round_id,
                "mode": mode, "manifest": manifest_rel, "preview": True, "executable": False,
                "snapshot_only": True, "stage_count": len(out), "stages": out}

    return {"plans": {"dispatch": _view("dispatch"), "acceptance": _view("acceptance")}}


def _reject_wrong_identity(output: Path, root: Path, manifest_rel: str, mode: str) -> None:
    plan_path = output / "plan.json"
    if not plan_path.is_file():
        return
    try:
        doc = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"existing output plan.json is unreadable: {exc}") from exc
    if (doc.get("manifest") != manifest_rel or doc.get("mode") != mode
            or doc.get("root") != str(root.resolve())):
        raise WorkflowError("output directory belongs to a different root/task/round/manifest/mode")


def audit(root: Path, manifest_rel: str, mode: str, output: Path, *,
          allow_missing_report: bool = False) -> dict:
    if allow_missing_report:
        raise WorkflowError("WORKFLOW_MISSING_REPORT_BYPASS_DISABLED: the missing-report bypass is retired")
    return run(root, manifest_rel, mode, output)


def prepare_review(root: Path, manifest_rel: str, output: Path, *,
                   decision: str, findings_path: Path) -> dict:
    """Retired compatibility entry point: automatic review proposals are disabled."""
    raise WorkflowError("WORKFLOW_REVIEW_DISABLED: automatic review proposals are retired; "
                        "the Steward owns the final governance decision")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subcommand", choices=["plan", "run", "summarize", "audit", "review"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest")
    parser.add_argument("--mode")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-missing-report", action="store_true")
    parser.add_argument("--decision")
    parser.add_argument("--findings", type=Path)
    args = parser.parse_args(argv)
    try:
        if (args.subcommand != "summarize" or args.output is None) and not args.manifest:
            args.manifest = _route_front(ReadSession(args.root)).get("round_manifest")
        mode = args.mode or "acceptance"
        root = args.root.resolve()
        identity = _sha((str(root) + "\n" + (args.manifest or "") + "\n" + mode).encode())
        output = args.output or root.parent / (root.name + "-steward-output") / identity
        if args.subcommand == "plan":
            if args.mode == "all":
                payload = plan_all(args.root, args.manifest or "")
            else:
                payload = plan(args.root, args.manifest or "", mode)
        elif args.subcommand == "run":
            payload = run(args.root, args.manifest or "", mode,
                          output)
        elif args.subcommand == "audit":
            payload = audit(args.root, args.manifest or "", mode,
                            output,
                            allow_missing_report=args.allow_missing_report)
        elif args.subcommand == "review":
            payload = prepare_review(args.root, args.manifest or "", args.output or Path.cwd(),
                                     decision=args.decision or "verification_failed",
                                     findings_path=args.findings)
        else:
            payload = summarize(output)
        print(json.dumps(payload, ensure_ascii=False))
        if args.subcommand in ("run", "summarize", "audit") and payload.get("verdict") != "pass":
            return 1
        return 0
    except WorkflowInputError as exc:
        print(json.dumps({"tool": TOOL, "result": "error", "errors": [{"code": "WORKFLOW_INPUT", "path": "", "message": str(exc)}]}, ensure_ascii=False), file=sys.stderr)
        return 2
    except WorkflowError as exc:
        print(json.dumps({"tool": TOOL, "result": "invalid", "errors": [{"code": "WORKFLOW_INVALID", "path": "", "message": str(exc)}]}, ensure_ascii=False), file=sys.stderr)
        return 1
    except (OSError, SessionError) as exc:
        print(json.dumps({"tool": TOOL, "result": "error", "errors": [{"code": "WORKFLOW_IO", "path": "", "message": str(exc)}]}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

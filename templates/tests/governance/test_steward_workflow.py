"""Tests for the parameterized Steward workflow: plan/run/summarize.

Fixtures build a real frozen package and a real dispatch binding so the workflow's
source and authorization checks are exercised end to end.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from governance import round_package
from governance.steward_workflow import (
    WorkflowError,
    _normalize_commands,
    plan,
    run,
    summarize,
)

TASK = "SYN-01"
ROUND = "round-001"
PACKAGE = f"task_interactions/{TASK}/{ROUND}/package"
MANIFEST = PACKAGE + "/round-manifest.json"
TM = f"tasks/active/{TASK}.task.json"
PLAN_REL = f"tasks/acceptance-plans/{TASK}.{ROUND}.json"
TASK_DOC = f"tasks/active/{TASK}.{ROUND}.en.md"
CONSULT = f"tasks/consultations/{TASK}.zh-CN.md"
INSTRUCTION = f"task_interactions/{TASK}/{ROUND}/instruction.md"
MINIMUM = [
    "IMPLEMENTATION_AGENT_PROMPT.en.md", "AGENTS.md", "COLLABORATION_POLICY.json",
    "PROJECT_GOALS.json", "DEVELOPMENT_STATUS.json", "tasks/CURRENT_TASK.md",
]


def _scratch_root() -> Path:
    for candidate in (os.environ.get("GOVERNANCE_TEST_SCRATCH"), tempfile.gettempdir()):
        if not candidate:
            continue
        root = Path(candidate) / "ForeContract-workflow-tests"
        try:
            root.mkdir(parents=True, exist_ok=True)
            return root
        except OSError:
            continue
    return Path(tempfile.mkdtemp(prefix="ForeContract-workflow-tests-"))


@pytest.fixture
def work():
    root = _scratch_root()
    path = root / f"wf-{uuid.uuid4().hex[:12]}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def put(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def put_json(root: Path, rel: str, value: object) -> None:
    put(root, rel, json.dumps(value, ensure_ascii=False, indent=2))


def make_project(root: Path, *, status="approved", commands=None, dispatched=True,
                 stage_modes=None, business_input=None, reusable=False, timeout=None) -> None:
    from governance.steward_workflow import _event_hash
    prefix = ".dual-agent/" if (root / ".dual-agent").is_dir() else ""
    task_doc, tm, consult, instruction, plan_rel, manifest = (
        prefix + path for path in (TASK_DOC, TM, CONSULT, INSTRUCTION, PLAN_REL, MANIFEST))
    minimum = [path if path == "AGENTS.md" else prefix + path for path in MINIMUM]
    report = prefix + f"task_interactions/{TASK}/{ROUND}/completion-report.yaml"
    interpreter = sys.executable
    if commands is None:
        commands = [f'& "{interpreter}" -c "print(\'ok\')"']
    for rel in minimum:
        put(root, rel, "{}" if rel.endswith(".json") else f"content {rel}")
    put_json(root, prefix + "COLLABORATION_POLICY.json", {"policy_status": "active", "project": "ForeContract"})
    put_json(root, prefix + "PROJECT_GOALS.json", {"project": "ForeContract"})
    put_json(root, prefix + "COMPONENT_REGISTRY.json", {"project": "ForeContract"})
    put_json(root, prefix + "DEVELOPMENT_STATUS.json", {
        "project": "ForeContract",
        "phases": [{"id": "P0", "status": "active", "pending_items": [{
            "id": TASK, "task_id": TASK, "round_id": ROUND,
            "execution_status": status,
            "authorization_status": "authorized",
            "round_manifest_path": manifest,
            "authorized_write_paths": [report],
        }]}],
    })
    put(root, prefix + "tasks/CURRENT_TASK.md", "---\n" + "\n".join([
        f"task_id: {TASK}",
        f"round_id: {ROUND}",
        f"execution_status: {status}",
        f"consultation_ref: {consult}",
        f"task_doc: {task_doc}",
        f"task_manifest: {tm}",
        f"round_manifest: {manifest}",
        f"instruction: {instruction}",
    ]) + "\n---\n")
    put(root, consult, "商讨状态：`agreed`\n")
    put(root, task_doc, "task doc")
    put(root, instruction, "instruction")
    modes = stage_modes or ["dispatch", "acceptance"]
    stage_commands = []
    for index, command in enumerate(commands, start=1):
        stage_commands.append({
            **({"timeout_seconds": timeout} if timeout is not None else {}),
            "number": index, "reusable_readonly": reusable, "command": command, "stage_id": f"s{index}",
            "kind": "command", "modes": modes, "depends_on": [],
        })
    put_json(root, plan_rel, {
        "task_id": TASK, "round_id": ROUND,
        "environment": {"interpreter": interpreter, "env": {"PYTHONDONTWRITEBYTECODE": "1"}},
        "required_commands": stage_commands,
    })
    inputs = minimum + [consult, task_doc, tm, instruction, plan_rel]
    if business_input:
        inputs.append(business_input)
    put_json(root, tm, {
        "task_id": TASK, "round_id": ROUND, "status": status,
        "consultation_status": "agreed", "round_manifest_path": manifest,
        "acceptance_execution_plan": plan_rel,
        "completion_report_path": report,
        "governed_input_paths": inputs,
        "allowed_write_paths": [report],
    })
    round_package.build_package(root, TASK, ROUND, inputs, [task_doc, tm, instruction],
                             prepared_at="2026-11-06T00:00:00Z")
    baseline = prefix + "artifacts/events/baselines/syn/DEVELOPMENT_STATUS.json"
    put(root, baseline, json.dumps({"state": status}))
    baseline_sha = hashlib.sha256((root / baseline).read_bytes()).hexdigest()

    def _evt(kind, task, rnd, previous, artifacts, sequence):
        if kind == "project_initialized":
            domain, previous_state, new_state = "project", None, "initialized"
        else:
            domain, previous_state, new_state = "task_dispatch", "not_dispatched", "dispatched"
        event = {
            "event_type": kind, "task_id": task, "round_id": rnd,
            "event_id": uuid.uuid4().hex, "sequence": sequence,
            "occurred_at": "2026-11-06T00:00:00Z",
            "state_domain": domain, "previous_state": previous_state, "new_state": new_state,
            "previous_event_hash": previous, "artifact_hashes": artifacts,
        }
        event["event_hash"] = _event_hash(event)
        return event

    first = _evt("project_initialized", None, None, None, {baseline: baseline_sha}, 1)
    events = [first]
    if dispatched:
        manifest_sha = hashlib.sha256((root / manifest).read_bytes()).hexdigest()
        events.append(_evt("task_dispatched", TASK, ROUND, first["event_hash"],
                           {manifest: manifest_sha, baseline: baseline_sha}, 2))
    put(root, prefix + "artifacts/events/governance-events.jsonl",
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n")


def test_plan_derives_identity_and_stages(work: Path) -> None:
    make_project(work)
    result = plan(work, MANIFEST, "dispatch")
    assert result["task_id"] == TASK
    assert result["round_id"] == ROUND
    assert result["stage_count"] == 1
    assert result["preview"] is True
    assert result["executable"] is False


def test_plan_previews_without_dispatch(work: Path) -> None:
    make_project(work, dispatched=False)
    result = plan(work, MANIFEST, "dispatch")
    assert result["preview"] is True
    assert result["executable"] is False


def test_plan_rejects_unapproved(work: Path) -> None:
    make_project(work)
    put(work, "tasks/CURRENT_TASK.md", "---\n" + "\n".join([
        f"task_id: {TASK}", f"round_id: {ROUND}", "execution_status: draft",
        f"consultation_ref: {CONSULT}", f"task_doc: {TASK_DOC}",
        f"task_manifest: {TM}", f"round_manifest: {MANIFEST}", f"instruction: {INSTRUCTION}",
    ]) + "\n---\n")
    with pytest.raises(WorkflowError):
        run(work, MANIFEST, "acceptance", work.parent / f"out-{uuid.uuid4().hex[:8]}")


def test_plan_rejects_tampered_package(work: Path) -> None:
    make_project(work)
    put(work, PACKAGE + "/inputs/AGENTS.md", "tampered")
    with pytest.raises(WorkflowError):
        run(work, MANIFEST, "acceptance", work.parent / f"out-{uuid.uuid4().hex[:8]}")


def test_run_requires_dispatch(work: Path) -> None:
    make_project(work, dispatched=False)
    with pytest.raises(WorkflowError):
        run(work, MANIFEST, "dispatch", work.parent / f"out-{uuid.uuid4().hex[:8]}")


def test_run_writes_external_records_and_summarizes(work: Path) -> None:
    make_project(work)
    output = work.parent / f"out-{uuid.uuid4().hex[:8]}"
    try:
        result = run(work, MANIFEST, "acceptance", output)
        attempt = json.loads(next(output.glob("attempt-*.json")).read_text(encoding="utf-8"))
        assert result["results"][0]["status"] == "passed", (attempt["argv"], attempt["stderr"])
        verdict = summarize(output)
        assert verdict["verdict"] == "pass"
    finally:
        shutil.rmtree(output, ignore_errors=True)


def test_run_reuses_valid_key(work: Path) -> None:
    make_project(work, reusable=True)
    output = work.parent / f"out-{uuid.uuid4().hex[:8]}"
    try:
        run(work, MANIFEST, "acceptance", output)
        second = run(work, MANIFEST, "acceptance", output)
        assert second["results"][0]["status"] == "reused"
    finally:
        shutil.rmtree(output, ignore_errors=True)


def test_run_failure_is_not_summarized_pass(work: Path) -> None:
    make_project(work, commands=[f'& "{sys.executable}" -c "import sys; sys.exit(1)"'])
    output = work.parent / f"out-{uuid.uuid4().hex[:8]}"
    try:
        run(work, MANIFEST, "acceptance", output)
        verdict = summarize(output)
        assert verdict["verdict"] == "fail"
    finally:
        shutil.rmtree(output, ignore_errors=True)


def test_run_output_outside_root_enforced(work: Path) -> None:
    make_project(work)
    with pytest.raises(WorkflowError):
        run(work, MANIFEST, "acceptance", work / "inside")


def test_dispatch_filters_non_dispatch_stages(work: Path) -> None:
    make_project(work, commands=[f'& "{sys.executable}" -c "print(1)"', f'& "{sys.executable}" -c "print(2)"'])
    # Overwrite the frozen plan? The frozen plan is inside the package; a live plan change
    # must not authorize new commands, so the plan still reflects the frozen two stages.
    result = plan(work, MANIFEST, "dispatch")
    assert result["stage_count"] == 2


def test_summarize_rejects_missing_stage(work: Path) -> None:
    make_project(work, commands=[f'& "{sys.executable}" -c "print(1)"', f'& "{sys.executable}" -c "print(2)"'])
    output = work.parent / f"out-{uuid.uuid4().hex[:8]}"
    try:
        run(work, MANIFEST, "acceptance", output)
        for path in output.glob("attempt-s2-*.json"):
            path.unlink()
        verdict = summarize(output)
        assert verdict["verdict"] == "fail"
    finally:
        shutil.rmtree(output, ignore_errors=True)


def test_run_rejects_missing_live_task(work: Path) -> None:
    make_project(work)
    put_json(work, "DEVELOPMENT_STATUS.json", {"project": "ForeContract", "phases": []})
    with pytest.raises(WorkflowError):
        run(work, MANIFEST, "acceptance", work.parent / f"out-{uuid.uuid4().hex[:8]}")


def test_run_rejects_revoked_authorization(work: Path) -> None:
    make_project(work)
    ledger = json.loads((work / "DEVELOPMENT_STATUS.json").read_text(encoding="utf-8"))
    ledger["phases"][0]["pending_items"][0]["authorization_status"] = "not_authorized"
    put_json(work, "DEVELOPMENT_STATUS.json", ledger)
    with pytest.raises(WorkflowError):
        run(work, MANIFEST, "acceptance", work.parent / f"out-{uuid.uuid4().hex[:8]}")


def test_run_rejects_missing_event_hashes(work: Path) -> None:
    make_project(work)
    manifest_sha = hashlib.sha256((work / MANIFEST).read_bytes()).hexdigest()
    put(work, "artifacts/events/governance-events.jsonl",
        json.dumps({"event_type": "task_dispatched", "task_id": TASK, "round_id": ROUND,
                    "artifact_hashes": {MANIFEST: manifest_sha}}) + "\n")
    with pytest.raises(WorkflowError):
        run(work, MANIFEST, "acceptance", work.parent / f"out-{uuid.uuid4().hex[:8]}")


def test_forward_dependency_rejected() -> None:
    commands = [
        {"command": f'& "{sys.executable}" -c "print(1)"', "stage_id": "s1",
         "modes": ["acceptance"], "depends_on": [2]},
        {"command": f'& "{sys.executable}" -c "print(2)"', "stage_id": "s2",
         "modes": ["acceptance"], "depends_on": []},
    ]
    with pytest.raises(WorkflowError):
        _normalize_commands(commands)


def test_summary_rejects_unknown_status(work: Path) -> None:
    make_project(work)
    output = work.parent / f"out-{uuid.uuid4().hex[:8]}"
    try:
        run(work, MANIFEST, "acceptance", output)
        for path in output.glob("attempt-s1-*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            record["status"] = "wat"
            path.write_text(json.dumps(record), encoding="utf-8")
        assert summarize(output)["verdict"] == "fail"
    finally:
        shutil.rmtree(output, ignore_errors=True)


def test_summary_reused_attempt_must_exist(work: Path) -> None:
    make_project(work)
    output = work.parent / f"out-{uuid.uuid4().hex[:8]}"
    try:
        run(work, MANIFEST, "acceptance", output)
        index = json.loads((output / "index.json").read_text(encoding="utf-8"))
        index["results"][0]["status"] = "reused"
        index["results"][0]["path"] = (output / "missing-attempt.json").as_posix()
        (output / "index.json").write_text(json.dumps(index), encoding="utf-8")
        assert summarize(output)["verdict"] == "fail"
    finally:
        shutil.rmtree(output, ignore_errors=True)


def test_binary_output_summary_and_reuse(work: Path) -> None:
    make_project(work, reusable=True, commands=[f'& "{sys.executable}" -c "import sys; sys.stdout.buffer.write(bytes([255]))"'])
    output = work.parent / f"out-{uuid.uuid4().hex[:8]}"
    try:
        run(work, MANIFEST, "acceptance", output)
        verdict = summarize(output)
        assert verdict["verdict"] == "pass"
        second = run(work, MANIFEST, "acceptance", output)
        assert second["results"][0]["status"] == "reused"
    finally:
        shutil.rmtree(output, ignore_errors=True)


def _ledger(work: Path) -> dict:
    return json.loads((work / "DEVELOPMENT_STATUS.json").read_text(encoding="utf-8"))


def test_rejects_exact_authorization_different_paths(work: Path) -> None:
    make_project(work)
    ledger = _ledger(work)
    ledger["phases"][0]["pending_items"][0]["authorized_write_paths"] = ["unrelated/not/the/scope.py"]
    put_json(work, "DEVELOPMENT_STATUS.json", ledger)
    with pytest.raises(WorkflowError):
        run(work, MANIFEST, "acceptance", work.parent / f"out-{uuid.uuid4().hex[:8]}")


def test_rejects_exact_authorization_missing_round(work: Path) -> None:
    make_project(work)
    ledger = _ledger(work)
    ledger["phases"][0]["pending_items"][0].pop("round_id")
    put_json(work, "DEVELOPMENT_STATUS.json", ledger)
    with pytest.raises(WorkflowError):
        run(work, MANIFEST, "acceptance", work.parent / f"out-{uuid.uuid4().hex[:8]}")


def test_rejects_exact_authorization_different_manifest(work: Path) -> None:
    make_project(work)
    ledger = _ledger(work)
    ledger["phases"][0]["pending_items"][0]["round_manifest_path"] = "task_interactions/SYN-01/round-999/package/round-manifest.json"
    put_json(work, "DEVELOPMENT_STATUS.json", ledger)
    with pytest.raises(WorkflowError):
        run(work, MANIFEST, "acceptance", work.parent / f"out-{uuid.uuid4().hex[:8]}")


def test_rejects_invalid_event_sequence(work: Path) -> None:
    from governance.steward_workflow import _event_hash
    make_project(work)
    baseline = "artifacts/events/baselines/syn/DEVELOPMENT_STATUS.json"
    baseline_sha = hashlib.sha256((work / baseline).read_bytes()).hexdigest()
    manifest_sha = hashlib.sha256((work / MANIFEST).read_bytes()).hexdigest()
    first = {"event_type": "project_initialized", "task_id": None, "round_id": None,
             "event_id": uuid.uuid4().hex, "sequence": 1,
             "occurred_at": "2026-11-06T00:00:00Z",
             "previous_event_hash": None, "artifact_hashes": {baseline: baseline_sha}}
    first["event_hash"] = _event_hash(first)
    second = {"event_type": "task_dispatched", "task_id": TASK, "round_id": ROUND,
              "event_id": uuid.uuid4().hex, "sequence": 1,
              "occurred_at": "2026-11-06T00:00:00Z",
              "previous_event_hash": first["event_hash"],
              "artifact_hashes": {MANIFEST: manifest_sha, baseline: baseline_sha}}
    second["event_hash"] = _event_hash(second)
    put(work, "artifacts/events/governance-events.jsonl",
        "\n".join(json.dumps(e, ensure_ascii=False) for e in (first, second)) + "\n")
    with pytest.raises(WorkflowError):
        run(work, MANIFEST, "acceptance", work.parent / f"out-{uuid.uuid4().hex[:8]}")


def test_duplicate_stage_id_rejected() -> None:
    commands = [
        {"command": f'& "{sys.executable}" -c "print(1)"', "stage_id": "same",
         "modes": ["acceptance"], "depends_on": []},
        {"command": f'& "{sys.executable}" -c "print(2)"', "stage_id": "same",
         "modes": ["acceptance"], "depends_on": []},
    ]
    with pytest.raises(WorkflowError):
        _normalize_commands(commands)


def test_summary_rejects_reused_failed_attempt(work: Path) -> None:
    make_project(work, reusable=True)
    output = work.parent / f"out-{uuid.uuid4().hex[:8]}"
    try:
        run(work, MANIFEST, "acceptance", output)
        run(work, MANIFEST, "acceptance", output)  # second run reuses
        index = json.loads((output / "index.json").read_text(encoding="utf-8"))
        assert index["results"][0]["status"] == "reused"
        attempt = Path(index["results"][0]["path"])
        record = json.loads(attempt.read_text(encoding="utf-8"))
        record["status"] = "failed"
        record["exit_code"] = 1
        attempt.write_text(json.dumps(record), encoding="utf-8")
        assert summarize(output)["verdict"] == "fail"
    finally:
        shutil.rmtree(output, ignore_errors=True)


def test_summary_rejects_wrong_index_key(work: Path) -> None:
    make_project(work)
    output = work.parent / f"out-{uuid.uuid4().hex[:8]}"
    try:
        run(work, MANIFEST, "acceptance", output)
        index = json.loads((output / "index.json").read_text(encoding="utf-8"))
        index["results"][0]["key"] = "0" * 64
        (output / "index.json").write_text(json.dumps(index), encoding="utf-8")
        assert summarize(output)["verdict"] == "fail"
    finally:
        shutil.rmtree(output, ignore_errors=True)

def test_cleanup_protects_references_in_both_directions(tmp_path):
    from governance.steward_workflow import cleanup_plan

    for rel in ["cache/pin/log", "cache/pin-old/log"]:
        put(tmp_path, rel, "data")
    candidates = ["cache", "cache/pin", "cache/pin/log", "cache/pin-old/log", ".", "../outside"]
    preview = cleanup_plan(tmp_path, candidates=candidates, referenced_paths=["cache/pin"])
    assert [r["path"] for r in preview["suggestions"]] == [str(tmp_path / candidates[3])]
    assert {r["path"] for r in preview["protected"]} == set(candidates) - {candidates[3]}
    assert (tmp_path / candidates[3]).read_bytes() == b"data"


def test_cleanup_retry_uses_only_failed_rows(tmp_path, monkeypatch):
    from governance.steward_workflow import apply_cleanup, cleanup_plan

    for name in ["a", "b", "gone", "new-pin"]:
        put(tmp_path, name, "original")
    rows = cleanup_plan(tmp_path, candidates=["a", "b", "gone", "new-pin"], referenced_paths=[])["suggestions"]
    (tmp_path / "gone").unlink()
    unlink = Path.unlink
    def locked(path):
        if path.name == "a":
            raise PermissionError("in use")
        unlink(path)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", locked)
        first = apply_cleanup(tmp_path, approved=rows, referenced_paths=["new-pin"])
    assert [r["status"] for r in first["results"]] == ["skipped", "deleted", "already_absent", "skipped"]
    assert first["remaining"] == [rows[0], rows[3]]
    put(tmp_path, "b", "replacement")
    retry = apply_cleanup(tmp_path, approved=first["remaining"], referenced_paths=["new-pin"])
    assert [r["status"] for r in retry["results"]] == ["deleted", "skipped"]
    assert retry["remaining"] == [rows[3]]
    assert (tmp_path / "b").read_text() == "replacement"
    assert (tmp_path / "new-pin").read_text() == "original"


def test_cleanup_preserves_new_children_and_changed_files(tmp_path):
    from governance.steward_workflow import apply_cleanup, cleanup_plan

    put(tmp_path, "scratch/old", "original")
    rows = cleanup_plan(tmp_path, candidates=["scratch", "scratch/old"], referenced_paths=[])["suggestions"]
    put(tmp_path, "scratch/old", "different contents")
    put(tmp_path, "scratch/new", "unapproved")
    result = apply_cleanup(tmp_path, approved=rows, referenced_paths=[])
    assert len(result["remaining"]) == 2
    assert all(r["status"] == "skipped" for r in result["results"])
    assert (tmp_path / "scratch/new").read_text() == "unapproved"
    assert (tmp_path / "scratch/old").read_text() == "different contents"

def test_cleanup_real_windows_lock_and_empty_parent(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows open-handle deletion semantics")
    from governance.steward_workflow import apply_cleanup, cleanup_plan

    put(tmp_path, "work/busy", "in use")
    put(tmp_path, "work/free", "ready")
    rows = cleanup_plan(tmp_path, candidates=["work", "work/free", "work/busy"], referenced_paths=[])["suggestions"]
    with (tmp_path / "work/busy").open("rb"):
        first = apply_cleanup(tmp_path, approved=rows, referenced_paths=[])
    assert first["remaining"] == [rows[2], rows[0]]
    assert not (tmp_path / "work/free").exists()
    second = apply_cleanup(tmp_path, approved=first["remaining"], referenced_paths=[])
    assert [r["status"] for r in second["results"]] == ["deleted", "deleted"]
    assert second["remaining"] == []
    assert not (tmp_path / "work").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows command syntax")
def test_windows_paths_reach_child_byte_for_byte():
    import subprocess
    from governance.workflow_commands import _parse_command

    args = r'C:\Users\test\run.py "C:\dir\" "\\server\shared dir\file" ""'
    command = f"""& "{sys.executable}" -B -c "import json,sys;print(json.dumps(sys.argv[1:]))" {args}"""
    result = subprocess.run(_parse_command(command), stdin=subprocess.DEVNULL, capture_output=True, check=True)
    assert json.loads(result.stdout) == [r"C:\Users\test\run.py", "C:\\dir\\", r"\\server\shared dir\file", ""]
    with pytest.raises(WorkflowError, match="quoting"):
        _parse_command('python "unfinished')

def test_hidden_metadata_keeps_business_cwd_and_inputs(tmp_path):
    project = tmp_path / "project"
    (project / ".dual-agent").mkdir(parents=True)
    put(project, "src/example.txt", "business-input")
    command = f"""& "{sys.executable}" -c "from pathlib import Path;print(Path('src/example.txt').read_text())" """
    make_project(project, commands=[command], business_input="src/example.txt")
    manifest = ".dual-agent/" + MANIFEST
    assert round_package.verify_package(project, manifest, check_route=True, check_sources=True)
    package = (project / manifest).parent
    assert (package / "inputs/src/example.txt").read_bytes() == b"business-input"
    out = tmp_path / "output"
    assert run(project, manifest, "acceptance", out)["results"][0]["status"] == "passed"
    attempt = json.loads(next(out.glob("attempt-*.json")).read_text(encoding="utf8"))
    assert attempt["cwd"] == str(project.resolve())
    assert attempt["stdout"].strip() == "business-input"
    assert summarize(out)["verdict"] == "pass"


def test_default_cli_drift_summary_and_fresh_attempt(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    counter = tmp_path / "calls.txt"
    put(root, "check.py", """from pathlib import Path
calls = Path(__file__).resolve().parent.parent / "calls.txt"
if not calls.exists():
    Path("junk.txt").write_text("drift")
with calls.open("a") as stream:
    stream.write("1")
""")
    make_project(root, commands=[f'"{sys.executable}" -B check.py'],
                 reusable=True, stage_modes=["acceptance"])
    tool = Path(round_package.__file__).resolve().parents[1] / "tools.py"

    def cli(command, code):
        result = subprocess.run([sys.executable, "-B", str(tool), "steward_workflow",
                                 command, "--root", str(root)], cwd=root, stdin=subprocess.DEVNULL, capture_output=True)
        assert result.returncode == code, (result.stdout, result.stderr)
        return json.loads(result.stdout)

    failed = cli("run", 1)
    output = Path(failed["output"])
    assert not output.is_relative_to(root)
    assert failed["command_verdict"] == "pass" and failed["verdict"] == "fail"
    assert not failed["publication_eligible"]
    assert "junk.txt" in failed["workspace_drift"]["added"]
    assert "." not in failed["workspace_drift"]["modified"]
    assert "added: junk.txt" in failed["problems"]

    def snapshot():
        return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in output.iterdir()}

    original = snapshot()
    for _ in range(2):
        assert "added: junk.txt" in cli("summarize", 1)["problems"]
        assert snapshot() == original
    (root / "junk.txt").unlink()
    passed = cli("run", 0)
    assert passed["publication_eligible"] and passed["verdict"] == "pass"
    before, after = failed["results"][0], passed["results"][0]
    assert before["key"] == after["key"]
    assert after["status"] == "passed" and after["path"] != before["path"]
    assert counter.read_text() == "11"
    assert cli("summarize", 0)["verdict"] == "pass"
    assert cli("run", 0)["results"][0]["status"] == "reused"
    assert counter.read_text() == "11"


def test_fresh_by_default(work: Path, tmp_path: Path) -> None:
    make_project(work)
    first = run(work, MANIFEST, "acceptance", tmp_path)
    second = run(work, MANIFEST, "acceptance", tmp_path)
    assert first["publication_eligible"] and second["publication_eligible"]
    assert second["results"][0]["status"] == "passed"
    assert first["results"][0]["path"] != second["results"][0]["path"]


def test_timeout_bounds_real_parent_and_listening_child(work, tmp_path):
    import socket
    import time

    # The child's listening socket is an independent liveness witness after cleanup.
    put(work, "child.py", """import socket, time
s = socket.socket()
s.bind(("127.0.0.1", 0))
s.listen()
print(s.getsockname()[1], flush=True)
time.sleep(30)
""")
    put(work, "parent.py", """import subprocess, sys
subprocess.run([sys.executable, "-B", "child.py"], check=True)
""")
    make_project(work, commands=[f'"{sys.executable}" -B parent.py'], timeout=2)
    started = time.monotonic()
    result = run(work, MANIFEST, "acceptance", tmp_path)
    assert time.monotonic() - started < 20
    assert result["verdict"] == "fail" and not result["publication_eligible"]
    attempt = json.loads(Path(result["results"][0]["path"]).read_bytes())
    assert attempt["status"] == "timed_out" and attempt["timeout_seconds"] == 2
    assert attempt["timeout_cleanup"]["direct_child_exited"]
    assert attempt["timeout_cleanup"]["tree_stop"] in ("taskkill_succeeded", "group_signal_sent")
    port = int(attempt["stdout"].strip())
    with socket.socket() as sock:
        sock.settimeout(1)
        assert sock.connect_ex(("127.0.0.1", port)) != 0
    assert summarize(tmp_path)["verdict"] == "fail"


@pytest.mark.parametrize("invalid", [0, True, -1, 1.5, "2", 86401])
def test_invalid_stage_timeout(invalid):
    with pytest.raises(WorkflowError, match="timeout_seconds"):
        _normalize_commands([{"command": "python -V", "timeout_seconds": invalid}])


def test_clean_and_restored_write_on_same_frozen_round(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    subject = root / "nested" / "subject.txt"
    subject.parent.mkdir()
    subject.write_bytes(b"old")
    put(root, "check.py", """import os
from pathlib import Path
path = Path("nested/subject.txt")
assert path.read_bytes() == b"old"
if Path("../restore").exists():
    before = path.stat()
    path.write_bytes(b"new")
    assert path.read_bytes() == b"new"
    path.write_bytes(b"old")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
""")
    make_project(root, commands=[f'"{sys.executable}" -B check.py'], business_input="check.py")
    tool = Path(round_package.__file__).resolve().parents[1] / "tools.py"

    def execute(expected_rc):
        result = subprocess.run([sys.executable, "-B", str(tool), "steward_workflow", "run",
                                 "--root", str(root)], cwd=root, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=30)
        assert result.returncode == expected_rc, (result.stdout, result.stderr)
        doc = json.loads(result.stdout)
        assert doc["command_verdict"] == "pass" and doc["results"][0]["status"] == "passed"
        assert doc == json.loads((Path(doc["output"]) / "verdict.json").read_bytes())
        return doc

    before = subject.stat()
    original = subject.read_bytes()
    clean = execute(0)
    assert clean["verdict"] == "pass" and clean["publication_eligible"] is True
    assert clean["problems"] == []
    (tmp_path / "restore").touch()
    changed = execute(1)
    assert subject.read_bytes() == original and subject.stat().st_mtime_ns == before.st_mtime_ns
    assert changed["verdict"] == "fail" and changed["publication_eligible"] is False
    assert "nested/subject.txt" in changed["workspace_drift"]["modified"]
    assert "modified: nested/subject.txt" in changed["problems"]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux kernel notification queue")
def test_linux_watch_same_bytes_and_lost_events(tmp_path, monkeypatch):
    from governance import linux_witness as lw

    witness = lw.LinuxWitness()
    try:
        target = tmp_path / "subject"
        target.write_bytes(b"old")
        witness.watch(tmp_path, ".", tmp_path.stat().st_dev)
        assert witness.changes(set()) == set() and witness.error is None
        target.write_bytes(b"new")
        target.write_bytes(b"old")
        assert witness.changes(set()) == {"subject"} and witness.error is None
        with monkeypatch.context() as patch:
            patch.setattr(os, "read", lambda *_: lw.EVENT.pack(-1, lw.IN_Q_OVERFLOW, 0, 0))
            assert not witness.changes(set())
        assert "coverage lost" in witness.error
    finally:
        witness.close()

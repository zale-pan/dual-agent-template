"""Exercise the installed draft command, including its exact write boundary."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from init_project import initialize


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    initialize(root, "Trial", False)
    return root


def call(root, *args):
    return subprocess.run([sys.executable, "-B", str(root / ".dual-agent/tools.py"), "new-round", *args],
                          cwd=root.parent, capture_output=True, stdin=subprocess.DEVNULL)


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() if p.is_file() else None
            for p in root.rglob("*")}


@pytest.mark.parametrize("structured", [False, True])
def test_draft_exact_delta_and_repeat_preserves_edits(project, structured):
    # Also protect an existing ledger, not just the default case of a missing one.
    if structured:
        ledger = project / ".dual-agent/artifacts/events/governance-events.jsonl"
        ledger.write_bytes(b"existing history\n")
    before = snapshot(project)
    task = ".dual-agent/task_interactions/APP-42"
    base = task + "/round-001"
    sheet = base + "/轮次工作单.md"
    extra = ["--structured"] if structured else []
    result = call(project, "APP-42", "round-001", *extra)
    assert result.returncode == 0, result.stderr
    after = snapshot(project)
    expected = {task, base, sheet}
    if structured:
        expected.update({base + "/instruction.draft.md", base + "/acceptance-plan.draft.json"})
        plan = json.loads(after[base + "/acceptance-plan.draft.json"])
        assert (plan["task_id"], plan["round_id"], plan["status"]) == ("APP-42", "round-001", "draft")
        assert plan["authorized_write_paths"] == plan["required_commands"] == []
        assert plan["worksheet"] == sheet
        assert (project / plan["instruction_draft"]).is_file()
    assert set(after) - set(before) == expected
    assert {p: after[p] for p in before} == before
    assert after[sheet].decode().startswith("# APP-42 / round-001")
    assert sheet in after[sheet].decode()
    (project / sheet).write_bytes(b"user edits\r\n")
    edited = snapshot(project)
    assert call(project, "APP-42", "round-001", "--structured").returncode != 0
    assert snapshot(project) == edited


@pytest.mark.parametrize("task,round_id", [("../escape","round-001"),("CON","round-001"),("APP","../other")])
def test_invalid_identity_has_no_side_effects(project, task, round_id):
    before = snapshot(project)
    assert call(project, task, round_id).returncode != 0
    assert snapshot(project) == before


def test_concurrent_creation_has_one_winner(project):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call(project, "APP", "round-002"), range(2)))
    assert sorted(r.returncode for r in results) == [0, 1]
    assert (project / ".dual-agent/task_interactions/APP/round-002/轮次工作单.md").is_file()


def test_linked_task_directory_cannot_redirect_draft(project):
    outside = project.parent / "outside"
    outside.mkdir()
    link = project / ".dual-agent/task_interactions/APP"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    assert call(project, "APP", "round-001").returncode != 0
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
def test_installed_cli_outputs_utf8_with_legacy_stream_encoding(project, encoding):
    root = project.with_name("项目")
    project.rename(root)
    env = dict(os.environ, PYTHONIOENCODING=encoding, PYTHONUTF8="0")
    args = [sys.executable, "-B", str(root / ".dual-agent/tools.py"),
            "new-round", "APP", "round-001"]
    result = subprocess.run(args, capture_output=True, env=env, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    assert "轮次工作单.md" in result.stdout.decode("utf-8")
    before = snapshot(root)
    repeated = subprocess.run(args, capture_output=True, env=env, stdin=subprocess.DEVNULL)
    assert repeated.returncode == 1
    assert "preserved: .dual-agent/task_interactions/APP/round-001" in repeated.stderr.decode("utf-8")
    assert b"Traceback" not in repeated.stderr
    assert snapshot(root) == before

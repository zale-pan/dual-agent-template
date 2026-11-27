"""Positive and negative tests for the immutable round package."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.round_package import PackageError, build_package, verify_package
from governance.round_package import main as package_main

TASK = "PKG-T01"
ROUND = "round-001"
PACKAGE = f"task_interactions/{TASK}/{ROUND}/package"
MANIFEST = PACKAGE + "/round-manifest.json"
REPORT = f"task_interactions/{TASK}/{ROUND}/completion-report.yaml"
READ_SET = [f"tasks/active/{TASK}.en.md", f"tasks/active/{TASK}.task.json", f"task_interactions/{TASK}/{ROUND}/instruction.md"]


def put(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, list[str]]:
    sources = [
        "IMPLEMENTATION_AGENT_PROMPT.en.md", "AGENTS.md", "COLLABORATION_POLICY.json",
        "PROJECT_GOALS.json", "DEVELOPMENT_STATUS.json", "tasks/CURRENT_TASK.md",
        f"tasks/consultations/{TASK}.zh-CN.md", f"tasks/active/{TASK}.en.md",
        f"tasks/active/{TASK}.task.json", f"task_interactions/{TASK}/{ROUND}/instruction.md",
    ]
    for rel in sources:
        put(tmp_path, rel, "source\n")
    put(tmp_path, f"tasks/consultations/{TASK}.zh-CN.md", "# Consultation\n\n商讨状态：`agreed`。\n")
    put(tmp_path, "tasks/CURRENT_TASK.md", "---\n" + "\n".join([
        f"task_id: {TASK}", "execution_status: approved",
        f"consultation_ref: tasks/consultations/{TASK}.zh-CN.md",
        f"task_doc: tasks/active/{TASK}.en.md",
        f"task_manifest: tasks/active/{TASK}.task.json",
        f"instruction: task_interactions/{TASK}/{ROUND}/instruction.md",
        f"round_manifest: {MANIFEST}",
    ]) + "\n---\n")
    put(tmp_path, f"tasks/active/{TASK}.task.json", json.dumps({
        "task_id": TASK, "round_manifest_path": MANIFEST,
        "completion_report_path": REPORT, "allowed_write_paths": ["governance/new_tool.py", REPORT],
        "governed_input_paths": sources,
        "required_commands": ["python -m pytest -q"],
    }))
    return tmp_path, sources


def build(repo: tuple[Path, list[str]]) -> tuple[Path, list[str]]:
    root, sources = repo
    result = build_package(root, TASK, ROUND, sources, READ_SET, prepared_at="2026-09-23T08:00:00Z")
    assert result == root / MANIFEST
    return root, sources


def test_build_and_historical_verification_ignore_later_source_changes(repo: tuple[Path, list[str]]) -> None:
    root, sources = build(repo)
    assert verify_package(root, MANIFEST, check_route=True, check_sources=True)["inputs"] == len(sources)
    put(root, "PROJECT_GOALS.json", "new live state\n")
    assert verify_package(root, MANIFEST)["inputs"] == len(sources)
    with pytest.raises(PackageError, match="source differs"):
        verify_package(root, MANIFEST, check_sources=True)
    with pytest.raises(PackageError, match="already exists"):
        build(repo)


def test_tamper_missing_extra_and_route_drift_fail(repo: tuple[Path, list[str]]) -> None:
    root, _ = build(repo)
    snapshot = root / PACKAGE / "inputs" / "AGENTS.md"
    snapshot.write_text("tampered", encoding="utf-8")
    with pytest.raises(PackageError, match="hash/length"):
        verify_package(root, MANIFEST)
    snapshot.write_text("source\n", encoding="utf-8")
    snapshot.unlink()
    with pytest.raises(PackageError, match="missing regular file"):
        verify_package(root, MANIFEST)
    snapshot.write_text("source\n", encoding="utf-8")
    put(root, PACKAGE + "/inputs/unexpected.txt", "extra")
    with pytest.raises(PackageError, match="unexpected package member"):
        verify_package(root, MANIFEST)
    (root / PACKAGE / "inputs/unexpected.txt").unlink()
    put(root, "tasks/CURRENT_TASK.md", "---\ntask_id: OTHER\n---\n")
    with pytest.raises(PackageError, match="route disagrees"):
        verify_package(root, MANIFEST, check_route=True)


def test_path_escape_and_package_write_authorization_fail(repo: tuple[Path, list[str]]) -> None:
    root, sources = repo
    with pytest.raises(PackageError, match="invalid relative"):
        build_package(root, TASK, ROUND, sources + ["../outside"], READ_SET, prepared_at="2026-09-23T08:00:00Z")
    with pytest.raises(PackageError, match="protected input"):
        build_package(root, TASK, ROUND, sources + ["secrets/model.key"], READ_SET, prepared_at="2026-09-23T08:00:00Z")
    manifest_path = root / f"tasks/active/{TASK}.task.json"
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    doc["allowed_write_paths"].append(PACKAGE + "/inputs/AGENTS.md")
    manifest_path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(PackageError, match="package path"):
        build_package(root, TASK, ROUND, sources, READ_SET, prepared_at="2026-09-23T08:00:00Z")


def test_unagreed_consultation_is_rejected(repo: tuple[Path, list[str]]) -> None:
    root, sources = repo
    put(root, f"tasks/consultations/{TASK}.zh-CN.md", "商讨状态：`awaiting_decision`\n")
    with pytest.raises(PackageError, match="not agreed"):
        build_package(root, TASK, ROUND, sources, READ_SET, prepared_at="2026-09-23T08:00:00Z")


def test_undeclared_extra_input_is_rejected(repo: tuple[Path, list[str]]) -> None:
    root, sources = repo
    put(root, "docs/extra.md", "extra")
    with pytest.raises(PackageError, match="inventory differs"):
        build_package(root, TASK, ROUND, sources + ["docs/extra.md"], READ_SET,
                      prepared_at="2026-09-23T08:00:00Z")


def test_input_order_is_bound_to_manifest_for_deterministic_bytes(repo: tuple[Path, list[str]]) -> None:
    root, sources = repo
    with pytest.raises(PackageError, match="inventory differs"):
        build_package(root, TASK, ROUND, list(reversed(sources)), READ_SET,
                      prepared_at="2026-09-23T08:00:00Z")


def test_dispatch_binding_required_when_requested(repo: tuple[Path, list[str]]) -> None:
    root, _ = build(repo)
    put(root, "artifacts/events/governance-events.jsonl", "")
    with pytest.raises(PackageError, match="dispatch event"):
        verify_package(root, MANIFEST, require_dispatch=True)
    digest = verify_package(root, MANIFEST)["manifest_sha256"]
    put(root, "artifacts/events/governance-events.jsonl", json.dumps({
        "event_type": "task_dispatched", "task_id": TASK, "round_id": ROUND,
        "artifact_hashes": {MANIFEST: digest},
    }) + "\n")
    assert verify_package(root, MANIFEST, require_dispatch=True)["inputs"] == 10


def test_verify_cli_is_read_only(repo: tuple[Path, list[str]], capsys) -> None:
    root, _ = build(repo)
    before = (root / MANIFEST).read_bytes()
    assert package_main(["--root", str(root), "--verify", MANIFEST, "--check-route"]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "pass"
    assert (root / MANIFEST).read_bytes() == before


@pytest.mark.parametrize("old", [
    "task_interactions/OTHER/round-003/package/inputs/tasks/current.md",
    "TASK_INTERACTIONS/OTHER/ROUND-003/PACKAGE/round-manifest.json",
])
def test_prior_package_is_refused_before_staging(repo, old):
    root, sources = repo
    put(root, old, "historical")
    with pytest.raises(PackageError, match="round package cannot"):
        build_package(root, TASK, ROUND, sources + [old], READ_SET, prepared_at="fixture")
    assert (root / old).read_text() == "historical"
    assert not (root / PACKAGE).exists()
    assert not list((root / PACKAGE).parent.glob(".round-package-*"))


def test_verifier_rejects_imported_nested_package(repo):
    root, _ = build(repo)
    path = root / MANIFEST
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["inputs"][0]["source_path"] = "task_interactions/OLD/round-002/package/inputs/a"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(PackageError, match="round package cannot"):
        verify_package(root, MANIFEST)

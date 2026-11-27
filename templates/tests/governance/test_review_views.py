"""Positive and negative tests for the generated-review contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governance.review_views import ViewError, check_views, render_views, write_views, main as review_main


CONFIG = {
    "views": [
        {"output": "docs/GOVERNANCE_REVIEW.zh-CN.md", "sources": ["PROJECT_GOALS.json", "DEVELOPMENT_STATUS.json", "COMPONENT_REGISTRY.json"]},
        {"output": "TASK_CATALOG.md", "sources": ["DEVELOPMENT_STATUS.json"]},
        {"output": "TRACEABILITY_MATRIX.md", "sources": ["PROJECT_GOALS.json", "DEVELOPMENT_STATUS.json"]},
    ]
}


def put(root: Path, rel: str, value: object) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    put(tmp_path, "review-views.json", CONFIG)
    put(tmp_path, "PROJECT_GOALS.json", {"goals": [
        {"id": "G-01", "name_zh": "目标", "status": "planned", "acceptance_criteria": [
            {"id": "G-01-AC01", "status": "planned", "evidence": []}
        ], "limitations": ["非生产"]}
    ]})
    put(tmp_path, "DEVELOPMENT_STATUS.json", {"current_phase": "P0", "overall_status": "planning", "phases": [
        {"id": "P0", "name_zh": "准备", "status": "active", "progress": {"completed_items": 0, "total_items": 1, "phase_gate_passed": False},
         "blockers": ["待核对"], "completed_details": [], "pending_items": [
             {"id": "P0-T01", "summary": "检查", "dependencies": [], "execution_status": "draft", "consultation_status": "awaiting_discussion", "acceptance_refs": ["G-01-AC01"]}
         ]}
    ], "next_actions": [{"task_id": "P0-T01", "reason": "先商讨"}]})
    put(tmp_path, "COMPONENT_REGISTRY.json", {"components": [
        {"component_id": "checker", "owner": "steward", "availability": "planned", "mutation": {"mode": "forbidden"}, "risk_level": "medium", "dependencies": [], "rollback": {"strategy": "restore"}}
    ]})
    return tmp_path


def test_render_is_deterministic_and_check_is_read_only(repo: Path) -> None:
    first = render_views(repo)
    assert first == render_views(repo)
    assert b"sha256=" in first["TASK_CATALOG.md"]
    assert check_views(repo) == sorted(first) or set(check_views(repo)) == set(first)
    assert not (repo / "docs").exists()
    assert set(write_views(repo)) == set(first)
    assert check_views(repo) == []
    before = {name: (repo / name).read_bytes() for name in first}
    assert check_views(repo) == []
    assert before == {name: (repo / name).read_bytes() for name in first}
    assert write_views(repo) == []


def test_review_cli_check_is_read_only(repo: Path, capsys) -> None:
    assert review_main(["--root", str(repo), "--check", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["result"] == "stale"
    assert not (repo / "docs").exists()
    assert review_main(["--root", str(repo), "--write", "--json"]) == 0
    capsys.readouterr()
    before = (repo / "TASK_CATALOG.md").read_bytes()
    assert review_main(["--root", str(repo), "--check", "--json"]) == 0
    assert (repo / "TASK_CATALOG.md").read_bytes() == before


def test_stale_manual_edit_missing_and_json_change_are_rejected(repo: Path) -> None:
    write_views(repo)
    (repo / "TASK_CATALOG.md").write_text("stronger claim", encoding="utf-8")
    assert check_views(repo) == ["TASK_CATALOG.md"]
    write_views(repo)
    (repo / "TRACEABILITY_MATRIX.md").unlink()
    assert "TRACEABILITY_MATRIX.md" in check_views(repo)
    put(repo, "DEVELOPMENT_STATUS.json", {"current_phase": "P1", "overall_status": "changed", "phases": [], "next_actions": []})
    assert set(check_views(repo)) == set(CONFIG["views"][i]["output"] for i in range(3))


def test_output_escape_is_rejected(repo: Path) -> None:
    config = dict(CONFIG)
    config["views"] = [dict(item) for item in CONFIG["views"]]
    config["views"][0]["output"] = "../escape.md"
    put(repo, "review-views.json", config)
    with pytest.raises(ViewError):
        render_views(repo)


def test_malformed_fact_array_fails_closed(repo: Path) -> None:
    put(repo, "PROJECT_GOALS.json", {"goals": "not-an-array"})
    with pytest.raises(ViewError, match="goals must be an array"):
        render_views(repo)


def test_symlink_output_is_rejected_when_supported(repo: Path) -> None:
    try:
        (repo / "TASK_CATALOG.md").symlink_to(repo / "PROJECT_GOALS.json")
    except OSError:
        pytest.skip("this Windows account cannot create symbolic links")
    with pytest.raises(ViewError, match="symlink"):
        check_views(repo)

"""Integration and failure-boundary tests for the template's project initializer."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from init_project import BASE, MANUAL, initialize


def test_default_cli_starts_with_fresh_views_and_leaves_business_tests_alone(tmp_path):
    project = tmp_path / "project"
    tests = project / "tests"
    tests.mkdir(parents=True)
    business = tests / "test_app.py"
    business.write_bytes(b"# project-owned\n")
    name = 'A "quoted" project'
    command = [sys.executable, "-B", str(BASE / "init_project.py"), str(project), "--name", name]
    subprocess.run(command, check=True)
    assert {p.name for p in project.iterdir()} == {"AGENTS.md", ".dual-agent", "tests"}
    assert business.read_bytes() == b"# project-owned\n"
    assert list(tests.iterdir()) == [business]
    assert json.loads((project / ".dual-agent/PROJECT_GOALS.json").read_bytes())["project"] == name
    assert (project / ".dual-agent/IMPLEMENTATION_AGENT_PROMPT.en.md").read_bytes() == (
        BASE / "IMPLEMENTATION_AGENT_BOOTSTRAP_PROMPT.en.md").read_bytes()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    subprocess.run([sys.executable, "-B", ".dual-agent/tools.py", "review_views", "--check"],
                   cwd=project, env=env, check=True)
    assert initialize(project, name, False) == 0


def test_opt_in_and_manual_modes(tmp_path):
    manual = tmp_path / "manual"
    initialize(manual, "Demo", True)
    assert {p.name for p in manual.iterdir()} == set(MANUAL)
    target = tmp_path / "full"
    initialize(target, "Demo", False, with_governance_tests=True)
    suite = Path("tests/governance")
    assert sorted(p.name for p in (target / ".dual-agent" / suite).glob("test_*.py")) == sorted(
        p.name for p in (BASE / "templates" / suite).glob("test_*.py"))
    with pytest.raises(ValueError):
        initialize(manual, "Demo", True, with_governance_tests=True)


@pytest.mark.parametrize("name", [" ", "a\nb", "a\x00b"])
def test_invalid_name_creates_nothing(tmp_path, name):
    target = tmp_path / "project"
    with pytest.raises(ValueError):
        initialize(target, name, False)
    assert not target.exists()


def test_derived_view_conflict_precedes_all_writes(tmp_path):
    hidden = tmp_path / ".dual-agent"
    hidden.mkdir()
    existing = hidden / "TASK_CATALOG.md"
    existing.write_bytes(b"owned")
    with pytest.raises(ValueError, match="differs"):
        initialize(tmp_path, "Demo", False)
    assert list(tmp_path.iterdir()) == [hidden]
    assert list(hidden.iterdir()) == [existing]
    assert existing.read_bytes() == b"owned"


def test_write_failure_rolls_back_only_new_paths(tmp_path, monkeypatch):
    existing = tmp_path / "existing"
    existing.write_bytes(b"owned")
    original_open = Path.open
    def fail_on_last_view(path, mode="r", *args, **kwargs):
        if path.name == "TRACEABILITY_MATRIX.md" and mode == "xb":
            assert (tmp_path / ".dual-agent/governance").is_dir()
            raise OSError("injected write failure")
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail_on_last_view)
    with pytest.raises(OSError, match="injected"):
        initialize(tmp_path, "Demo", False)
    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_bytes() == b"owned"


def test_linked_target_ancestor_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "alias"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="linked"):
        initialize(link / "project", "Demo", True)
    assert not list(outside.iterdir())


def test_upgrade_updates_engine_and_preserves_project_data(tmp_path):
    project = tmp_path / "project"
    initialize(project, "Demo", False)
    tool = project / ".dual-agent" / "tools.py"
    missing = project / ".dual-agent" / "governance" / "daily_round.py"
    status = project / ".dual-agent" / "DEVELOPMENT_STATUS.json"
    agents = project / "AGENTS.md"
    doc = project / ".dual-agent" / "docs" / "STRUCTURED_ROUND.zh-CN.md"
    tool.write_text("# stale tools.py\n", encoding="utf-8")
    missing.unlink()
    status.write_text('{"project": "Demo", "custom": true}\n', encoding="utf-8")
    agents.write_text("# Custom project AGENTS\n", encoding="utf-8")
    doc.write_text("# Custom project structured round doc\n", encoding="utf-8")

    preview = initialize(project, "Demo", False, upgrade_preview=True)
    assert ".dual-agent/docs/STRUCTURED_ROUND.zh-CN.md" in preview["manual_comparison"]

    with pytest.raises(ValueError, match="plan-sha256"):
        initialize(project, "Demo", False, upgrade=True)

    result = initialize(project, "Demo", False, upgrade=True, plan_sha256=preview["plan_sha256"])
    assert ".dual-agent/tools.py" in result["updated"]
    assert ".dual-agent/governance/daily_round.py" in result["created"]
    assert ".dual-agent/DEVELOPMENT_STATUS.json" in result["manual_comparison"]
    assert "AGENTS.md" in result["manual_comparison"]
    assert ".dual-agent/docs/STRUCTURED_ROUND.zh-CN.md" in result["manual_comparison"]
    assert result["unchanged"] > 0
    assert tool.read_text(encoding="utf-8") != "# stale tools.py\n"
    assert missing.is_file()
    assert status.read_text(encoding="utf-8") == '{"project": "Demo", "custom": true}\n'
    assert agents.read_text(encoding="utf-8") == "# Custom project AGENTS\n"
    assert doc.read_text(encoding="utf-8") == "# Custom project structured round doc\n"


def test_upgrade_rejects_missing_or_manual_project(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="existing .dual-agent"):
        initialize(empty, "Demo", False, upgrade=True)
    with pytest.raises(ValueError, match="structured"):
        initialize(empty, "Demo", True, upgrade=True)


def test_upgrade_rejects_stale_plan_sha256_after_preview(tmp_path, monkeypatch):
    import init_project as init_mod

    project = tmp_path / "project"
    initialize(project, "Demo", False)
    missing = project / ".dual-agent" / "governance" / "daily_round.py"
    missing.unlink()
    preview = initialize(project, "Demo", False, upgrade_preview=True)
    tool = project / ".dual-agent" / "tools.py"
    tool.write_text("# modified after preview\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed since preview"):
        initialize(project, "Demo", False, upgrade=True, plan_sha256=preview["plan_sha256"])
    assert tool.read_text(encoding="utf-8") == "# modified after preview\n"
    assert not missing.exists()

    # Also reject if template-side pending bytes change after preview, before any write.
    preview2 = initialize(project, "Demo", False, upgrade_preview=True)
    orig_plan = init_mod._plan_upgrade
    def mutated_plan(tgt, contents):
        contents = dict(contents)
        contents[".dual-agent/tools.py"] = b"# template side changed\n"
        return orig_plan(tgt, contents)
    monkeypatch.setattr(init_mod, "_plan_upgrade", mutated_plan)
    with pytest.raises(ValueError, match="changed since preview"):
        initialize(project, "Demo", False, upgrade=True, plan_sha256=preview2["plan_sha256"])
    assert tool.read_text(encoding="utf-8") == "# modified after preview\n"
    assert not missing.exists()

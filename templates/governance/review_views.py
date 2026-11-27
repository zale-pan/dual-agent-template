"""Deterministic JSON-to-Markdown governance review views.

``--check`` only reads; ``--write`` is a separate, explicit Steward operation.
This module is a template component; target-project integrations require independent verification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path


SOURCE_NAMES = {"PROJECT_GOALS.json", "DEVELOPMENT_STATUS.json", "COMPONENT_REGISTRY.json"}
VIEW_NAMES = {
    "docs/GOVERNANCE_REVIEW.zh-CN.md",
    "TASK_CATALOG.md",
    "TRACEABILITY_MATRIX.md",
}


class ViewError(ValueError):
    """Invalid view input, mapping, or destination."""


def _path(root: Path, rel: str, *, allow_missing: bool = False) -> Path:
    if not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/"):
        raise ViewError(f"invalid relative path: {rel!r}")
    parts = rel.split("/")
    if any(part in ("", ".", "..") or ":" in part for part in parts):
        raise ViewError(f"invalid relative path: {rel!r}")
    target = root
    for part in parts:
        target = target / part
        if target.is_symlink():
            raise ViewError(f"symlink path is forbidden: {rel}")
        if target.exists() and getattr(os.lstat(target), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ViewError(f"reparse path is forbidden: {rel}")
    if not allow_missing and not target.is_file():
        raise ViewError(f"missing file: {rel}")
    if target.exists() and not target.is_file():
        raise ViewError(f"not a regular file: {rel}")
    return target


def _load_json(root: Path, rel: str) -> tuple[dict, str]:
    raw = _path(root, rel).read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ViewError(f"invalid JSON in {rel}: {exc}") from exc
    if not isinstance(value, dict):
        raise ViewError(f"JSON root must be an object: {rel}")
    required_arrays = {
        "PROJECT_GOALS.json": ("goals",),
        "DEVELOPMENT_STATUS.json": ("phases", "next_actions"),
        "COMPONENT_REGISTRY.json": ("components",),
    }
    for field in required_arrays.get(rel, ()):
        if not isinstance(value.get(field), list):
            raise ViewError(f"{rel}.{field} must be an array")
    return value, hashlib.sha256(raw).hexdigest()


def _cell(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value) if value else "—"
    text = str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")
    return text or "—"


def _table(headers: list[str], rows: list[list[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def _tasks(status: dict) -> list[tuple[str, dict]]:
    result = []
    for phase in status.get("phases", []):
        if not isinstance(phase, dict):
            continue
        phase_id = str(phase.get("id", "?"))
        for field in ("completed_details", "pending_items"):
            for task in phase.get(field, []):
                if isinstance(task, dict):
                    result.append((phase_id, task))
    return result


def _render_overview(sources: dict[str, dict]) -> str:
    goals = sources["PROJECT_GOALS.json"]
    status = sources["DEVELOPMENT_STATUS.json"]
    registry = sources["COMPONENT_REGISTRY.json"]
    lines = ["# 治理与规划审阅", "", "> 本页仅为 JSON 事实源派生视图；不构成编码授权或独立验证证据。", ""]
    lines += ["## 目标与验收", "", _table(
        ["Goal", "名称", "状态", "AC 数", "限制"],
        [[g.get("id"), g.get("name_zh", g.get("name_en")), g.get("status"), len(g.get("acceptance_criteria", [])), g.get("limitations", [])]
         for g in goals.get("goals", []) if isinstance(g, dict)],
    ).rstrip(), ""]
    lines += ["## 阶段、状态与下一动作", "", _table(
        ["阶段", "名称", "状态", "完成/总数", "门禁"],
        [[p.get("id"), p.get("name_zh", p.get("name_en")), p.get("status"),
          f"{p.get('progress', {}).get('completed_items', 0)}/{p.get('progress', {}).get('total_items', 0)}",
          p.get("progress", {}).get("phase_gate_passed")]
         for p in status.get("phases", []) if isinstance(p, dict)],
    ).rstrip(), ""]
    next_actions = status.get("next_actions", [])
    lines.append("下一动作：" + (", ".join(f"{a.get('task_id')}: {a.get('reason', '')}" for a in next_actions if isinstance(a, dict)) or "无"))
    lines += ["", "## 组件边界", "", _table(
        ["组件", "Owner", "可用性", "修改模式", "风险", "依赖", "回滚"],
        [[c.get("component_id"), c.get("owner"), c.get("availability"),
          c.get("mutation", {}).get("mode"), c.get("risk_level"), c.get("dependencies", []),
          c.get("rollback", {}).get("strategy")]
         for c in registry.get("components", []) if isinstance(c, dict)],
    ).rstrip(), ""]
    lines += ["## 事实、假设、风险、缺口与成熟度", ""]
    facts = [
        ("事实", f"当前阶段 {status.get('current_phase', '—')}；整体状态 {status.get('overall_status', '—')}。"),
        ("假设", goals.get("assumptions", ["未在 JSON 中记录；不从叙述推断。"])),
        ("风险", goals.get("risks", []) + status.get("risks", []) if isinstance(goals.get("risks", []), list) and isinstance(status.get("risks", []), list) else []),
        ("缺口", [b for p in status.get("phases", []) if isinstance(p, dict) for b in p.get("blockers", [])]),
        ("成熟度", "各 Goal/AC 与任务的状态见下表；文件存在不等于 verified。"),
    ]
    lines.append(_table(["类别", "JSON 记录"], [[name, value] for name, value in facts]).rstrip())
    lines.append("")
    return "\n".join(lines)


def _render_catalog(sources: dict[str, dict]) -> str:
    status = sources["DEVELOPMENT_STATUS.json"]
    rows = []
    for phase_id, task in _tasks(status):
        rows.append([phase_id, task.get("task_id", task.get("id")), task.get("summary", "—"),
                     task.get("dependencies", []), task.get("execution_status"), task.get("consultation_status")])
    return "# 全量任务清单\n\n> `DEVELOPMENT_STATUS.json` 的派生视图。\n\n" + _table(
        ["Phase", "Task", "结果", "依赖", "执行状态", "商讨状态"], rows)


def _render_trace(sources: dict[str, dict]) -> str:
    goals = sources["PROJECT_GOALS.json"]
    status = sources["DEVELOPMENT_STATUS.json"]
    tasks = _tasks(status)
    rows = []
    for goal in goals.get("goals", []):
        if not isinstance(goal, dict):
            continue
        for ac in goal.get("acceptance_criteria", []):
            if not isinstance(ac, dict):
                continue
            ac_id = ac.get("id")
            refs = [t.get("task_id", t.get("id")) for _, t in tasks if ac_id in t.get("acceptance_refs", [])]
            rows.append([goal.get("id"), ac_id, refs, ac.get("status"), ac.get("evidence", [])])
    return "# 目标—任务—证据追踪矩阵\n\n> Goal/AC 来自 `PROJECT_GOALS.json`，任务来自 `DEVELOPMENT_STATUS.json`。\n\n" + _table(
        ["Goal", "AC", "任务", "状态", "证据"], rows)


RENDERERS = {
    "docs/GOVERNANCE_REVIEW.zh-CN.md": _render_overview,
    "TASK_CATALOG.md": _render_catalog,
    "TRACEABILITY_MATRIX.md": _render_trace,
}


def render_views(root: Path, config_rel: str = "review-views.json") -> dict[str, bytes]:
    root = root.resolve()
    config, _ = _load_json(root, config_rel)
    views = config.get("views")
    if not isinstance(views, list) or len(views) != len(VIEW_NAMES):
        raise ViewError("view mapping must list exactly the three configured outputs")
    seen = set()
    rendered: dict[str, bytes] = {}
    cache: dict[str, tuple[dict, str]] = {}
    for entry in views:
        if not isinstance(entry, dict) or entry.get("output") not in VIEW_NAMES:
            raise ViewError("unexpected output path")
        output = entry["output"]
        key = output.casefold()
        if key in seen:
            raise ViewError("duplicate output path")
        seen.add(key)
        source_paths = entry.get("sources")
        if not isinstance(source_paths, list) or not source_paths or len(set(source_paths)) != len(source_paths):
            raise ViewError(f"invalid source list for {output}")
        if not set(source_paths) <= SOURCE_NAMES:
            raise ViewError(f"unexpected source for {output}")
        sources = {}
        digests = {}
        for source in source_paths:
            if source not in cache:
                cache[source] = _load_json(root, source)
            sources[source], digests[source] = cache[source]
        required = {
            "docs/GOVERNANCE_REVIEW.zh-CN.md": SOURCE_NAMES,
            "TASK_CATALOG.md": {"DEVELOPMENT_STATUS.json"},
            "TRACEABILITY_MATRIX.md": {"PROJECT_GOALS.json", "DEVELOPMENT_STATUS.json"},
        }[output]
        if set(source_paths) != required:
            raise ViewError(f"wrong sources for {output}")
        _path(root, output, allow_missing=True)
        marker = "<!-- GENERATED by governance.review_views; sources: " + ", ".join(
            f"{name} sha256={digests[name]}" for name in sorted(digests)) + " -->\n"
        body = RENDERERS[output](sources)
        rendered[output] = (marker + body.rstrip("\n") + "\n").encode("utf-8")
    if seen != {name.casefold() for name in VIEW_NAMES}:
        raise ViewError("missing review output")
    for source, (_, digest) in cache.items():
        if hashlib.sha256(_path(root, source).read_bytes()).hexdigest() != digest:
            raise ViewError(f"source changed during rendering: {source}")
    return rendered


def check_views(root: Path, config_rel: str = "review-views.json") -> list[str]:
    rendered = render_views(root, config_rel)
    mismatches = []
    for rel, expected in rendered.items():
        path = _path(root.resolve(), rel, allow_missing=True)
        if not path.is_file() or path.read_bytes() != expected:
            mismatches.append(rel)
    return mismatches


def write_views(root: Path, config_rel: str = "review-views.json") -> list[str]:
    rendered = render_views(root, config_rel)
    changed = []
    for rel in rendered:
        path = _path(root.resolve(), rel, allow_missing=True)
        temporary = path.with_name(path.name + ".view-tmp")
        if temporary.exists() or temporary.is_symlink():
            raise ViewError(f"temporary output already exists: {temporary}")
    for rel, data in rendered.items():
        path = _path(root.resolve(), rel, allow_missing=True)
        if path.is_file() and path.read_bytes() == data:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".view-tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)
        changed.append(rel)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--config", default="review-views.json")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        changed = check_views(args.root, args.config) if args.check else write_views(args.root, args.config)
        result = "pass" if not changed or args.write else "stale"
        payload = {"tool": "review_views", "mode": "check" if args.check else "write", "result": result, "paths": changed}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(payload)
        return 0 if result == "pass" else 1
    except (OSError, ViewError) as exc:
        print(json.dumps({"tool": "review_views", "result": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

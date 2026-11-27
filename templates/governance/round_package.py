"""Build and verify immutable, path-preserving round input packages.

Verification is read-only; building is an explicit Task-Steward operation and never
overwrites an existing package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from governance.layout import metadata_path
from governance.read_session import ReadSession, SessionError

MINIMUM_ROOT_INPUTS = {
    "IMPLEMENTATION_AGENT_PROMPT.en.md",
    "AGENTS.md",
    "COLLABORATION_POLICY.json",
    "PROJECT_GOALS.json",
    "DEVELOPMENT_STATUS.json",
    "tasks/CURRENT_TASK.md",
}
READ_SET_KEYS = ("task_doc", "task_manifest", "instruction")
BLOCKED_SEGMENTS = {".git", "secrets", "credentials", "private_keys"}


class PackageError(ValueError):
    """Invalid package, path, hash, or route."""


def _exact(rel: str) -> str:
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        raise PackageError(f"invalid relative path: {rel!r}")
    parts = rel.split("/")
    if any(part in ("", ".", "..") or ":" in part or part.endswith((" ", "."))
           or any(ord(char) < 32 for char in part) for part in parts):
        raise PackageError(f"invalid relative path: {rel!r}")
    if any(part.casefold() in BLOCKED_SEGMENTS for part in parts) or rel.casefold().endswith((".pem", ".key", ".p12", ".env")):
        raise PackageError(f"protected input path is forbidden: {rel}")
    return rel


def _unique(paths: list[str]) -> None:
    normalized = [unicodedata.normalize("NFC", path).casefold() for path in paths]
    if len(normalized) != len(set(normalized)):
        raise PackageError("duplicate or case/Unicode-colliding paths")


def _file(root: Path, rel: str, *, required: bool = True) -> Path:
    _exact(rel)
    path = root
    for part in rel.split("/"):
        path = path / part
        if path.is_symlink():
            raise PackageError(f"symlink/reparse path is forbidden: {rel}")
        if path.exists() and getattr(os.lstat(path), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise PackageError(f"symlink/reparse path is forbidden: {rel}")
    if required and not path.is_file():
        raise PackageError(f"missing regular file: {rel}")
    if path.exists() and not path.is_file():
        raise PackageError(f"not a regular file: {rel}")
    return path


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _front_from_text(text: str) -> dict[str, str]:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise PackageError("CURRENT_TASK front matter is missing")
    block = text[4:].split("\n---\n", 1)[0]
    front = {}
    for line in block.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            front[key.strip()] = value.strip()
    return front


def _front(root: Path) -> dict[str, str]:
    text = _file(root, metadata_path(root, "tasks/CURRENT_TASK.md")).read_text(encoding="utf-8")
    return _front_from_text(text)


def _ids(task_id: str, round_id: str) -> None:
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", task_id):
        raise PackageError("invalid task ID")
    if not isinstance(round_id, str) or len(round_id) != 9 or not round_id.startswith("round-") or not round_id[6:].isdigit():
        raise PackageError("invalid round ID")


def _location(root: Path, task_id: str, round_id: str) -> str:
    _ids(task_id, round_id)
    return metadata_path(root, f"task_interactions/{task_id}/{round_id}/package")


def _required_paths(root: Path, front: dict[str, str], task_id: str, round_id: str) -> set[str]:
    required = {rel if rel == "AGENTS.md" else metadata_path(root, rel) for rel in MINIMUM_ROOT_INPUTS}
    for key in ("consultation_ref", "task_doc", "task_manifest", "instruction"):
        value = front.get(key)
        if not value or value == "NONE":
            raise PackageError(f"CURRENT_TASK lacks {key}")
        required.add(_exact(value))
    expected_instruction = metadata_path(root, f"task_interactions/{task_id}/{round_id}/instruction.md")
    if front["instruction"] != expected_instruction:
        raise PackageError("round instruction path disagrees with task and round")
    if front.get("task_id") != task_id or front.get("execution_status") != "approved":
        raise PackageError("CURRENT_TASK is not the approved task")
    if front.get("round_manifest") != _location(root, task_id, round_id) + "/round-manifest.json":
        raise PackageError("CURRENT_TASK does not point at this package")
    if front.get("task_manifest") != metadata_path(root, f"tasks/active/{task_id}.task.json"):
        raise PackageError("CURRENT_TASK task manifest path disagrees with task ID")
    return required


def _check_input_source(rel: str) -> None:
    if re.fullmatch(r"(?:\.dual-agent/)?task_interactions/[^/]+/round-[^/]+/implementation-baseline\.json", rel, re.I):
        raise PackageError(f"temporary implementation baseline cannot be frozen: {rel}")
    if re.match(r"^(?:\.dual-agent/)?task_interactions/[^/]+/round-[^/]+/package(?:/|$)", rel, re.I):
        raise PackageError(f"round package cannot be an input source: {rel}")


def build_package(
    root: Path, task_id: str, round_id: str, inputs: list[str], read_set: list[str],
    *, runtime: str = "implementation_agent", prepared_at: str,
) -> Path:
    """Copy exact live input bytes into a new package; never modify an old package."""
    root = root.resolve()
    package_rel = _location(root, task_id, round_id)
    package = root / Path(*package_rel.split("/"))
    if package.exists() or package.is_symlink():
        raise PackageError("package already exists; use a new round")
    _file(root, package_rel, required=False)
    front = _front(root)
    inputs = [_exact(rel) for rel in inputs]
    read_set = [_exact(rel) for rel in read_set]
    _unique(inputs)
    _unique(read_set)
    for rel in inputs:
        _check_input_source(rel)
    if not _required_paths(root, front, task_id, round_id) <= set(inputs):
        raise PackageError("required governed input is omitted")
    if not set(read_set) <= set(inputs) or not read_set:
        raise PackageError("agent_read_set must be a non-empty subset of inputs")
    if not {front[key] for key in READ_SET_KEYS} <= set(read_set):
        raise PackageError("agent_read_set omits task contract, authorization, or instruction")
    consultation = _file(root, front["consultation_ref"]).read_text(encoding="utf-8")
    if not re.search(r"商讨状态\s*[：:]\s*`?agreed`?", consultation):
        raise PackageError("consultation is not agreed")
    manifest_path = _file(root, front["task_manifest"])
    try:
        task_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError("task manifest is invalid JSON") from exc
    if task_manifest.get("task_id") != task_id or task_manifest.get("round_manifest_path") != package_rel + "/round-manifest.json":
        raise PackageError("task manifest does not bind this package")
    declared_inputs = task_manifest.get("governed_input_paths")
    if not isinstance(declared_inputs, list) or not declared_inputs:
        raise PackageError("task manifest must declare every governed_input_paths entry")
    for rel in declared_inputs:
        _exact(rel)
    _unique(declared_inputs)
    if declared_inputs != inputs:
        raise PackageError("package inventory differs from task governed_input_paths")
    report_rel = metadata_path(root, f"task_interactions/{task_id}/{round_id}/completion-report.yaml")
    if task_manifest.get("completion_report_path") != report_rel:
        raise PackageError("completion report path disagrees with round")
    allowed = task_manifest.get("allowed_write_paths")
    if not isinstance(allowed, list) or report_rel not in allowed:
        raise PackageError("completion report is not exactly authorized")
    for rel in allowed:
        _exact(rel)
        if rel == package_rel or rel.startswith(package_rel + "/"):
            raise PackageError("package path cannot be authorized output")
    pins = task_manifest.get("bound_inputs_sha256", {})
    if not isinstance(pins, dict) or any(rel not in inputs or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) for rel, digest in pins.items()):
        raise PackageError("invalid approved input bindings")
    round_dir = package.parent
    round_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".round-package-", dir=round_dir))
    try:
        entries = []
        for rel in inputs:
            raw = _file(root, rel).read_bytes()
            pinned = pins.get(rel)
            if pinned is not None and _sha(raw) != pinned:
                raise PackageError(f"input changed after approval: {rel}")
            if _file(root, rel).read_bytes() != raw:
                raise PackageError(f"source changed during copying: {rel}")
            target = stage / "inputs" / Path(*rel.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            entries.append({
                "kind": "governed_input", "source_path": rel,
                "snapshot_path": package_rel + "/inputs/" + rel,
                "sha256": _sha(raw), "bytes": len(raw),
            })
        manifest = {
            "schema_version": "2.0.0", "document_type": "round_package_manifest",
            "task_id": task_id, "round_id": round_id, "prepared_at": prepared_at,
            "prepared_by_role": "task_steward", "implementation_runtime": runtime,
            "hash_algorithm": "sha256", "package_root": package_rel,
            "agent_read_set": [package_rel + "/inputs/" + rel for rel in read_set],
            "inputs": entries,
            "required_commands": task_manifest.get("required_commands", []),
            "completion_report_path": report_rel,
        }
        if task_manifest.get("implementation_scope_check") is True:
            manifest["implementation_scope_check"] = True
        (stage / "round-manifest.json").write_bytes(
            (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        )
        if package.exists():
            raise PackageError("package appeared during build")
        os.replace(stage, package)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return package / "round-manifest.json"


def verify_package(
    root: Path, manifest_rel: str, *, check_route: bool = False,
    check_sources: bool = False, require_dispatch: bool = False,
    session: ReadSession | None = None,
) -> dict:
    """Verify package contents without changing files; live source comparison is optional."""
    root = root.resolve()
    if session is None:
        session = ReadSession(root)
    _file(root, manifest_rel)
    try:
        doc = json.loads(session.read_text(manifest_rel))
    except (SessionError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError("package manifest is invalid JSON") from exc
    if not isinstance(doc, dict) or doc.get("schema_version") != "2.0.0" or doc.get("document_type") != "round_package_manifest":
        raise PackageError("not a supported round package manifest")
    task_id, round_id = doc.get("task_id"), doc.get("round_id")
    package_rel = _location(root, task_id, round_id)
    if manifest_rel != package_rel + "/round-manifest.json" or doc.get("package_root") != package_rel:
        raise PackageError("manifest path or package_root disagrees")
    if doc.get("hash_algorithm") != "sha256" or doc.get("prepared_by_role") != "task_steward":
        raise PackageError("manifest hashing or owner is invalid")
    entries = doc.get("inputs")
    if not isinstance(entries, list) or not entries:
        raise PackageError("empty or invalid package inputs")
    sources = []
    snapshots = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise PackageError("invalid input entry")
        source = _exact(entry.get("source_path"))
        snapshot = _exact(entry.get("snapshot_path"))
        _check_input_source(source)
        if snapshot != package_rel + "/inputs/" + source:
            raise PackageError(f"snapshot is not path-preserving: {source}")
        _file(root, snapshot)
        raw = session.read_bytes(snapshot)
        if entry.get("bytes") != len(raw) or entry.get("sha256") != _sha(raw):
            raise PackageError(f"package input hash/length mismatch: {source}")
        sources.append(source)
        snapshots.append(snapshot)
        if check_sources:
            _file(root, source)
            if session.read_bytes(source) != raw:
                raise PackageError(f"source differs before dispatch: {source}")
    _unique(sources)
    _unique(snapshots)
    package = root / Path(*package_rel.split("/"))
    actual = set()
    for path in package.rglob("*"):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise PackageError(f"symlink in package: {rel}")
        if path.is_file():
            actual.add(rel)
        elif not path.is_dir():
            raise PackageError(f"special file in package: {rel}")
    if actual != set(snapshots) | {manifest_rel}:
        raise PackageError("missing or unexpected package member")
    read_set = doc.get("agent_read_set")
    if not isinstance(read_set, list) or not read_set or not set(read_set) <= set(snapshots):
        raise PackageError("agent_read_set is not a non-empty package subset")
    _unique(read_set)
    snapshot_front = _front_from_text(session.read_text(package_rel + "/inputs/" + metadata_path(root, "tasks/CURRENT_TASK.md")))
    if not _required_paths(root, snapshot_front, task_id, round_id) <= set(sources):
        raise PackageError("required governed input is missing from package")
    if not {package_rel + "/inputs/" + snapshot_front[key] for key in READ_SET_KEYS} <= set(read_set):
        raise PackageError("agent_read_set omits task contract, authorization, or instruction")
    consultation = session.read_text(package_rel + "/inputs/" + snapshot_front["consultation_ref"])
    if not re.search(r"商讨状态\s*[：:]\s*`?agreed`?", consultation):
        raise PackageError("frozen consultation is not agreed")
    if doc.get("completion_report_path") != metadata_path(root, f"task_interactions/{task_id}/{round_id}/completion-report.yaml"):
        raise PackageError("completion report path disagrees")
    task_doc = json.loads(session.read_text(package_rel + "/inputs/" + snapshot_front["task_manifest"]))
    if not isinstance(task_doc, dict):
        raise PackageError("frozen task manifest must be a JSON object")
    if task_doc.get("round_manifest_path") != manifest_rel:
        raise PackageError("task manifest snapshot points elsewhere")
    declared_inputs = task_doc.get("governed_input_paths")
    if not isinstance(declared_inputs, list) or declared_inputs != sources:
        raise PackageError("frozen task input inventory differs from package")
    _unique(declared_inputs)
    pins = task_doc.get("bound_inputs_sha256", {})
    digests = {item["source_path"]: item["sha256"] for item in entries}
    if not isinstance(pins, dict) or any(rel not in digests or digests[rel] != digest for rel, digest in pins.items()):
        raise PackageError("frozen bytes differ from approved input bindings")
    allowed = task_doc.get("allowed_write_paths")
    if not isinstance(allowed, list) or doc["completion_report_path"] not in allowed:
        raise PackageError("completion report is not authorized by frozen task manifest")
    for rel in allowed:
        _exact(rel)
        if rel.startswith(package_rel + "/"):
            raise PackageError("package path is authorized for writing")
    if check_route:
        front = _front_from_text(session.read_text(metadata_path(root, "tasks/CURRENT_TASK.md")))
        for key in ("task_id", "execution_status", "task_manifest", "round_manifest"):
            if front.get(key) != snapshot_front.get(key):
                raise PackageError(f"live route disagrees with package snapshot: {key}")
        if front.get("round_manifest") != manifest_rel:
            raise PackageError("live route points at a different package")
    if require_dispatch:
        _file(root, metadata_path(root, "artifacts/events/governance-events.jsonl"))
        digest = session.sha256(manifest_rel)
        found = False
        for line in session.read_text(metadata_path(root, "artifacts/events/governance-events.jsonl")).splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PackageError("invalid governance event log") from exc
            if (event.get("event_type") == "task_dispatched" and event.get("task_id") == task_id
                    and event.get("round_id") == round_id and event.get("artifact_hashes", {}).get(manifest_rel) == digest):
                found = True
        if not found:
            raise PackageError("dispatch event does not bind package manifest")
    return {"task_id": task_id, "round_id": round_id, "inputs": len(entries), "manifest_sha256": session.sha256(manifest_rel)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--verify", metavar="MANIFEST_REL")
    parser.add_argument("--task-id")
    parser.add_argument("--round-id")
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--read", action="append", default=[])
    parser.add_argument("--prepared-at")
    parser.add_argument("--runtime")
    parser.add_argument("--check-route", action="store_true")
    parser.add_argument("--check-sources", action="store_true")
    parser.add_argument("--require-dispatch", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.build:
            front = _front(args.root)
            args.task_id = args.task_id or front.get("task_id")
            args.round_id = args.round_id or front.get("round_id")
            args.prepared_at = args.prepared_at or datetime.now(timezone.utc).isoformat()
            if not args.input:
                task = json.loads(_file(args.root, front["task_manifest"]).read_text(encoding="utf-8"))
                args.input = task["governed_input_paths"]
            if not args.read:
                args.read = [front[k] for k in READ_SET_KEYS]
            args.runtime = args.runtime or front.get("assigned_agent") or "implementation_agent"
            manifest = build_package(args.root, args.task_id, args.round_id, args.input, args.read,
                                     runtime=args.runtime, prepared_at=args.prepared_at)
            route = metadata_path(args.root, "tasks/CURRENT_TASK.md")
            rel = manifest.relative_to(args.root.resolve()).as_posix()
            result = {"result": "built", "manifest": str(manifest),
                      "receipt_fields": {"manifest_sha256": _sha(manifest.read_bytes()),
                                         "route_sha256": _sha(_file(args.root, route).read_bytes()),
                                         "evidence": [rel]}}
        else:
            result = {"result": "pass", **verify_package(args.root, args.verify,
                    check_route=args.check_route, check_sources=args.check_sources,
                    require_dispatch=args.require_dispatch)}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, PackageError) as exc:
        print(json.dumps({"result": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

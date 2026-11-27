"""Initialize a new project from the local dual-agent template. Never overwrite files."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory

from templates.governance.review_views import SOURCE_NAMES, render_views

BASE = Path(__file__).resolve().parent
MANUAL = {
    "TASK_STEWARD_BOOTSTRAP_PROMPT.zh-CN.md": "TASK_STEWARD_BOOTSTRAP_PROMPT.zh-CN.md",
    "IMPLEMENTATION_AGENT_BOOTSTRAP_PROMPT.zh-CN.md": "IMPLEMENTATION_AGENT_BOOTSTRAP_PROMPT.zh-CN.md",
    "轮次工作单.md": "templates/docs/lifecycle/轮次工作单.md",
}
SKIP = {".git", ".pytest_cache", ".ruff_cache", "__pycache__"}


def check_path(path):
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"linked path: {part}")
        if part != path and not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"expected directory: {part}")


def _is_tool_path(rel: str) -> bool:
    return rel == ".dual-agent/tools.py" or rel.startswith(".dual-agent/governance/")


def _plan_upgrade(target: Path, contents: dict[str, bytes]) -> dict:
    plan = {"missing": [], "different_tools": [], "manual_comparison": [], "unchanged": 0, "_snapshots": {}}
    digest_inputs = {}
    for rel, data in sorted(contents.items()):
        dest = target / rel
        check_path(dest)
        target_sha = hashlib.sha256(data).hexdigest()
        if not dest.exists():
            plan["missing"].append(rel)
            plan["_snapshots"][rel] = None
            digest_inputs[rel] = ("create", None, target_sha)
        elif not dest.is_file():
            raise ValueError(f"expected regular file: {dest}")
        else:
            existing = dest.read_bytes()
            existing_sha = hashlib.sha256(existing).hexdigest()
            if existing == data:
                plan["unchanged"] += 1
                digest_inputs[rel] = ("unchanged", existing_sha, target_sha)
            elif _is_tool_path(rel):
                plan["different_tools"].append(rel)
                plan["_snapshots"][rel] = existing
                digest_inputs[rel] = ("update", existing_sha, target_sha)
            else:
                plan["manual_comparison"].append(rel)
                digest_inputs[rel] = ("manual_comparison", existing_sha, target_sha)
    plan["plan_sha256"] = hashlib.sha256(
        json.dumps(digest_inputs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return plan


def _atomic_write(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    scratch = dest.parent / f".upgrade-{os.getpid()}-{dest.name}.tmp"
    try:
        with scratch.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(scratch, dest)
    finally:
        scratch.unlink(missing_ok=True)


def initialize(target, name, manual, *, with_governance_tests=False, upgrade_preview=False, upgrade=False, plan_sha256=None):
    if manual and (with_governance_tests or upgrade or upgrade_preview):
        raise ValueError("governance tests and upgrade require structured initialization")
    if upgrade and upgrade_preview:
        raise ValueError("choose either --upgrade or --upgrade-preview")
    if not name.strip() or any(ord(c) < 32 for c in name):
        raise ValueError("expected a non-empty single-line project name")
    target = Path(os.path.abspath(target))
    check_path(target)
    if target == Path(target.anchor) or target.is_relative_to(BASE) or BASE.is_relative_to(target):
        raise ValueError("choose a separate project directory")
    if target.exists() and not target.is_dir():
        raise ValueError(f"expected project directory: {target}")
    sources = {dst: BASE / src for dst, src in MANUAL.items()}
    if not manual:
        tree = BASE / "templates"
        sources = {p.relative_to(tree).as_posix(): p for p in tree.rglob("*")
                   if not SKIP.intersection(p.relative_to(tree).parts) and p.is_file()
                   and (with_governance_tests or not p.relative_to(tree).is_relative_to("tests/governance"))}
        sources["IMPLEMENTATION_AGENT_PROMPT.en.md"] = BASE / "IMPLEMENTATION_AGENT_BOOTSTRAP_PROMPT.en.md"
    contents = {}
    for rel, source in sources.items():
        check_path(source)
        data = source.read_bytes()
        if source.suffix == ".json":
            document = json.loads(data)
            if document.get("project") == "PROJECT_NAME":
                document["project"] = name
                data = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
        elif source.name == "PROJECT_BRIEF.md":
            data = data.replace(b"PROJECT_NAME", name.replace("|", r"\|").encode())
        contents[rel] = data
    if not manual:
        # Render using the customized facts before touching the target or checking conflicts.
        with TemporaryDirectory() as scratch:
            staging = Path(scratch)
            for rel in (*SOURCE_NAMES, "review-views.json"):
                (staging / rel).write_bytes(contents[rel])
            contents.update(render_views(staging))
        contents = {rel if rel == "AGENTS.md" else ".dual-agent/" + rel: data
                    for rel, data in contents.items()}
    if upgrade_preview:
        if not target.is_dir():
            raise ValueError("upgrade preview requires an existing project")
        plan = _plan_upgrade(target, contents)
        plan.pop("_snapshots", None)
        return plan
    if upgrade:
        if not (target / ".dual-agent").is_dir():
            raise ValueError("upgrade requires an existing .dual-agent project")
        if not plan_sha256:
            raise ValueError("upgrade requires --plan-sha256 from --upgrade-preview")
        plan = _plan_upgrade(target, contents)
        if plan["plan_sha256"] != plan_sha256:
            raise ValueError("upgrade plan changed since preview; re-run --upgrade-preview")
        snapshots = plan["_snapshots"]
        result = {"updated": [], "created": [], "manual_comparison": plan["manual_comparison"], "unchanged": plan["unchanged"], "plan_sha256": plan["plan_sha256"]}
        for rel in plan["missing"]:
            dest = target / rel
            check_path(dest)
            if dest.exists():
                raise ValueError(f"target changed during upgrade: {rel}")
            _atomic_write(dest, contents[rel])
            result["created"].append(rel)
        for rel in plan["different_tools"]:
            dest = target / rel
            check_path(dest)
            if not dest.is_file() or dest.read_bytes() != snapshots[rel]:
                raise ValueError(f"target changed during upgrade: {rel}")
            _atomic_write(dest, contents[rel])
            result["updated"].append(rel)
        return result
    pending = {}
    for rel, data in contents.items():
        dest = target / rel
        check_path(dest)
        if dest.exists():
            if not dest.is_file() or dest.read_bytes() != data:
                raise ValueError(f"existing content differs: {dest}")
        else:
            pending[dest] = data
    with ExitStack() as rollback:
        for dest, data in pending.items():
            check_path(dest)
            for directory in reversed(dest.parents):
                if not directory.exists():
                    directory.mkdir()
                    rollback.callback(directory.rmdir)
            with dest.open("xb") as stream:
                rollback.callback(dest.unlink)
                stream.write(data)
        rollback.pop_all()
    return len(pending)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("--name", required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--with-governance-tests", action="store_true", help="include optional governance tests")
    modes.add_argument("--manual", action="store_true", help="copy just the worksheet and two role prompts")
    parser.add_argument("--upgrade-preview", action="store_true", help="compare template paths without writing")
    parser.add_argument("--upgrade", action="store_true", help="upgrade engine tools without overwriting project data")
    parser.add_argument("--plan-sha256", help="bind --upgrade to a reviewed --upgrade-preview digest")
    args = parser.parse_args()
    try:
        count = initialize(
            args.target,
            args.name,
            args.manual,
            with_governance_tests=args.with_governance_tests,
            upgrade_preview=args.upgrade_preview,
            upgrade=args.upgrade,
            plan_sha256=args.plan_sha256,
        )
    except (OSError, ValueError) as error:
        parser.exit(1, f"Initialization stopped: {error}\n")
    if args.upgrade_preview or args.upgrade:
        print(json.dumps(count, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    print(f"Created {count} files in {args.target.absolute()}. Complete the lightweight P0 before dispatch.")

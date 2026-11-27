"""Prepare round documents without changing authorization, dispatch or history."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import stat
import sys

from governance.read_session import ReadSession, SessionError, _rel_reason
from governance.round_package import _ids


def ordinary_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"expected an ordinary directory: {path}")


def create_round(directory, task, round_id, *, structured=False):
    _ids(task, round_id)
    if _rel_reason(task) or not round_id.isascii():
        raise ValueError("unsafe task or round ID")
    directory = directory.absolute()
    if directory.name != ".dual-agent":
        raise ValueError("run new-round from an initialized project's .dual-agent/tools.py")
    for parent in (*reversed(directory.parents), directory):
        ordinary_directory(parent)
    interactions = directory / "task_interactions"
    ordinary_directory(interactions)
    task_dir = interactions / task
    target = task_dir / round_id
    rel = target.relative_to(directory.parent).as_posix()
    sheet = f"{rel}/轮次工作单.md"
    source = ReadSession(directory).read_text("docs/lifecycle/轮次工作单.md")
    title, separator, body = source.partition("\n")
    if "<任务>" not in title or "<轮次>" not in title:
        raise ValueError("worksheet title must contain <任务> and <轮次>")
    title = title.replace("<任务>", task).replace("<轮次>", round_id)
    header = f"状态：draft（待审）。工作单：`{sheet}`。"
    files = {"轮次工作单.md": title + separator + "\n" + header + "\n" + body}
    if structured:
        instruction = f"{rel}/instruction.draft.md"
        plan = f"{rel}/acceptance-plan.draft.json"
        files["instruction.draft.md"] = (
            f"# {task} / {round_id} 指令草稿\n\n状态：draft。尚未下发。\n\n"
            f"- 工作单：`{sheet}`\n- 验收计划草稿：`{plan}`\n"
        )
        files["acceptance-plan.draft.json"] = json.dumps({
            "document_type": "acceptance_plan_draft", "schema_version": "0.1",
            "task_id": task, "round_id": round_id, "status": "draft",
            "worksheet": sheet, "instruction_draft": instruction,
            "authorized_write_paths": [], "required_commands": [], "extra_inputs": [],
        }, ensure_ascii=False, indent=2) + "\n"
    task_dir.mkdir(exist_ok=True)
    ordinary_directory(task_dir)
    # Exclusive acquisition of the round prevents two invocations from filling the same draft.
    try:
        target.mkdir()
    except FileExistsError:
        raise ValueError(f"round path already exists; preserved: {rel}") from None
    with ExitStack() as rollback:
        rollback.callback(target.rmdir)
        for filename, content in files.items():
            path = target / filename
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                rollback.callback(path.unlink)
                stream.write(content)
        rollback.pop_all()
    return [f"{rel}/{filename}" for filename in files]


def main(directory, argv):
    parser = argparse.ArgumentParser(prog="tools.py new-round", description=__doc__)
    parser.add_argument("task_id")
    parser.add_argument("round_id")
    parser.add_argument("--structured", action="store_true", help="also prepare instruction and acceptance-plan drafts")
    args = parser.parse_args(argv)
    try:
        paths = create_round(directory, args.task_id, args.round_id, structured=args.structured)
    except (OSError, ValueError, SessionError) as exc:
        print(f"new-round: {exc}", file=sys.stderr)
        return 1
    print("draft\n" + "\n".join(paths))
    return 0

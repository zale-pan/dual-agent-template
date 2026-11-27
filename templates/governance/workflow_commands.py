"""Normalize commands, run subprocesses and verify recorded attempts."""

from __future__ import annotations

from pathlib import Path
import base64
import json
import os
import shlex
import signal
import subprocess
import tempfile
import uuid
from governance.workflow_common import (
    WorkflowError,
    _sha,
    _utcnow,
)


MODES = ("dispatch", "acceptance")


def _parse_command(command: str) -> list[str]:
    text = command.strip()
    if text.startswith("&"):
        text = text[1:].strip()
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    if os.name == "nt":
        # Direct argv, with quotes for grouping; backslashes are literal path characters.
        lexer.escape = ""
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise WorkflowError(f"invalid command quoting: {exc}") from exc
    if not tokens:
        raise WorkflowError("empty required command")
    return tokens


def _normalize_commands(commands: object) -> list[dict]:
    """Normalize command forms; reject forward/self/cyclic dependency metadata."""
    if not isinstance(commands, list) or not commands:
        raise WorkflowError("acceptance plan declares no commands")
    stages: list[dict] = []
    for index, entry in enumerate(commands, start=1):
        if isinstance(entry, str):
            argv = _parse_command(entry)
            stage = {"stage_id": f"stage-{index}", "kind": "command", "modes": ["acceptance"], "depends_on": []}
        elif isinstance(entry, dict):
            command = entry.get("command")
            if not isinstance(command, str) or not command.strip():
                raise WorkflowError(f"command {index} has no command string")
            argv = _parse_command(command)
            modes = entry.get("modes", ["acceptance"])
            if not isinstance(modes, list) or not modes or any(m not in MODES for m in modes):
                raise WorkflowError(f"command {index} has invalid modes")
            depends = entry.get("depends_on", [])
            if not isinstance(depends, list) or any(not isinstance(d, int) for d in depends):
                raise WorkflowError(f"command {index} has invalid depends_on")
            stage = {
                "stage_id": str(entry.get("stage_id", f"stage-{index}")),
                "kind": str(entry.get("kind", "command")),
                "modes": list(modes),
                "depends_on": [int(d) for d in depends],
            }
        else:
            raise WorkflowError(f"command {index} is not a string or object")
        reusable = entry.get("reusable_readonly", False) if isinstance(entry, dict) else False
        if not isinstance(reusable, bool):
            raise WorkflowError(f"command {index} reusable_readonly must be boolean")
        timeout = entry.get("timeout_seconds", 300) if isinstance(entry, dict) else 300
        if type(timeout) is not int or not 1 <= timeout <= 86400:
            raise WorkflowError(f"command {index} timeout_seconds must be an integer from 1 to 86400")
        stage["timeout_seconds"] = timeout
        stage["reusable_readonly"] = reusable
        stage["argv"] = argv
        stages.append(stage)
    for index, stage in enumerate(stages, start=1):
        for dep in stage["depends_on"]:
            if dep < 1 or dep >= index:
                raise WorkflowError(f"stage {index} has an illegal forward/self dependency {dep}")
    seen_ids: set[str] = set()
    for stage in stages:
        if stage["stage_id"] in seen_ids:
            raise WorkflowError(f"duplicate stage_id: {stage['stage_id']}")
        seen_ids.add(stage["stage_id"])
    return stages


def _raw_sha(record: dict, stream: str) -> bool:
    encoded = record.get(f"{stream}_b64")
    if not isinstance(encoded, str):
        return False
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (ValueError, TypeError):
        return False
    return _sha(raw) == record.get(f"{stream}_sha256")


def _attempt_valid(record: dict, key: str, argv: list[str]) -> bool:
    if record.get("key") != key or record.get("argv") != argv:
        return False
    if record.get("status") != "passed" or record.get("exit_code") != 0:
        return False
    return _raw_sha(record, "stdout") and _raw_sha(record, "stderr")


def _find_reuse(output: Path, item: dict, stage_id: str, key: str, argv: list[str]) -> str | None:
    if item.get("key") != key or not isinstance(item.get("path"), str):
        return None
    path = Path(item["path"])
    if path.resolve().parent != output or not path.name.startswith("attempt-"):
        return None
    try:
        raw = path.read_bytes()
        if _sha(raw) != item.get("attempt_sha256"):
            return None
        record = json.loads(raw)
    except (OSError, UnicodeError, ValueError):
        return None
    if record.get("stage_id") == stage_id and _attempt_valid(record, key, argv):
        return path.as_posix()
    return None


def _stop_timed_out(process):
    """Only target the process tree started for this stage. Bound cleanup as well."""
    tree_stop = "unconfirmed"
    try:
        if os.name == "nt":
            taskkill = Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"
            result = subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=5, check=False)
            if result.returncode == 0:
                tree_stop = "taskkill_succeeded"
        else:
            os.killpg(process.pid, signal.SIGKILL)
            tree_stop = "group_signal_sent"
    except (KeyError, OSError, subprocess.TimeoutExpired):
        pass
    try:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"direct_child_exited": process.poll() is not None, "tree_stop": tree_stop}


def _write_attempt(output: Path, stage: dict, key: str, root: Path, env: dict[str, str]) -> tuple[dict, str]:
    attempt_id = uuid.uuid4().hex
    out_path = output / f"attempt-{stage['stage_id']}-{attempt_id}.json"
    started = _utcnow()
    cleanup = None
    timeout = stage["timeout_seconds"]
    # A descendant may inherit stdout/stderr. Waiting on pipe EOF would defeat the deadline.
    with tempfile.TemporaryFile(dir=output) as stdout, tempfile.TemporaryFile(dir=output) as stderr:
        try:
            proc = subprocess.Popen(stage["argv"], cwd=root, env={**os.environ, **env},
                                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                    start_new_session=os.name != "nt")
        except OSError as exc:
            status, exit_code = "probe_error", 2
            stderr.write(f"{type(exc).__name__}: {exc}".encode("utf-8", errors="replace"))
        else:
            try:
                exit_code = proc.wait(timeout=timeout)
                status = "passed" if exit_code == 0 else ("blocked" if exit_code == 2 else "failed")
            except subprocess.TimeoutExpired:
                cleanup = _stop_timed_out(proc)
                status, exit_code = "timed_out", proc.returncode
        stdout.seek(0)
        stderr.seek(0)
        stdout_raw = stdout.read(os.fstat(stdout.fileno()).st_size)
        stderr_raw = stderr.read(os.fstat(stderr.fileno()).st_size)
    ended = _utcnow()
    record = {
        "index": stage.get("index"),
        "stage_id": stage["stage_id"],
        "key": key,
        "argv": stage["argv"],
        "cwd": str(root),
        "env": env,
        "attempt_id": attempt_id,
        "started_at": started,
        "ended_at": ended,
        "timeout_seconds": timeout,
        "timeout_cleanup": cleanup,
        "exit_code": exit_code,
        "status": status,
        "stdout": stdout_raw.decode("utf-8", errors="replace"),
        "stderr": stderr_raw.decode("utf-8", errors="replace"),
        "stdout_sha256": _sha(stdout_raw),
        "stderr_sha256": _sha(stderr_raw),
        "stdout_b64": base64.b64encode(stdout_raw).decode("ascii"),
        "stderr_b64": base64.b64encode(stderr_raw).decode("ascii"),
    }
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record, out_path.as_posix()


def _verify_attempt(record: dict, plan_item: dict) -> list[str]:
    problems: list[str] = []
    sid = plan_item.get("stage_id")
    if record.get("stage_id") != sid:
        problems.append(f"{sid}: record stage_id disagrees")
    if record.get("key") != plan_item.get("key"):
        problems.append(f"{sid}: stage key disagrees with the plan")
    if record.get("argv") != plan_item.get("argv"):
        problems.append(f"{sid}: argv disagrees with the plan")
    if not _raw_sha(record, "stdout"):
        problems.append(f"{sid}: stdout raw bytes/hash mismatch")
    if not _raw_sha(record, "stderr"):
        problems.append(f"{sid}: stderr raw bytes/hash mismatch")
    status = record.get("status")
    exit_code = record.get("exit_code")
    if status not in ("passed", "failed", "blocked", "probe_error", "timed_out"):
        problems.append(f"{sid}: unknown or missing status {status!r}")
    elif status != "passed":
        problems.append(f"{sid}: referenced attempt status {status} is not a pass")
    if status == "passed" and exit_code != 0:
        problems.append(f"{sid}: status passed with non-zero exit")
    if status in ("failed", "blocked") and exit_code == 0:
        problems.append(f"{sid}: failed/blocked with zero exit")
    return problems

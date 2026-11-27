"""Preview, apply and resume approved cleanup rows."""

from __future__ import annotations

from pathlib import Path
import re
import stat
from governance.workflow_common import (
    WorkflowError,
    _is_reparse,
)


def _cleanup_root(candidate: Path) -> Path:
    root = candidate.absolute()
    if root == Path(root.anchor) or ".." in root.parts:
        raise WorkflowError("expected a dedicated cleanup directory")
    for part in (*reversed(root.parents), root):
        info = part.lstat()
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            raise WorkflowError("cleanup root is not an ordinary directory")
    return root


def _cleanup_target(root: Path, rel: str) -> Path:
    parts = rel.replace(chr(92), "/").split("/")
    if any(part in ("", ".", "..") or part.endswith((" ", "."))
           or any(c in part for c in '<>:|?*"') or any(ord(c) < 32 for c in part)
           or re.fullmatch(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[0-9¹²³])(?:\..*)?", part, re.I)
           for part in parts):
        raise WorkflowError(f"invalid cleanup path: {rel!r}")
    target = root
    for part in parts:
        target = target / part
        try:
            info = target.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise WorkflowError("cleanup path contains a link or reparse point")
    return target


def _cleanup_identity(target: Path) -> list[int]:
    info = target.lstat()
    if _is_reparse(info) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise WorkflowError("unsupported cleanup object")
    identity = [info.st_dev, info.st_ino, info.st_mode]
    if hasattr(info, "st_birthtime_ns"):
        identity.append(info.st_birthtime_ns)
    if stat.S_ISREG(info.st_mode):
        identity.extend([info.st_size, info.st_mtime_ns])
    return identity


def _check_cleanup_references(target: Path, references: list[Path]) -> None:
    for ref in references:
        if target.is_relative_to(ref) or ref.is_relative_to(target):
            raise WorkflowError(f"intersects current reference: {ref}")


def cleanup_plan(candidate: Path, *, candidates: list[str], referenced_paths: list[str]) -> dict:
    """Read-only inventory. Each file and each empty-directory removal needs its own row."""
    root = _cleanup_root(candidate)
    references = [_cleanup_target(root, rel) for rel in referenced_paths]
    suggestions = []
    protected = []
    seen = set()
    for rel in candidates:
        try:
            target = _cleanup_target(root, rel)
            if target in seen:
                continue
            seen.add(target)
            _check_cleanup_references(target, references)
            identity = _cleanup_identity(target)
        except (WorkflowError, OSError) as exc:
            protected.append({"path": rel, "reason": str(exc)})
            continue
        suggestions.append({"path": str(target), "identity": identity,
                            "reason": "unreferenced candidate artifact",
                            "references": sorted(referenced_paths)})
    return {"suggestions": suggestions, "protected": protected}


def apply_cleanup(candidate: Path, *, approved: list[dict], referenced_paths: list[str]) -> dict:
    """Apply exact externally approved rows. Retry with remaining and fresh references."""
    root = _cleanup_root(candidate)
    references = [_cleanup_target(root, rel) for rel in referenced_paths]
    results = []
    remaining = []
    # Never enumerate descendants during execution. Remove explicitly approved children first.
    for row in sorted(approved, key=lambda item: len(Path(item["path"]).parts), reverse=True):
        path = Path(row["path"])
        reason = ""
        try:
            target = _cleanup_target(root, path.relative_to(root).as_posix())
            _check_cleanup_references(target, references)
            identity = _cleanup_identity(target)
            if identity != row["identity"]:
                raise WorkflowError("identity changed; new inventory and approval required")
            if stat.S_ISDIR(identity[2]):
                target.rmdir()
            else:
                target.unlink()
            try:
                target.lstat()
            except FileNotFoundError:
                status = "deleted"
            else:
                raise WorkflowError("path still present after removal")
        except FileNotFoundError:
            status = "already_absent"
        except (OSError, ValueError, WorkflowError) as exc:
            status = "skipped"
            reason = str(exc)
            remaining.append(row)
        results.append({"path": str(path), "status": status, "reason": reason})
    return {"results": results, "remaining": remaining}

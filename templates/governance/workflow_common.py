"""Shared workflow errors, fingerprints and file attributes."""

from __future__ import annotations

from datetime import datetime
import hashlib
import stat
from datetime import timezone


FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class WorkflowError(ValueError):
    """Invalid or unapproved workflow input."""


class WorkflowInputError(WorkflowError):
    """Unreadable or unparseable invocation input."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_reparse(info: object) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)

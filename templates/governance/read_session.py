"""Restricted read-only session with canonical-path safety and stat-keyed dedup.

A ``ReadSession`` is a single-execution read cache. Byte reads are deduplicated by a
path + stat signature (size, mtime_ns, inode, ctime_ns); a replaced, resized or
mtime-changed file is re-read, and every read validates the path lexically and against
symlink/reparse/disk-alias/root escape. JSON parse is cached by content hash.

This module only reads; it never repairs, writes, deletes, or uses mtime/size as a
cross-run content proof.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import stat
import sys
from governance.linux_witness import LinuxWitness
from governance.macos_witness import MacOSWitness
import time
import unicodedata
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

WILDCARD_CHARS = "*?[]"
RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _nt_change_time_ns(path: Path) -> int | None:
    """Return the NTFS ChangeTime (100ns units since 1601) via FileBasicInfo.

    ChangeTime advances on any content or metadata write (including ``os.utime``),
    so a same-size in-place rewrite whose mtime/atime are restored still changes it.
    """
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        class _FileBasicInfo(ctypes.Structure):
            _fields_ = [
                ("CreationTime", ctypes.c_int64),
                ("LastAccessTime", ctypes.c_int64),
                ("LastWriteTime", ctypes.c_int64),
                ("ChangeTime", ctypes.c_int64),
                ("FileAttributes", wintypes.DWORD),
            ]
        # Opening a directory handle requires FILE_FLAG_BACKUP_SEMANTICS (0x02000000).
        try:
            is_dir = path.is_dir()
        except OSError:
            is_dir = False
        flags = 0x02000000 if is_dir else 0x80
        handle = kernel32.CreateFileW(str(path), 0x80000000, 0x7, None, 3, flags, None)
        if handle == -1:
            return None
        try:
            info = _FileBasicInfo()
            ok = kernel32.GetFileInformationByHandleEx(
                handle, 0, ctypes.byref(info), ctypes.sizeof(info)
            )
            if not ok:
                return None
            return int(info.ChangeTime)
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError, ValueError):
        return None


class SessionError(ValueError):
    """Invalid, unreadable or unsafe session input."""


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict:
    seen: set[str] = set()
    result = {}
    for key, item in pairs:
        if key in seen:
            raise SessionError(f"duplicate JSON key: {key}")
        seen.add(key)
        result[key] = item
    return result


def _reject_constant(name: str) -> object:
    raise SessionError(f"non-finite JSON number: {name}")


def _parse_strict(text: str) -> object:
    return json.loads(text, object_pairs_hook=_strict_pairs, parse_constant=_reject_constant)


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _rel_reason(rel: object) -> str | None:
    if not isinstance(rel, str) or not rel:
        return "empty path"
    if _nfc(rel) != rel:
        return "path is not Unicode NFC"
    if rel.startswith(("\\\\", "//")):
        return "UNC path"
    if rel.startswith(("/", "\\")):
        return "absolute path"
    if re.match(r"^[A-Za-z]:", rel):
        return "drive-letter path"
    if "\\" in rel:
        return "backslash separator"
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "empty or traversal segment"
    if any(":" in part for part in parts):
        return "alternate data stream"
    if any(any(ord(char) < 32 for char in part) for part in parts):
        return "control character"
    if any(part != part.rstrip(" .") for part in parts):
        return "trailing dot or space"
    for part in parts:
        if part.split(".")[0].casefold() in RESERVED_NAMES:
            return f"reserved device name: {part}"
    if any(char in rel for char in WILDCARD_CHARS):
        return "wildcard or directory grant"
    return None


def _alias_key(name: str) -> str:
    return _nfc(name).casefold()


@dataclass
class ReadSession:
    """One stable-view read cache with path safety and stat-keyed dedup."""

    root: Path
    _bytes: dict[str, tuple[tuple, bytes]] = field(default_factory=dict, init=False, repr=False)
    _json: dict[str, tuple[str, object]] = field(default_factory=dict, init=False, repr=False)
    _cache_witness: LinuxWitness | MacOSWitness | None = field(default=None, init=False, repr=False)
    _validated: set[str] = field(default_factory=set, init=False, repr=False)
    _dir_cache: dict[str, list[str]] = field(default_factory=dict, init=False, repr=False)
    _started: float = field(default_factory=time.monotonic, init=False, repr=False)
    counters: dict[str, int] = field(
        default_factory=lambda: {
            "read_bytes": 0, "stat": 0, "path_checks": 0, "bytes": 0, "dedup_hits": 0,
        },
        init=False,
    )
    performance: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.root = self.root.resolve()

    def _safe_rel(self, rel: object) -> str:
        reason = _rel_reason(rel)
        if reason:
            raise SessionError(f"invalid session path: {reason}")
        key = _nfc(rel)
        self.counters["path_checks"] += 1
        # Re-validate the full parent chain on every read: a directory swapped for a
        # junction or renamed after a previous read must not stay cached as safe.
        current = self.root
        for part in key.split("/"):
            current = current / part
            self.counters["stat"] += 1
            try:
                info = os.lstat(current)
            except OSError:
                info = None
            if info is not None and (
                stat.S_ISLNK(info.st_mode)
                or (getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)
            ):
                raise SessionError(f"symlink/reparse path is forbidden: {key}")
        spelling = self._disk_spelling(key)
        if spelling is not None and spelling != key:
            raise SessionError(f"on-disk spelling differs: {spelling}")
        return key

    def _disk_spelling(self, rel: str) -> str | None:
        parts = rel.split("/")
        current = self.root
        walked: list[str] = []
        try:
            for index, part in enumerate(parts):
                if not current.is_dir():
                    return None
                info = os.lstat(current)
                change = _nt_change_time_ns(current)
                if change is None:
                    # No stable directory token: never reuse a listing.
                    entries = os.listdir(current)
                else:
                    # The token combines every cheap directory witness available so a
                    # rename invalidates the cached listing even if one field is coarse.
                    dir_key = (str(current), info.st_ino, change, info.st_mtime_ns, info.st_size)
                    entries = self._dir_cache.get(dir_key)
                    if entries is None:
                        entries = os.listdir(current)
                        self._dir_cache[dir_key] = entries
                actual = next((name for name in entries if _alias_key(name) == _alias_key(part)), None)
                if actual is None:
                    return None
                walked.append(actual)
                current = current / actual
        except OSError:
            return None
        return "/".join(walked)

    def _path(self, rel: str) -> Path:
        return self.root / Path(*rel.split("/"))

    def _signature(self, path: Path) -> tuple | None:
        try:
            info = path.stat()
        except OSError:
            return None
        change = _nt_change_time_ns(path)
        if change is None:
            if os.name == "nt":
                # A failed ChangeTime query cannot prove content stability; forcing a
                # re-read (signature None) is safer than silently trusting creation time.
                return None
            change = info.st_ctime_ns
        return (info.st_ino, info.st_size, info.st_mtime_ns, change)

    def record_phase(self, name: str, start: float) -> None:
        self.performance[name] = round(time.monotonic() - start, 6)

    def read_bytes(self, rel: object) -> bytes:
        key = self._safe_rel(rel)
        path = self._path(key)
        self.counters["stat"] += 1
        signature = self._signature(path)
        if sys.platform in ("linux", "darwin"):
            # ctime can repeat even after same-size writes. Arm the directory before
            # the first read and invalidate cached bytes before serving another hit.
            if self._cache_witness is None:
                self._cache_witness = MacOSWitness() if sys.platform == "darwin" else LinuxWitness()
            witness = self._cache_witness
            parent = path.parent
            try:
                parent_info = parent.stat()
                witness.watch(parent, parent.relative_to(self.root).as_posix(), parent_info.st_dev)
                if sys.platform == "darwin":
                    witness.watch(path, key, parent_info.st_dev)
                if witness.changes(set()):
                    self._bytes.clear()
                info = path.stat()
                if witness.error or info.st_nlink != 1 or info.st_dev != parent_info.st_dev:
                    signature = None
            except OSError:
                signature = None
        cached = self._bytes.get(key)
        if cached is not None and signature is not None and cached[0] == signature:
            self.counters["dedup_hits"] += 1
            return cached[1]
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise SessionError(f"unreadable input: {key}: {exc}") from exc
        self.counters["read_bytes"] += 1
        self.counters["bytes"] += len(raw)
        self._bytes[key] = (signature, raw)
        return raw

    def read_text(self, rel: object) -> str:
        raw = self.read_bytes(rel)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SessionError(f"not UTF-8: {rel}: {exc}") from exc
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def read_json(self, rel: object) -> object:
        raw = self.read_bytes(rel)
        digest = hashlib.sha256(raw).hexdigest()
        cached = self._json.get(self._safe_rel(rel))
        if cached is not None and cached[0] == digest:
            return cached[1]
        try:
            value = _parse_strict(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, SessionError) as exc:
            raise SessionError(f"invalid JSON in {rel}: {exc}") from exc
        self._json[self._safe_rel(rel)] = (digest, value)
        return value

    def read_json_object(self, rel: object) -> dict:
        value = self.read_json(rel)
        if not isinstance(value, dict):
            raise SessionError(f"JSON root must be an object: {rel}")
        return value

    def sha256(self, rel: object) -> str:
        return hashlib.sha256(self.read_bytes(rel)).hexdigest()

    def file_state(self, rel: object) -> tuple[bool, str | None]:
        key = self._safe_rel(rel)
        self.counters["stat"] += 1
        try:
            return self._path(key).is_file(), None
        except OSError as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def snapshot(self) -> dict:
        return {
            "counters": dict(self.counters),
            "phases": dict(self.performance),
            "cache_entries": len(self._bytes),
            "elapsed_seconds": round(time.monotonic() - self._started, 6),
        }

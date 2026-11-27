"""Tests for the read-only session: path safety and stat-keyed dedup."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from governance.read_session import ReadSession, SessionError, _rel_reason


def _scratch_root() -> Path:
    for candidate in (os.environ.get("GOVERNANCE_TEST_SCRATCH"), tempfile.gettempdir()):
        if not candidate:
            continue
        root = Path(candidate) / "ForeContract-read-session-tests"
        try:
            root.mkdir(parents=True, exist_ok=True)
            return root
        except OSError:
            continue
    return Path(tempfile.mkdtemp(prefix="ForeContract-read-session-tests-"))


@pytest.fixture
def work():
    root = _scratch_root()
    path = root / f"session-{uuid.uuid4().hex[:12]}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def put(root: Path, rel: str, content: str | bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def test_read_bytes_dedups_when_unchanged(work: Path) -> None:
    put(work, "x.json", b'{"n":1}')
    session = ReadSession(work)
    a = session.read_bytes("x.json")
    b = session.read_bytes("x.json")
    assert a == b == b'{"n":1}'
    assert session.counters["read_bytes"] == 1
    assert session.counters["dedup_hits"] == 1


def test_read_bytes_reflects_replacement(work: Path) -> None:
    put(work, "x.json", b'{"n":1}')
    session = ReadSession(work)
    session.read_bytes("x.json")
    tmp = work / "replacement"
    tmp.write_bytes(b'{"n":9}')
    os.replace(tmp, work / "x.json")
    assert session.read_bytes("x.json") == b'{"n":9}'


def test_read_bytes_reflects_same_size_mtime_restore(work: Path) -> None:
    put(work, "x.json", b'{"n":1}')
    session = ReadSession(work)
    before = session.read_bytes("x.json")
    st = (work / "x.json").stat()
    (work / "x.json").write_bytes(b'{"n":2}')
    os.utime(work / "x.json", ns=(st.st_atime_ns, st.st_mtime_ns))
    after = session.read_bytes("x.json")
    assert after != before
    assert after == b'{"n":2}'


def test_read_json_parse_skips(work: Path) -> None:
    put(work, "a.json", json.dumps({"x": 1}))
    session = ReadSession(work)
    a = session.read_json("a.json")
    b = session.read_json("a.json")
    assert a == b == {"x": 1}
    assert session.counters["read_bytes"] == 1


def test_read_json_strict_duplicate_key(work: Path) -> None:
    put(work, "dup.json", '{"x": 1, "x": 2}')
    session = ReadSession(work)
    with pytest.raises(SessionError):
        session.read_json("dup.json")


def test_read_text_translates_crlf(work: Path) -> None:
    put(work, "route.md", b"---\r\ntask_id: T-01\r\n---\r\n")
    session = ReadSession(work)
    text = session.read_text("route.md")
    assert text.startswith("---\n")
    assert "\r" not in text


@pytest.mark.parametrize("rel", [
    "../escape", "/absolute", "C:/win", "\\\\unc\\path", "a\\b", "a/**", "a:b", "a/\u0301.py",
])
def test_invalid_paths_are_rejected(work: Path, rel: str) -> None:
    session = ReadSession(work)
    with pytest.raises(SessionError):
        session.read_bytes(rel)


def test_file_state_probes_without_reading(work: Path) -> None:
    put(work, "a.txt", "x")
    session = ReadSession(work)
    present, error = session.file_state("a.txt")
    assert present and error is None
    assert session.counters["read_bytes"] == 0


def test_unreadable_file_raises(work: Path) -> None:
    session = ReadSession(work)
    with pytest.raises(SessionError):
        session.read_bytes("missing.json")


def test_junction_escape_rejected(work: Path) -> None:
    outside = work.parent / f"junction-target-{uuid.uuid4().hex[:10]}"
    outside.mkdir(parents=True, exist_ok=True)
    put(outside, "secret.txt", "outside")
    link = work / "linked"
    try:
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        else:
            link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        shutil.rmtree(outside)
        pytest.skip(f"cannot create directory link: {exc}")
    try:
        session = ReadSession(work)
        with pytest.raises(SessionError):
            session.read_bytes("linked/secret.txt")
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()
        shutil.rmtree(outside, ignore_errors=True)


def test_rel_reason_rejects_bad_paths() -> None:
    assert _rel_reason("ok/file.txt") is None
    assert _rel_reason("../x") is not None
    assert _rel_reason("") is not None
    assert _rel_reason(None) is not None

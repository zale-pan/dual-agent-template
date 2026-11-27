"""Portable API doubles plus real macOS vnode checks in CI."""
import os
import sys
from types import SimpleNamespace

import pytest

from governance import macos_witness as mw
from governance import read_session
from governance import steward_workflow as workflow


@pytest.fixture
def kernel(monkeypatch):
    class Queue:
        def __init__(self):
            self.events = []
            self.closed = False
            self.fail = False

        def control(self, registrations, count, timeout):
            if self.fail:
                raise OSError("queue unavailable")
            if registrations:
                return []
            result = self.events[:count]
            del self.events[:count]
            return result

        def close(self):
            self.closed = True

    queue = Queue()
    names = ("KQ_NOTE_WRITE", "KQ_NOTE_EXTEND", "KQ_NOTE_ATTRIB", "KQ_NOTE_LINK",
             "KQ_NOTE_RENAME", "KQ_NOTE_DELETE", "KQ_NOTE_REVOKE",
             "KQ_FILTER_VNODE", "KQ_EV_ADD", "KQ_EV_CLEAR", "KQ_EV_ERROR")
    api = SimpleNamespace(**{name: 1 << i for i, name in enumerate(names)})
    queues = []
    def new_queue():
        result = queue if not queues else Queue()
        queues.append(result)
        return result
    api.kqueue = new_queue
    api.kevent = lambda ident, **kwargs: SimpleNamespace(ident=ident, **kwargs)
    monkeypatch.setattr(mw, "select", api)
    handles = {}
    queue.handles = handles
    def open_path(path, flags):
        fd = 1000000 + len(handles)
        handles[fd] = os.lstat(path)
        return fd
    def close_fd(fd):
        del handles[fd]
    monkeypatch.setattr(mw, "os", SimpleNamespace(
        O_RDONLY=0, O_NONBLOCK=1, O_NOFOLLOW=2,
        open=open_path, close=close_fd, fstat=lambda fd: handles[fd], lstat=os.lstat))
    return queue, api


def test_notifications_and_idempotent_close(tmp_path, kernel):
    queue, api = kernel
    target = tmp_path / "file"
    target.write_bytes(b"old")
    witness = mw.MacOSWitness()
    witness.watch(target, "file", target.stat().st_dev)
    witness.watch(target, "file", target.stat().st_dev)
    assert witness.error is None and len(witness.paths) == 1
    fd = next(iter(witness.paths))
    assert witness.changes(set()) == set()
    queue.events = [SimpleNamespace(ident=fd, flags=0, fflags=api.KQ_NOTE_WRITE)]
    assert witness.changes(set()) == {"file"}
    queue.events = [SimpleNamespace(ident=fd, flags=0, fflags=api.KQ_NOTE_REVOKE)]
    assert witness.changes(set()) == set()
    assert "coverage lost" in witness.error
    witness.close()
    witness.close()
    assert queue.closed
    assert queue.handles == {}


def test_unavailable_registration_and_queue(tmp_path, kernel):
    queue, _ = kernel
    target = tmp_path / "file"
    target.write_bytes(b"old")
    witness = mw.MacOSWitness()
    queue.fail = True
    witness.watch(target, "file", target.stat().st_dev)
    assert "incomplete macOS watch" in witness.error
    assert not witness.paths
    assert queue.handles == {}
    witness.close()


def test_macos_cache_uses_notifications_even_with_equal_signature(tmp_path, kernel, monkeypatch):
    queue, api = kernel
    monkeypatch.setattr(read_session.sys, "platform", "darwin")
    target = tmp_path / "file"
    target.write_bytes(b"old")
    session = read_session.ReadSession(tmp_path)
    monkeypatch.setattr(session, "_signature", lambda path: (1, 3, 0, 0))
    try:
        assert session.read_bytes("file") == b"old"
        target.write_bytes(b"new")
        fd = next(fd for fd, rel in session._cache_witness.paths.items() if rel == "file")
        queue.events = [SimpleNamespace(ident=fd, flags=0, fflags=api.KQ_NOTE_WRITE)]
        assert session.read_bytes("file") == b"new"
        target.write_bytes(b"end")
        queue.fail = True
        assert session.read_bytes("file") == b"end"
    finally:
        session._cache_witness.close()


@pytest.mark.parametrize("outcome", ["clean", "restored", "unavailable"])
def test_macos_inventory_selects_notification_witness(tmp_path, kernel, monkeypatch, outcome):
    queue, api = kernel
    monkeypatch.setattr(workflow.sys, "platform", "darwin")
    # Exercise the normal witness selection without requiring an unrelated package fixture.
    class Inventory(workflow._AuditSession):
        def __init__(self, root, manifest, witness):
            self.root = root
            self.change_witness = witness
            self.session = read_session.ReadSession(root)
            self.finalized = False
            self.whole_root_inventories = 0
            self.phase_seconds = {}
        def _authority_still_valid(self):
            return True
    monkeypatch.setattr(workflow, "_AuditSession", Inventory)
    target = tmp_path / "file"
    target.write_bytes(b"old")
    audit = workflow.audit_session(tmp_path, "unused")
    try:
        initial, signatures, dirs, unavailable = audit._walk_inventory()
        assert isinstance(audit.change_witness, mw.MacOSWitness)
        assert not unavailable
        assert initial == audit._walk_inventory(final=True)[0]
        fd = next(fd for fd, rel in audit.change_witness.paths.items() if rel == "file")
        audit.inventory, audit.signatures, audit.dirs = initial, signatures, dirs
        audit._unavailable = unavailable
        if outcome == "restored":
            queue.events = [SimpleNamespace(ident=fd, flags=0, fflags=api.KQ_NOTE_WRITE)]
        if outcome == "unavailable":
            queue.fail = True
        result = audit.finalize()
        assert result["publication_eligible"] is (outcome == "clean")
        if outcome == "restored":
            assert "file" in result["modified"]
        if outcome == "unavailable":
            assert any("macOS change events" in problem for problem in result["problems"])

    finally:
        audit.change_witness.close()
        if audit.session._cache_witness:
            audit.session._cache_witness.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS vnode notifications")
def test_real_macos_restored_write_and_directory_change(tmp_path):
    target = tmp_path / "file"
    target.write_bytes(b"old")
    witness = mw.MacOSWitness()
    try:
        witness.watch(tmp_path, ".", tmp_path.stat().st_dev)
        witness.watch(target, "file", target.stat().st_dev)
        assert witness.error is None
        assert witness.changes(set()) == set()
        before = target.stat()
        target.write_bytes(b"new")
        target.write_bytes(b"old")
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert "file" in witness.changes(set())
        assert witness.error is None
        assert target.read_bytes() == b"old"
        assert target.stat().st_mtime_ns == before.st_mtime_ns
        transient = tmp_path / "transient"
        transient.mkdir()
        transient.rmdir()
        assert "." in witness.changes(set())
    finally:
        witness.close()

"""Linux-only change notifications for the duration of one governed run."""
import ctypes
import os
from pathlib import Path
import struct
import weakref


# linux/inotify.h. Subscribe only to changes, so the inventories' own reads are harmless.
IN_MODIFY = 0x2
IN_ATTRIB = 0x4
IN_MOVES = 0x40 | 0x80
IN_CREATE_DELETE = 0x100 | 0x200
IN_SELF_CHANGES = 0x400 | 0x800
IN_UNMOUNT = 0x2000
IN_Q_OVERFLOW = 0x4000
IN_IGNORED = 0x8000
IN_ONLYDIR = 0x01000000
IN_DONT_FOLLOW = 0x02000000
IN_ISDIR = 0x40000000
EVENT = struct.Struct("=iIII")
WATCH_MASK = (IN_MODIFY | IN_ATTRIB | IN_MOVES | IN_CREATE_DELETE | IN_SELF_CHANGES
              | IN_ONLYDIR | IN_DONT_FOLLOW)
LOCAL_FILESYSTEMS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "tmpfs"}


class LinuxWitness:
    def __init__(self):
        self.error = None
        self._release = None
        self.paths = {}
        try:
            # Network and host-shared mounts can hide writes from another kernel.
            self.devices = {}
            for line in Path("/proc/self/mountinfo").read_bytes().splitlines():
                fields, fs = line.split(b" - ", 1)
                major, minor = map(int, fields.split()[2].split(b":"))
                self.devices[os.makedev(major, minor)] = os.fsdecode(fs.split()[0])
            libc = ctypes.CDLL(None, use_errno=True)
            self.add_watch = libc.inotify_add_watch
            self.add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
            fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
            if fd < 0:
                raise OSError(ctypes.get_errno(), "inotify_init1")
            self.fd = fd
            self._release = weakref.finalize(self, os.close, fd)
        except (OSError, AttributeError, ValueError) as exc:
            self.error = f"Linux stability witness unavailable: {exc}"

    def watch(self, path, rel, device):
        if self.error:
            return
        fs = self.devices.get(device, "unknown")
        if fs not in LOCAL_FILESYSTEMS:
            self.error = f"Linux stability witness unavailable on {fs}: {rel}"
            return
        wd = self.add_watch(self.fd, os.fsencode(path), WATCH_MASK)
        if wd < 0 or (wd in self.paths and self.paths[wd] != rel):
            self.error = f"incomplete Linux directory watch: {rel}"
            return
        self.paths[wd] = rel

    def changes(self, skip_dirs):
        touched = set()
        while not self.error:
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            except OSError as exc:
                self.error = f"cannot read Linux change events: {exc}"
                break
            if not data:
                self.error = "Linux change event stream closed"
                break
            pos = 0
            while pos < len(data):
                wd, mask, _, length = EVENT.unpack_from(data, pos)
                pos += EVENT.size
                name = os.fsdecode(data[pos:pos + length].split(b"\0", 1)[0])
                pos += length
                if mask & (IN_Q_OVERFLOW | IN_UNMOUNT | IN_IGNORED) or wd not in self.paths:
                    self.error = "Linux watch coverage lost (overflow, unmount or lost watch)"
                    break
                if mask & IN_ISDIR and name in skip_dirs:
                    continue
                touched.add((Path(self.paths[wd]) / name).as_posix())
        return touched

    def close(self):
        if self._release is not None:
            self._release()


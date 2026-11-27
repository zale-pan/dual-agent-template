"""macOS vnode notifications held for one audit or read session."""
import os
import select
import stat
import weakref


def _release(queue, descriptors):
    try:
        queue.close()
    finally:
        for fd in descriptors:
            os.close(fd)
        descriptors.clear()


class MacOSWitness:
    def __init__(self):
        self.error = None
        self.paths = {}
        self._release = None
        try:
            self.queue = select.kqueue()
            self._release = weakref.finalize(self, _release, self.queue, self.paths)
        except (OSError, AttributeError) as exc:
            self.error = f"macOS stability witness unavailable: {exc}"

    def watch(self, path, rel, device):
        if self.error:
            return
        if rel in self.paths.values():
            return
        fd = None
        try:
            flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
            fd = os.open(path, flags)
            info = os.fstat(fd)
            current = os.lstat(path)
            if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino) or info.st_dev != device:
                raise OSError("watch identity changed")
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise OSError("watch requires a regular file or directory")
            mask = (select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_ATTRIB
                    | select.KQ_NOTE_LINK | select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE
                    | select.KQ_NOTE_REVOKE)
            event = select.kevent(fd, filter=select.KQ_FILTER_VNODE,
                                 flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR, fflags=mask)
            self.queue.control([event], 0, 0)
            self.paths[fd] = rel
            fd = None
        except (OSError, AttributeError, ValueError) as exc:
            self.error = f"incomplete macOS watch for {rel}: {exc}"
        finally:
            if fd is not None:
                os.close(fd)

    def changes(self, skip_dirs):
        # A directory notification cannot identify which child changed.
        # Keep it even when the change may concern an excluded child.
        touched = set()
        while not self.error:
            try:
                events = self.queue.control(None, 256, 0)
                if not events:
                    break
                for event in events:
                    if (event.ident not in self.paths or event.flags & select.KQ_EV_ERROR
                            or event.fflags & select.KQ_NOTE_REVOKE):
                        self.error = "macOS watch coverage lost"
                        break
                    touched.add(self.paths[event.ident])
            except (OSError, ValueError) as exc:
                self.error = f"cannot read macOS change events: {exc}"
        return touched

    def close(self):
        if self._release is not None:
            self._release()

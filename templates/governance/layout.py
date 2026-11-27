"""Repository-relative names for installed metadata; project commands keep the repository cwd."""
from pathlib import Path
import stat


def metadata_prefix(root: Path) -> str:
    directory = root / ".dual-agent"
    try:
        info = directory.lstat()
    except FileNotFoundError:
        return ""
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("expected an ordinary .dual-agent directory")
    return ".dual-agent/"


def metadata_path(root: Path, rel: str) -> str:
    return metadata_prefix(root) + rel

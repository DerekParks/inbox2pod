"""Publish complete files only after their contents have reached disk."""

import os
import tempfile
from pathlib import Path


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_mkdir(path: Path) -> None:
    if not path.exists():
        durable_mkdir(path.parent)
        path.mkdir(exist_ok=True)
        sync_directory(path.parent)


def durable_write(path: Path, data: bytes) -> None:
    durable_mkdir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

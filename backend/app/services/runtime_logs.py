import base64
import hashlib
import os
from pathlib import Path

MAX_CHUNK = 1024 * 1024


def _anchor(handle, offset: int) -> str:
    handle.seek(max(0, offset - 64))
    return hashlib.sha256(handle.read(min(offset, 64))).hexdigest()[:16]


def _read_lines(handle, offset: int, limit: int) -> bytes:
    handle.seek(offset)
    data = handle.read(limit)
    if data and not data.endswith(b"\n"):
        end = data.rfind(b"\n")
        if end >= 0 or len(data) < limit:
            data = data[:end + 1]
    return data


def _result(handle, inode: int, offset: int, data: bytes, gap: bool = False) -> dict:
    return {
        "inode": inode,
        "offset": offset,
        "anchor": _anchor(handle, offset),
        "content": base64.b64encode(data).decode("ascii"),
        "gap": gap,
    }


def read_chunk(path: Path, inode: int, offset: int, anchor: str) -> dict:
    with path.open("rb") as handle:
        stat = os.fstat(handle.fileno())
        if inode == stat.st_ino and offset <= stat.st_size and anchor == _anchor(handle, offset):
            data = _read_lines(handle, offset, MAX_CHUNK)
            return _result(handle, stat.st_ino, offset + len(data), data)

        previous_data = b""
        gap = bool(inode)
        rotated = path.with_name(path.name + ".1")
        try:
            with rotated.open("rb") as previous:
                previous_stat = os.fstat(previous.fileno())
                previous_offset = offset if inode else 0
                if not inode or previous_stat.st_size >= offset and anchor == _anchor(previous, offset):
                    previous_data = _read_lines(previous, previous_offset, MAX_CHUNK)
                    new_offset = previous_offset + len(previous_data)
                    if previous_stat.st_size > new_offset and len(previous_data) > 0:
                        return _result(previous, previous_stat.st_ino, new_offset, previous_data)
                    gap = False
        except FileNotFoundError:
            pass
        data = _read_lines(handle, 0, MAX_CHUNK - len(previous_data))
        return _result(handle, stat.st_ino, len(data), previous_data + data, gap)


def tail_log(path: Path, limit: int) -> list[str]:
    with path.open("rb") as handle:
        size = handle.seek(0, 2)
        start = max(0, size - 256 * 1024)
        handle.seek(start)
        if start:
            handle.readline()
        return handle.read().decode("utf-8", errors="replace").splitlines()[-limit:]

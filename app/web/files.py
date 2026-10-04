"""Workspace file helpers: path confinement, listings, snapshots and zip export.

All functions are synchronous (file system work); call them via ``asyncio.to_thread``.
"""

from __future__ import annotations

import mimetypes
import os
import re
import shutil
import stat
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, Iterator, List, Optional, Tuple

from app.web.schemas import iso


MAX_LIST_ENTRIES = 2000
MAX_SNAPSHOT_FILES = 2000
MAX_ZIP_FILES = 20000
MAX_NAME_BYTES = 200
SKIPPED_DIRS = frozenset({"node_modules", "__pycache__"})
ZIP_CHUNK_SIZE = 256 * 1024

_UNSAFE_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')

# Extra mappings that the platform mime database may lack.
mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("text/markdown", ".markdown")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("text/csv", ".csv")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("text/x-python", ".py")
mimetypes.add_type("text/plain", ".log")
mimetypes.add_type("text/x-typescript", ".ts")
mimetypes.add_type("application/x-yaml", ".yaml")
mimetypes.add_type("application/x-yaml", ".yml")
mimetypes.add_type("application/toml", ".toml")


class PathViolation(ValueError):
    """A requested path is invalid or escapes the workspace."""


def guess_mime(name: str) -> Optional[str]:
    return mimetypes.guess_type(name, strict=False)[0]


def _is_skipped(name: str) -> bool:
    return name.startswith(".") or name in SKIPPED_DIRS


def resolve_path(workspace: Path, relative: str) -> Path:
    """Resolve a client supplied workspace-relative path, confined to ``workspace``.

    Symlinks are followed, so links pointing outside the workspace are rejected too.

    Raises:
        PathViolation: For absolute paths, NUL bytes or paths escaping the workspace.
    """
    if "\x00" in relative:
        raise PathViolation("Invalid path")
    cleaned = relative.replace("\\", "/").strip()
    if cleaned.startswith("/") or re.match(r"^[A-Za-z]:", cleaned):
        raise PathViolation("Absolute paths are not allowed")
    root = workspace.resolve()
    target = (root / cleaned).resolve()
    if target != root and not target.is_relative_to(root):
        raise PathViolation("Path is outside the workspace")
    return target


def resolve_entry(workspace: Path, relative: str) -> Path:
    """Like :func:`resolve_path` but keeps the last component unresolved, so that a
    symlink itself (not its target) is addressed. The workspace root is rejected."""
    cleaned = relative.replace("\\", "/").strip().rstrip("/")
    name = PurePosixPath(cleaned).name
    if name in ("", ".", ".."):
        raise PathViolation("Invalid path")
    parent = resolve_path(workspace, str(PurePosixPath(cleaned).parent))
    return parent / name


def file_entry(workspace_root: Path, path: Path, st: Optional[os.stat_result] = None):
    """FileEntry dict of ``path`` (``workspace_root`` must be resolved)."""
    st = st or path.stat()
    is_dir = stat.S_ISDIR(st.st_mode)
    return {
        "path": path.relative_to(workspace_root).as_posix(),
        "name": path.name,
        "is_dir": is_dir,
        "size": 0 if is_dir else st.st_size,
        "modified_at": iso(datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)),
        "mime": None if is_dir else guess_mime(path.name),
    }


def _entries(directory: Path) -> List[os.DirEntry]:
    try:
        with os.scandir(directory) as iterator:
            return sorted(
                (entry for entry in iterator if not _is_skipped(entry.name)),
                key=lambda entry: entry.name,
            )
    except OSError:
        return []


def _walk(root: Path) -> Iterator[Tuple[Path, os.stat_result]]:
    """Pre-order walk (sorted by name) of visible entries. Symlinked directories are
    not followed and symlinks pointing outside ``root`` are skipped."""
    stack = [iter(_entries(root))]
    while stack:
        entry = next(stack[-1], None)
        if entry is None:
            stack.pop()
            continue
        path = Path(entry.path)
        try:
            if entry.is_symlink():
                target = path.resolve()
                if not target.is_relative_to(root) or target.is_dir():
                    continue
                st = target.stat()
            else:
                st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        yield path, st
        if stat.S_ISDIR(st.st_mode) and not entry.is_symlink():
            stack.append(iter(_entries(path)))


def list_files(workspace: Path, limit: int = MAX_LIST_ENTRIES) -> List[dict]:
    """Recursive FileEntry listing (at most ``limit`` entries)."""
    if not workspace.is_dir():
        return []
    root = workspace.resolve()
    entries = []
    for path, st in _walk(root):
        entries.append(file_entry(root, path, st))
        if len(entries) >= limit:
            break
    return entries


Snapshot = Dict[str, Tuple[int, int]]


def snapshot(workspace: Path, limit: int = MAX_SNAPSHOT_FILES) -> Optional[Snapshot]:
    """``{path: (mtime_ns, size)}`` of visible files; None when there are more than
    ``limit`` files (diffing is skipped for such workspaces)."""
    if not workspace.is_dir():
        return {}
    root = workspace.resolve()
    result: Snapshot = {}
    for path, st in _walk(root):
        if stat.S_ISDIR(st.st_mode):
            continue
        if len(result) >= limit:
            return None
        result[path.relative_to(root).as_posix()] = (st.st_mtime_ns, st.st_size)
    return result


def diff_snapshots(before: Snapshot, after: Snapshot) -> List[str]:
    """Paths that were added, removed or modified (sorted)."""
    changed = {path for path, meta in after.items() if before.get(path) != meta}
    changed.update(path for path in before if path not in after)
    return sorted(changed)


def sanitize_filename(name: str) -> str:
    """A safe single path component derived from a client supplied file name."""
    name = unicodedata.normalize("NFC", name or "")
    name = name.replace("\\", "/").split("/")[-1]
    name = _UNSAFE_NAME_CHARS.sub("_", name).strip().lstrip(".").strip()
    name = name.rstrip(". ")
    if not name:
        return "file"
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        suffix = PurePosixPath(name).suffix
        if len(suffix.encode("utf-8")) > 20:
            suffix = ""
        stem = name[: len(name) - len(suffix)] if suffix else name
        while len((stem + suffix).encode("utf-8")) > MAX_NAME_BYTES:
            stem = stem[:-1]
        name = (stem.rstrip(". ") or "file") + suffix
    return name


def sanitize_dir(relative: str) -> str:
    """Sanitized workspace-relative directory ("" for the workspace root)."""
    parts = []
    for part in (relative or "").replace("\\", "/").split("/"):
        part = part.strip()
        if part in ("", "."):
            continue
        if part == "..":
            raise PathViolation("Invalid directory")
        parts.append(sanitize_filename(part))
    return "/".join(parts)


def unique_path(directory: Path, name: str, taken: Optional[set] = None) -> Path:
    """``directory/name`` or ``directory/name (n).ext`` when that already exists."""
    taken = taken if taken is not None else set()
    candidate = directory / name
    if not candidate.exists() and candidate not in taken:
        return candidate
    stem, suffix = PurePosixPath(name).stem, PurePosixPath(name).suffix
    counter = 1
    while True:
        candidate = directory / f"{stem} ({counter}){suffix}"
        if not candidate.exists() and candidate not in taken:
            return candidate
        counter += 1


def delete_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def remove_tree(path: Path) -> None:
    """Delete a directory tree if it exists (errors are ignored)."""
    shutil.rmtree(path, ignore_errors=True)


class _ZipSink:
    """Write-only, non-seekable stream collecting zip output for streaming."""

    def __init__(self):
        self._chunks: List[bytes] = []
        self._position = 0
        self.buffered = 0

    def write(self, data: bytes) -> int:
        self._chunks.append(bytes(data))
        self._position += len(data)
        self.buffered += len(data)
        return len(data)

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        self.buffered = 0
        return data


def iter_zip(workspace: Path) -> Iterator[bytes]:
    """Stream the visible files of ``workspace`` as a zip archive (sync iterator)."""
    sink = _ZipSink()
    root = workspace.resolve() if workspace.is_dir() else None
    with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        count = 0
        for path, st in _walk(root) if root else ():
            if stat.S_ISDIR(st.st_mode):
                continue
            count += 1
            if count > MAX_ZIP_FILES:
                break
            try:
                source = open(path, "rb")
            except OSError:
                continue
            info = zipfile.ZipInfo.from_file(path, path.relative_to(root).as_posix())
            info.compress_type = zipfile.ZIP_DEFLATED
            with source, archive.open(info, "w", force_zip64=True) as target:
                while chunk := source.read(ZIP_CHUNK_SIZE):
                    target.write(chunk)
                    if sink.buffered >= ZIP_CHUNK_SIZE:
                        yield sink.drain()
            if sink.buffered >= ZIP_CHUNK_SIZE:
                yield sink.drain()
    data = sink.drain()
    if data:
        yield data

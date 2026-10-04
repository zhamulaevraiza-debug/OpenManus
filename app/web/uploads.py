"""Streaming ``multipart/form-data`` upload parser.

File parts are written straight to a staging directory while the request body
arrives, so uploads never sit in memory and the per-file size limit is enforced
before the whole body has been received.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, BinaryIO, Dict, List, Optional, Tuple

from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import (
    MultipartParser,
    MultipartState,
    parse_options_header,
)


FILE_FIELDS = ("files", "file")
MAX_FIELD_BYTES = 4096
MAX_FIELDS = 8


class UploadError(Exception):
    """Invalid or oversized upload; carries the HTTP status to answer with."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class StagedFile:
    """An uploaded file written to the staging directory."""

    filename: str
    path: Path
    size: int = 0


@dataclass
class _Part:
    name: str = ""
    file: Optional[StagedFile] = None
    handle: Optional[BinaryIO] = None
    data: bytearray = field(default_factory=bytearray)


@dataclass
class ParsedUpload:
    files: List[StagedFile]
    fields: Dict[str, str]

    def discard(self) -> None:
        """Delete the staged files (after they were moved, this is a no-op)."""
        for staged in self.files:
            staged.path.unlink(missing_ok=True)


class _UploadCollector:
    """Parser callbacks; file bytes are queued and written outside the parser."""

    def __init__(self, staging_dir: Path, max_file_bytes: int, max_files: int):
        self.staging_dir = staging_dir
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files
        self.files: List[StagedFile] = []
        self.fields: Dict[str, str] = {}
        self.pending_writes: List[Tuple[BinaryIO, bytes]] = []
        self._handles: List[BinaryIO] = []
        self._part = _Part()
        self._header_name = b""
        self._header_value = b""
        self._disposition = b""
        self._field_count = 0

    # -- python-multipart callbacks
    def on_part_begin(self) -> None:
        self._part = _Part()
        self._disposition = b""

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_name += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def on_header_end(self) -> None:
        if self._header_name.strip().lower() == b"content-disposition":
            self._disposition = self._header_value
        self._header_name = self._header_value = b""

    def on_headers_finished(self) -> None:
        _, options = parse_options_header(self._disposition)
        if b"name" not in options:
            raise UploadError(400, "Malformed multipart part (no field name)")
        part = self._part
        part.name = options[b"name"].decode("utf-8", "replace")
        if b"filename" not in options:
            self._field_count += 1
            if self._field_count > MAX_FIELDS:
                raise UploadError(400, "Too many form fields")
            return
        if part.name not in FILE_FIELDS:
            raise UploadError(400, f"Unexpected file field '{part.name}'")
        if len(self.files) >= self.max_files:
            raise UploadError(400, f"At most {self.max_files} files per upload")
        path = self.staging_dir / uuid.uuid4().hex
        handle = open(path, "wb")
        self._handles.append(handle)
        part.file = StagedFile(options[b"filename"].decode("utf-8", "replace"), path)
        part.handle = handle
        self.files.append(part.file)

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        part = self._part
        chunk = data[start:end]
        if part.file is None:
            if len(part.data) + len(chunk) > MAX_FIELD_BYTES:
                raise UploadError(400, "Form field is too large")
            part.data.extend(chunk)
            return
        part.file.size += len(chunk)
        if part.file.size > self.max_file_bytes:
            limit_mb = self.max_file_bytes // (1024 * 1024)
            raise UploadError(413, f"File is larger than {limit_mb} MB")
        self.pending_writes.append((part.handle, chunk))

    def on_part_end(self) -> None:
        if self._part.file is None:
            self.fields[self._part.name] = self._part.data.decode("utf-8", "replace")

    # -- helpers
    def close_handles(self) -> None:
        for handle in self._handles:
            handle.close()
        self._handles.clear()

    def take_writes(self) -> List[Tuple[BinaryIO, bytes]]:
        writes, self.pending_writes = self.pending_writes, []
        return writes


def _write_all(writes: List[Tuple[BinaryIO, bytes]]) -> None:
    for handle, chunk in writes:
        handle.write(chunk)


async def receive_upload(
    content_type: str,
    body: AsyncIterator[bytes],
    staging_dir: Path,
    max_file_bytes: int,
    max_files: int,
) -> ParsedUpload:
    """Parse a multipart body, staging every file part on disk.

    Raises:
        UploadError: Not multipart, malformed, too many files/fields or a file over
            ``max_file_bytes`` (413). Staged files are removed in that case.
    """
    media_type, options = parse_options_header(content_type)
    boundary = options.get(b"boundary")
    if media_type != b"multipart/form-data" or not boundary:
        raise UploadError(400, "Expected a multipart/form-data body")

    staging_dir.mkdir(parents=True, exist_ok=True)
    collector = _UploadCollector(staging_dir, max_file_bytes, max_files)
    callbacks = {
        name: getattr(collector, name)
        for name in (
            "on_part_begin",
            "on_part_data",
            "on_part_end",
            "on_header_field",
            "on_header_value",
            "on_header_end",
            "on_headers_finished",
        )
    }
    parser = MultipartParser(boundary, callbacks)
    try:
        async for chunk in body:
            try:
                parser.write(chunk)
            except MultipartParseError:
                raise UploadError(400, "Malformed multipart body") from None
            writes = collector.take_writes()
            if writes:
                await asyncio.to_thread(_write_all, writes)
        parser.finalize()
        if parser.state != MultipartState.END:
            raise UploadError(400, "Incomplete multipart body")
    except BaseException:
        collector.close_handles()
        ParsedUpload(collector.files, {}).discard()
        raise
    collector.close_handles()
    if not collector.files:
        raise UploadError(400, "No files in the upload")
    for staged in collector.files:
        os.chmod(staged.path, 0o644)
    return ParsedUpload(collector.files, collector.fields)

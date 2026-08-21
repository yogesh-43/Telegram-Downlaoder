"""Download a Telegram file over several DC connections. Stock Telethon is one chunk at a time."""

from __future__ import annotations

import asyncio
import inspect
import math
import os
import time
from pathlib import Path
from typing import Any, Callable

from telethon import TelegramClient, utils
from telethon.errors import FileMigrateError, FloodError, TimedOutError
from telethon.network import MTProtoSender
from telethon.tl.functions.upload import GetFileRequest
from telethon.tl.types import upload as upload_types

PART_SIZE = 512 * 1024
MIN_PARALLEL_BYTES = PART_SIZE * 2
MAX_CONNECTIONS = 12
_SENDER_SLOTS = asyncio.Semaphore(MAX_CONNECTIONS)
_EXPORT_LOCK = asyncio.Lock()
_FLOOD_LOCK = asyncio.Lock()
_FLOOD_UNTIL = 0.0
_AUTH_KEYS: dict[int, Any] = {}

ProgressCb = Callable[[int, int], Any]


class DownloadAborted(Exception):
    """Raised when the queue UI cancels a job."""


class UseStockDownload(Exception):
    """CDN / migrate — Telethon's own downloader handles these."""


def connections_for_size(file_size: int) -> int:
    if file_size < MIN_PARALLEL_BYTES:
        return 1
    if file_size < 10 * 1024 * 1024:
        return 6
    if file_size < 50 * 1024 * 1024:
        return 8
    return MAX_CONNECTIONS


def part_plan(file_size: int, part_size: int, connections: int) -> list[tuple[int, int]]:
    """Per connection: (first_offset, how_many_parts). Earlier connections get the remainder."""
    part_count = max(1, math.ceil(file_size / part_size))
    connections = max(1, min(connections, part_count))
    base, extra = divmod(part_count, connections)
    plan: list[tuple[int, int]] = []
    for index in range(connections):
        count = base + (1 if index < extra else 0)
        plan.append((index * part_size, count))
    return plan


def part_path(dest: Path) -> Path:
    return Path(str(dest) + ".part")


async def _notify(callback: ProgressCb | None, received: int, total: int) -> None:
    if not callback:
        return
    result = callback(received, total)
    if inspect.isawaitable(result):
        await result


async def _open_sender(client: TelegramClient, dc_id: int, auth_key) -> MTProtoSender:
    dc = await client._get_dc(dc_id)
    sender = MTProtoSender(auth_key, loggers=client._log)
    await sender.connect(
        client._connection(
            dc.ip_address,
            dc.port,
            dc.id,
            loggers=client._log,
            proxy=client._proxy,
            local_addr=client._local_addr,
        )
    )
    return sender


async def _auth_key_for_dc(client: TelegramClient, dc_id: int):
    if dc_id == client.session.dc_id:
        return client.session.auth_key
    cached = _AUTH_KEYS.get(dc_id)
    if cached is not None:
        return cached
    async with _EXPORT_LOCK:
        cached = _AUTH_KEYS.get(dc_id)
        if cached is not None:
            return cached
        sender = await client._create_exported_sender(dc_id)
        _AUTH_KEYS[dc_id] = sender.auth_key
        await sender.disconnect()
        return _AUTH_KEYS[dc_id]


async def _wait_flood() -> None:
    delay = _FLOOD_UNTIL - time.time()
    if delay > 0:
        await asyncio.sleep(delay)


async def _note_flood(seconds: int) -> None:
    global _FLOOD_UNTIL
    wait = max(1, int(seconds or 3))
    async with _FLOOD_LOCK:
        _FLOOD_UNTIL = max(_FLOOD_UNTIL, time.time() + wait + 0.5)


async def _get_part(sender: MTProtoSender, location, offset: int, limit: int) -> bytes:
    # sender.send keeps flood-waits on this connection. client._call would freeze
    # every GetFileRequest on the whole client after one FLOOD_WAIT.
    request = GetFileRequest(location, offset=offset, limit=limit)
    last_error: Exception | None = None
    timeouts = 0
    floods = 0
    while timeouts < 4 and floods < 40:
        try:
            await _wait_flood()
            result = await sender.send(request)
            if isinstance(result, upload_types.FileCdnRedirect):
                raise UseStockDownload()
            return result.bytes
        except UseStockDownload:
            raise
        except FileMigrateError:
            raise
        except FloodError as exc:
            last_error = exc
            floods += 1
            wait = int(getattr(exc, "seconds", 3) or 3)
            await _note_flood(wait)
            await asyncio.sleep(wait + 0.5)
        except (TimedOutError, ConnectionError, OSError) as exc:
            last_error = exc
            timeouts += 1
            await asyncio.sleep(1)
    raise last_error or RuntimeError("download timed out")


async def _worker(
    client: TelegramClient,
    dc_id: int,
    auth_key,
    location,
    out,
    file_size: int,
    start_offset: int,
    part_count: int,
    stride: int,
    progress: dict[str, int],
    progress_callback: ProgressCb | None,
    should_abort: Callable[[], bool] | None,
) -> None:
    async with _SENDER_SLOTS:
        sender = await _open_sender(client, dc_id, auth_key)
        try:
            offset = start_offset
            for _ in range(part_count):
                if should_abort and should_abort():
                    raise DownloadAborted()
                if offset >= file_size:
                    return
                chunk = await _get_part(sender, location, offset, PART_SIZE)
                if not chunk:
                    return
                data = chunk[: file_size - offset]
                out.seek(offset)
                out.write(data)
                out.flush()
                offset += stride
                progress["received"] += len(data)
                await _notify(progress_callback, progress["received"], file_size)
        finally:
            await sender.disconnect()


async def _download_on_dc(
    client: TelegramClient,
    location,
    dest: Path,
    file_size: int,
    dc_id: int,
    connections: int,
    progress_callback: ProgressCb | None,
    should_abort: Callable[[], bool] | None,
) -> None:
    plan = part_plan(file_size, PART_SIZE, connections)
    stride = len(plan) * PART_SIZE
    auth_key = await _auth_key_for_dc(client, dc_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    progress = {"received": 0}
    with dest.open("wb", buffering=0) as out:
        try:
            async with asyncio.TaskGroup() as group:
                for start_offset, count in plan:
                    if count <= 0:
                        continue
                    group.create_task(
                        _worker(
                            client,
                            dc_id,
                            auth_key,
                            location,
                            out,
                            file_size,
                            start_offset,
                            count,
                            stride,
                            progress,
                            progress_callback,
                            should_abort,
                        )
                    )
        except ExceptionGroup as errors:
            for exc in errors.exceptions:
                if isinstance(exc, (FileMigrateError, DownloadAborted, UseStockDownload, FloodError)):
                    raise exc from None
            raise errors.exceptions[0]
        out.flush()
        os.fsync(out.fileno())


async def _parallel_download(
    client: TelegramClient,
    location,
    dest: Path,
    file_size: int,
    dc_id: int,
    connections: int,
    progress_callback: ProgressCb | None,
    should_abort: Callable[[], bool] | None,
) -> None:
    incomplete = part_path(dest)
    current_dc = dc_id
    last_error: Exception | None = None
    for _ in range(4):
        if incomplete.exists():
            incomplete.unlink()
        try:
            await _download_on_dc(
                client,
                location,
                incomplete,
                file_size,
                current_dc,
                connections,
                progress_callback,
                should_abort,
            )
            incomplete.replace(dest)
            await _notify(progress_callback, file_size, file_size)
            return
        except FileMigrateError as exc:
            last_error = exc
            current_dc = exc.new_dc
            _AUTH_KEYS.pop(current_dc, None)
        except FloodError as exc:
            last_error = exc
            wait = int(getattr(exc, "seconds", 3) or 3)
            await _note_flood(wait)
            await asyncio.sleep(wait + 1)
        except (DownloadAborted, UseStockDownload):
            if incomplete.exists():
                incomplete.unlink()
            raise
    raise last_error or RuntimeError("download failed")


async def _stock_download(
    client: TelegramClient,
    message,
    dest: Path,
    file_size: int,
    progress_callback: ProgressCb | None,
) -> None:
    dc_id, location = utils.get_input_location(message)
    dest.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {
        "file_size": file_size or None,
        "part_size_kb": 512,
        "progress_callback": progress_callback,
    }
    if dc_id:
        kwargs["dc_id"] = dc_id
    await client.download_file(location, str(dest), **kwargs)
    with dest.open("rb") as saved:
        os.fsync(saved.fileno())


async def download_message_media(
    client: TelegramClient,
    message,
    dest: str | Path,
    *,
    file_size: int = 0,
    progress_callback: ProgressCb | None = None,
    should_abort: Callable[[], bool] | None = None,
) -> None:
    dest = Path(dest)
    size = file_size or int(getattr(getattr(message, "file", None), "size", 0) or 0)
    connections = connections_for_size(size)
    dc_id, location = utils.get_input_location(message)
    dc_id = dc_id or client.session.dc_id
    if connections <= 1 or not size:
        await _stock_download(client, message, dest, size, progress_callback)
        return

    try:
        await _parallel_download(
            client,
            location,
            dest,
            size,
            dc_id,
            connections,
            progress_callback,
            should_abort,
        )
    except UseStockDownload:
        incomplete = part_path(dest)
        if incomplete.exists():
            incomplete.unlink()
        if dest.exists():
            dest.unlink()
        await _stock_download(client, message, dest, size, progress_callback)

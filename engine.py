"""Telegram session, media scan, and download queue. Official Telethon API only."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from telethon import TelegramClient, utils as tl_utils
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.errors import (
    ApiIdInvalidError,
    ApiIdPublishedFloodError,
    AuthKeyDuplicatedError,
    AuthKeyInvalidError,
    AuthKeyUnregisteredError,
    FileReferenceExpiredError,
    FloodError,
    FloodWaitError,
    PhoneNumberBannedError,
    PhoneNumberFloodError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.tl.types import (
    InputMessagesFilterDocument,
    InputMessagesFilterGif,
    InputMessagesFilterMusic,
    InputMessagesFilterPhotos,
    InputMessagesFilterRoundVideo,
    InputMessagesFilterVideo,
    InputMessagesFilterVoice,
)

from fast_download import DownloadAborted, download_message_media
from media_utils import (
    dated_name,
    index_saved_files,
    list_downloaded_files,
    media_fingerprint,
    media_stem_id,
    normalize_phone,
    parse_chat_ref,
    resolve_local_file,
    sanitize_filename,
    saved_path,
    unique_path,
    unique_size_in_folder,
)

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = DATA_DIR / "config.json"
SESSION_PATH = DATA_DIR / "tg_session"
DEFAULT_DOWNLOADS = Path.home() / "Downloads" / "TG Saver"

FILTER_BY_KIND = {
    "photo": InputMessagesFilterPhotos,
    "video": InputMessagesFilterVideo,
    "document": InputMessagesFilterDocument,
    "audio": InputMessagesFilterMusic,
    "voice": InputMessagesFilterVoice,
    "gif": InputMessagesFilterGif,
    "video_note": InputMessagesFilterRoundVideo,
}

DEFAULT_CONFIG = {
    "api_id": 0,
    "api_hash": "",
    "download_dir": str(DEFAULT_DOWNLOADS),
    "concurrency": 3,
    "prefix_date": True,
    "scan_limit": 300,
}


def load_config() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    load_dotenv(ROOT / ".env")
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    env_id = os.getenv("API_ID", "").strip()
    env_hash = os.getenv("API_HASH", "").strip()
    if env_id.isdigit() and not cfg.get("api_id"):
        cfg["api_id"] = int(env_id)
    if env_hash and not cfg.get("api_hash"):
        cfg["api_hash"] = env_hash
    old_app_dir = (ROOT / "downloads").resolve()
    try:
        if Path(cfg["download_dir"]).resolve() == old_app_dir:
            cfg["download_dir"] = str(DEFAULT_DOWNLOADS)
            save_config(cfg)
    except OSError:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def kind_of(message) -> str | None:
    if not message or not message.media:
        return None
    if message.sticker:
        return "sticker"
    if message.gif:
        return "gif"
    if message.voice:
        return "voice"
    if message.video_note:
        return "video_note"
    if message.video:
        return "video"
    if message.audio:
        return "audio"
    if message.photo:
        return "photo"
    if message.document:
        return "document"
    return None


def _file_size(message) -> int:
    f = getattr(message, "file", None)
    if f is not None and getattr(f, "size", None):
        return int(f.size)
    doc = getattr(message, "document", None)
    if doc is not None and getattr(doc, "size", None):
        return int(doc.size)
    return 0


def _original_name(message) -> str | None:
    f = getattr(message, "file", None)
    name = getattr(f, "name", None) if f is not None else None
    return name or None


def serialize_message(message, chat_title: str) -> dict[str, Any]:
    kind = kind_of(message) or "document"
    date = message.date or datetime.now(timezone.utc)
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    size = _file_size(message)
    stem_id = media_stem_id(message)
    filename = dated_name(kind, stem_id, date, _original_name(message), prefix_date=False)
    return {
        "id": message.id,
        "kind": kind,
        "filename": filename,
        "size": size,
        "stem_id": stem_id,
        "date": date.isoformat(),
        "duration": getattr(getattr(message, "file", None), "duration", None),
        "text": (message.message or "")[:180],
        "chat_title": chat_title,
    }


def login_error_message(exc: BaseException) -> str:
    if isinstance(exc, PhoneNumberInvalidError):
        return "That phone number is not valid. Include the country code, for example +9198XXXXXXXX."
    if isinstance(exc, PhoneNumberBannedError):
        return (
            "Telegram blocked this login from this machine. Cloud VMs (AWS, GCP, Azure) are often blocked. "
            "Log in on your own PC, then copy data/tg_session.session onto the server."
        )
    if isinstance(exc, PhoneNumberFloodError):
        return "Too many login attempts. Wait a few hours, then try once."
    if isinstance(exc, FloodWaitError):
        return f"Telegram asked us to wait {exc.seconds} seconds before sending another code."
    if isinstance(exc, ApiIdInvalidError):
        return "API ID or API Hash is invalid. Create an app at https://my.telegram.org/apps."
    if isinstance(exc, ApiIdPublishedFloodError):
        return "This API ID is public and blocked. Create a new app at https://my.telegram.org/apps."
    text = str(exc) or exc.__class__.__name__
    if "database is locked" in text.lower():
        return "Another TG Saver process is using the session. Stop the other python app.py and try again."
    return text


class TelegramEngine:
    def __init__(self) -> None:
        self.config = load_config()
        self.client: TelegramClient | None = None
        self.phone: str | None = None
        self.phone_code_hash: str | None = None
        self.me: dict[str, Any] | None = None
        self.jobs: dict[str, dict[str, Any]] = {}
        self._queue_lock = asyncio.Lock()
        self._path_lock = asyncio.Lock()
        self._reserved_paths: set[str] = set()
        self._busy_media: set[str] = set()
        self._saved_media: set[str] = set()
        self._active = 0

    async def status(self) -> dict[str, Any]:
        ready = bool(self.client and self.client.is_connected() and await self.client.is_user_authorized())
        return {
            "ready": ready,
            "has_api": bool(self.config.get("api_id") and self.config.get("api_hash")),
            "user": self.me,
            "settings": {
                "download_dir": self.config["download_dir"],
                "concurrency": int(self.config["concurrency"]),
                "prefix_date": bool(self.config["prefix_date"]),
                "scan_limit": int(self.config["scan_limit"]),
                "api_id": int(self.config.get("api_id") or 0) or "",
                "api_hash": str(self.config.get("api_hash") or ""),
            },
            "queue": self.queue_snapshot(),
            "folder": str(self._download_root()),
            "saved": len(self.list_local_files()),
        }

    def save_settings(self, **fields: Any) -> dict:
        allowed = {"download_dir", "concurrency", "prefix_date", "scan_limit", "api_id", "api_hash"}
        for key, value in fields.items():
            if key in allowed and value is not None:
                self.config[key] = value
        save_config(self.config)
        return self.config

    async def replace_api(self, api_id: int, api_hash: str) -> None:
        api_hash = api_hash.strip()
        changed = int(self.config.get("api_id") or 0) != api_id or str(self.config.get("api_hash") or "") != api_hash
        if changed:
            await self._wipe_session()
        elif self.client:
            await self.client.disconnect()
            self.client = None
        self.save_settings(api_id=api_id, api_hash=api_hash)

    async def _ensure_client(self) -> TelegramClient:
        api_id = int(self.config.get("api_id") or 0)
        api_hash = str(self.config.get("api_hash") or "")
        if not api_id or not api_hash:
            raise RuntimeError("Set API ID and API Hash from https://my.telegram.org/apps first.")
        if self.client is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self.client = TelegramClient(
                str(SESSION_PATH),
                api_id,
                api_hash,
                flood_sleep_threshold=24,
            )
        if not self.client.is_connected():
            await self.client.connect()
        return self.client

    async def connect_existing(self) -> bool:
        try:
            client = await self._ensure_client()
        except RuntimeError:
            return False
        if await client.is_user_authorized():
            await self._store_me()
            return True
        return False

    async def send_code(self, phone: str) -> str:
        try:
            self.phone = normalize_phone(phone)
            return await self._send_code_once()
        except (AuthKeyUnregisteredError, AuthKeyDuplicatedError, AuthKeyInvalidError):
            await self._wipe_session()
            try:
                return await self._send_code_once()
            except Exception as exc:
                raise RuntimeError(login_error_message(exc)) from exc
        except Exception as exc:
            raise RuntimeError(login_error_message(exc)) from exc

    async def _send_code_once(self) -> str:
        client = await self._ensure_client()
        if await client.is_user_authorized():
            await self._store_me()
            return "ready"
        # A second click would use Telethon's deprecated ResendCodeRequest and 400.
        getattr(client, "_phone_code_hash", {}).pop(self.phone, None)
        result = await client.send_code_request(self.phone)
        self.phone_code_hash = result.phone_code_hash
        return "code_sent"

    async def _wipe_session(self) -> None:
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None
        self.me = None
        for suffix in ("", "-journal"):
            path = Path(str(SESSION_PATH) + ".session" + suffix)
            if path.exists():
                path.unlink()

    async def verify_code(self, code: str) -> str:
        client = await self._ensure_client()
        if not self.phone or not self.phone_code_hash:
            raise RuntimeError("Request a login code first.")
        try:
            await client.sign_in(self.phone, code.strip(), phone_code_hash=self.phone_code_hash)
        except SessionPasswordNeededError:
            return "need_password"
        await self._store_me()
        return "ready"

    async def verify_password(self, password: str) -> str:
        client = await self._ensure_client()
        await client.sign_in(password=password)
        await self._store_me()
        return "ready"

    async def _store_me(self) -> None:
        user = await self.client.get_me()
        self.me = {
            "id": user.id,
            "name": " ".join(p for p in (user.first_name, user.last_name) if p) or user.username or "Account",
            "username": user.username,
            "phone": user.phone,
        }

    async def logout(self) -> None:
        await self._wipe_session()

    async def list_chats(self, query: str = "", limit: int = 80) -> list[dict]:
        client = await self._ensure_client()
        q = (query or "").lower().strip()
        chats: list[dict] = []
        async for dialog in client.iter_dialogs(limit=250):
            title = dialog.name or ""
            username = getattr(dialog.entity, "username", None)
            if getattr(dialog.entity, "forum", False) or dialog.is_group:
                kind = "group"
            elif dialog.is_channel:
                kind = "channel"
            else:
                kind = "user"
            chats.append(
                {
                    "id": dialog.id,
                    "title": title,
                    "username": username,
                    "kind": kind,
                    "forum": bool(getattr(dialog.entity, "forum", False)),
                    "ref": f"@{username}" if username else str(dialog.id),
                    "topics": [],
                }
            )
        chats.sort(key=lambda row: {"group": 0, "channel": 1, "user": 2}.get(row["kind"], 9))
        pool = chats if q else chats[:limit]
        for row in pool:
            if row["forum"]:
                row["topics"] = await self._forum_topics(row["id"])
        if q:
            filtered = []
            for row in pool:
                title_hit = q in row["title"].lower() or q in str(row.get("username") or "").lower()
                topics = [t for t in row["topics"] if q in t["title"].lower()]
                if title_hit:
                    filtered.append(row)
                elif topics:
                    filtered.append({**row, "topics": topics})
            return filtered[:limit]
        return pool

    async def _forum_topics(self, chat_id: int) -> list[dict]:
        try:
            result = await self.client(
                GetForumTopicsRequest(
                    peer=chat_id,
                    offset_date=datetime(1970, 1, 1, tzinfo=timezone.utc),
                    offset_id=0,
                    offset_topic=0,
                    limit=100,
                )
            )
        except Exception:
            return []
        topics = []
        for topic in result.topics:
            title = getattr(topic, "title", None)
            if not title or getattr(topic, "hidden", False):
                continue
            topics.append(
                {
                    "id": topic.id,
                    "title": title,
                    "ref": f"{chat_id}_{topic.id}",
                }
            )
        return topics

    async def resolve_chat(self, text: str):
        client = await self._ensure_client()
        ref = parse_chat_ref(text)
        entity = await client.get_entity(ref.entity)
        title = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(ref.entity)
        if ref.topic_id:
            peer_id = tl_utils.get_peer_id(entity)
            topic_title = next((t["title"] for t in await self._forum_topics(peer_id) if t["id"] == ref.topic_id), None)
            if topic_title:
                title = f"{title} - {topic_title}"
        return entity, title, ref

    async def scan(self, chat: str, kinds: list[str], limit: int) -> dict[str, Any]:
        entity, title, ref = await self.resolve_chat(chat)
        wanted = [k for k in kinds if k in FILTER_BY_KIND] or list(FILTER_BY_KIND)
        items_by_file: dict[str, dict] = {}

        async def collect(kind: str) -> None:
            kwargs: dict[str, Any] = {"limit": limit, "filter": FILTER_BY_KIND[kind]()}
            if ref.topic_id:
                kwargs["reply_to"] = ref.topic_id
            async for message in self.client.iter_messages(entity, **kwargs):
                found = kind_of(message)
                if found != kind:
                    continue
                key = media_fingerprint(message)
                prev = items_by_file.get(key)
                if prev is None or message.id > prev["id"]:
                    items_by_file[key] = serialize_message(message, title)

        await asyncio.gather(*(collect(kind) for kind in wanted))
        items = sorted(items_by_file.values(), key=lambda row: row["id"], reverse=True)
        folder = Path(self.config["download_dir"]) / sanitize_filename(title)
        index = index_saved_files(self._download_root())
        prefix = bool(self.config.get("prefix_date"))
        for item in items:
            name = dated_name(
                item["kind"],
                item["stem_id"],
                datetime.fromisoformat(item["date"]),
                item["filename"],
                prefix_date=prefix,
            )
            item["saved"] = self._existing_copy(folder, name, item["size"], item["stem_id"], index) is not None
        return {"chat": title, "chat_id": getattr(entity, "id", None), "count": len(items), "items": items}

    def queue_snapshot(self) -> list[dict[str, Any]]:
        active = [job for job in self.jobs.values() if job["status"] not in {"done", "skipped"}]
        return sorted(active, key=lambda j: j["created_at"], reverse=True)

    def queue_view(self) -> dict[str, Any]:
        return {
            "queue": self.queue_snapshot(),
            "folder": str(self._download_root()),
            "saved": len(self.list_local_files()),
        }

    def _download_root(self) -> Path:
        return Path(self.config["download_dir"])

    def list_local_files(self) -> list[dict[str, Any]]:
        writing = {
            job["path"]
            for job in self.jobs.values()
            if job.get("path") and job["status"] == "downloading"
        }
        return list_downloaded_files(self._download_root(), writing)

    def resolve_saved_file(self, relative: str) -> Path:
        return resolve_local_file(self._download_root(), relative)

    def open_download_folder(self) -> str:
        folder = self._download_root()
        folder.mkdir(parents=True, exist_ok=True)
        opener = shutil.which("xdg-open") or shutil.which("open")
        if opener:
            subprocess.Popen(
                [opener, str(folder)],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return str(folder)

    def _existing_copy(self, folder: Path, filename: str, size: int, stem_id: int, index: dict | None = None):
        index = index if index is not None else index_saved_files(self._download_root())
        return saved_path(index, filename, size, stem_id) or unique_size_in_folder(folder, size)

    async def enqueue(self, chat: str, message_ids: list[int]) -> tuple[list[str], int]:
        if not message_ids:
            raise ValueError("Select at least one file.")
        entity, title, _ref = await self.resolve_chat(chat)
        folder = Path(self.config["download_dir"]) / sanitize_filename(title)
        inflight = {
            job["message_id"]
            for job in self.jobs.values()
            if job["status"] in {"queued", "downloading"} and job.get("chat_ref") == chat
        }
        wanted: list[int] = []
        seen: set[int] = set()
        for msg_id in message_ids:
            if msg_id in seen or msg_id in inflight:
                continue
            seen.add(msg_id)
            wanted.append(msg_id)
        messages = await self.client.get_messages(entity, ids=wanted) if wanted else []
        if messages is None:
            messages = []
        elif not isinstance(messages, list):
            messages = [messages]
        index = index_saved_files(self._download_root())
        prefix = bool(self.config.get("prefix_date"))
        job_ids: list[str] = []
        skipped = 0
        for message in messages:
            if not message or not getattr(message, "media", None):
                continue
            kind = kind_of(message) or "document"
            date = message.date or datetime.now(timezone.utc)
            stem_id = media_stem_id(message)
            size = _file_size(message)
            filename = dated_name(kind, stem_id, date, _original_name(message), prefix_date=prefix)
            if self._existing_copy(folder, filename, size, stem_id, index):
                skipped += 1
                continue
            job_id = uuid.uuid4().hex[:12]
            self.jobs[job_id] = {
                "id": job_id,
                "chat": title,
                "chat_ref": chat,
                "message_id": message.id,
                "filename": filename,
                "status": "queued",
                "received": 0,
                "total": size,
                "error": None,
                "path": None,
                "speed": 0.0,
                "created_at": time.time(),
            }
            job_ids.append(job_id)
            asyncio.create_task(self._run_job(job_id, entity, folder, message.id), name=f"dl-{job_id}")
        return job_ids, skipped

    async def cancel(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job and job["status"] in {"queued", "downloading"}:
            job["status"] = "cancelled"

    async def _run_job(self, job_id: str, entity, folder: Path, message_id: int) -> None:
        job = self.jobs[job_id]
        concurrency = max(1, min(8, int(self.config.get("concurrency") or 3)))
        while True:
            if job["status"] == "cancelled":
                return
            async with self._queue_lock:
                if self._active < concurrency:
                    self._active += 1
                    break
            await asyncio.sleep(0.15)
        job["status"] = "downloading"
        last_t = time.time()
        last_bytes = 0

        def on_progress(received: int, total: int) -> None:
            nonlocal last_t, last_bytes
            if job["status"] == "cancelled":
                raise DownloadAborted()
            job["received"] = received
            job["total"] = total or job["total"]
            now = time.time()
            dt = now - last_t
            if dt >= 0.2:
                job["speed"] = (received - last_bytes) / dt
                last_t = now
                last_bytes = received

        dest: Path | None = None
        media_key: str | None = None
        try:
            message = await self.client.get_messages(entity, ids=message_id)
            if not message or not message.media:
                raise RuntimeError("Message has no downloadable media.")
            kind = kind_of(message) or "document"
            date = message.date or datetime.now(timezone.utc)
            filename = dated_name(
                kind,
                media_stem_id(message),
                date,
                _original_name(message),
                prefix_date=bool(self.config.get("prefix_date")),
            )
            job["filename"] = filename
            media_key = media_fingerprint(message)
            size = _file_size(message)
            existing = self._existing_copy(folder, filename, size, media_stem_id(message))
            async with self._path_lock:
                if media_key in self._busy_media or media_key in self._saved_media or existing:
                    if existing:
                        job["path"] = str(existing)
                        job["received"] = existing.stat().st_size
                        job["total"] = job["received"]
                    job["status"] = "skipped"
                    self._saved_media.add(media_key)
                    return
                dest, state = unique_path(
                    folder,
                    filename,
                    expected_size=size or None,
                    reserved=self._reserved_paths,
                )
                job["path"] = str(dest)
                job["total"] = size
                if state == "exists":
                    self._saved_media.add(media_key)
                    job["received"] = dest.stat().st_size
                    job["status"] = "skipped"
                    return
                self._busy_media.add(media_key)
                self._reserved_paths.add(str(dest))
            kwargs = {
                "file_size": job["total"] or size,
                "progress_callback": on_progress,
                "should_abort": lambda: job["status"] == "cancelled",
            }
            while True:
                try:
                    await download_message_media(self.client, message, dest, **kwargs)
                    break
                except FileReferenceExpiredError:
                    message = await self.client.get_messages(entity, ids=message_id)
                    kwargs["file_size"] = _file_size(message) or kwargs["file_size"]
                except FloodError as exc:
                    if job["status"] == "cancelled":
                        return
                    wait = int(getattr(exc, "seconds", 3) or 3)
                    job["error"] = f"Telegram pause {wait}s, then retry"
                    await asyncio.sleep(wait + 1)
                    job["error"] = None
            if job["status"] == "cancelled":
                return
            if not dest.exists() or dest.stat().st_size <= 0:
                job["status"] = "error"
                job["error"] = "Download finished but the file was not saved."
                return
            job["received"] = dest.stat().st_size
            job["total"] = job["received"]
            job["path"] = str(dest)
            job["speed"] = 0.0
            job["status"] = "done"
            self._saved_media.add(media_key)
        except DownloadAborted:
            job["status"] = "cancelled"
        except Exception as exc:  # noqa: BLE001 — surface any Telethon/IO failure in the queue UI
            if job["status"] != "cancelled":
                job["status"] = "error"
                job["error"] = str(exc)
        finally:
            async with self._path_lock:
                if media_key:
                    self._busy_media.discard(media_key)
                if dest is not None:
                    self._reserved_paths.discard(str(dest))
            async with self._queue_lock:
                self._active = max(0, self._active - 1)
            job = self.jobs.get(job_id)
            if job and job["status"] in {"done", "skipped"}:
                self.jobs.pop(job_id, None)

    async def close(self) -> None:
        if self.client and self.client.is_connected():
            await self.client.disconnect()

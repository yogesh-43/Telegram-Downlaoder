"""Filename, chat-link, and media-type helpers. No Telegram I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_SPACE = re.compile(r"\s+")
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}_")
_STEM_ID = re.compile(r"(?:video|photo|audio|file|gif|voice|videonote)_(\d+)$", re.I)

KIND_FILTERS = ("photo", "video", "audio", "document", "voice", "gif", "video_note")

KIND_EXTENSION = {
    "photo": ".jpg",
    "video": ".mp4",
    "audio": ".mp3",
    "voice": ".ogg",
    "gif": ".mp4",
    "video_note": ".mp4",
    "document": "",
    "sticker": ".webp",
}


@dataclass(frozen=True)
class ChatRef:
    raw: str
    entity: str | int
    topic_id: int | None = None


def sanitize_filename(name: str, max_len: int = 180) -> str:
    cleaned = _UNSAFE.sub("_", (name or "").strip())
    cleaned = _MULTI_SPACE.sub(" ", cleaned).strip(" .")
    if not cleaned:
        cleaned = "telegram_file"
    if len(cleaned) > max_len:
        stem, ext = Path(cleaned).stem, Path(cleaned).suffix
        cleaned = stem[: max_len - len(ext)] + ext
    return cleaned


def normalize_phone(phone: str) -> str:
    """Digits-only phone. Telegram needs the country code, e.g. +9198XXXXXXXX."""
    raw = (phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not raw.startswith("+") or len(digits) < 10:
        raise ValueError("Enter the number with country code, for example +9198XXXXXXXX.")
    return digits


def media_stem_id(message) -> int:
    """Stable id for filenames so the same Telegram file keeps one name."""
    doc = getattr(message, "document", None)
    doc_id = getattr(doc, "id", None)
    if doc_id:
        return int(doc_id)
    photo = getattr(message, "photo", None)
    photo_id = getattr(photo, "id", None)
    if photo_id:
        return int(photo_id)
    return int(getattr(message, "id", 0) or 0)


def media_fingerprint(message) -> str:
    """Same Telegram file across messages shares this key."""
    doc = getattr(message, "document", None)
    doc_id = getattr(doc, "id", None)
    if doc_id:
        return f"doc:{doc_id}"
    photo = getattr(message, "photo", None)
    photo_id = getattr(photo, "id", None)
    if photo_id:
        return f"photo:{photo_id}"
    return f"msg:{getattr(message, 'id', 0)}"


def _match_keys(name: str, size: int) -> list[str]:
    """Keys so '2025-12-08_clip.mp4' and 'clip.mp4' of the same size collide."""
    n = Path(name).name.lower()
    keys = [f"{size}:{n}"]
    if _DATE_PREFIX.match(n):
        keys.append(f"{size}:{n[11:]}")
    found = _STEM_ID.search(Path(n).stem)
    if found:
        keys.append(f"{size}:id:{found.group(1)}")
    return keys


def index_saved_files(root: Path) -> dict[str, Path]:
    """Map size+name keys to complete files already on disk."""
    index: dict[str, Path] = {}
    if not root.exists():
        return index
    for path in root.rglob("*"):
        if not path.is_file() or path.name.startswith(".") or path.name.endswith(".part"):
            continue
        size = path.stat().st_size
        if size <= 0:
            continue
        for key in _match_keys(path.name, size):
            index.setdefault(key, path)
    return index


def saved_path(index: dict[str, Path], filename: str, size: int, stem_id: int | None = None) -> Path | None:
    """Return an existing complete copy, or None."""
    if not size or not index:
        return None
    for key in _match_keys(filename, size):
        if key in index:
            return index[key]
    if stem_id:
        return index.get(f"{size}:id:{stem_id}")
    return None


def unique_size_in_folder(folder: Path, size: int) -> Path | None:
    """If this folder has exactly one complete file of this size, it is the same media."""
    if not size or not folder.exists():
        return None
    hits: list[Path] = []
    for path in folder.rglob("*"):
        if not path.is_file() or path.name.startswith(".") or path.name.endswith(".part"):
            continue
        if path.stat().st_size != size:
            continue
        hits.append(path)
        if len(hits) > 1:
            return None
    return hits[0] if hits else None


def unique_path(
    folder: Path,
    filename: str,
    expected_size: int | None = None,
    reserved: set[str] | None = None,
) -> tuple[Path, str]:
    """Return a writable path. 'exists' means the file is already complete."""
    folder.mkdir(parents=True, exist_ok=True)
    reserved = reserved or set()
    stem, ext = Path(filename).stem, Path(filename).suffix

    def complete(path: Path) -> bool:
        if not path.exists():
            return False
        size = path.stat().st_size
        if expected_size and size == expected_size:
            return True
        return expected_size is None and size > 0

    def occupied(path: Path) -> bool:
        return str(path) in reserved or path.exists()

    n = 1
    while True:
        path = folder / filename if n == 1 else folder / f"{stem} ({n}){ext}"
        if complete(path):
            return path, "exists"
        if not occupied(path):
            return path, "new"
        n += 1


def list_downloaded_files(root: Path, skip_paths: set[str] | None = None) -> list[dict]:
    """Files already on disk under the download folder, newest first."""
    skip_paths = skip_paths or set()
    if not root.exists():
        return []
    files: list[dict] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name.startswith(".") or path.name.endswith(".part"):
            continue
        if str(path) in skip_paths:
            continue
        size = path.stat().st_size
        if size <= 0:
            continue
        relative = path.relative_to(root)
        files.append(
            {
                "name": path.name,
                "chat": relative.parts[0] if len(relative.parts) > 1 else "",
                "size": size,
                "mtime": path.stat().st_mtime,
                "path": relative.as_posix(),
            }
        )
    files.sort(key=lambda row: row["mtime"], reverse=True)
    return files


def resolve_local_file(root: Path, relative: str) -> Path:
    """Return a file under root, or raise FileNotFoundError for missing/escaped paths."""
    if not relative or Path(relative).is_absolute():
        raise FileNotFoundError(relative)
    base = root.resolve()
    path = (base / relative).resolve()
    if not path.is_file() or not path.is_relative_to(base):
        raise FileNotFoundError(relative)
    return path


def parse_chat_ref(text: str) -> ChatRef:
    """Turn @name, t.me links, web.telegram.org URLs, or numeric IDs into a Telethon entity."""
    s = (text or "").strip()
    if not s:
        raise ValueError("Enter a channel username, invite link, or chat ID.")

    if "web.telegram.org" in s:
        s = _from_web_telegram(s)

    if "t.me/" in s or "telegram.me/" in s:
        return _from_tme(s)

    if s.startswith("@"):
        return ChatRef(raw=text, entity=s)

    if "_" in s:
        left, right = s.rsplit("_", 1)
        if right.isdigit() and _looks_like_id(left):
            return ChatRef(raw=text, entity=_normalize_channel_id(left), topic_id=int(right))

    if _looks_like_id(s):
        return ChatRef(raw=text, entity=_normalize_channel_id(s))

    return ChatRef(raw=text, entity=s.lstrip("@"))


def _from_web_telegram(url: str) -> str:
    fragment = url.split("#", 1)[-1] if "#" in url else url
    if "p=" in fragment:
        fragment = fragment.split("p=", 1)[-1].split("&", 1)[0]
    fragment = fragment.split("/")[-1]
    return fragment


def _from_tme(url: str) -> ChatRef:
    path = url.split("t.me/", 1)[-1].split("telegram.me/", 1)[-1]
    path = path.split("?", 1)[0].strip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        raise ValueError("Could not parse that Telegram link.")

    if parts[0] in {"c"} and len(parts) >= 2:
        channel_id = _normalize_channel_id(parts[1])
        topic_id = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else None
        return ChatRef(raw=url, entity=channel_id, topic_id=topic_id)

    if parts[0] in {"+", "joinchat"} or parts[0].startswith("+"):
        return ChatRef(raw=url, entity=url if url.startswith("http") else f"https://t.me/{path}")

    username = parts[0].lstrip("@")
    topic_id = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
    return ChatRef(raw=url, entity=username, topic_id=topic_id)


def _looks_like_id(value: str) -> bool:
    v = value.strip()
    if v.startswith("-") and v[1:].isdigit():
        return True
    return v.isdigit()


def _normalize_channel_id(value: str) -> int:
    raw = str(value).strip()
    negative = raw.startswith("-")
    digits = raw[1:] if negative else raw
    if not digits.isdigit():
        raise ValueError(f"Not a numeric chat ID: {value}")
    number = int(digits)
    if negative:
        return -number
    # Public/private channel IDs from t.me/c/ are stored as -100{id}
    if digits.startswith("100") and len(digits) >= 12:
        return -number
    if len(digits) >= 8:
        return int(f"-100{digits}")
    return number


def dated_name(kind: str, message_id: int, date: datetime, original: str | None, prefix_date: bool) -> str:
    ext = Path(original).suffix if original else KIND_EXTENSION.get(kind, "")
    if original and Path(original).stem:
        base = sanitize_filename(original)
    else:
        label = {
            "photo": "Photo",
            "video": "Video",
            "audio": "Audio",
            "voice": "Voice",
            "gif": "GIF",
            "video_note": "VideoNote",
            "document": "File",
            "sticker": "Sticker",
        }.get(kind, "File")
        base = f"{label}_{message_id}{ext}"
    if prefix_date:
        return f"{date.strftime('%Y-%m-%d')}_{base}"
    return base

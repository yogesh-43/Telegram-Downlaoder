from datetime import datetime
from pathlib import Path

import pytest

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


def test_sanitize_strips_path_chars():
    assert sanitize_filename('a/b\\c:d*.mp4') == "a_b_c_d_.mp4"
    assert sanitize_filename("   ") == "telegram_file"


def test_normalize_phone_requires_country_code():
    assert normalize_phone("+91 98765 43210") == "919876543210"
    with pytest.raises(ValueError):
        normalize_phone("9876543210")


def test_parse_username_and_links():
    assert parse_chat_ref("@mychannel").entity == "@mychannel"
    ref = parse_chat_ref("https://t.me/c/1234567890/12")
    assert ref.entity == -1001234567890
    assert ref.topic_id == 12
    assert parse_chat_ref("https://t.me/durov").entity == "durov"


def test_parse_group_topic_ref():
    ref = parse_chat_ref("-1001234567890_12")
    assert ref.entity == -1001234567890
    assert ref.topic_id == 12


def test_parse_numeric_channel_id():
    assert parse_chat_ref("-1001234567890").entity == -1001234567890
    assert parse_chat_ref("1234567890").entity == -1001234567890


def test_unique_path_skips_complete_file(tmp_path: Path):
    existing = tmp_path / "clip.mp4"
    existing.write_bytes(b"12345")
    path, state = unique_path(tmp_path, "clip.mp4", expected_size=5)
    assert state == "exists"
    assert path == existing


def test_unique_path_renames_when_size_differs(tmp_path: Path):
    (tmp_path / "clip.mp4").write_bytes(b"xx")
    path, state = unique_path(tmp_path, "clip.mp4", expected_size=99)
    assert state == "new"
    assert path.name == "clip (2).mp4"


def test_unique_path_avoids_reserved_name(tmp_path: Path):
    reserved = {str(tmp_path / "clip.mp4")}
    path, state = unique_path(tmp_path, "clip.mp4", expected_size=99, reserved=reserved)
    assert state == "new"
    assert path.name == "clip (2).mp4"


def test_saved_path_matches_dated_or_plain_name(tmp_path: Path):
    folder = tmp_path / "Web Dev"
    folder.mkdir()
    existing = folder / "Week 23.2 Excalidraw.mp4"
    existing.write_bytes(b"12345")
    index = index_saved_files(tmp_path)
    assert saved_path(index, "2025-12-08_Week 23.2 Excalidraw.mp4", 5) == existing
    assert saved_path(index, "Video_99.mp4", 5, stem_id=99) is None
    (folder / "2025-12-08_Video_99.mp4").write_bytes(b"12345")
    index = index_saved_files(tmp_path)
    assert saved_path(index, "Video_99.mp4", 5, stem_id=99) is not None


def test_unique_size_in_folder(tmp_path: Path):
    folder = tmp_path / "chat"
    folder.mkdir()
    only = folder / "clip.mp4"
    only.write_bytes(b"abcdef")
    assert unique_size_in_folder(folder, 6) == only
    (folder / "other.mp4").write_bytes(b"abcdef")
    assert unique_size_in_folder(folder, 6) is None


def test_media_fingerprint_prefers_document_id():
    class Doc:
        id = 99

    class Msg:
        id = 1
        document = Doc()
        photo = None

    assert media_fingerprint(Msg()) == "doc:99"
    assert media_stem_id(Msg()) == 99


def test_dated_name_uses_original_and_date():
    name = dated_name("video", 9, datetime(2026, 8, 20), "Holiday.mp4", True)
    assert name == "2026-08-20_Holiday.mp4"
    assert dated_name("photo", 3, datetime(2026, 1, 1), None, False) == "Photo_3.jpg"


def test_list_downloaded_files_skips_active_and_empty(tmp_path: Path):
    chat = tmp_path / "Channel"
    chat.mkdir()
    saved = chat / "clip.mp4"
    saved.write_bytes(b"12345")
    writing = chat / "partial.mp4"
    writing.write_bytes(b"xx")
    (chat / "empty.mp4").write_bytes(b"")
    (chat / "clip.mp4.part").write_bytes(b"not finished")
    files = list_downloaded_files(tmp_path, skip_paths={str(writing)})
    assert [row["path"] for row in files] == ["Channel/clip.mp4"]
    assert files[0]["name"] == "clip.mp4"
    assert files[0]["chat"] == "Channel"
    assert files[0]["size"] == 5


def test_resolve_local_file_rejects_escape(tmp_path: Path):
    inside = tmp_path / "chat"
    inside.mkdir()
    target = inside / "a.mp4"
    target.write_bytes(b"ok")
    assert resolve_local_file(tmp_path, "chat/a.mp4") == target.resolve()
    with pytest.raises(FileNotFoundError):
        resolve_local_file(tmp_path, "../secret.txt")

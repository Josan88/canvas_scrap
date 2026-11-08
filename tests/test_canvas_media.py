import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main
from canvasync.utils import canvas_media
from canvasync.utils.canvas_media import find_media_embeds, needs_media_embed_retry
from canvasync.utils.obsidian import html_to_obsidian

VIDEO = "Week 9 Lecture-20250508_090230-Meeting Recording.mp4"
IFRAME = (
    '<p><strong>Recorded Video:</strong></p><p><iframe title="Video player for '
    f'{VIDEO}" data-media-type="video" '
    'src="https://canvas.example/media_attachments_iframe/1736772?verifier=abc" '
    'data-media-id="m-5nzKmUGQfVvWeLaRUK3hvNV1ueVuHQrq"></iframe></p>'
)


def test_find_media_embeds_dedupes_and_reads_title():
    html = IFRAME + IFRAME + '<iframe src="/media_objects_iframe/m-abcDEF123?type=video"></iframe>'
    assert find_media_embeds(html) == [
        {"key": "1736772", "title": VIDEO},
        {"key": "m-abcDEF123", "title": ""},
    ]


def test_find_media_embeds_ignores_youtube_and_file_links():
    html = (
        '<iframe src="https://www.youtube.com/embed/abcdefghijk"></iframe>'
        '<a href="/courses/1/files/99">notes.pdf</a>'
    )
    assert find_media_embeds(html) == []


def test_downloaded_video_is_embedded(tmp_path):
    (tmp_path / VIDEO).write_bytes(b"")
    md = html_to_obsidian(IFRAME, file_id_map={"1736772": VIDEO}, output_dir=str(tmp_path))
    assert f"![[{VIDEO}]]" in md
    assert "[Video: " in md and "media_attachments_iframe/1736772" in md


def test_missing_video_falls_back_to_player_link(tmp_path):
    md = html_to_obsidian(IFRAME, file_id_map={"1736772": VIDEO}, output_dir=str(tmp_path))
    assert "![[" not in md
    assert f"[Video: {VIDEO}](https://canvas.example/media_attachments_iframe/1736772" in md


def test_needs_media_embed_retry(tmp_path):
    md_path = tmp_path / "Week 8.md"
    md_path.write_text("**Recorded Video:**\n", encoding="utf-8")
    assert needs_media_embed_retry(IFRAME, str(md_path)) is True

    md_path.write_text("[Video: x](https://canvas.example/media_attachments_iframe/1736772)", encoding="utf-8")
    assert needs_media_embed_retry(IFRAME, str(md_path)) is False

    # Once the video is on disk, the note must embed it.
    (tmp_path / VIDEO).write_bytes(b"")
    assert needs_media_embed_retry(IFRAME, str(md_path), {"1736772": VIDEO}) is True
    md_path.write_text(f"![[{VIDEO}]] media_attachments_iframe/1736772", encoding="utf-8")
    assert needs_media_embed_retry(IFRAME, str(md_path), {"1736772": VIDEO}) is False


def test_download_embedded_media_uses_files_api(tmp_path, monkeypatch):
    processed = []
    monkeypatch.setattr(
        main,
        "process_canvas_file",
        lambda info, folder, *a, **k: processed.append((info["id"], info["display_name"], folder)),
    )

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": 1736772, "display_name": VIDEO, "url": "https://c/dl", "updated_at": "2026-02-25T00:32:23Z"}

    class Session:
        def __init__(self):
            self.urls = []

        def get(self, url, headers=None, timeout=None):
            self.urls.append(url)
            return Resp()

    session = Session()
    file_id_map = {}
    main.download_embedded_media(
        IFRAME + IFRAME, str(tmp_path), {}, "https://canvas.example/", {}, file_id_map, session=session
    )
    assert session.urls == ["https://canvas.example/api/v1/files/1736772"]
    assert processed == [(1736772, VIDEO, str(tmp_path))]
    assert file_id_map == {"1736772": VIDEO}


def test_download_embedded_media_respects_toggle(tmp_path, monkeypatch):
    monkeypatch.setattr(canvas_media, "_downloads_enabled", False)
    monkeypatch.setattr(main, "process_canvas_file", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    file_id_map = {}
    main.download_embedded_media(IFRAME, str(tmp_path), {}, "https://c", {}, file_id_map, session=object())
    assert file_id_map == {}


def test_media_object_embeds_keep_player_link_without_download(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "process_canvas_file", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    html = '<iframe title="Video player for Lec 1" src="https://c/media_objects_iframe/m-abc123?type=video"></iframe>'
    file_id_map = {}
    main.download_embedded_media(html, str(tmp_path), {}, "https://c", {}, file_id_map, session=object())
    assert file_id_map == {}
    md = html_to_obsidian(html, file_id_map=file_id_map, output_dir=str(tmp_path))
    assert md == "[Video: Lec 1](https://c/media_objects_iframe/m-abc123?type=video)"

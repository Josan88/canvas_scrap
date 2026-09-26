import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main


def test_announcements_export_markdown_and_files(tmp_path, monkeypatch):
    calls = []

    def fake_topic(*args, **kwargs):
        calls.append((args, kwargs))
        return 1

    monkeypatch.setattr(main, "process_canvas_discussion_topic", fake_topic)
    monkeypatch.setattr(
        main,
        "get_paginated_canvas_items",
        lambda *args, **kwargs: [
            {
                "id": 156711,
                "title": "Updated Working Solutions",
                "message": '<a href="/courses/1596/files/1960775">here</a>',
                "posted_at": "2026-09-23T07:30:07Z",
            }
        ],
    )
    monkeypatch.setattr(main, "get_existing_file_metadata_local", lambda *a, **k: {"mtime": 1})
    monkeypatch.setattr(main, "_should_regenerate_resource", lambda *a, **k: False)

    count = main.process_course_announcements(
        1596,
        "MTH20017",
        str(tmp_path),
        "https://canvas.example",
        {},
        announcements_folder_path=str(tmp_path / "Announcements"),
        processed_canvas_file_ids={},
    )

    assert count == 1
    topic, kwargs = calls[0][0][0], calls[0][1]
    assert topic["id"] == 156711
    assert kwargs["section_name"] == "Announcements"
    assert kwargs["force_regen"] is False


def test_linked_announcement_file_is_downloaded(tmp_path, monkeypatch):
    downloaded = []

    def fake_process_canvas_file(file_info, folder_path, *args, **kwargs):
        downloaded.append(
            (file_info.get("id"), file_info.get("display_name"), folder_path, kwargs.get("dest_label"))
        )
        return 1

    download_dir = tmp_path / "dl"
    download_dir.mkdir()
    monkeypatch.setattr(main, "process_canvas_file", fake_process_canvas_file)
    monkeypatch.setattr(main, "DOWNLOAD_DIR", str(download_dir))
    monkeypatch.setattr(main, "save_file_locally", lambda *a, **k: True)
    monkeypatch.setattr(main, "get_paginated_canvas_items", lambda *a, **k: [])
    monkeypatch.setattr(main, "needs_transcript_retry", lambda *a, **k: False)
    monkeypatch.setattr(main, "html_to_obsidian", lambda html, **k: "body")

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": 1960775,
                "display_name": "Working Solution.pdf",
                "url": "https://example/file",
                "updated_at": "2026-09-24T03:16:58Z",
            }

    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            assert "/files/1960775" in url
            return FakeResp()

    topic = {
        "id": 156711,
        "title": "Updated Working Solutions",
        "message": (
            '<p>Please refer to the updated working solutions '
            '<a href="https://swinburnesarawak.instructure.com/courses/1596/files/1960775'
            '?verifier=abc&wrap=1">here</a>.</p>'
        ),
        "user_name": "Esther",
        "posted_at": "2026-09-23T07:30:07Z",
        "attachments": [],
    }
    main.process_canvas_discussion_topic(
        topic,
        1596,
        str(tmp_path),
        {},
        "https://canvas.example/",
        {},
        session=FakeSession(),
        section_name="Announcements",
    )

    assert downloaded
    assert downloaded[0][0] == 1960775
    assert downloaded[0][1] == "Working Solution.pdf"
    assert downloaded[0][3].endswith("/Announcements/Updated Working Solutions")

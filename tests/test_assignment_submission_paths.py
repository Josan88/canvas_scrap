import os
import sys

# Ensure the project root is on sys.path so main can be imported.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main


def test_submission_attachments_saved_in_submission_subfolder(tmp_path, monkeypatch):
    download_dir = tmp_path / "temp_downloads"
    download_dir.mkdir()
    monkeypatch.setattr(main, "DOWNLOAD_DIR", str(download_dir))

    attachment_calls = []

    def fake_process_canvas_file(
        file_info,
        folder_path,
        processed_canvas_file_ids,
        canvas_headers,
        session=None,
        timeout=20,
        summary=None,
        course_name=None,
        dest_label=None,
    ):
        attachment_calls.append((file_info.get("display_name"), folder_path, dest_label))
        return 0

    saved_files = []

    def fake_save_file_locally(local_path, filename, folder_path):
        saved_files.append((filename, folder_path))
        return True

    monkeypatch.setattr(main, "process_canvas_file", fake_process_canvas_file)
    monkeypatch.setattr(main, "save_file_locally", fake_save_file_locally)

    assignment_info = {
        "name": "Essay 1",
        "description": None,
        "updated_at": "2026-01-01T00:00:00Z",
        "submission": {
            "workflow_state": "submitted",
            "attachments": [
                {
                    "id": 101,
                    "display_name": "essay_submission.pdf",
                    "url": "https://example.test/file/101",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
        },
    }

    main.process_canvas_assignment(
        assignment_info=assignment_info,
        assignments_root_path=str(tmp_path),
        processed_canvas_file_ids={},
        canvas_api_url="https://canvas.example.test",
        canvas_headers={},
        force_regen_assignments=False,
    )

    expected_assignment_path = str(tmp_path / "Essay 1")
    expected_submission_path = str(tmp_path / "Essay 1" / "submission")

    # Assignment markdown stays in the assignment root folder.
    assert ("Essay 1.md", expected_assignment_path) in saved_files

    # Submission attachment should be routed to a dedicated submission subfolder.
    assert attachment_calls == [
        (
            "essay_submission.pdf",
            expected_submission_path,
            "None/Assignments/Essay 1/submission",
        )
    ]


def test_no_attachments_means_no_submission_attachment_processing(tmp_path, monkeypatch):
    download_dir = tmp_path / "temp_downloads"
    download_dir.mkdir()
    monkeypatch.setattr(main, "DOWNLOAD_DIR", str(download_dir))

    attachment_calls = []

    def fake_process_canvas_file(
        file_info,
        folder_path,
        processed_canvas_file_ids,
        canvas_headers,
        session=None,
        timeout=20,
        summary=None,
        course_name=None,
        dest_label=None,
    ):
        attachment_calls.append((file_info.get("display_name"), folder_path, dest_label))
        return 0

    monkeypatch.setattr(main, "process_canvas_file", fake_process_canvas_file)
    monkeypatch.setattr(main, "save_file_locally", lambda *_args, **_kwargs: True)

    assignment_info = {
        "name": "Essay 2",
        "description": None,
        "updated_at": "2026-01-01T00:00:00Z",
        "submission": {
            "workflow_state": "submitted",
            "attachments": [],
        },
    }

    main.process_canvas_assignment(
        assignment_info=assignment_info,
        assignments_root_path=str(tmp_path),
        processed_canvas_file_ids={},
        canvas_api_url="https://canvas.example.test",
        canvas_headers={},
        force_regen_assignments=False,
    )

    assert attachment_calls == []

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main
from canvasync.utils import transcribe
from canvasync.utils.background import PDF, TRANSCRIBE, BackgroundJobs, background_jobs
from canvasync.utils.canvas_media import add_transcript_links
from canvasync.utils.obsidian import html_to_obsidian

VIDEO = "Lec 1.mp4"
TRANSCRIPT = "Lec 1_transcript.md"
IFRAME = (
    f'<p>Intro</p><iframe title="Video player for {VIDEO}" data-media-type="video" '
    'src="https://canvas.example/media_attachments_iframe/17?verifier=a"></iframe><p>After</p>'
)


@pytest.fixture
def jobs():
    runner = BackgroundJobs()
    runner.start()
    yield runner
    runner.stop()


def test_submit_runs_inline_until_started():
    runner = BackgroundJobs()
    ran = []
    assert runner.submit(PDF, lambda: ran.append(threading.current_thread().name))
    assert ran == [threading.current_thread().name]
    with pytest.raises(ValueError):
        runner.submit("gpu", lambda: None)


def test_jobs_of_one_kind_run_in_order_and_kinds_overlap(jobs):
    release = threading.Event()
    order = []
    jobs.submit(TRANSCRIBE, lambda: (release.wait(5), order.append("transcribe 1")))
    jobs.submit(TRANSCRIBE, lambda: order.append("transcribe 2"))
    # The PDF worker isn't stuck behind the blocked transcription.
    jobs.submit(PDF, lambda: (order.append("pdf"), release.set()))
    jobs.wait()
    assert order == ["pdf", "transcribe 1", "transcribe 2"]
    assert jobs.pending() == {PDF: 0, TRANSCRIBE: 0}


def test_duplicate_keys_are_dropped_and_failures_dont_stop_the_worker(jobs, capsys):
    ran = []
    assert jobs.submit(PDF, lambda: 1 / 0, key="a.pdf")
    assert not jobs.submit(PDF, lambda: ran.append("again"), key="a.pdf")
    assert jobs.submit(PDF, lambda: ran.append("b"), key="b.pdf")
    jobs.wait()
    assert ran == ["b"]
    assert "Background PDF extraction job failed" in capsys.readouterr().out


def test_add_transcript_links_matches_a_fresh_render(tmp_path):
    (tmp_path / VIDEO).write_bytes(b"")
    ids = {"17": VIDEO}
    note = tmp_path / "Week 1.md"
    note.write_bytes(html_to_obsidian(IFRAME, file_id_map=ids, output_dir=str(tmp_path)).replace("\n", "\r\n").encode())
    os.utime(note, (1_600_000_000, 1_600_000_000))
    other = tmp_path / "Other.md"
    other.write_text("no video here", encoding="utf-8")

    assert add_transcript_links(str(tmp_path / VIDEO)) == 0  # no transcript yet
    (tmp_path / TRANSCRIPT).write_text("---\nsource: x\n---\n", encoding="utf-8")
    assert add_transcript_links(str(tmp_path / VIDEO)) == 1

    fresh = html_to_obsidian(IFRAME, file_id_map=ids, output_dir=str(tmp_path))
    assert note.read_bytes().decode() == fresh.replace("\n", "\r\n")  # line endings kept
    assert os.path.getmtime(note) == 1_600_000_000  # Canvas change detection unaffected
    assert other.read_text(encoding="utf-8") == "no video here"
    assert add_transcript_links(str(tmp_path / VIDEO)) == 0  # already linked


def test_background_sync_records_summary_and_links_notes(tmp_path, monkeypatch):
    first, second = tmp_path / "Announcements", tmp_path / "Week 9"
    first.mkdir()
    second.mkdir()
    for folder in (first, second):
        (folder / VIDEO).write_bytes(b"video")
    monkeypatch.setattr(main, "get_existing_file_metadata_local", lambda folder, name: {"x": 1})
    monkeypatch.setattr(main, "has_file_changed", lambda *a, **k: False)
    monkeypatch.setattr(transcribe, "_available", True)
    started = threading.Event()
    release = threading.Event()

    def slow_transcribe(path, settings=None):
        started.set()
        release.wait(5)
        with open(transcribe.transcript_path(path), "w", encoding="utf-8") as f:
            f.write("transcript")
        return True, None

    monkeypatch.setattr(main, "transcribe_media", slow_transcribe)
    main._new_transcripts.clear()
    info = {"id": 17, "display_name": VIDEO, "url": "https://x", "updated_at": None}
    processed = {}
    summary = main.SummaryCollector()
    background_jobs.start()
    try:
        main.process_canvas_file(info, str(first), processed, {}, summary=summary, course_name="C", dest_label="C/A")
        main.process_canvas_file(info, str(second), processed, {}, summary=summary, course_name="C", dest_label="C/W")
        assert started.wait(5)
        # The sync carries on and writes its notes before the transcript exists.
        for folder in (first, second):
            md = html_to_obsidian(IFRAME, file_id_map={"17": VIDEO}, output_dir=str(folder))
            (folder / "Note.md").write_text(md, encoding="utf-8")
        assert "Transcript:" not in (second / "Note.md").read_text(encoding="utf-8")
        release.set()
        background_jobs.wait()
    finally:
        release.set()
        background_jobs.stop()

    assert main.link_new_transcripts() == 2
    for folder in (first, second):
        assert (folder / TRANSCRIPT).read_text(encoding="utf-8") == "transcript"
        assert "Transcript: [[Lec 1_transcript]]" in (folder / "Note.md").read_text(encoding="utf-8")
    assert summary.per_course["C"]["C/A"] == [(TRANSCRIPT, "transcribed")]


def test_new_pdf_summary_waits_for_background_extraction(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "get_existing_file_metadata_local", lambda folder, name: None)
    monkeypatch.setattr(main, "download_canvas_file", lambda *a, **k: True)

    def save(src, name, folder):
        (tmp_path / name).write_bytes(b"%PDF")
        return True

    monkeypatch.setattr(main, "save_file_locally", save)
    monkeypatch.setattr(main, "opendataloader_pdf", object())
    release = threading.Event()

    def slow_extract(pdf_path, output_dir):
        release.wait(5)
        return False, "layout model crashed"

    monkeypatch.setattr(main, "extract_pdf_with_diagnostics", slow_extract)
    summary = main.SummaryCollector()
    info = {"id": 5, "display_name": "Week 1.pdf", "url": "https://x", "updated_at": None}
    background_jobs.start()
    try:
        assert main.process_canvas_file(info, str(tmp_path), {}, {}, summary=summary, course_name="C", dest_label="C/F") == 1
        assert not summary.has_changes()  # recorded once extraction finishes
        release.set()
        background_jobs.wait()
    finally:
        release.set()
        background_jobs.stop()
    assert summary.per_course["C"]["C/F"] == [("Week 1.pdf", "created (extraction failed)")]

import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main
from canvasync.utils import transcribe
from canvasync.utils.canvas_media import needs_media_embed_retry
from canvasync.utils.obsidian import html_to_obsidian
from canvasync.utils.transcribe import (
    TranscriptionSettings,
    format_timestamp,
    group_paragraphs,
    needs_transcript,
    transcript_name,
)

VIDEO = "Week 9 Lecture-20250508_090230-Meeting Recording.mp4"
TRANSCRIPT = "Week 9 Lecture-20250508_090230-Meeting Recording_transcript.md"
IFRAME = (
    f'<iframe title="Video player for {VIDEO}" data-media-type="video" '
    'src="https://canvas.example/media_attachments_iframe/1736772?verifier=abc"></iframe>'
)


def seg(start, end, text):
    return SimpleNamespace(start=start, end=end, text=text)


class FakeModel:
    def __init__(self, segments=None, error=None):
        self.segments = segments or [seg(0, 4, " Hello everyone."), seg(4, 8, " Today, statistics.")]
        self.error = error
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return iter(self.segments), SimpleNamespace(language="en")


@pytest.fixture
def fake_whisper(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(transcribe, "_available", True)
    monkeypatch.setattr(transcribe, "_get_model", lambda settings: model)
    monkeypatch.setattr(transcribe, "load_audio", lambda path: np.zeros(16000 * 125, np.float32))
    transcribe.set_transcription_settings(TranscriptionSettings())
    yield model
    transcribe.set_transcription_settings(TranscriptionSettings())


def test_transcript_name_and_timestamps():
    assert transcript_name(VIDEO) == TRANSCRIPT
    assert transcript_name(os.path.join("a", "b", "x.MOV")) == "x_transcript.md"
    assert format_timestamp(65) == "01:05"
    assert format_timestamp(6674.9) == "1:51:14"


def test_group_paragraphs_breaks_on_pause_and_sentence_end():
    segments = [
        seg(0, 20, "First part"),
        seg(20, 50, "ends here."),  # 45 s reached at a sentence end -> break after
        seg(50, 60, "Second paragraph."),
        seg(65, 70, "After a pause."),  # 5 s pause -> break before
        seg(70, 71, "   "),
    ]
    assert group_paragraphs(segments) == [
        (0, "First part ends here."),
        (50, "Second paragraph."),
        (65, "After a pause."),
    ]


def test_group_paragraphs_hard_cap_without_punctuation():
    segments = [seg(t, t + 10, "word") for t in range(0, 200, 10)]
    starts = [start for start, _ in group_paragraphs(segments)]
    assert starts == [0, 90, 180]


def test_transcribe_media_writes_markdown(tmp_path, fake_whisper):
    video = tmp_path / VIDEO
    video.write_bytes(b"")
    ok, error = transcribe.transcribe_media(str(video))
    assert (ok, error) == (True, None)
    text = (tmp_path / TRANSCRIPT).read_text(encoding="utf-8")
    assert f'source: "{VIDEO}"' in text
    assert 'duration: "02:05"' in text and 'model: "small.en"' in text
    assert f"Video: [[{VIDEO}]]" in text
    assert "**[00:00]** Hello everyone. Today, statistics." in text
    assert fake_whisper.calls[0]["language"] == "en"
    assert not [p for p in os.listdir(tmp_path) if p.endswith(".partial")]


def test_transcribe_media_failure_leaves_no_file(tmp_path, fake_whisper):
    fake_whisper.error = RuntimeError("decoder exploded")
    video = tmp_path / VIDEO
    video.write_bytes(b"")
    ok, error = transcribe.transcribe_media(str(video))
    assert ok is False and "decoder exploded" in error
    assert os.listdir(tmp_path) == [VIDEO]


def test_auto_language_detects(tmp_path, fake_whisper):
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"")
    settings = TranscriptionSettings(model="large-v3-turbo", language="auto")
    transcribe.transcribe_media(str(video), settings)
    assert fake_whisper.calls[0]["language"] is None


def test_needs_transcript(tmp_path):
    video = tmp_path / VIDEO
    assert needs_transcript(str(video)) is False  # not downloaded
    video.write_bytes(b"")
    assert needs_transcript(str(video)) is True
    (tmp_path / TRANSCRIPT).write_text("x", encoding="utf-8")
    assert needs_transcript(str(video)) is False
    (tmp_path / "notes.pdf").write_bytes(b"")
    assert needs_transcript(str(tmp_path / "notes.pdf")) is False
    transcribe.set_transcription_settings(TranscriptionSettings(enabled=False))
    try:
        (tmp_path / TRANSCRIPT).unlink()
        assert needs_transcript(str(video)) is False
    finally:
        transcribe.set_transcription_settings(TranscriptionSettings())


def test_note_links_transcript_once_it_exists(tmp_path):
    (tmp_path / VIDEO).write_bytes(b"")
    md = html_to_obsidian(IFRAME, file_id_map={"1736772": VIDEO}, output_dir=str(tmp_path))
    assert "Transcript:" not in md

    (tmp_path / TRANSCRIPT).write_text("x", encoding="utf-8")
    md = html_to_obsidian(IFRAME, file_id_map={"1736772": VIDEO}, output_dir=str(tmp_path))
    assert f"![[{VIDEO}]]" in md
    assert f"Transcript: [[{TRANSCRIPT[:-3]}]]" in md


def test_existing_note_regenerates_for_new_transcript(tmp_path):
    (tmp_path / VIDEO).write_bytes(b"")
    note = tmp_path / "Week 9.md"
    note.write_text(f"![[{VIDEO}]] media_attachments_iframe/1736772", encoding="utf-8")
    ids = {"1736772": VIDEO}
    assert needs_media_embed_retry(IFRAME, str(note), ids) is False
    (tmp_path / TRANSCRIPT).write_text("x", encoding="utf-8")
    assert needs_media_embed_retry(IFRAME, str(note), ids) is True
    note.write_text(note.read_text(encoding="utf-8") + f"\nTranscript: [[{TRANSCRIPT[:-3]}]]", encoding="utf-8")
    assert needs_media_embed_retry(IFRAME, str(note), ids) is False


def test_process_canvas_file_transcribes_unchanged_video_then_copies(tmp_path, monkeypatch):
    first, second = tmp_path / "Announcements", tmp_path / "Week 9"
    first.mkdir()
    second.mkdir()
    (first / VIDEO).write_bytes(b"video")
    (second / VIDEO).write_bytes(b"video")
    monkeypatch.setattr(main, "get_existing_file_metadata_local", lambda folder, name: {"x": 1})
    monkeypatch.setattr(main, "has_file_changed", lambda *a, **k: False)
    monkeypatch.setattr(transcribe, "_available", True)
    calls = []

    def fake_transcribe(path, settings=None):
        calls.append(path)
        with open(transcribe.transcript_path(path), "w", encoding="utf-8") as f:
            f.write("transcript")
        return True, None

    monkeypatch.setattr(main, "transcribe_media", fake_transcribe)
    info = {"id": 1736772, "display_name": VIDEO, "url": "https://x", "updated_at": None}
    processed = {}
    summary = main.SummaryCollector()

    main.process_canvas_file(info, str(first), processed, {}, summary=summary, course_name="C", dest_label="C/A")
    main.process_canvas_file(info, str(second), processed, {}, summary=summary, course_name="C", dest_label="C/W")

    assert calls == [str(first / VIDEO)]  # transcribed once
    assert (second / TRANSCRIPT).read_text(encoding="utf-8") == "transcript"
    assert summary.per_course["C"]["C/A"] == [(TRANSCRIPT, "transcribed")]


def test_batched_transcription_passes_batch_options(tmp_path, fake_whisper):
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"")
    transcribe.transcribe_media(str(video), TranscriptionSettings(batch_size=8))
    assert fake_whisper.calls[-1]["batch_size"] == 8
    assert fake_whisper.calls[-1]["without_timestamps"] is False
    # Batching needs VAD to cut the audio into pieces; without it, run unbatched.
    transcribe.transcribe_media(str(video), TranscriptionSettings(batch_size=8, vad_filter=False))
    assert "batch_size" not in fake_whisper.calls[-1]
    transcribe.transcribe_media(str(video), TranscriptionSettings(batch_size=1))
    assert "batch_size" not in fake_whisper.calls[-1]


def test_configure_transcription_reads_batch_size():
    import configparser

    config = configparser.ConfigParser()
    config.read_string("[TRANSCRIPTION]\nBATCH_SIZE = 16\n")
    assert transcribe.configure_transcription(config).batch_size == 16
    config.read_string("[TRANSCRIPTION]\nBATCH_SIZE = 0\n")
    assert transcribe.configure_transcription(config).batch_size == 1
    transcribe.set_transcription_settings(TranscriptionSettings())

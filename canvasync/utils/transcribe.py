"""Transcribe downloaded Canvas videos (lecture recordings etc.) to Markdown.

Uses faster-whisper (CTranslate2). Audio is decoded with ffmpeg into memory,
so any container ffmpeg reads works (mp4, mov, mkv, webm, m4a, ...).

A transcript is saved next to its video as ``<video stem>_transcript.md``.
It is written to a temporary file and renamed into place, so an interrupted
run never leaves a partial transcript that later runs would treat as done.
"""

import configparser
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional, Tuple

MEDIA_EXTENSIONS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv",
    ".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac",
}
TRANSCRIPT_SUFFIX = "_transcript"
SAMPLE_RATE = 16000

# Start a new paragraph once one covers this many seconds and a sentence ends,
# after a pause this long, or unconditionally at the hard cap.
_PARAGRAPH_SECONDS = 45.0
_PARAGRAPH_PAUSE = 3.0
_PARAGRAPH_MAX_SECONDS = 90.0


@dataclass
class TranscriptionSettings:
    enabled: bool = True
    model: str = "small.en"
    device: str = "auto"  # auto, cpu, cuda
    compute_type: str = "auto"  # auto -> int8 on CPU, float16 on CUDA
    language: str = "en"  # "auto" to detect
    beam_size: int = 1
    cpu_threads: int = 0  # 0 -> one per physical core (estimated)
    # Speech pieces decoded together (faster-whisper's BatchedInferencePipeline).
    # 1 = one 30 s window at a time, each conditioned on the text before it.
    # 8 measured ~1.5x faster on CPU but dropped words (20% -> 31% word error rate).
    batch_size: int = 1
    vad_filter: bool = True
    # Primes Whisper to write punctuation and capitals; larger models otherwise
    # sometimes emit a lowercase, unpunctuated transcript.
    initial_prompt: str = "Welcome to today's lecture. Let's get started."


_settings = TranscriptionSettings()
_model = None
_model_key = None
_available: Optional[bool] = None
_UNAVAILABLE_REASON = "faster-whisper is not installed (pip install faster-whisper)"


def configure_transcription(config: configparser.ConfigParser) -> TranscriptionSettings:
    """Load ``[TRANSCRIPTION]`` settings from *config* for this run."""
    global _settings
    section = "TRANSCRIPTION"
    defaults = TranscriptionSettings()

    def get(option: str, default: str) -> str:
        if not config.has_section(section):
            return default
        value = config.get(section, option, fallback=default).strip()
        return value if value else default

    def get_bool(option: str, default: bool) -> bool:
        return get(option, str(default)).lower() in {"1", "true", "yes", "y", "on"}

    def get_int(option: str, default: int) -> int:
        try:
            return int(get(option, str(default)))
        except ValueError:
            return default

    _settings = TranscriptionSettings(
        enabled=get_bool("TRANSCRIBE_VIDEOS", defaults.enabled),
        model=get("MODEL", defaults.model),
        device=get("DEVICE", defaults.device).lower(),
        compute_type=get("COMPUTE_TYPE", defaults.compute_type).lower(),
        language=get("LANGUAGE", defaults.language).lower(),
        beam_size=max(1, get_int("BEAM_SIZE", defaults.beam_size)),
        cpu_threads=max(0, get_int("CPU_THREADS", defaults.cpu_threads)),
        batch_size=max(1, get_int("BATCH_SIZE", defaults.batch_size)),
        vad_filter=get_bool("VAD_FILTER", defaults.vad_filter),
        initial_prompt=(
            config.get(section, "INITIAL_PROMPT", fallback=defaults.initial_prompt).strip()
            if config.has_section(section)
            else defaults.initial_prompt
        ),
    )
    return _settings


def set_transcription_settings(settings: TranscriptionSettings) -> None:
    global _settings
    _settings = settings


def transcription_settings() -> TranscriptionSettings:
    return _settings


def transcription_enabled() -> bool:
    return _settings.enabled


def transcription_available() -> bool:
    """True when faster-whisper can be imported. Prints a notice once per run when it can't."""
    global _available
    if _available is None:
        try:
            import faster_whisper  # noqa: F401

            _available = True
        except ImportError:
            _available = False
            print(f"Skipping video transcription: {_UNAVAILABLE_REASON}")
    return _available


def is_media_file(filename: Optional[str]) -> bool:
    return bool(filename) and os.path.splitext(filename)[1].lower() in MEDIA_EXTENSIONS


def transcript_name(media_filename: str) -> str:
    """``Lecture 1.mp4`` -> ``Lecture 1_transcript.md``."""
    return f"{os.path.splitext(os.path.basename(media_filename))[0]}{TRANSCRIPT_SUFFIX}.md"


def transcript_link_target(media_filename: str) -> str:
    """Wikilink target (no ``.md``) of a media file's transcript."""
    return os.path.splitext(transcript_name(media_filename))[0]


def transcript_path(media_path: str) -> str:
    return os.path.join(os.path.dirname(media_path), transcript_name(media_path))


def needs_transcript(media_path: str) -> bool:
    """True when transcription is on and *media_path* exists without a transcript."""
    return (
        transcription_enabled()
        and is_media_file(media_path)
        and os.path.exists(media_path)
        and not os.path.exists(transcript_path(media_path))
    )


def copy_transcript(source_media_path: str, dest_media_path: str) -> bool:
    """Copy a transcript alongside a copied video. Returns True when a file was copied."""
    if not is_media_file(dest_media_path):
        return False
    src = transcript_path(source_media_path)
    dest = os.path.join(
        os.path.dirname(dest_media_path), transcript_name(dest_media_path)
    )
    if src == dest or not os.path.exists(src) or os.path.exists(dest):
        return False
    try:
        shutil.copy2(src, dest)
        return True
    except OSError as e:
        print(f"Could not copy transcript '{os.path.basename(src)}': {e}")
        return False


# --- Formatting ---


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def group_paragraphs(segments: Iterable) -> list:
    """Group Whisper segments (with ``start``, ``end``, ``text``) into ``(start, text)`` paragraphs."""
    paragraphs = []
    current = []
    para_start = None
    last_end = None
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        if current:
            span = seg.start - para_start
            pause = seg.start - last_end
            sentence_done = current[-1][-1:] in ".?!"
            if (
                pause >= _PARAGRAPH_PAUSE
                or span >= _PARAGRAPH_MAX_SECONDS
                or (span >= _PARAGRAPH_SECONDS and sentence_done)
            ):
                paragraphs.append((para_start, " ".join(current)))
                current = []
        if not current:
            para_start = seg.start
        current.append(text)
        last_end = seg.end
    if current:
        paragraphs.append((para_start, " ".join(current)))
    return paragraphs


def _yaml_str(value: str) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def format_transcript_markdown(
    media_filename: str,
    paragraphs: list,
    duration: float,
    language: str,
    model: str,
) -> str:
    name = os.path.basename(media_filename)
    lines = [
        "---",
        f"source: {_yaml_str(name)}",
        f"duration: {_yaml_str(format_timestamp(duration))}",
        f"language: {_yaml_str(language or '')}",
        f"model: {_yaml_str(model)}",
        f"transcribed: {_yaml_str(date.today().isoformat())}",
        "---",
        "",
        f"# Transcript: {os.path.splitext(name)[0]}",
        "",
        f"Video: [[{name}]]",
        "",
    ]
    if not paragraphs:
        lines += ["_No speech was detected in this recording._", ""]
    for start, text in paragraphs:
        lines += [f"**[{format_timestamp(start)}]** {text}", ""]
    return "\n".join(lines)


# --- Transcription ---


def _resolve_device(device: str) -> str:
    if device in ("cpu", "cuda"):
        return device
    try:
        import ctranslate2

        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


def _resolve_compute_type(compute_type: str, device: str) -> str:
    if compute_type and compute_type != "auto":
        return compute_type
    return "float16" if device == "cuda" else "int8"


def _resolve_threads(cpu_threads: int) -> int:
    if cpu_threads > 0:
        return cpu_threads
    # os.cpu_count() counts SMT threads; Whisper on CPU runs best at one per core.
    return max(1, (os.cpu_count() or 2) // 2)


def _batched(settings: TranscriptionSettings) -> bool:
    # The batched pipeline finds its pieces with VAD; without it, it only takes
    # audio shorter than 30 s.
    return settings.batch_size > 1 and settings.vad_filter


def _get_model(settings: TranscriptionSettings):
    """Load (once per run, cached by settings) and return the faster-whisper model.

    With batching on, returns a ``BatchedInferencePipeline`` wrapping it; both
    have the same ``transcribe(audio, ...)`` call.
    """
    global _model, _model_key
    device = _resolve_device(settings.device)
    compute_type = _resolve_compute_type(settings.compute_type, device)
    threads = _resolve_threads(settings.cpu_threads)
    batched = _batched(settings)
    key = (settings.model, device, compute_type, threads, batched)
    if _model is not None and _model_key == key:
        return _model
    from faster_whisper import BatchedInferencePipeline, WhisperModel

    batch_note = f", batch={settings.batch_size}" if batched else ""
    print(
        f"Loading Whisper model '{settings.model}' "
        f"(device={device}, compute={compute_type}, threads={threads}{batch_note})..."
    )
    model = WhisperModel(
        settings.model, device=device, compute_type=compute_type, cpu_threads=threads
    )
    _model = BatchedInferencePipeline(model=model) if batched else model
    _model_key = key
    return _model


def load_audio(media_path: str):
    """Decode *media_path* to 16 kHz mono float32 samples."""
    import numpy as np

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        # faster-whisper's own decoder (PyAV) works when ffmpeg is not on PATH.
        from faster_whisper import decode_audio

        return decode_audio(media_path, sampling_rate=SAMPLE_RATE)
    cmd = [
        ffmpeg, "-nostdin", "-v", "error", "-i", media_path,
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-",
    ]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    proc = subprocess.run(cmd, capture_output=True, **kwargs)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(f"ffmpeg could not read the audio: {err[-1] if err else proc.returncode}")
    return np.frombuffer(proc.stdout, dtype=np.float32)


def _with_progress(segments, duration: float, every: float = 0.1):
    """Yield segments unchanged, printing progress roughly every 10% of the audio."""
    next_mark = every
    started = time.time()
    for seg in segments:
        if duration > 0 and seg.end / duration >= next_mark:
            elapsed = time.time() - started
            print(
                f"    {format_timestamp(seg.end)} / {format_timestamp(duration)} "
                f"({seg.end / duration:.0%}, {elapsed / 60:.1f} min elapsed)"
            )
            while seg.end / duration >= next_mark:
                next_mark += every
        yield seg


def transcribe_media(
    media_path: str, settings: Optional[TranscriptionSettings] = None
) -> Tuple[bool, Optional[str]]:
    """Transcribe *media_path* to ``<stem>_transcript.md`` next to it.

    Returns ``(success, error)``. Overwrites an existing transcript (the video
    changed).
    """
    settings = settings or _settings
    out_path = transcript_path(media_path)
    if not transcription_available():
        return False, _UNAVAILABLE_REASON
    try:
        model = _get_model(settings)
    except Exception as e:
        return False, f"could not load Whisper model '{settings.model}': {e}"

    name = os.path.basename(media_path)
    language = settings.language
    if settings.model.endswith(".en"):
        language = "en"
    elif language in ("", "auto"):
        language = None

    tmp_path = os.path.join(os.path.dirname(out_path), f".{os.path.basename(out_path)}.partial")
    try:
        started = time.time()
        audio = load_audio(media_path)
        duration = len(audio) / SAMPLE_RATE
        print(f"Transcribing '{name}' ({format_timestamp(duration)} of audio)...")
        batch_options = {}
        if _batched(settings):
            # Timestamps give sentence-length segments (as unbatched) instead of
            # one segment per ~30 s piece, so paragraphs still break at pauses.
            batch_options = {"batch_size": settings.batch_size, "without_timestamps": False}
        segments, info = model.transcribe(
            audio,
            language=language,
            beam_size=settings.beam_size,
            vad_filter=settings.vad_filter,
            initial_prompt=settings.initial_prompt or None,
            **batch_options,
        )
        paragraphs = group_paragraphs(_with_progress(segments, duration))
        content = format_transcript_markdown(
            name, paragraphs, duration, getattr(info, "language", language or ""), settings.model
        )
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, out_path)
        elapsed = time.time() - started
        print(
            f"  -> Saved '{os.path.basename(out_path)}' in {elapsed / 60:.1f} min "
            f"({duration / max(elapsed, 1e-6):.1f}x realtime)"
        )
        return True, None
    except Exception as e:
        return False, str(e) or e.__class__.__name__
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

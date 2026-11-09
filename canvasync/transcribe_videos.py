"""Transcribe videos already in the sync folder, without running a Canvas sync.

The sync transcribes new and existing videos on its own; this command is for
working through a backlog (or re-transcribing with a different model) without
waiting on the rest of the sync. Notes pick up their ``Transcript:`` links on
the next sync.
"""

import argparse
import configparser
import os
from pathlib import Path
from typing import Dict, List, Tuple

from canvasync.config import CONFIG_FILE
from canvasync.utils.transcribe import (
    configure_transcription,
    copy_transcript,
    is_media_file,
    transcript_path,
    transcribe_media,
    transcription_available,
)


def find_media(paths: List[Path]) -> List[Path]:
    found = []
    for path in paths:
        if path.is_file() and is_media_file(path.name):
            found.append(path)
        elif path.is_dir():
            found.extend(
                p for p in sorted(path.rglob("*"))
                if p.is_file()
                and is_media_file(p.name)
                and not any(part.startswith(".") for part in p.relative_to(path).parts)
            )
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "paths", nargs="*", type=Path,
        help="Video files or folders (default: LOCAL_ROOT_DIR from config.ini)",
    )
    parser.add_argument("--course", help="Only course folders whose name contains this text")
    parser.add_argument("--force", action="store_true", help="Re-transcribe videos that already have a transcript")
    parser.add_argument("--list", action="store_true", help="Only list the videos that would be transcribed")
    parser.add_argument("--model", help="Whisper model for this run (overrides [TRANSCRIPTION] MODEL)")
    args = parser.parse_args(argv)

    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    settings = configure_transcription(config)
    if args.model:
        settings.model = args.model

    paths = args.paths
    if not paths:
        root = config.get("STORAGE", "LOCAL_ROOT_DIR", fallback="").strip()
        if not root:
            parser.error(f"no paths given and LOCAL_ROOT_DIR is not set in {CONFIG_FILE}")
        root_path = Path(root)
        paths = [root_path]
        if args.course:
            paths = [p for p in root_path.iterdir() if p.is_dir() and args.course.lower() in p.name.lower()]
            if not paths:
                print(f"No course folder matching '{args.course}' under {root_path}")
                return 1

    media = find_media(paths)
    # The same lecture is often embedded in several places; transcribe each once.
    groups: Dict[Tuple[str, int], List[Path]] = {}
    for path in media:
        groups.setdefault((path.name, path.stat().st_size), []).append(path)

    todo = []
    for copies in groups.values():
        have = [p for p in copies if os.path.exists(transcript_path(str(p)))]
        if args.force or not have:
            todo.append(copies)
        elif len(have) < len(copies):
            for p in copies:
                copy_transcript(str(have[0]), str(p))

    if not todo:
        print(f"All {len(media)} video(s) already have transcripts.")
        return 0

    print(f"{len(todo)} video(s) to transcribe:")
    for copies in todo:
        n = len(copies) - 1
        extra = f"  (+{n} {'copy' if n == 1 else 'copies'})" if n else ""
        print(f"  - {copies[0]}{extra}")
    if args.list:
        return 0
    if not transcription_available():
        return 1

    failures = 0
    for i, copies in enumerate(todo, 1):
        source = copies[0]
        print(f"\n[{i}/{len(todo)}] {source.parent.name}/{source.name}")
        ok, error = transcribe_media(str(source), settings)
        if not ok:
            failures += 1
            print(f"  -> Error: {error}")
            continue
        for other in copies[1:]:
            if args.force and os.path.exists(transcript_path(str(other))):
                os.remove(transcript_path(str(other)))
            if copy_transcript(str(source), str(other)):
                print(f"  -> Copied transcript to {other.parent}")

    print(f"\nDone: {len(todo) - failures} transcribed, {failures} failed.")
    return 1 if failures else 0

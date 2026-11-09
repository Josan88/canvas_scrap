# Canvas to Local Storage Sync

Sync content from Canvas LMS to local storage with incremental updates.

This project pulls course content (assignments, pages, files, discussions, and optional JSON reports) from Canvas and stores it in a local folder structure. It is local-storage-only and optimized to skip unchanged resources on repeat runs.

## Features

- Interactive course selection (`all`, specific numbers, or `last` selection)
- Incremental sync with timestamp-based change detection
- Assignment export to Markdown (including rubric/details)
- Page export to Markdown (including page body)
- Discussion export to Markdown plus optional course-level discussion JSON
- Announcement export to Markdown, including linked and attached files, plus announcements JSON
- Linked file discovery from assignments/pages/discussions (`/files/{id}` links)
- Embedded Canvas video download (lecture recordings in the Canvas media player): saved next to the note and embedded with `![[video.mp4]]`, plus a link back to the Canvas player
- Video transcription: downloaded videos are transcribed locally with Whisper (`faster-whisper`) to `<video name>_transcript.md` with timestamped paragraphs, linked from the note under the video
- PDF handling:
  - Saves original PDF files
  - Extracts PDF content to `*_pdf.md` using `opendataloader_pdf`
- Office file handling (docx, pptx, xlsx):
  - Saves original Office files
  - Converts to PDF automatically using Python libraries (`python-docx`, `python-pptx`, `openpyxl` + `reportlab`)
  - Extracts the generated PDF to `*_pdf.md` using `opendataloader_pdf`
- Optional course reports (JSON): announcements, quizzes, enrollments, calendar events, groups, analytics, gradebook history, submissions summary
- Optional global inbox conversations export (`Conversations/conversations.json`)
- Endpoint auto-disable for unavailable Canvas APIs (HTTP 403/404), persisted to config

## Requirements

- Python 3.10+
- Java 11+ (required at runtime for PDF extraction flow)
- ffmpeg on `PATH` (used to read audio for video transcription)
- Canvas API token with access to the courses you want to sync

If Java is missing, the app exits before sync starts.

## Installation

1. Create/activate a virtual environment (recommended)
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Create your config file from the example and update values:

```powershell
copy config.ini.example config.ini
```

## Configuration

Configure `config.ini`.

### `[CANVAS]`

- `API_URL`: Canvas base URL (example: `https://yourschool.instructure.com`)
- `API_KEY`: Canvas API token

### `[STORAGE]`

- `STORAGE_TYPE`: must be `local` in this project
- `LOCAL_ROOT_DIR`: root directory for synced output (example: `./canvas_sync`)
- `FORCE_REGENERATE_ASSIGNMENTS`: `true/false`; when `true`, assignment Markdown is regenerated even if unchanged

### `[LAST_SELECTION]`

- `COURSE_IDS`: comma-separated course IDs; managed automatically by the app

### `[PERFORMANCE]` (optional)

- `REQUEST_TIMEOUT` (default `20`)
- `MAX_RETRIES` (default `3`)
- `BACKOFF_FACTOR` (default `0.5`)
- `CANVAS_PER_PAGE` (default `100`)
- `HTTP_POOL_MAXSIZE` (default `20`)
- `BACKGROUND_JOBS` (default `true`): extract PDFs and transcribe videos on two background workers while the sync keeps downloading, then wait for them before the summary. Transcription runs on the CPU and PDF extraction mostly in the hybrid server (on the GPU when there is one), so they overlap. Their progress lines are interleaved with the sync's output. `false` does each file's extraction or transcription before moving on

### `[EXPORTS]`

Toggle optional exports with `true/false`:

- `EXPORT_ANNOUNCEMENTS` (default `true`)
- `EXPORT_DISCUSSIONS` (default `true`)
- `EXPORT_QUIZZES` (default `true`)
- `EXPORT_ENROLLMENTS` (default `true`)
- `EXPORT_CALENDAR_EVENTS` (default `true`)
- `EXPORT_GROUPS` (default `true`)
- `EXPORT_ANALYTICS_ACTIVITY` (default `true`)
- `EXPORT_GRADEBOOK_HISTORY` (default `true`)
- `EXPORT_SUBMISSIONS_SUMMARY` (default `false`)
- `EXPORT_INBOX_CONVERSATIONS` (default `false`)
- `DOWNLOAD_EMBEDDED_VIDEOS` (default `true`): download Canvas-hosted videos embedded in content. Lecture recordings can be hundreds of MB each; when `false`, notes keep a link to the Canvas player instead

### `[TRANSCRIPTION]` (optional)

- `TRANSCRIBE_VIDEOS` (default `true`): transcribe downloaded videos to `<video name>_transcript.md`
- `MODEL` (default `small.en`): Whisper model. `small.en` runs at about 7x realtime on an 8-10 core laptop CPU (a 2-hour lecture in about 16 minutes); `large-v3-turbo` is more accurate on difficult audio but about 2.5x slower
- `DEVICE` (default `auto`): `auto`, `cpu` or `cuda`. Only NVIDIA GPUs are supported; `auto` falls back to the CPU
- `COMPUTE_TYPE` (default `auto`): `int8` on CPU, `float16` on CUDA
- `LANGUAGE` (default `en`): spoken language code, or `auto` to detect (`*.en` models are English-only)
- `BEAM_SIZE` (default `1`): `1` is greedy decoding (fastest); `5` is slightly more accurate and about half the speed
- `CPU_THREADS` (default `0`): `0` uses one thread per physical core
- `BATCH_SIZE` (default `1`, off): decode this many speech pieces at once (faster-whisper's batched pipeline; needs `VAD_FILTER`). `8` was about 1.5x faster on a 10-core laptop CPU but less accurate: on a test lecture it dropped words, raising the word error rate from 20% to 31% (both measured against `large-v3-turbo`), because each piece is transcribed without the text before it
- `VAD_FILTER` (default `true`): skip silence before transcribing
- `INITIAL_PROMPT`: example text that steers punctuation and spelling; add course terms or names that come out wrong

The model is downloaded from Hugging Face on first use (about 500 MB for `small.en`).

If quizzes/analytics/gradebook endpoints return 403/404, the corresponding export can be auto-disabled and persisted to `config.ini`.

## Usage

Run:

```powershell
python main.py
```

You will be prompted to choose courses:

- Enter numbers like `1,3,5`
- Enter `all`
- Enter `last` to reuse previous selection
- Enter `quit` to exit

At the end of the run, the script prints a summary and waits for Enter before exiting.

## Live Quiz Attempts

Canvas only lets students read a quiz's questions while their attempt is running, and quizzes set to hide results never show them again after submission. To keep a copy, start the quiz in Canvas, then run:

```powershell
python live_quiz.py
```

It checks every active course (or only the ones you name with `--course 1596`, repeatable) for classic-quiz attempts you've started but not submitted. Each one is saved to `Course Name/Quizzes/<Quiz Title>/<Quiz Title> - attempt N.md`. Question images, such as a scanned question sheet, are downloaded next to the note and embedded with `![[image.png]]`, and equation-editor images become `$LaTeX$`. Answers you've already saved in Canvas are shown as ticked options or under "Saved answer".

Notes:

- It only sends GET requests, so it never starts, saves or submits an attempt. Run it again to refresh the note.
- It finds quizzes through the course's assignments, so it works when a course hides its Quizzes page (the reason `EXPORT_QUIZZES` gets auto-disabled).
- New Quizzes aren't supported: Canvas doesn't give students their New Quiz attempts through the API.
- Quizzes that need an access code, a lockdown browser or a campus IP address can refuse the request (HTTP 401/403).

## Transcribing Videos Already on Disk

The sync transcribes videos as they are downloaded, and also transcribes previously downloaded videos that have no transcript yet. To work through a backlog without running a full sync:

```powershell
python transcribe_videos.py
```

This scans `LOCAL_ROOT_DIR` for videos without a `*_transcript.md`. The same recording embedded in several places is transcribed once and the transcript copied to the other folders. Notes get their `Transcript:` link on the next sync.

Optional arguments:

- `python transcribe_videos.py "path/to/folder or video.mp4"`: only these files or folders
- `--course "EAT40006"`: only course folders whose name contains this text
- `--list`: only list what would be transcribed
- `--force`: re-transcribe videos that already have a transcript
- `--model large-v3-turbo`: use a different Whisper model for this run

## NotebookLM Sync Tracking

After syncing Canvas locally, preview which sources would be uploaded to NotebookLM:

```powershell
python notebooklm_sync.py
```

The NotebookLM sync is dry-run by default. It scans `LOCAL_ROOT_DIR` from `config.ini`, includes normal Markdown files and original PDFs, and skips `*_pdf.md` extraction files because NotebookLM can process PDFs directly.

To preview one course only:

```powershell
python notebooklm_sync.py --course "Course Folder Name"
```

To upload new or changed sources and save tracking state:

```powershell
python notebooklm_sync.py --apply
```

The tracking state is saved to `.notebooklm_sync.json`, which is ignored by git. The script creates or reuses one NotebookLM notebook per course folder, using the title prefix `Canvas Sync - `.

Optional flags:

- `--include-pdf-extracts`: also sync `*_pdf.md` files as a fallback for problematic PDFs
- `--include-submissions`: also sync files inside `submission` folders
- `--max-list 25`: limit the number of pending uploads printed in the preview

## Output Structure

Under `LOCAL_ROOT_DIR`, each course gets its own folder. Typical layout:

```text
canvas_sync/
  Course Name/
    Assignments/
      Assignment A/
        Assignment A.md
        linked_file.ext
    Discussions/
      Topic Title/
        Topic Title.md
        linked_file.ext
    Announcements/
      Announcement Title/
        Announcement Title.md
        linked_file.ext
        Lecture Recording.mp4
        Lecture Recording_transcript.md
    Quizzes/
      Quiz Title/
        Quiz Title - attempt 1.md   (from live_quiz.py)
        question_image.png
    Reports/
      announcements.json
      discussion_topics.json
      quizzes.json
      enrollments.json
      calendar_events.json
      groups.json
      analytics_activity.json
      gradebook_history.json
      submissions_summary.json
    Page Title/
      Page Title.md
      linked_file.ext
    SomeFile.pdf
    SomeFile_pdf.md
    Report.docx
    Report.pdf
    Report_pdf.md
    Slides.pptx
    Slides.pdf
    Slides_pdf.md
    Data.xlsx
    Data.pdf
    Data_pdf.md
  Conversations/
    conversations.json
```

Exact files depend on what exists in Canvas and which exports are enabled.

## Incremental Sync Behavior

The sync is designed to avoid unnecessary writes/downloads:

- Existing local metadata is checked before saving resources
- Change detection is primarily timestamp-driven (`updated_at` vs local mtime)
- Linked files discovered multiple times in one run are deduplicated by Canvas file ID
- If a PDF is unchanged but its extracted `*_pdf.md` is missing, extraction is attempted
- If an Office file (docx/pptx/xlsx) is unchanged but its generated PDF or `*_pdf.md` is missing, conversion and extraction are attempted
- If a video is unchanged but its `*_transcript.md` is missing, it is transcribed; a changed video is re-transcribed
- A note written before its video's transcript finished (background jobs) gets its `Transcript:` link at the end of the same run; the note's modification time is kept

Run the tool twice in a row to verify unchanged resources are skipped.

## Troubleshooting

- `401 Unauthorized`
  - Verify `API_KEY` and `API_URL` in `config.ini`
- No courses listed
  - Check token permissions and whether courses are date-restricted
- `403/404` on optional reports
  - Some institutions disable specific endpoints; auto-disable may be applied for that export
- Java check failure at startup
  - Install Java 11+ and ensure it is on `PATH`
- Slow sync
  - Adjust `[PERFORMANCE]` values (`CANVAS_PER_PAGE`, `HTTP_POOL_MAXSIZE`, retries/timeouts)

## Notes

- Storage backends other than local filesystem are not supported in this codebase.
- A temporary download folder is used during sync and cleaned up at the end.
- The script starts a background hybrid server process for PDF extraction and stops it on completion.

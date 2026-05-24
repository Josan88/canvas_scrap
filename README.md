# Canvas to Local Storage Sync

Sync content from Canvas LMS to local storage with incremental updates.

This project pulls course content (assignments, pages, files, discussions, and optional JSON reports) from Canvas and stores it in a local folder structure. It is local-storage-only and optimized to skip unchanged resources on repeat runs.

## Features

- Interactive course selection (`all`, specific numbers, or `last` selection)
- Incremental sync with timestamp-based change detection
- Assignment export to Markdown (including rubric/details)
- Page export to Markdown (including page body)
- Discussion export to Markdown plus optional course-level discussion JSON
- Linked file discovery from assignments/pages/discussions (`/files/{id}` links)
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

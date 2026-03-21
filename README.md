# Canvas to Storage Scraper & Sync (Local-Only Branch)

This branch syncs Canvas LMS content to local storage only.

## Features

- Connects to the Canvas LMS API to fetch courses, modules, pages, assignments, and linked files.
- Saves all synced content to a local directory while preserving course structure.
- Uses stable, safe folder names (`Course Name [course_id]`) to avoid collisions.
- Generates assignment and page PDFs per course.
- Exports optional JSON reports (announcements, discussions, quizzes, enrollments, calendar events, groups, analytics, gradebook, submissions, inbox conversations).
- Performs change detection to avoid re-downloading unchanged content.
- Reuses a shared HTTP session with retries and connection pooling.

## Folder Structure

```
LOCAL_ROOT_DIR/
├── Course Name [12345]/
│   ├── Assignments/
│   ├── Reports/
│   ├── Direct Module Files...
│   └── Page Title/... 
└── Conversations/
    └── conversations.json
```

## Setup

1. Install dependencies:

```sh
pip install -r requirements.txt
```

2. Copy `config.ini.example` to `config.ini`.
3. Set your Canvas values in `config.ini`:
   - `API_URL`
   - `API_KEY`
4. In `[STORAGE]`, keep:
   - `STORAGE_TYPE = local`
   - `LOCAL_ROOT_DIR = ./canvas_sync` (or your preferred path)

## Run

```sh
python main.py
```

The script will prompt for course selection, sync content, and print a summary of created/updated files.

## Performance Tuning

Optional `[PERFORMANCE]` keys in `config.ini`:

- `REQUEST_TIMEOUT`
- `MAX_RETRIES`
- `BACKOFF_FACTOR`
- `CANVAS_PER_PAGE`
- `HTTP_POOL_MAXSIZE`

## Export Toggles

Optional `[EXPORTS]` keys control report generation. Most defaults are enabled; heavier exports such as submissions and inbox can remain disabled unless needed.

## Notes on File Discovery

Many Canvas instances do not expose a complete Files API listing. This script discovers files through module items, assignment descriptions, and page content.

If a file exists in Canvas but is not linked from modules/pages/assignments, it may not be discovered.

## Build Executable

```powershell
pip install pyinstaller
pyinstaller CanvasSync.spec
```

Generated output is placed under `dist/CanvasSync/`.

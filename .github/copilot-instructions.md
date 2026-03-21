# Canvas to Storage Sync - AI Coding Agent Instructions (Local-Only)

## Project Overview

Single-file Python script (`main.py`) that syncs Canvas LMS content to **local storage only**. It preserves course structure and syncs only changed content using timestamp/size checks. Many institutions disable Canvas Files API, so files are discovered from module/page/assignment links.

## Architecture & Critical Patterns

### Main Processing Flow

1. Load config → authenticate Canvas → fetch courses
2. User selects courses (remembers last selection in `config.ini`)
3. For each course: process assignments → modules → pages → linked files
4. Track changes in `SummaryCollector`, print report, cleanup temp files

### Shared HTTP Session Pattern

Always pass `session` through call chains to reuse the connection pool (`HTTPAdapter` + retry).

```python
process_canvas_file(..., session=session, timeout=request_timeout)
```

Never create ad-hoc sessions in helpers unless the function explicitly supports optional session fallback.

### HTML → PDF Conversion (`html_to_pdf_elements()`)

- Uses BeautifulSoup to parse Canvas HTML and ReportLab to render styled PDF blocks.
- Preserves headings/lists/links/code/blockquote formatting.
- Uses `inline_buffer` accumulation for inline content before block flush.
- Fallback: if parsing yields nothing, use plain text extraction.

```python
html_elements = html_to_pdf_elements(description, styles)
content.extend(html_elements)
```

### Change Detection (`has_file_changed()`)

Two-phase check:

1. Compare `size`
2. Compare update time (`updated_at` from Canvas ISO vs local mtime epoch)

```python
if not has_file_changed(existing_metadata, canvas_size=file_size, canvas_updated_at=updated_at):
    return 0
```

Use `get_existing_file_metadata_local()` before processing files/resources.

### File Discovery Strategy

- Assignments: scan description HTML for `/files/(\d+)`
- Pages: combine Pages API results + module page items
- Modules: process `File` and `Page` items explicitly
- De-dup with `processed_canvas_file_ids`

### PDF Generation Patterns

- Assignments PDF: title → due date → points → rubric → description
- Page PDF: title → Canvas link → body

Always escape user-provided content before `Paragraph`:

```python
escaped_title = html.escape(assignment_name, quote=False)
content.append(Paragraph(escaped_title, title_style))
```

## Developer Workflows

### Setup & Running

```powershell
pip install -r requirements.txt

# Configure Canvas API in config.ini
[CANVAS]
API_URL = https://school.instructure.com
API_KEY = <token>

python main.py
```

### Testing Changes

1. Use a test course with varied HTML and linked files.
2. Run once, then run again to verify unchanged items are skipped.
3. Confirm local folder structure and PDF/report outputs.

### Debugging Canvas API Issues

- `401 Unauthorized`: verify `API_KEY`
- `404 Not Found`: endpoint may be disabled at institution
- Rate limits: increase `BACKOFF_FACTOR`
- Pagination issues: verify `Link` parsing in `get_paginated_canvas_items()`

## Project Conventions

### Error Handling

```python
try:
    response.raise_for_status()
except requests.RequestException as e:
    print(f"Error: {e}")
    return 0
```

Do not silently suppress errors unless explicitly using a `suppress_errors=True` optional path.

### File Naming

```python
safe_name = sanitize_filename(name)
```

Always sanitize folder/file names before writing to disk.

### Pagination

```python
items = get_paginated_canvas_items(url, headers, session, timeout, per_page)
```

Prefer `per_page=100` to reduce round trips when allowed.

### Timestamp Handling

- Canvas timestamps: ISO 8601 with `Z`
- Local file timestamps: epoch seconds (`os.path.getmtime()`)

Normalize comparisons via existing helper logic.

## Integration Points

### Canvas API

- Auth header: `Authorization: Bearer <API_KEY>`
- Core endpoints: `/courses`, `/assignments`, `/modules`, `/pages`, `/files/{id}`
- Pagination: parse `Link` header (`rel="next"`)

### Local Storage

- Root: `LOCAL_ROOT_DIR` from config
- Operations: `os.makedirs(..., exist_ok=True)`, `shutil.move()`
- Metadata: `os.path.getsize()`, `os.path.getmtime()`

## Key Files

- `main.py`: complete app logic (single-file architecture)
- `config.ini`: runtime config
- `config.ini.example`: template config
- `temp_canvas_downloads/`: temporary downloads, cleaned pre/post run
- `CanvasSync.spec`: PyInstaller build config

## Common Pitfalls

### Files not syncing

- Cause: file is not linked from module/page/assignment
- Fix: ensure file is linked in Canvas content

### PDF conversion errors

- Cause: malformed HTML
- Fix: keep fallback behavior and defensive parsing

### Duplicate downloads

- Cause: de-dup set not used consistently
- Fix: ensure `processed_canvas_file_ids` is threaded through calls

### Large course performance

- Tune: `HTTP_POOL_MAXSIZE`, `CANVAS_PER_PAGE`, `REQUEST_TIMEOUT`

## Extending Features

1. New content type: follow `process_canvas_assignment()` / `process_canvas_file()` patterns
2. New storage backend (future): implement `get_or_create_folder_X()`, `get_existing_file_metadata_X()`, `save_file_X()`
3. New PDF formatting: extend `html_to_pdf_elements()` tag handlers

## Testing Without Canvas Account

```python
import requests_mock

with requests_mock.Mocker() as m:
    m.get('https://canvas.instructure.com/api/v1/courses', json=[...])
    # run sync logic
```

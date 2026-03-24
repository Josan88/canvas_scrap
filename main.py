import os
import sys
import subprocess
import time
import requests
import configparser
import shutil
import re
from typing import Optional, Dict, List, DefaultDict, Any, Set
from collections import defaultdict
from urllib.parse import urlparse, unquote
from bs4 import BeautifulSoup
from bs4.element import Tag, NavigableString
import datetime

import opendataloader_pdf
import html
import json
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter


# --- Configuration ---
CONFIG_FILE = "config.ini"
DOWNLOAD_DIR = "temp_canvas_downloads"

# Performance tuning defaults (overridable via config.ini [PERFORMANCE])
DEFAULT_REQUEST_TIMEOUT = 20  # seconds
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_FACTOR = 0.5
DEFAULT_CANVAS_PER_PAGE = 100
DEFAULT_HTTP_POOL_MAXSIZE = 20


# --- Helper Functions ---
class SummaryCollector:
    """Collects a per-course summary of updated/created files grouped by destination folder label."""

    def __init__(self):
        # Structure: { course_name: { dest_label: [ (filename, action) ] } }
        self.per_course: Dict[str, DefaultDict[str, List[tuple]]] = {}
        self.downloaded_pdfs: List[str] = []

    def add_file(self, course_name: str, dest_label: str, filename: str, action: str):
        if not course_name or not dest_label or not filename:
            return
        if course_name not in self.per_course:
            self.per_course[course_name] = defaultdict(list)
        self.per_course[course_name][dest_label].append((filename, action))

    def has_changes(self) -> bool:
        return any(self.per_course.get(c) for c in self.per_course)

    def get_action_count(self, action: str) -> int:
        count = 0
        for folders in self.per_course.values():
            for items in folders.values():
                count += sum(1 for _, item_action in items if item_action == action)
        return count

    def print_summary(self):
        print("\n=== Summary of Updates ===")
        if not self.has_changes():
            print("No files or folders were updated across the selected courses.")
            return
        for course_name, folders in self.per_course.items():
            print(f"\nCourse: {course_name}")
            for dest_label, items in folders.items():
                print(f"  Folder: {dest_label}")
                for filename, action in items:
                    print(f"    - {filename}  [{action}]")
        print("\n==========================")


def sanitize_filename(name):
    """Removes invalid characters from a string to make it a valid filename, and collapses multiple spaces."""
    clean_name = re.sub(r'[\\/*?:"<>|]', " ", name).strip()
    return re.sub(r"\s+", " ", clean_name)


def sanitize_folder_name(name: str, fallback: str = "Unnamed") -> str:
    """Sanitizes folder names and guarantees a non-empty result."""
    safe_name = sanitize_filename(name or "")
    return safe_name if safe_name else fallback


def get_existing_file_metadata_local(folder_path, filename):
    """Gets metadata of an existing file in local folder."""
    if not folder_path or not filename:
        return None
    path = os.path.join(folder_path, filename)
    if os.path.exists(path):
        try:
            return {
                "size": os.path.getsize(path),
                "modified_time": os.path.getmtime(path),
            }
        except OSError as error:
            print(f"Error getting metadata for '{path}': {error}")
    return None


def has_file_changed(existing_metadata, canvas_size=None, canvas_updated_at=None):
    """Checks if file has changed based on metadata."""
    if not existing_metadata:
        return True  # New file
    if canvas_size is not None and existing_metadata["size"] != canvas_size:
        return True
    if canvas_updated_at and existing_metadata["modified_time"]:
        try:
            canvas_time = datetime.datetime.fromisoformat(
                canvas_updated_at.replace("Z", "+00:00")
            )
            existing_mod = existing_metadata["modified_time"]
            if isinstance(existing_mod, (int, float)):
                existing_time = datetime.datetime.fromtimestamp(
                    float(existing_mod), tz=canvas_time.tzinfo
                )
            else:
                existing_time = datetime.datetime.fromisoformat(
                    str(existing_mod).replace("Z", "+00:00")
                )
            if canvas_time > existing_time:
                return True
        except (ValueError, TypeError):
            pass  # If parsing fails, assume changed
    return False


def display_courses_and_get_selection(courses, last_course_ids=None):
    """Displays available courses and gets user selection."""
    print("\nAvailable courses:")
    for i, course in enumerate(courses, 1):
        course_name = course.get("name", "Unnamed")
        course_code = course.get("course_code", "")
        marker = (
            " (last selected)"
            if last_course_ids and str(course.get("id")) in last_course_ids
            else ""
        )
        print(f"{i}. {course_name} ({course_code}){marker}")

    print("\nOptions:")
    print("- Enter course numbers separated by commas (e.g., 1,3,5)")
    print("- Enter 'all' to select all courses")
    print("- Enter 'last' to use last selection" if last_course_ids else "")
    print("- Enter 'quit' to exit")

    while True:
        try:
            user_input = input("\nSelect courses to sync: ").strip().lower()

            if user_input == "quit":
                return []

            if user_input == "all":
                return courses

            if user_input == "last" and last_course_ids:
                # Find courses that match the last selected IDs

                last_courses = [
                    course
                    for course in courses
                    if str(course.get("id")) in last_course_ids
                ]
                if last_courses:
                    print(f"Using last selection: {len(last_courses)} course(s)")
                    return last_courses
                else:
                    print(
                        "Last selected courses are no longer available. Please select manually."
                    )
                    continue

            # Parse comma-separated numbers
            selections = []
            for part in user_input.split(","):
                part = part.strip()
                if part.isdigit():
                    idx = int(part) - 1
                    if 0 <= idx < len(courses):
                        selections.append(courses[idx])
                    else:
                        print(f"Invalid course number: {int(part)}")
                        selections = []
                        break
                else:
                    print(f"Invalid input: {part}")
                    selections = []
                    break

            if selections:
                return selections
            else:
                print("No valid courses selected. Please try again.")

        except KeyboardInterrupt:
            print("\nOperation cancelled.")
            return []
        except Exception as e:
            print(f"Error processing selection: {e}")
            return []


def save_last_selection(selected_courses):
    """Saves the selected course IDs to config file."""
    if not selected_courses:
        return

    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)

    if not config.has_section("LAST_SELECTION"):
        config.add_section("LAST_SELECTION")

    course_ids = [
        str(course.get("id")) for course in selected_courses if course.get("id")
    ]
    config.set("LAST_SELECTION", "COURSE_IDS", ",".join(course_ids))

    with open(CONFIG_FILE, "w") as configfile:
        config.write(configfile)


def load_last_selection():
    """Loads the last selected course IDs from config file."""
    if not os.path.exists(CONFIG_FILE):
        return None

    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)

    if config.has_section("LAST_SELECTION") and config.has_option(
        "LAST_SELECTION", "COURSE_IDS"
    ):
        course_ids_str = config.get("LAST_SELECTION", "COURSE_IDS").strip()
        if course_ids_str:
            return set(course_ids_str.split(","))

    return None


def get_canvas_quizzes(
    course_id: int,
    session: requests.Session,
    api_url: str,
    api_key: str,
    timeout: int = 30,
    per_page: int = 100,
    on_endpoint_unavailable=None,
) -> list:
    """
    Fetch quizzes for a given Canvas course using the Quizzes API.
    Returns a list of quiz dicts, or empty list on error.
    """
    url = f"{(api_url or '').rstrip('/')}/api/v1/courses/{course_id}/quizzes"
    headers = {"Authorization": f"Bearer {api_key}"}
    quizzes = []
    params = {"per_page": per_page}
    try:
        while url:
            response = session.get(url, headers=headers, params=params, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            quizzes.extend(data)
            # Handle pagination
            link = response.headers.get("Link", "")
            next_url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    next_url = part[part.find("<") + 1 : part.find(">")]
                    break
            url = next_url
            params = {}  # Only use params on first request
    except requests.RequestException as e:
        if _is_endpoint_unavailable_error(e):
            status_code = e.response.status_code if e.response is not None else None
            print(
                f"Quizzes endpoint appears unavailable for course {course_id} (HTTP {status_code})."
            )
            if on_endpoint_unavailable:
                on_endpoint_unavailable("EXPORT_QUIZZES", status_code)
        print(f"Error fetching quizzes for course {course_id}: {e}")
        return []
    return quizzes


# --- Local Storage Functions ---


def get_or_create_local_folder(local_root_dir, folder_name, parent_path=None):
    """Creates a local folder if it doesn't exist. Returns the full path."""
    folder_name = sanitize_folder_name(folder_name)
    if parent_path:
        folder_path = os.path.join(parent_path, folder_name)
    else:
        folder_path = os.path.join(local_root_dir, folder_name)

    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"Created local folder: '{folder_path}'")
    return folder_path


def get_existing_files_in_local_folder(folder_path):
    """Returns a set of filenames that already exist in a local folder."""
    if not os.path.exists(folder_path):
        return set()
    try:
        return {
            f
            for f in os.listdir(folder_path)
            if os.path.isfile(os.path.join(folder_path, f))
        }
    except OSError as error:
        print(f"Error reading local folder '{folder_path}': {error}")
        return set()


def save_file_locally(local_path, filename, folder_path):
    """Moves a file from temp directory to the specified local folder."""
    if not os.path.exists(local_path):
        return False
    try:
        destination_path = os.path.join(folder_path, filename)
        shutil.move(local_path, destination_path)
        print(f"Saved '{filename}' to local storage: '{folder_path}'")
        return True
    except OSError as error:
        print(f"An error occurred saving file locally: {error}")
        return False


def get_paginated_canvas_items(
    url,
    headers,
    session: Optional[requests.Session],
    timeout: int,
    per_page: int,
    suppress_errors: bool = False,
):
    """Handles Canvas API pagination to retrieve all items from an endpoint using a shared session, with per_page sizing."""
    if session is None:
        session = requests.Session()
    # Append per_page if not already present
    if "per_page=" not in url:
        url += ("&" if "?" in url else "?") + f"per_page={per_page}"
    items, next_url = [], url
    while next_url:
        try:
            response = session.get(next_url, headers=headers, timeout=timeout)
            response.raise_for_status()
            items.extend(response.json())
            next_url = None
            if "Link" in response.headers:
                links = requests.utils.parse_header_links(response.headers["Link"])
                next_url = next(
                    (link["url"] for link in links if link.get("rel") == "next"), None
                )
        except requests.exceptions.RequestException as e:
            if not suppress_errors:
                print(f"Error fetching data from Canvas: {e}")
            break
    return items


def download_canvas_file(
    file_url, local_path, headers, session: Optional[requests.Session], timeout: int
):
    """Downloads a file from a Canvas URL to a local path."""
    if session is None:
        session = requests.Session()
    try:
        with session.get(file_url, headers=headers, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return True
    except requests.exceptions.RequestException as e:
        print(f"Failed to download {file_url}: {e}")
        return False


# --- Main Sync Logic ---


def process_canvas_file(
    file_info,
    folder_path,
    processed_canvas_file_ids,
    canvas_headers,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    summary: Optional[SummaryCollector] = None,
    course_name: Optional[str] = None,
    dest_label: Optional[str] = None,
):
    """Helper function to check, download, and save/upload a single Canvas file."""
    file_id = file_info.get("id")
    filename = file_info.get("display_name")
    file_download_url = file_info.get("url")
    file_size = file_info.get("size")
    file_updated_at = file_info.get("updated_at")

    if (
        not all([file_id, filename, file_download_url])
        or file_id in processed_canvas_file_ids
    ):
        return 0

    processed_canvas_file_ids.add(file_id)

    existing_metadata = get_existing_file_metadata_local(folder_path, filename)

    # Check if file has changed
    if not has_file_changed(
        existing_metadata, canvas_size=file_size, canvas_updated_at=file_updated_at
    ):
        return 0  # No change

    print(f"{'Updating' if existing_metadata else 'New'} file found: '{filename}'")
    local_filepath = os.path.join(DOWNLOAD_DIR, filename)
    if download_canvas_file(
        file_download_url, local_filepath, canvas_headers, session, timeout
    ):
        success = save_file_locally(local_filepath, filename, folder_path)

        if success:
            if filename.lower().endswith(".pdf") and opendataloader_pdf:
                pdf_path = os.path.join(folder_path, filename)
                print(f"Extracting '{filename}' using Hybrid Mode...")
                try:
                    opendataloader_pdf.convert(
                        input_path=[pdf_path],
                        output_dir=folder_path,
                        format="markdown",
                        hybrid="docling-fast",
                        hybrid_fallback=True,
                        quiet=True,
                    )
                except Exception as e:
                    if summary and course_name and dest_label:
                        summary.add_file(
                            course_name,
                            dest_label,
                            filename,
                            "failed_extraction",
                        )
                    print(f"  -> Error extracting {filename}: {e}")
                    return 0

            # Record in summary only after optional extraction succeeds
            if summary and course_name and dest_label:
                summary.add_file(
                    course_name,
                    dest_label,
                    filename,
                    "updated" if existing_metadata else "created",
                )
            return 1
        else:
            # If save/upload failed, remove the downloaded file
            if os.path.exists(local_filepath):
                os.remove(local_filepath)
    return 0


def process_canvas_assignment(
    assignment_info,
    assignments_root_path,
    processed_canvas_file_ids,
    canvas_api_url,
    canvas_headers,
    force_regen_assignments=False,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    summary: Optional[SummaryCollector] = None,
    course_name: Optional[str] = None,
):
    """Saves an assignment's details and linked files."""
    if session is None:
        session = requests.Session()
    new_items_count = 0
    assignment_name = assignment_info.get("name")
    description = assignment_info.get("description")
    due_at = assignment_info.get("due_at")
    points_possible = assignment_info.get("points_possible")
    rubric = assignment_info.get("rubric") or assignment_info.get("rubric_settings")
    updated_at = assignment_info.get("updated_at")

    if not assignment_name:
        return 0

    safe_assignment_name = sanitize_filename(assignment_name)
    assignment_folder_name = safe_assignment_name

    assignment_storage_path = get_or_create_local_folder(
        assignments_root_path, assignment_folder_name
    )
    md_filename = f"{safe_assignment_name}.md"
    existing_metadata = get_existing_file_metadata_local(
        assignment_storage_path, md_filename
    )

    # Check if assignment has changed (or force regeneration via config)
    if not force_regen_assignments and not has_file_changed(
        existing_metadata, canvas_updated_at=updated_at
    ):
        # Still need to process linked files, but skip generation
        pass
    else:
        print(
            f"{'Updating' if existing_metadata else 'New'} assignment found: '{assignment_name}'"
        )
        local_md_path = os.path.join(DOWNLOAD_DIR, md_filename)
        try:
            md_lines = []
            md_lines.append(f"# {assignment_name}\\n")
            if due_at:
                md_lines.append(f"**Due:** {due_at}")
            else:
                md_lines.append(f"**Due:** N/A")

            if points_possible:
                md_lines.append(f"**Points:** {points_possible}\\n")
            else:
                md_lines.append(f"**Points:** N/A\\n")

            if rubric and len(rubric) > 0:
                md_lines.append("## Rubric\\n")
                for criterion in rubric:
                    if isinstance(criterion, dict):
                        desc = criterion.get("description", "")
                        pts = criterion.get("points", 0)
                        md_lines.append(f"### {desc} ({pts} points)")
                        if criterion.get("long_description"):
                            plain = BeautifulSoup(
                                criterion["long_description"], "html.parser"
                            ).get_text(" ", strip=True)
                            md_lines.append(f"*{plain}*")
                        ratings = criterion.get("ratings", [])
                        for rating in ratings:
                            r_desc = rating.get("description", "")
                            r_pts = rating.get("points", 0)
                            md_lines.append(f"- {r_desc} ({r_pts} points)")
                            if rating.get("long_description"):
                                r_plain = BeautifulSoup(
                                    rating["long_description"], "html.parser"
                                ).get_text(" ", strip=True)
                                md_lines.append(f"  *{r_plain}*")
                        md_lines.append("")
                md_lines.append("---")

            if description:
                md_lines.append(description)

            with open(local_md_path, "w", encoding="utf-8") as out:
                out.write("\n".join(md_lines))

            success = save_file_locally(
                local_md_path,
                md_filename,
                assignment_storage_path,
            )
            if success:
                new_items_count += 1
                if summary and course_name:
                    dest_label = f"{course_name}/Assignments/{assignment_folder_name}"
                    summary.add_file(
                        course_name,
                        dest_label,
                        md_filename,
                        "updated" if existing_metadata else "created",
                    )
        except Exception as e:
            print(f"Could not save assignment '{assignment_name}' as Markdown: {e}")

    # Avoid listing entire folder contents to reduce API calls; rely on per-file metadata checks.

    # Scan the assignment description for linked files
    if description:
        soup = BeautifulSoup(description, "html.parser")
        for link in soup.find_all("a", href=True):
            if not isinstance(link, Tag):
                continue
            href = link.get("href", "")
            if not isinstance(href, str):
                continue
            match = re.search(r"/files/(\d+)", href)
            if match:
                file_id = match.group(1)
                file_api_url = f"{canvas_api_url}/api/v1/files/{file_id}"
                try:
                    file_info_resp = session.get(
                        file_api_url, headers=canvas_headers, timeout=timeout
                    )
                    file_info_resp.raise_for_status()
                    if file_info_resp.ok:
                        new_items_count += process_canvas_file(
                            file_info_resp.json(),
                            assignment_storage_path,
                            processed_canvas_file_ids,
                            canvas_headers,
                            session=session,
                            timeout=timeout,
                            summary=summary,
                            course_name=course_name,
                            dest_label=f"{course_name}/Assignments/{assignment_folder_name}",
                        )
                except requests.RequestException as e:
                    print(f"Could not fetch file link from assignment: {e}")

    return new_items_count


def _parse_iso_utc(dt_str: str):
    """Parse an ISO 8601 string (potentially with trailing 'Z') into a timezone-aware datetime in UTC.

    Returns None if parsing fails or input is falsy.
    """
    if not dt_str or not isinstance(dt_str, str):
        return None
    try:
        from datetime import datetime, timezone

        ds = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ds)
        # Ensure tz-aware and in UTC
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _to_utc_datetime(value):
    """Best-effort conversion of various timestamp representations to UTC datetime.

    Supports:
    - ISO 8601 strings (with or without 'Z')
    - POSIX timestamps (float/int seconds since epoch)
    Returns None if conversion fails.
    """
    from datetime import datetime, timezone

    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        return _parse_iso_utc(value)
    return None


def _max_iso_datetime(values: List[str]):
    """Return the max ISO timestamp (UTC) from a list of timestamp strings."""
    try:
        from datetime import timezone

        timestamps = [_parse_iso_utc(v) for v in values if v]
        timestamps = [t for t in timestamps if t]
        if not timestamps:
            return None
        return max(timestamps).astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _max_timestamp_from_items(items: List[Dict], keys: List[str]):
    """Best-effort extraction of the newest timestamp from a list of dict items."""
    if not items:
        return None
    collected = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = item.get(key)
            if value:
                collected.append(value)
    return _max_iso_datetime(collected)


def _should_regenerate_resource(existing_metadata, newest_iso: Optional[str]):
    """Decide whether to regenerate a derived resource based on newest item timestamp."""
    if not existing_metadata:
        return True
    if newest_iso:
        existing_dt = _to_utc_datetime(existing_metadata.get("modified_time"))
        newest_dt = _parse_iso_utc(newest_iso)
        if existing_dt and newest_dt and newest_dt <= existing_dt:
            return False
    return True


def _export_json_resource(
    data,
    filename: str,
    folder_path,
    existing_metadata=None,
    summary: Optional[SummaryCollector] = None,
    course_name: Optional[str] = None,
    dest_label: Optional[str] = None,
):
    """Serialize data to JSON, save locally, record summary, and cleanup temp file."""
    local_json_path = os.path.join(DOWNLOAD_DIR, filename)
    try:
        with open(local_json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        success = save_file_locally(local_json_path, filename, folder_path)

        if success and summary and course_name and dest_label:
            summary.add_file(
                course_name,
                dest_label,
                filename,
                "updated" if existing_metadata else "created",
            )
        return 1 if success else 0
    finally:
        if os.path.exists(local_json_path):
            try:
                os.remove(local_json_path)
            except OSError:
                pass


def _get_bool_config(
    config: configparser.ConfigParser,
    section: str,
    option: str,
    default: bool,
) -> bool:
    if not config.has_section(section):
        return default
    try:
        value = config.get(section, option, fallback=str(default)).strip().lower()
        return value in {"1", "true", "yes", "y", "on"}
    except Exception:
        return default


def _is_endpoint_unavailable_error(exc: requests.RequestException) -> bool:
    """Return True when Canvas endpoint is inaccessible for this tenant (403/404)."""
    response = getattr(exc, "response", None)
    if response is None:
        return False
    return response.status_code in {403, 404}


def _persist_export_toggle(
    config: configparser.ConfigParser,
    option: str,
    value: bool,
    config_path: str = CONFIG_FILE,
) -> bool:
    """Persist one export toggle in config.ini without touching unrelated settings."""
    try:
        if not config.has_section("EXPORTS"):
            config.add_section("EXPORTS")
        config.set("EXPORTS", option, "true" if value else "false")
        with open(config_path, "w", encoding="utf-8") as configfile:
            config.write(configfile)
        return True
    except OSError as error:
        print(f"Could not persist {option} in {config_path}: {error}")
        return False


def _get_canvas_page_key(page: Dict[str, Any]) -> Optional[str]:
    """Return a stable identity key for a Canvas page dict."""
    if not isinstance(page, dict):
        return None

    page_id = page.get("page_id") or page.get("id")
    if page_id:
        return f"id:{page_id}"

    slug = page.get("url") or page.get("page_url")
    if slug:
        return f"slug:{slug}"

    html_url = page.get("html_url")
    if html_url:
        return f"html:{html_url}"

    title = page.get("title")
    if title:
        return f"title:{title}"

    return None


def _extract_course_page_slugs_from_html(html_body: str, course_id: int) -> Set[str]:
    """Extract Canvas page slugs from HTML links for a specific course."""
    if not html_body:
        return set()

    slug_pattern = re.compile(
        rf"(?:https?://[^\"'\s]+)?/courses/{course_id}/pages/([^\"'#?\s/]+)",
        re.IGNORECASE,
    )
    found = set()
    for match in slug_pattern.findall(html_body):
        if isinstance(match, str) and match.strip():
            found.add(unquote(match.strip()))
    return found


def process_canvas_discussion_topic(
    topic_info,
    course_id: int,
    discussions_root_path,
    processed_canvas_file_ids,
    canvas_api_url,
    canvas_headers,
    session=None,
    timeout=20,
    summary=None,
    course_name=None,
):
    """Saves a discussion topic and its entries into a PDF."""
    if session is None:
        session = requests.Session()

    new_items_count = 0
    topic_id = topic_info.get("id")
    topic_title = topic_info.get("title")
    message = topic_info.get("message") or ""
    author = topic_info.get("user_name") or "Unknown"
    posted_at = topic_info.get("posted_at") or topic_info.get("created_at")
    updated_at = topic_info.get("last_reply_at") or topic_info.get("updated_at")

    if not topic_title:
        return 0

    safe_topic_title = sanitize_filename(topic_title)
    # Create a subfolder for the discussion topic
    topic_storage_path = get_or_create_local_folder(
        discussions_root_path, safe_topic_title
    )

    md_filename = f"{safe_topic_title}.md"
    existing_metadata = get_existing_file_metadata_local(
        topic_storage_path, md_filename
    )

    md_already_exists = not has_file_changed(
        existing_metadata, canvas_updated_at=updated_at
    )

    entries_url = f"{canvas_api_url}/api/v1/courses/{course_id}/discussion_topics/{topic_id}/entries"
    entries = get_paginated_canvas_items(
        entries_url, canvas_headers, session, timeout, 100, suppress_errors=True
    )

    if not md_already_exists:
        print(
            f"{'Updating' if existing_metadata else 'New'} discussion topic found: '{topic_title}'"
        )
        local_md_path = os.path.join(DOWNLOAD_DIR, md_filename)
        try:
            md_lines = []

            md_lines.append(f"# {topic_title}\\n")
            md_lines.append(f"**Author:** {author}")
            if posted_at:
                md_lines.append(f"**Posted:** {posted_at}")
            md_lines.append("\n**Prompt:**\n")
            if message:
                md_lines.append(message)

            md_lines.append("\n---\n\n## Replies\n")
            if entries:
                for entry in entries:
                    e_author = entry.get("user_name") or "Unknown"
                    e_date = entry.get("created_at") or ""
                    e_message = entry.get("message") or ""

                    md_lines.append(f"### {e_author} - _{e_date}_\\n")
                    if e_message:
                        md_lines.append(e_message)
                    md_lines.append("\\n")

            with open(local_md_path, "w", encoding="utf-8") as out:
                out.write("\n".join(md_lines))

            success = save_file_locally(
                local_md_path,
                md_filename,
                topic_storage_path,
            )
            if success:
                new_items_count += 1
                if summary and course_name:
                    dest_label = f"{course_name}/Discussions/{safe_topic_title}"
                    summary.add_file(
                        course_name,
                        dest_label,
                        md_filename,
                        "updated" if existing_metadata else "created",
                    )
        except Exception as e:
            import traceback

            traceback.print_exc()
            print(f"Could not save discussion '{topic_title}' as Markdown: {e}")

    # Process linked files in the topic prompt and entries
    def extract_and_process_files(html_content, label):
        nonlocal new_items_count
        if not html_content:
            return
        soup = BeautifulSoup(html_content, "html.parser")
        for link in soup.find_all("a", href=True):
            if not isinstance(link, Tag):
                continue
            href = link.get("href", "")
            if not isinstance(href, str):
                continue
            # Look for Canvas file links
            match = re.search(r"/files/(\d+)", href)
            if match:
                file_id_from_link = match.group(1)
                # Check if we've already handled this file ID in this sync run
                if int(file_id_from_link) in processed_canvas_file_ids:
                    continue

                file_api_url = f"{canvas_api_url}/api/v1/files/{file_id_from_link}"
                try:
                    file_info_resp = session.get(
                        file_api_url,
                        headers=canvas_headers,
                        timeout=timeout,
                    )
                    file_info_resp.raise_for_status()
                    file_data = file_info_resp.json()

                    added = process_canvas_file(
                        file_data,
                        topic_storage_path,
                        processed_canvas_file_ids,
                        canvas_headers,
                        session=session,
                        timeout=timeout,
                        summary=summary,
                        course_name=course_name,
                        dest_label=f"{course_name}/Discussions/{label}",
                    )
                    new_items_count += added
                except requests.RequestException as e:
                    print(
                        f"Could not fetch file link {file_id_from_link} from discussion '{topic_title}': {e}"
                    )
                except Exception as e:
                    print(
                        f"Error processing file {file_id_from_link} in discussion '{topic_title}': {e}"
                    )

    # Extract files from topic prompt
    extract_and_process_files(message, safe_topic_title)

    # Extract files from all entries
    if entries:
        for entry in entries:
            extract_and_process_files(entry.get("message"), safe_topic_title)

    return new_items_count


def collect_course_pages(
    course_id: int,
    course_name: str,
    canvas_api_url: str,
    canvas_headers: dict,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    per_page: int = DEFAULT_CANVAS_PER_PAGE,
) -> List[Dict[str, Any]]:
    """Collect course pages from Pages API and module page items with de-duplication."""
    if session is None:
        session = requests.Session()

    base_url = (canvas_api_url or "").rstrip("/")
    pages_map: Dict[str, Dict[str, Any]] = {}
    discovered_slugs: Set[str] = set()

    def add_page_and_collect_slugs(page_payload: Dict[str, Any]) -> None:
        if not isinstance(page_payload, dict):
            return
        page_key = _get_canvas_page_key(page_payload)
        if page_key and page_key not in pages_map:
            pages_map[page_key] = page_payload
        slug = page_payload.get("url") or page_payload.get("page_url")
        if isinstance(slug, str) and slug.strip():
            discovered_slugs.add(slug.strip())
        body = page_payload.get("body")
        if isinstance(body, str) and body.strip():
            discovered_slugs.update(
                _extract_course_page_slugs_from_html(body, course_id)
            )

    def fetch_page_details_by_slug(page_slug: str) -> Optional[Dict[str, Any]]:
        if not page_slug:
            return None
        page_detail_url = f"{base_url}/api/v1/courses/{course_id}/pages/{page_slug}"
        try:
            page_resp = session.get(
                page_detail_url,
                headers=canvas_headers,
                timeout=timeout,
            )
            page_resp.raise_for_status()
            return page_resp.json()
        except requests.RequestException as e:
            print(
                f"Warning: Could not fetch page details '{page_slug}' in '{course_name}': {e}"
            )
            return None

    pages_with_body_url = f"{base_url}/api/v1/courses/{course_id}/pages?include[]=body"
    pages_from_api = get_paginated_canvas_items(
        pages_with_body_url,
        canvas_headers,
        session,
        timeout,
        per_page,
        suppress_errors=True,
    )

    # Some Canvas instances reject include[]=body. Fallback to plain pages list.
    if not pages_from_api:
        pages_url = f"{base_url}/api/v1/courses/{course_id}/pages"
        pages_from_api = get_paginated_canvas_items(
            pages_url,
            canvas_headers,
            session,
            timeout,
            per_page,
            suppress_errors=True,
        )
        if pages_from_api:
            print(
                f"Pages API in '{course_name}' does not support include[]=body; using per-page detail fetch fallback."
            )

    for page in pages_from_api or []:
        # If body is missing from the list endpoint, fetch full page details by slug.
        if not page.get("body"):
            page_slug = page.get("url") or page.get("page_url")
            if page_slug:
                detailed_page = fetch_page_details_by_slug(page_slug)
                if detailed_page:
                    page = detailed_page

        add_page_and_collect_slugs(page)

    modules_url = f"{base_url}/api/v1/courses/{course_id}/modules"
    modules = get_paginated_canvas_items(
        modules_url, canvas_headers, session, timeout, per_page
    )

    for module in modules or []:
        module_id = module.get("id")
        if not module_id:
            continue

        items_url = f"{base_url}/api/v1/courses/{course_id}/modules/{module_id}/items"
        module_items = get_paginated_canvas_items(
            items_url,
            canvas_headers,
            session,
            timeout,
            per_page,
        )

        for item in module_items:
            if item.get("type") != "Page" or not item.get("url"):
                continue

            try:
                resp = session.get(item["url"], headers=canvas_headers, timeout=timeout)
                resp.raise_for_status()
                page_details = resp.json()
            except requests.RequestException as e:
                print(
                    f"Warning: Could not fetch module page details in '{course_name}': {e}"
                )
                continue

            add_page_and_collect_slugs(page_details)

    # Fallback discovery path: crawl page links found in HTML and fetch each slug directly.
    pending_slugs = list(discovered_slugs)
    visited_slugs: Set[str] = set()
    max_slug_fetches = 500

    while pending_slugs and len(visited_slugs) < max_slug_fetches:
        slug = pending_slugs.pop()
        if not isinstance(slug, str) or not slug.strip():
            continue
        slug = slug.strip()
        if slug in visited_slugs:
            continue
        visited_slugs.add(slug)

        page_details = fetch_page_details_by_slug(slug)
        if not page_details:
            continue

        before_slug_count = len(discovered_slugs)
        before_page_count = len(pages_map)
        add_page_and_collect_slugs(page_details)

        if (
            len(discovered_slugs) > before_slug_count
            or len(pages_map) > before_page_count
        ):
            for new_slug in discovered_slugs:
                if new_slug not in visited_slugs:
                    pending_slugs.append(new_slug)

    if len(visited_slugs) >= max_slug_fetches:
        print(
            f"Warning: Page link crawl limit reached in '{course_name}' ({max_slug_fetches})."
        )

    pages = list(pages_map.values())
    try:
        pages.sort(key=lambda p: (p.get("title") or "").lower())
    except Exception:
        pass
    return pages


def process_canvas_page(
    page_data,
    course_storage_path,
    processed_canvas_file_ids,
    canvas_api_url,
    canvas_headers,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    summary: Optional[SummaryCollector] = None,
    course_name: Optional[str] = None,
    processed_canvas_page_keys: Optional[set] = None,
):
    """Saves a page's details as PDF and downloads linked files from the page body."""
    if session is None:
        session = requests.Session()

    page_key = _get_canvas_page_key(page_data)
    if processed_canvas_page_keys is not None and page_key:
        if page_key in processed_canvas_page_keys:
            return 0
        processed_canvas_page_keys.add(page_key)

    page_title = page_data.get("title")
    html_body = page_data.get("body") or ""

    if not page_title:
        return 0

    new_items_count = 0
    safe_page_title = sanitize_filename(page_title)
    page_folder_name = safe_page_title

    page_storage_path = get_or_create_local_folder(
        course_storage_path, page_folder_name
    )

    if not page_storage_path:
        return 0

    md_filename = f"{safe_page_title}.md"
    updated_at = page_data.get("updated_at")

    existing_metadata = get_existing_file_metadata_local(page_storage_path, md_filename)

    if has_file_changed(existing_metadata, canvas_updated_at=updated_at):
        print(
            f"{'Updating' if existing_metadata else 'New'} page found: '{page_title}'"
        )
        local_md_path = os.path.join(DOWNLOAD_DIR, md_filename)
        try:
            md_lines = []
            md_lines.append(f"# {page_title}\\n")
            if html_body:
                md_lines.append(html_body)

            with open(local_md_path, "w", encoding="utf-8") as out:
                out.write("\n".join(md_lines))

            success = save_file_locally(
                local_md_path,
                md_filename,
                page_storage_path,
            )
            if success:
                new_items_count += 1
                if summary and course_name:
                    dest_label = f"{course_name}/{page_folder_name}"
                    summary.add_file(
                        course_name,
                        dest_label,
                        md_filename,
                        "updated" if existing_metadata else "created",
                    )
        except Exception as e:
            escaped_error = html.escape(str(e), quote=False)
            print(f"Could not save page '{page_title}' as Markdown: {escaped_error}")

    soup = BeautifulSoup(html_body, "html.parser")
    for link in soup.find_all("a", href=True):
        if not isinstance(link, Tag):
            continue
        href = link.get("href", "")
        if not isinstance(href, str):
            continue
        match = re.search(r"/files/(\d+)", href)
        if match:
            file_id_from_page = match.group(1)
            file_api_url = f"{canvas_api_url}/api/v1/files/{file_id_from_page}"
            try:
                file_info_resp = session.get(
                    file_api_url,
                    headers=canvas_headers,
                    timeout=timeout,
                )
                file_info_resp.raise_for_status()
            except requests.RequestException as e:
                print(f"Could not fetch file link from page '{page_title}': {e}")
                continue

            new_items_count += process_canvas_file(
                file_info_resp.json(),
                page_storage_path,
                processed_canvas_file_ids,
                canvas_headers,
                session=session,
                timeout=timeout,
                summary=summary,
                course_name=course_name,
                dest_label=f"{course_name}/{page_folder_name}" if course_name else None,
            )

    return new_items_count


def process_course_announcements(
    course_id: int,
    course_name: str,
    reports_folder_path_or_id,
    canvas_api_url: str,
    canvas_headers: dict,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    per_page: int = DEFAULT_CANVAS_PER_PAGE,
    summary: Optional[SummaryCollector] = None,
):
    if session is None:
        session = requests.Session()

    base_url = (canvas_api_url or "").rstrip("/")
    announcements_url = (
        f"{base_url}/api/v1/announcements?context_codes[]=course_{course_id}"
    )
    announcements = get_paginated_canvas_items(
        announcements_url,
        canvas_headers,
        session,
        timeout,
        per_page,
        suppress_errors=True,
    )
    if not announcements:
        return 0

    latest_ts = _max_timestamp_from_items(
        announcements, ["posted_at", "last_reply_at", "updated_at"]
    )
    filename = "announcements.json"
    existing_metadata = get_existing_file_metadata_local(
        reports_folder_path_or_id, filename
    )

    if not _should_regenerate_resource(existing_metadata, latest_ts):
        return 0

    print(
        f"{'Updating' if existing_metadata else 'New'} announcements for '{course_name}'"
    )
    reports_label = f"{course_name}/Reports"
    return _export_json_resource(
        announcements,
        filename,
        reports_folder_path_or_id,
        existing_metadata,
        summary,
        course_name,
        reports_label,
    )


def process_course_discussions(
    course_id: int,
    course_name: str,
    reports_folder_path_or_id,
    canvas_api_url: str,
    canvas_headers: dict,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    per_page: int = DEFAULT_CANVAS_PER_PAGE,
    summary: Optional[SummaryCollector] = None,
    discussions_folder_path=None,
):
    if session is None:
        session = requests.Session()

    base_url = (canvas_api_url or "").rstrip("/")
    discussions_url = f"{base_url}/api/v1/courses/{course_id}/discussion_topics"
    discussions = get_paginated_canvas_items(
        discussions_url,
        canvas_headers,
        session,
        timeout,
        per_page,
        suppress_errors=True,
    )
    if not discussions:
        return 0

    latest_ts = _max_timestamp_from_items(
        discussions, ["last_reply_at", "posted_at", "updated_at"]
    )
    filename = "discussion_topics.json"
    existing_metadata = get_existing_file_metadata_local(
        reports_folder_path_or_id, filename
    )

    pdf_count = 0
    if discussions_folder_path and discussions:
        for topic in discussions:
            try:
                pdf_count += process_canvas_discussion_topic(
                    topic,
                    course_id,
                    discussions_folder_path,
                    set(),
                    canvas_api_url,
                    canvas_headers,
                    session,
                    timeout,
                    summary,
                    course_name,
                )
            except Exception as e:
                print(f"Error processing PDF for topic {topic.get('title')}: {e}")

    if not _should_regenerate_resource(existing_metadata, latest_ts):
        return pdf_count

    print(
        f"{'Updating' if existing_metadata else 'New'} discussions for '{course_name}'"
    )
    reports_label = f"{course_name}/Reports"
    json_count = _export_json_resource(
        discussions,
        filename,
        reports_folder_path_or_id,
        existing_metadata,
        summary,
        course_name,
        reports_label,
    )

    return json_count + pdf_count


def process_course_quizzes(
    course_id: int,
    course_name: str,
    reports_folder_path_or_id,
    canvas_api_url: str,
    canvas_headers: dict,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    per_page: int = DEFAULT_CANVAS_PER_PAGE,
    summary: Optional[SummaryCollector] = None,
    on_endpoint_unavailable=None,
):
    if session is None:
        session = requests.Session()

    base_url = (canvas_api_url or "").rstrip("/")
    quizzes_url = f"{base_url}/api/v1/courses/{course_id}/quizzes"
    quizzes = []
    params = {"per_page": per_page}
    try:
        while quizzes_url:
            response = session.get(
                quizzes_url, headers=canvas_headers, params=params, timeout=timeout
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                quizzes.extend(data)
            link = response.headers.get("Link", "")
            next_url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    next_url = part[part.find("<") + 1 : part.find(">")]
                    break
            quizzes_url = next_url
            params = {}
    except requests.RequestException as e:
        if _is_endpoint_unavailable_error(e):
            status_code = e.response.status_code if e.response is not None else None
            print(
                f"Quizzes report endpoint appears unavailable for course {course_id} (HTTP {status_code})."
            )
            if on_endpoint_unavailable:
                on_endpoint_unavailable("EXPORT_QUIZZES", status_code)
        return 0

    if not quizzes:
        return 0

    latest_ts = _max_timestamp_from_items(quizzes, ["updated_at", "published_at"])
    filename = "quizzes.json"
    existing_metadata = get_existing_file_metadata_local(
        reports_folder_path_or_id, filename
    )

    if not _should_regenerate_resource(existing_metadata, latest_ts):
        return 0

    print(f"{'Updating' if existing_metadata else 'New'} quizzes for '{course_name}'")
    reports_label = f"{course_name}/Reports"
    return _export_json_resource(
        quizzes,
        filename,
        reports_folder_path_or_id,
        existing_metadata,
        summary,
        course_name,
        reports_label,
    )


def process_course_enrollments(
    course_id: int,
    course_name: str,
    reports_folder_path_or_id,
    canvas_api_url: str,
    canvas_headers: dict,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    per_page: int = DEFAULT_CANVAS_PER_PAGE,
    summary: Optional[SummaryCollector] = None,
):
    if session is None:
        session = requests.Session()

    base_url = (canvas_api_url or "").rstrip("/")
    enrollments_url = f"{base_url}/api/v1/courses/{course_id}/enrollments"
    enrollments = get_paginated_canvas_items(
        enrollments_url,
        canvas_headers,
        session,
        timeout,
        per_page,
        suppress_errors=True,
    )
    if not enrollments:
        return 0

    latest_ts = _max_timestamp_from_items(
        enrollments, ["updated_at", "last_activity_at", "created_at"]
    )
    filename = "enrollments.json"
    existing_metadata = get_existing_file_metadata_local(
        reports_folder_path_or_id, filename
    )

    if not _should_regenerate_resource(existing_metadata, latest_ts):
        return 0

    print(
        f"{'Updating' if existing_metadata else 'New'} enrollments for '{course_name}'"
    )
    reports_label = f"{course_name}/Reports"
    return _export_json_resource(
        enrollments,
        filename,
        reports_folder_path_or_id,
        existing_metadata,
        summary,
        course_name,
        reports_label,
    )


def process_course_calendar_events(
    course_id: int,
    course_name: str,
    reports_folder_path_or_id,
    canvas_api_url: str,
    canvas_headers: dict,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    per_page: int = DEFAULT_CANVAS_PER_PAGE,
    summary: Optional[SummaryCollector] = None,
):
    if session is None:
        session = requests.Session()

    base_url = (canvas_api_url or "").rstrip("/")
    calendar_url = (
        f"{base_url}/api/v1/calendar_events?context_codes[]=course_{course_id}"
    )
    calendar_events = get_paginated_canvas_items(
        calendar_url,
        canvas_headers,
        session,
        timeout,
        per_page,
        suppress_errors=True,
    )
    if not calendar_events:
        return 0

    latest_ts = _max_timestamp_from_items(
        calendar_events, ["updated_at", "start_at", "end_at"]
    )
    filename = "calendar_events.json"
    existing_metadata = get_existing_file_metadata_local(
        reports_folder_path_or_id, filename
    )

    if not _should_regenerate_resource(existing_metadata, latest_ts):
        return 0

    print(
        f"{'Updating' if existing_metadata else 'New'} calendar events for '{course_name}'"
    )
    reports_label = f"{course_name}/Reports"
    return _export_json_resource(
        calendar_events,
        filename,
        reports_folder_path_or_id,
        existing_metadata,
        summary,
        course_name,
        reports_label,
    )


def process_course_groups(
    course_id: int,
    course_name: str,
    reports_folder_path_or_id,
    canvas_api_url: str,
    canvas_headers: dict,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    per_page: int = DEFAULT_CANVAS_PER_PAGE,
    summary: Optional[SummaryCollector] = None,
):
    if session is None:
        session = requests.Session()

    base_url = (canvas_api_url or "").rstrip("/")
    groups_url = f"{base_url}/api/v1/courses/{course_id}/groups"
    groups = get_paginated_canvas_items(
        groups_url,
        canvas_headers,
        session,
        timeout,
        per_page,
        suppress_errors=True,
    )
    if not groups:
        return 0

    latest_ts = _max_timestamp_from_items(groups, ["updated_at", "created_at"])
    filename = "groups.json"
    existing_metadata = get_existing_file_metadata_local(
        reports_folder_path_or_id, filename
    )

    if not _should_regenerate_resource(existing_metadata, latest_ts):
        return 0

    print(f"{'Updating' if existing_metadata else 'New'} groups for '{course_name}'")
    reports_label = f"{course_name}/Reports"
    return _export_json_resource(
        groups,
        filename,
        reports_folder_path_or_id,
        existing_metadata,
        summary,
        course_name,
        reports_label,
    )


def process_course_analytics_activity(
    course_id: int,
    course_name: str,
    reports_folder_path_or_id,
    canvas_api_url: str,
    canvas_headers: dict,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    summary: Optional[SummaryCollector] = None,
    on_endpoint_unavailable=None,
):
    if session is None:
        session = requests.Session()

    base_url = (canvas_api_url or "").rstrip("/")
    analytics_url = f"{base_url}/api/v1/courses/{course_id}/analytics/activity"
    try:
        resp = session.get(analytics_url, headers=canvas_headers, timeout=timeout)
        resp.raise_for_status()
        analytics_payload = resp.json()
    except requests.RequestException as e:
        if _is_endpoint_unavailable_error(e):
            status_code = e.response.status_code if e.response is not None else None
            print(
                f"Analytics endpoint appears unavailable for course {course_id} (HTTP {status_code})."
            )
            if on_endpoint_unavailable:
                on_endpoint_unavailable("EXPORT_ANALYTICS_ACTIVITY", status_code)
        print(f"Could not fetch analytics for course {course_id}: {e}")
        return 0

    if not analytics_payload:
        return 0

    combined_items: List[Dict] = []
    if isinstance(analytics_payload, list):
        combined_items = [i for i in analytics_payload if isinstance(i, dict)]
    elif isinstance(analytics_payload, dict):
        for value in analytics_payload.values():
            if isinstance(value, list):
                combined_items.extend([i for i in value if isinstance(i, dict)])

    latest_ts = _max_timestamp_from_items(
        combined_items, ["created_at", "updated_at", "last_activity_at"]
    )
    filename = "analytics_activity.json"
    existing_metadata = get_existing_file_metadata_local(
        reports_folder_path_or_id, filename
    )

    if not _should_regenerate_resource(existing_metadata, latest_ts):
        return 0

    print(
        f"{'Updating' if existing_metadata else 'New'} analytics activity for '{course_name}'"
    )
    reports_label = f"{course_name}/Reports"
    return _export_json_resource(
        analytics_payload,
        filename,
        reports_folder_path_or_id,
        existing_metadata,
        summary,
        course_name,
        reports_label,
    )


def process_course_gradebook_history(
    course_id: int,
    course_name: str,
    reports_folder_path_or_id,
    canvas_api_url: str,
    canvas_headers: dict,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    summary: Optional[SummaryCollector] = None,
    on_endpoint_unavailable=None,
):
    if session is None:
        session = requests.Session()

    base_url = (canvas_api_url or "").rstrip("/")
    history_url = f"{base_url}/api/v1/courses/{course_id}/gradebook_history/feed"
    try:
        resp = session.get(history_url, headers=canvas_headers, timeout=timeout)
        resp.raise_for_status()
        history_payload = resp.json()
    except requests.RequestException as e:
        if _is_endpoint_unavailable_error(e):
            status_code = e.response.status_code if e.response is not None else None
            print(
                f"Gradebook history endpoint appears unavailable for course {course_id} (HTTP {status_code})."
            )
            if on_endpoint_unavailable:
                on_endpoint_unavailable("EXPORT_GRADEBOOK_HISTORY", status_code)
        print(f"Could not fetch gradebook history for course {course_id}: {e}")
        return 0

    if not history_payload:
        return 0

    latest_ts = _max_timestamp_from_items(
        history_payload, ["graded_at", "posted_at", "created_at", "updated_at"]
    )
    filename = "gradebook_history.json"
    existing_metadata = get_existing_file_metadata_local(
        reports_folder_path_or_id, filename
    )

    if not _should_regenerate_resource(existing_metadata, latest_ts):
        return 0

    print(
        f"{'Updating' if existing_metadata else 'New'} gradebook history for '{course_name}'"
    )
    reports_label = f"{course_name}/Reports"
    return _export_json_resource(
        history_payload,
        filename,
        reports_folder_path_or_id,
        existing_metadata,
        summary,
        course_name,
        reports_label,
    )


def process_course_submissions_summary(
    course_id: int,
    course_name: str,
    assignments: List[Dict],
    reports_folder_path_or_id,
    canvas_api_url: str,
    canvas_headers: dict,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    per_page: int = DEFAULT_CANVAS_PER_PAGE,
    summary: Optional[SummaryCollector] = None,
):
    if session is None:
        session = requests.Session()

    if not assignments:
        return 0

    base_url = (canvas_api_url or "").rstrip("/")
    collected_submissions = []

    for assignment in assignments:
        assignment_id = assignment.get("id")
        if not assignment_id:
            continue
        assignment_name = assignment.get("name")
        submissions_url = f"{base_url}/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions"
        submissions = get_paginated_canvas_items(
            submissions_url,
            canvas_headers,
            session,
            timeout,
            per_page,
            suppress_errors=True,
        )
        for submission in submissions or []:
            if not isinstance(submission, dict):
                continue
            collected_submissions.append(
                {
                    "assignment_id": assignment_id,
                    "assignment_name": assignment_name,
                    "id": submission.get("id"),
                    "user_id": submission.get("user_id"),
                    "submitted_at": submission.get("submitted_at"),
                    "graded_at": submission.get("graded_at"),
                    "posted_at": submission.get("posted_at"),
                    "workflow_state": submission.get("workflow_state"),
                    "score": submission.get("score"),
                    "grade": submission.get("grade"),
                    "attempt": submission.get("attempt"),
                }
            )

    if not collected_submissions:
        return 0

    latest_ts = _max_timestamp_from_items(
        collected_submissions, ["graded_at", "posted_at", "submitted_at"]
    )
    filename = "submissions_summary.json"
    existing_metadata = get_existing_file_metadata_local(
        reports_folder_path_or_id, filename
    )

    if not _should_regenerate_resource(existing_metadata, latest_ts):
        return 0

    print(
        f"{'Updating' if existing_metadata else 'New'} submissions summary for '{course_name}'"
    )
    reports_label = f"{course_name}/Reports"
    return _export_json_resource(
        collected_submissions,
        filename,
        reports_folder_path_or_id,
        existing_metadata,
        summary,
        course_name,
        reports_label,
    )


def process_inbox_conversations(
    root_storage_path,
    canvas_api_url: str,
    canvas_headers: dict,
    session: Optional[requests.Session] = None,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    per_page: int = DEFAULT_CANVAS_PER_PAGE,
    summary: Optional[SummaryCollector] = None,
):
    """Fetch user inbox conversations (global, not course-specific)."""
    if session is None:
        session = requests.Session()

    base_url = (canvas_api_url or "").rstrip("/")
    conversations_url = f"{base_url}/api/v1/conversations"
    conversations = get_paginated_canvas_items(
        conversations_url,
        canvas_headers,
        session,
        timeout,
        per_page,
        suppress_errors=True,
    )
    if not conversations:
        return 0

    latest_ts = _max_timestamp_from_items(
        conversations, ["last_message_at", "updated_at"]
    )
    conversations_folder = get_or_create_local_folder(
        root_storage_path, "Conversations"
    )

    if not conversations_folder:
        return 0

    filename = "conversations.json"
    existing_metadata = get_existing_file_metadata_local(conversations_folder, filename)

    if not _should_regenerate_resource(existing_metadata, latest_ts):
        return 0

    print(f"{'Updating' if existing_metadata else 'New'} inbox conversations archive")
    return _export_json_resource(
        conversations,
        filename,
        conversations_folder,
        existing_metadata,
        summary,
        "Inbox",
        "Inbox/Conversations",
    )


def main():
    """Main function to run the sync process."""
    print("--- Starting Canvas to Storage Sync ---")
    summary = SummaryCollector()

    config = configparser.ConfigParser()
    if not os.path.exists(CONFIG_FILE):
        print(f"ERROR: Config file '{CONFIG_FILE}' not found.")
        return
    config.read(CONFIG_FILE)

    try:
        canvas_api_url = config["CANVAS"]["API_URL"]
        canvas_api_key = config["CANVAS"]["API_KEY"]
        storage_type = config["STORAGE"]["STORAGE_TYPE"].lower()
        # Optional: force regenerate assignment PDFs regardless of Canvas updated_at
        force_regen_assignments = config["STORAGE"].get(
            "FORCE_REGENERATE_ASSIGNMENTS", "false"
        ).strip().lower() in {"1", "true", "yes", "y", "on"}

        if storage_type != "local":
            print(
                f"ERROR: Unsupported STORAGE_TYPE '{storage_type}'. "
                "This branch supports local storage only. "
                "Set STORAGE_TYPE=local and configure LOCAL_ROOT_DIR."
            )
            return
        local_root_dir = config["STORAGE"]["LOCAL_ROOT_DIR"]
    except KeyError as e:
        print(f"ERROR: Missing config key in {CONFIG_FILE}: {e}")
        return

    canvas_headers = {"Authorization": f"Bearer {canvas_api_key}"}

    root_storage_path = os.path.abspath(local_root_dir)
    if not os.path.exists(root_storage_path):
        os.makedirs(root_storage_path)
    print(f"Syncing to local directory: '{root_storage_path}'")

    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR)

    # Performance tuning from config (optional)
    try:
        perf_cfg = config["PERFORMANCE"] if config.has_section("PERFORMANCE") else {}
        request_timeout = int(perf_cfg.get("REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT))
        max_retries = int(perf_cfg.get("MAX_RETRIES", DEFAULT_MAX_RETRIES))
        backoff_factor = float(perf_cfg.get("BACKOFF_FACTOR", DEFAULT_BACKOFF_FACTOR))
        canvas_per_page = int(perf_cfg.get("CANVAS_PER_PAGE", DEFAULT_CANVAS_PER_PAGE))
        http_pool_maxsize = int(
            perf_cfg.get("HTTP_POOL_MAXSIZE", DEFAULT_HTTP_POOL_MAXSIZE)
        )
    except Exception:
        request_timeout = DEFAULT_REQUEST_TIMEOUT
        max_retries = DEFAULT_MAX_RETRIES
        backoff_factor = DEFAULT_BACKOFF_FACTOR
        canvas_per_page = DEFAULT_CANVAS_PER_PAGE
        http_pool_maxsize = DEFAULT_HTTP_POOL_MAXSIZE

    # Export toggles
    export_announcements = _get_bool_config(
        config, "EXPORTS", "EXPORT_ANNOUNCEMENTS", True
    )
    export_discussions = _get_bool_config(config, "EXPORTS", "EXPORT_DISCUSSIONS", True)
    export_quizzes = _get_bool_config(config, "EXPORTS", "EXPORT_QUIZZES", True)
    export_enrollments = _get_bool_config(config, "EXPORTS", "EXPORT_ENROLLMENTS", True)
    export_calendar_events = _get_bool_config(
        config, "EXPORTS", "EXPORT_CALENDAR_EVENTS", True
    )
    export_groups = _get_bool_config(config, "EXPORTS", "EXPORT_GROUPS", True)
    export_analytics = _get_bool_config(
        config, "EXPORTS", "EXPORT_ANALYTICS_ACTIVITY", True
    )
    export_gradebook = _get_bool_config(
        config, "EXPORTS", "EXPORT_GRADEBOOK_HISTORY", True
    )
    export_submissions = _get_bool_config(
        config, "EXPORTS", "EXPORT_SUBMISSIONS_SUMMARY", False
    )
    export_inbox = _get_bool_config(
        config, "EXPORTS", "EXPORT_INBOX_CONVERSATIONS", False
    )

    runtime_export_flags = {
        "EXPORT_QUIZZES": export_quizzes,
        "EXPORT_ANALYTICS_ACTIVITY": export_analytics,
        "EXPORT_GRADEBOOK_HISTORY": export_gradebook,
    }
    disabled_endpoints_this_run: Dict[str, int] = {}

    def disable_export_after_endpoint_failure(option: str, status_code: Optional[int]):
        if option in disabled_endpoints_this_run:
            return
        runtime_export_flags[option] = False
        disabled_endpoints_this_run[option] = status_code or 0
        persisted = _persist_export_toggle(config, option, False)
        if persisted:
            print(
                f"Auto-disabled {option} after HTTP {status_code}. Saved to {CONFIG_FILE}."
            )
        else:
            print(f"Auto-disabled {option} after HTTP {status_code} for this run only.")

    # Shared HTTP session with retries and connection pooling

    session = requests.Session()
    retries = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST", "PUT", "PATCH"),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retries,
        pool_connections=http_pool_maxsize,
        pool_maxsize=http_pool_maxsize,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    print("\nFetching courses from Canvas...")
    courses_url = f"{canvas_api_url}/api/v1/courses"
    courses = get_paginated_canvas_items(
        courses_url, canvas_headers, session, request_timeout, canvas_per_page
    )
    if not courses:
        print("No courses found.")
        return

    # Filter out restricted courses
    available_courses = [
        course
        for course in courses
        if course.get("id") and not course.get("access_restricted_by_date")
    ]

    if not available_courses:
        print("No available courses found (all may be restricted).")
        return

    # Load last selection
    last_course_ids = load_last_selection()

    # Get user selection
    selected_courses = display_courses_and_get_selection(
        available_courses, last_course_ids
    )

    if not selected_courses:
        print("No courses selected. Exiting.")
        return

    # Save the selection for next time
    save_last_selection(selected_courses)

    print(f"\nSelected {len(selected_courses)} course(s) to sync.")

    for course in selected_courses:
        course_name, course_id = course.get("name", "Unnamed"), course.get("id")
        course_folder_name = sanitize_folder_name(
            course_name, fallback="Unnamed Course"
        )

        print(f"\n--- Processing Course: {course_name} ---")

        # --- Process Quizzes ---
        if runtime_export_flags["EXPORT_QUIZZES"]:
            print("Searching for quizzes...")
            quizzes = get_canvas_quizzes(
                course_id,
                session,
                canvas_api_url,
                canvas_api_key,
                timeout=request_timeout,
                per_page=canvas_per_page,
                on_endpoint_unavailable=disable_export_after_endpoint_failure,
            )
            if quizzes:
                print(f"Found {len(quizzes)} quizzes in '{course_name}':")
                for quiz in quizzes:
                    title = quiz.get("title", "(untitled)")
                    due_at = quiz.get("due_at", "N/A")
                    points = quiz.get("points_possible", "N/A")
                    print(f"  - {title} | Due: {due_at} | Points: {points}")
            else:
                print("No quizzes found.")

        course_storage_path = get_or_create_local_folder(
            local_root_dir, course_folder_name
        )

        # Prepare per-course reports folder for aggregated exports
        reports_folder_path = get_or_create_local_folder(course_storage_path, "Reports")

        processed_canvas_file_ids = set()
        processed_canvas_page_keys = set()
        new_items_synced = 0

        # --- Process Assignments ---
        print("Searching for assignments...")
        assignments_url = (
            f"{canvas_api_url}/api/v1/courses/{course_id}/assignments?include[]=rubric"
        )
        assignments = get_paginated_canvas_items(
            assignments_url, canvas_headers, session, request_timeout, canvas_per_page
        )
        if assignments:
            assignments_folder_path = get_or_create_local_folder(
                course_storage_path, "Assignments"
            )

            if assignments_folder_path:
                for assignment in assignments:
                    new_items_synced += process_canvas_assignment(
                        assignment,
                        assignments_folder_path,
                        processed_canvas_file_ids,
                        canvas_api_url,
                        canvas_headers,
                        force_regen_assignments=force_regen_assignments,
                        session=session,
                        timeout=request_timeout,
                        summary=summary,
                        course_name=course_name,
                    )

        # --- Process Modules (Files and Pages) ---
        print("Searching for files and pages in modules...")
        modules_url = f"{canvas_api_url}/api/v1/courses/{course_id}/modules"
        modules = get_paginated_canvas_items(
            modules_url, canvas_headers, session, request_timeout, canvas_per_page
        )

        for module in modules:
            items_url = f"{canvas_api_url}/api/v1/courses/{course_id}/modules/{module['id']}/items"
            module_items = get_paginated_canvas_items(
                items_url, canvas_headers, session, request_timeout, canvas_per_page
            )

            for item in module_items:
                try:
                    # Case 1: Item is a direct file link
                    if item.get("type") == "File":
                        file_details_resp = session.get(
                            item["url"], headers=canvas_headers, timeout=request_timeout
                        )
                        file_details_resp.raise_for_status()
                        new_items_synced += process_canvas_file(
                            file_details_resp.json(),
                            course_storage_path,
                            processed_canvas_file_ids,
                            canvas_headers,
                            session=session,
                            timeout=request_timeout,
                            summary=summary,
                            course_name=course_name,
                            dest_label=f"{course_name}",
                        )

                    # Case 2: Item is a Page, which we save as an HTML file
                    elif item.get("type") == "Page":
                        page_resp = session.get(
                            item["url"], headers=canvas_headers, timeout=request_timeout
                        )
                        page_resp.raise_for_status()
                        page_data = page_resp.json()
                        new_items_synced += process_canvas_page(
                            page_data,
                            course_storage_path,
                            processed_canvas_file_ids,
                            canvas_api_url,
                            canvas_headers,
                            session=session,
                            timeout=request_timeout,
                            summary=summary,
                            course_name=course_name,
                            processed_canvas_page_keys=processed_canvas_page_keys,
                        )

                except requests.exceptions.RequestException as e:
                    print(f"Could not retrieve details for a module item: {e}")
                except Exception as e:
                    print(f"An unexpected error occurred processing module item: {e}")

        print("Searching for pages outside modules...")
        discovered_pages = collect_course_pages(
            course_id=course_id,
            course_name=course_name,
            canvas_api_url=canvas_api_url,
            canvas_headers=canvas_headers,
            session=session,
            timeout=request_timeout,
            per_page=canvas_per_page,
        )
        for page_data in discovered_pages:
            new_items_synced += process_canvas_page(
                page_data,
                course_storage_path,
                processed_canvas_file_ids,
                canvas_api_url,
                canvas_headers,
                session=session,
                timeout=request_timeout,
                summary=summary,
                course_name=course_name,
                processed_canvas_page_keys=processed_canvas_page_keys,
            )

        # --- Course-level reports and exports ---
        if reports_folder_path:
            if export_announcements:
                try:
                    new_items_synced += process_course_announcements(
                        course_id,
                        course_name,
                        reports_folder_path,
                        canvas_api_url,
                        canvas_headers,
                        session=session,
                        timeout=request_timeout,
                        per_page=canvas_per_page,
                        summary=summary,
                    )
                except Exception as e:
                    print(f"Error exporting announcements for '{course_name}': {e}")

            if export_discussions:
                try:
                    discussions_folder_path = get_or_create_local_folder(
                        course_storage_path, "Discussions"
                    )
                    new_items_synced += process_course_discussions(
                        course_id,
                        course_name,
                        reports_folder_path,
                        canvas_api_url,
                        canvas_headers,
                        session=session,
                        timeout=request_timeout,
                        per_page=canvas_per_page,
                        summary=summary,
                        discussions_folder_path=discussions_folder_path,
                    )
                except Exception as e:
                    print(f"Error exporting discussions for '{course_name}': {e}")

            if runtime_export_flags["EXPORT_QUIZZES"]:
                try:
                    new_items_synced += process_course_quizzes(
                        course_id,
                        course_name,
                        reports_folder_path,
                        canvas_api_url,
                        canvas_headers,
                        session=session,
                        timeout=request_timeout,
                        per_page=canvas_per_page,
                        summary=summary,
                        on_endpoint_unavailable=disable_export_after_endpoint_failure,
                    )
                except Exception as e:
                    print(f"Error exporting quizzes for '{course_name}': {e}")

            if export_enrollments:
                try:
                    new_items_synced += process_course_enrollments(
                        course_id,
                        course_name,
                        reports_folder_path,
                        canvas_api_url,
                        canvas_headers,
                        session=session,
                        timeout=request_timeout,
                        per_page=canvas_per_page,
                        summary=summary,
                    )
                except Exception as e:
                    print(f"Error exporting enrollments for '{course_name}': {e}")

            if export_calendar_events:
                try:
                    new_items_synced += process_course_calendar_events(
                        course_id,
                        course_name,
                        reports_folder_path,
                        canvas_api_url,
                        canvas_headers,
                        session=session,
                        timeout=request_timeout,
                        per_page=canvas_per_page,
                        summary=summary,
                    )
                except Exception as e:
                    print(f"Error exporting calendar events for '{course_name}': {e}")

            if export_groups:
                try:
                    new_items_synced += process_course_groups(
                        course_id,
                        course_name,
                        reports_folder_path,
                        canvas_api_url,
                        canvas_headers,
                        session=session,
                        timeout=request_timeout,
                        per_page=canvas_per_page,
                        summary=summary,
                    )
                except Exception as e:
                    print(f"Error exporting groups for '{course_name}': {e}")

            if runtime_export_flags["EXPORT_ANALYTICS_ACTIVITY"]:
                try:
                    new_items_synced += process_course_analytics_activity(
                        course_id,
                        course_name,
                        reports_folder_path,
                        canvas_api_url,
                        canvas_headers,
                        session=session,
                        timeout=request_timeout,
                        summary=summary,
                        on_endpoint_unavailable=disable_export_after_endpoint_failure,
                    )
                except Exception as e:
                    print(f"Error exporting analytics for '{course_name}': {e}")

            if runtime_export_flags["EXPORT_GRADEBOOK_HISTORY"]:
                try:
                    new_items_synced += process_course_gradebook_history(
                        course_id,
                        course_name,
                        reports_folder_path,
                        canvas_api_url,
                        canvas_headers,
                        session=session,
                        timeout=request_timeout,
                        summary=summary,
                        on_endpoint_unavailable=disable_export_after_endpoint_failure,
                    )
                except Exception as e:
                    print(f"Error exporting gradebook history for '{course_name}': {e}")

            if export_submissions:
                try:
                    new_items_synced += process_course_submissions_summary(
                        course_id,
                        course_name,
                        assignments or [],
                        reports_folder_path,
                        canvas_api_url,
                        canvas_headers,
                        session=session,
                        timeout=request_timeout,
                        per_page=canvas_per_page,
                        summary=summary,
                    )
                except Exception as e:
                    print(f"Error exporting submissions for '{course_name}': {e}")

        if new_items_synced == 0:
            print(
                "All discoverable files, pages, assignments, and reports for this course are already up to date."
            )
        else:
            print(f"Synced/updated {new_items_synced} item(s) for '{course_name}'.")

    # Global (user-level) inbox conversations archive
    if export_inbox:
        try:
            inbox_changes = process_inbox_conversations(
                root_storage_path,
                canvas_api_url,
                canvas_headers,
                session=session,
                timeout=request_timeout,
                per_page=canvas_per_page,
                summary=summary,
            )
            if inbox_changes:
                print(f"Archived {inbox_changes} inbox conversation export(s).")
        except Exception as e:
            print(f"Error exporting inbox conversations: {e}")

    # Extract newly downloaded PDFs to Markdown using opendataloader-pdf
    # Print summary before cleanup
    summary.print_summary()
    failed_extractions = summary.get_action_count("failed_extraction")
    if failed_extractions:
        print(f"Extraction failures: {failed_extractions}")
    if disabled_endpoints_this_run:
        print("Auto-disabled endpoints this run:")
        for option, status in disabled_endpoints_this_run.items():
            if status:
                print(f"  - {option} (HTTP {status})")
            else:
                print(f"  - {option}")

    shutil.rmtree(DOWNLOAD_DIR)
    print("\n--- Sync Complete ---")

    # Prevent automatic exit so users can read the summary, especially when double-clicking an exe
    try:
        input("\nPress Enter to exit...")
    except EOFError:
        # Non-interactive environment; just return
        pass


if __name__ == "__main__":
    import sys
    import subprocess
    import time

    # When packaged with PyInstaller, sys.executable points to the exe wrapper itself.
    # Intercept the subprocess command to prevent an infinite loop (fork bomb).
    if (
        len(sys.argv) >= 3
        and sys.argv[1] == "-m"
        and sys.argv[2] == "opendataloader_pdf.hybrid_server"
    ):
        # Strip the '-m' and module name so argparse in the hybrid server doesn't fail
        del sys.argv[1:3]

        # Use direct import instead of runpy.run_module for better PyInstaller compatibility
        from opendataloader_pdf.hybrid_server import main as hybrid_main

        hybrid_main()
        sys.exit(0)

    server_process = None
    try:
        # On Windows, prevent the child process from popping up a new terminal window
        kwargs = {}
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0x08000000
            )

        print("Starting hybrid server in background...")
        server_process = subprocess.Popen(
            [sys.executable, "-m", "opendataloader_pdf.hybrid_server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
        # Give the server a few seconds to initialize
        time.sleep(3)

        main()
    finally:
        if server_process:
            print("\nStopping hybrid server...")
            server_process.terminate()
            try:
                server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_process.kill()

"""Export classic-quiz attempts that are currently in progress to Markdown.

Students can't read a quiz's questions through ``/courses/:id/quizzes/:id/questions``
(teacher only), and many courses disable the Quizzes page, which makes the quiz
list endpoint return 404. While an attempt is running, though, Canvas serves
that attempt's questions to the student through
``/api/v1/quiz_submissions/:id/questions``. This module finds quizzes through
the course's assignments (plus the quiz list when it is available), picks out
attempts that are in progress, and saves each one next to the course's other
notes as ``Quizzes/<title>/<title> - attempt N.md``, with question images
downloaded beside it.

Every request here is a GET, so running it never starts, saves or submits an attempt.
"""

import argparse
import configparser
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

import requests
from bs4 import BeautifulSoup, NavigableString
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from canvasync.config import (
    CONFIG_FILE,
    DEFAULT_CANVAS_PER_PAGE,
    DEFAULT_MAX_RETRIES,
    DEFAULT_BACKOFF_FACTOR,
    DEFAULT_REQUEST_TIMEOUT,
)
from canvasync.storage.local_fs import get_or_create_local_folder
from canvasync.utils.obsidian import html_to_obsidian
from canvasync.utils.sanitize import sanitize_filename, sanitize_folder_name

# Canvas calls an attempt that has been started but not submitted "untaken".
LIVE_WORKFLOW_STATES = {"untaken"}

_FILE_ID_RE = re.compile(r"/files/(\d+)")


class CanvasClient:
    """Minimal GET-only Canvas client that keeps HTTP status codes visible."""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        session: Optional[requests.Session] = None,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
        per_page: int = DEFAULT_CANVAS_PER_PAGE,
    ):
        self.base = (api_url or "").rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.session = session or requests.Session()
        self.timeout = timeout
        self.per_page = per_page

    def _url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.base}{path}"

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        response = self.session.get(
            self._url(path), headers=self.headers, params=params, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def get_all(self, path: str, params: Optional[dict] = None) -> List[Any]:
        params = {"per_page": self.per_page, **(params or {})}
        url: Optional[str] = self._url(path)
        items: List[Any] = []
        while url:
            response = self.session.get(
                url, headers=self.headers, params=params, timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                items.extend(data)
            url = response.links.get("next", {}).get("url")
            params = None  # the next link already carries the query string
        return items

    def download(self, url: str, dest_path: str) -> None:
        with self.session.get(
            self._url(url), headers=self.headers, stream=True, timeout=self.timeout
        ) as response:
            response.raise_for_status()
            tmp_path = dest_path + ".part"
            with open(tmp_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=8192):
                    handle.write(chunk)
        os.replace(tmp_path, dest_path)


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _local_time(moment: datetime) -> str:
    """``2026-11-09 11:09 (UTC+08:00)`` - Windows' %Z gives long zone names."""
    local = moment.astimezone()
    return f"{local:%Y-%m-%d %H:%M} (UTC{local.isoformat()[-6:]})"


def _format_time(value: Optional[str]) -> str:
    parsed = _parse_time(value)
    return _local_time(parsed) if parsed else "—"


def _status_of(error: requests.RequestException) -> Optional[int]:
    response = getattr(error, "response", None)
    return response.status_code if response is not None else None


def is_live_attempt(attempt: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """True when *attempt* has been started, not submitted, and has not run out of time."""
    if attempt.get("workflow_state") not in LIVE_WORKFLOW_STATES:
        return False
    if attempt.get("finished_at"):
        return False
    end_at = _parse_time(attempt.get("end_at"))
    now = now or datetime.now(timezone.utc)
    return end_at is None or end_at > now


def find_course_quiz_ids(client: CanvasClient, course_id: int) -> List[int]:
    """Quiz IDs in a course, found via assignments and, when enabled, the quiz list.

    Graded quizzes always have an assignment, which keeps working when the
    course hides its Quizzes page. Practice quizzes and ungraded surveys only
    show up in the quiz list.
    """
    quiz_ids: List[int] = []
    seen: Set[int] = set()

    def add(quiz_id: Any) -> None:
        if isinstance(quiz_id, int) and quiz_id not in seen:
            seen.add(quiz_id)
            quiz_ids.append(quiz_id)

    try:
        for assignment in client.get_all(f"/api/v1/courses/{course_id}/assignments"):
            if "online_quiz" in (assignment.get("submission_types") or []):
                add(assignment.get("quiz_id"))
    except requests.RequestException as e:
        print(f"  Could not list assignments (HTTP {_status_of(e)}).")

    try:
        for quiz in client.get_all(f"/api/v1/courses/{course_id}/quizzes"):
            add(quiz.get("id"))
    except requests.RequestException:
        pass  # Quizzes page disabled for the course (404) or not visible (403)

    return quiz_ids


def get_live_attempts(
    client: CanvasClient, course_id: int, quiz_id: int
) -> List[Dict[str, Any]]:
    """The current user's in-progress attempts on one quiz."""
    try:
        data = client.get(f"/api/v1/courses/{course_id}/quizzes/{quiz_id}/submission")
    except requests.RequestException:
        return []
    attempts = data.get("quiz_submissions", []) if isinstance(data, dict) else []
    return [a for a in attempts if is_live_attempt(a)]


def fetch_attempt_questions(
    client: CanvasClient, quiz_submission_id: int
) -> List[Dict[str, Any]]:
    data = client.get(f"/api/v1/quiz_submissions/{quiz_submission_id}/questions")
    questions = data.get("quiz_submission_questions", []) if isinstance(data, dict) else []
    return sorted(questions, key=lambda q: q.get("position") or 0)


def _image_file_id(img) -> Optional[str]:
    for attr in ("data-api-endpoint", "src"):
        value = img.get(attr) or ""
        match = _FILE_ID_RE.search(value)
        if match:
            return match.group(1)
    return None


def embed_question_images(
    html_content: Optional[str],
    dest_folder: str,
    client: CanvasClient,
    downloaded: Dict[str, str],
) -> str:
    """Download course-file images and equation images into Obsidian embeds.

    ``html_to_obsidian`` strips ``<img>`` tags, which would drop questions that
    are a picture of the question sheet. Course-file images are downloaded
    into *dest_folder* and replaced with ``![[file]]``; equation-editor
    images become ``$latex$``. *downloaded* maps file ID to saved filename
    so a file used twice is fetched once.
    """
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    for img in soup.find_all("img"):
        latex = img.get("data-equation-content")
        if latex:
            img.replace_with(NavigableString(f"${latex.strip()}$"))
            continue

        file_id = _image_file_id(img)
        if not file_id:
            continue
        filename = downloaded.get(file_id)
        if not filename:
            try:
                info = client.get(f"/api/v1/files/{file_id}")
                filename = sanitize_filename(
                    info.get("display_name") or f"image_{file_id}"
                )
                dest_path = os.path.join(dest_folder, filename)
                size = info.get("size")
                if not (
                    os.path.exists(dest_path)
                    and size is not None
                    and os.path.getsize(dest_path) == size
                ):
                    client.download(info["url"], dest_path)
                    print(f"  Saved image '{filename}'")
                downloaded[file_id] = filename
            except (requests.RequestException, KeyError, OSError) as e:
                print(f"  Could not download image file {file_id}: {e}")
                continue
        img.replace_with(NavigableString(f"![[{filename}]]"))
    return str(soup)


def _to_markdown(
    html_content: Optional[str],
    dest_folder: str,
    client: CanvasClient,
    downloaded: Dict[str, str],
) -> str:
    return html_to_obsidian(
        embed_question_images(html_content, dest_folder, client, downloaded)
    )


def _selected_ids(answer: Any) -> Set[str]:
    if answer is None or isinstance(answer, dict):
        return set()
    if isinstance(answer, list):
        return {str(a) for a in answer if not isinstance(a, dict)}
    return {str(answer)}


def format_live_attempt_md(
    quiz: Dict[str, Any],
    attempt: Dict[str, Any],
    questions: List[Dict[str, Any]],
    course_name: str,
    dest_folder: str,
    client: CanvasClient,
) -> str:
    downloaded: Dict[str, str] = {}
    title = quiz.get("title") or f"Quiz {quiz.get('id')}"
    allowed = quiz.get("allowed_attempts")
    attempts_label = (
        f"{attempt.get('attempt')} of {allowed}"
        if isinstance(allowed, int) and allowed > 0
        else f"{attempt.get('attempt')}"
    )

    lines = [f"# {title}", ""]
    lines.append(f"**Course:** {course_name}")
    lines.append(f"**Attempt:** {attempts_label} (in progress)")
    lines.append(f"**Started:** {_format_time(attempt.get('started_at'))}")
    lines.append(f"**Must finish by:** {_format_time(attempt.get('end_at'))}")
    lines.append(f"**Due:** {_format_time(quiz.get('due_at'))}")
    if quiz.get("time_limit"):
        lines.append(f"**Time limit:** {quiz['time_limit']} minutes")
    lines.append(f"**Points:** {quiz.get('points_possible')}")
    lines.append(f"**Questions:** {len(questions)}")
    if quiz.get("html_url"):
        lines.append(f"**Canvas:** [Open quiz]({quiz['html_url']})")
    lines.append(f"**Exported:** {_local_time(datetime.now(timezone.utc))}")
    lines.append("")

    description = _to_markdown(quiz.get("description"), dest_folder, client, downloaded)
    if description:
        lines += [description, ""]
    lines += ["---", ""]

    if not questions:
        lines.append("*Canvas returned no questions for this attempt.*")
        return "\n".join(lines) + "\n"

    for index, question in enumerate(questions, 1):
        q_type = (question.get("question_type") or "unknown").replace("_question", "")
        name = question.get("question_name") or f"Question {index}"
        heading = f"## {index}. {name} ({q_type.replace('_', ' ')})"
        if question.get("flagged"):
            heading += " — flagged"
        lines += [heading, ""]

        text = _to_markdown(question.get("question_text"), dest_folder, client, downloaded)
        lines += [text or "*(No question text)*", ""]

        answer = question.get("answer")
        options = question.get("answers") or []
        if options:
            chosen = _selected_ids(answer)
            for option in options:
                option_text = _to_markdown(
                    option.get("html") or option.get("text"), dest_folder, client, downloaded
                ) or "*(empty)*"
                mark = "x" if str(option.get("id")) in chosen else " "
                lines.append(f"- [{mark}] {option_text}")
            lines.append("")

        matches = question.get("matches") or []
        if matches:
            lines.append("**Match with:**")
            lines += [f"- {m.get('text')}" for m in matches]
            lines.append("")

        saved = _describe_saved_answer(question, answer, dest_folder, client, downloaded)
        if saved:
            lines += [saved, ""]

    return "\n".join(lines).rstrip() + "\n"


def _describe_saved_answer(
    question: Dict[str, Any],
    answer: Any,
    dest_folder: str,
    client: CanvasClient,
    downloaded: Dict[str, str],
) -> str:
    """Your currently saved answer for question types that aren't a pick-list."""
    if answer in (None, "", [], {}):
        return ""
    q_type = question.get("question_type")
    if q_type == "file_upload_question":
        count = len(answer) if isinstance(answer, list) else 1
        return f"**Saved answer:** {count} file(s) uploaded"
    if q_type == "essay_question":
        return "**Saved answer:**\n\n" + _to_markdown(answer, dest_folder, client, downloaded)
    if isinstance(answer, dict):
        pairs = "\n".join(f"- {key}: {value}" for key, value in answer.items())
        return f"**Saved answer:**\n{pairs}"
    if question.get("answers") and q_type in (
        "multiple_choice_question",
        "true_false_question",
        "multiple_answers_question",
    ):
        return ""  # already shown as ticked options
    if isinstance(answer, list):
        rendered = ", ".join(str(a) for a in answer)
        return f"**Saved answer:** {rendered}"
    return f"**Saved answer:** {answer}"


def export_course_live_quizzes(
    client: CanvasClient,
    course: Dict[str, Any],
    root_dir: str,
) -> List[str]:
    """Save every in-progress attempt in *course*. Returns the Markdown paths written."""
    course_id = course["id"]
    course_name = course.get("name") or f"Course {course_id}"
    written: List[str] = []

    for quiz_id in find_course_quiz_ids(client, course_id):
        attempts = get_live_attempts(client, course_id, quiz_id)
        if not attempts:
            continue
        try:
            quiz = client.get(f"/api/v1/courses/{course_id}/quizzes/{quiz_id}")
        except requests.RequestException as e:
            print(f"  Could not read quiz {quiz_id} (HTTP {_status_of(e)}).")
            continue

        title = quiz.get("title") or f"Quiz {quiz_id}"
        safe_title = sanitize_filename(title) or f"Quiz {quiz_id}"
        course_folder = get_or_create_local_folder(
            root_dir, sanitize_folder_name(course_name, fallback="Unnamed Course")
        )
        quizzes_folder = get_or_create_local_folder(course_folder, "Quizzes")
        quiz_folder = get_or_create_local_folder(quizzes_folder, safe_title)

        for attempt in attempts:
            print(f"  Live attempt {attempt.get('attempt')} of '{title}'")
            try:
                questions = fetch_attempt_questions(client, attempt["id"])
            except requests.RequestException as e:
                status = _status_of(e)
                hint = (
                    " (the quiz may need an access code, a lockdown browser or a"
                    " campus IP address)"
                    if status in (401, 403)
                    else ""
                )
                print(f"  Could not read the attempt's questions (HTTP {status}){hint}.")
                continue

            md = format_live_attempt_md(
                quiz, attempt, questions, course_name, quiz_folder, client
            )
            md_path = os.path.join(
                quiz_folder, f"{safe_title} - attempt {attempt.get('attempt')}.md"
            )
            with open(md_path, "w", encoding="utf-8") as handle:
                handle.write(md)
            print(f"  Saved {len(questions)} question(s) to '{md_path}'")
            written.append(md_path)

    return written


def _build_session(max_retries: int, backoff_factor: float) -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def select_courses(
    courses: Iterable[Dict[str, Any]], course_ids: Optional[Iterable[int]]
) -> List[Dict[str, Any]]:
    available = [
        c for c in courses if c.get("id") and not c.get("access_restricted_by_date")
    ]
    if not course_ids:
        return available
    wanted = {int(cid) for cid in course_ids}
    return [c for c in available if c["id"] in wanted]


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Save quiz attempts you have started but not submitted to Markdown. "
            "Read-only: never starts, saves or submits an attempt."
        )
    )
    parser.add_argument(
        "--course",
        type=int,
        action="append",
        dest="course_ids",
        metavar="COURSE_ID",
        help="Only check this course (repeatable). Default: all active courses.",
    )
    parser.add_argument("--config", default=CONFIG_FILE, help="Path to config.ini")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    config = configparser.ConfigParser()
    if not config.read(args.config):
        print(f"ERROR: Config file '{args.config}' not found.")
        return 1
    try:
        api_url = config["CANVAS"]["API_URL"]
        api_key = config["CANVAS"]["API_KEY"]
        root_dir = os.path.abspath(config["STORAGE"]["LOCAL_ROOT_DIR"])
    except KeyError as e:
        print(f"ERROR: Missing config key in {args.config}: {e}")
        return 1

    perf = config["PERFORMANCE"] if config.has_section("PERFORMANCE") else {}
    client = CanvasClient(
        api_url,
        api_key,
        session=_build_session(
            int(perf.get("MAX_RETRIES", DEFAULT_MAX_RETRIES)),
            float(perf.get("BACKOFF_FACTOR", DEFAULT_BACKOFF_FACTOR)),
        ),
        timeout=int(perf.get("REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT)),
        per_page=int(perf.get("CANVAS_PER_PAGE", DEFAULT_CANVAS_PER_PAGE)),
    )

    try:
        courses = client.get_all(
            "/api/v1/courses", {"enrollment_state": "active"}
        )
    except requests.RequestException as e:
        print(f"ERROR: Could not list courses (HTTP {_status_of(e)}): {e}")
        return 1

    selected = select_courses(courses, args.course_ids)
    if not selected:
        print("No matching active courses.")
        return 1

    written: List[str] = []
    for course in selected:
        print(f"Checking {course.get('name')} ...")
        written += export_course_live_quizzes(client, course, root_dir)

    if written:
        print(f"\nSaved {len(written)} live quiz attempt(s).")
    else:
        print(
            "\nNo quiz attempts in progress. Start the quiz in Canvas first, then run"
            " this again (New Quizzes aren't supported: Canvas doesn't expose their"
            " attempts to students through the API)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

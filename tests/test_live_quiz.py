import os
import sys
from datetime import datetime, timezone

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from canvasync import live_quiz
from canvasync.live_quiz import CanvasClient

NOW = datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)


def _http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"HTTP {status}", response=response)


class FakeClient(CanvasClient):
    def __init__(self, routes, files=None):
        super().__init__("https://canvas.example", "token")
        self.routes = routes
        self.files = files or {}
        self.downloads = []

    def get(self, path, params=None):
        value = self.routes[path]
        if isinstance(value, Exception):
            raise value
        return value

    def get_all(self, path, params=None):
        return self.get(path, params)

    def download(self, url, dest_path):
        self.downloads.append(url)
        with open(dest_path, "wb") as handle:
            handle.write(self.files[url])


@pytest.mark.parametrize(
    "attempt, expected",
    [
        ({"workflow_state": "untaken", "end_at": "2026-11-13T15:59:59Z"}, True),
        ({"workflow_state": "untaken", "end_at": None}, True),
        ({"workflow_state": "untaken", "end_at": "2026-11-09T03:00:00Z"}, False),
        ({"workflow_state": "complete", "end_at": "2026-11-13T15:59:59Z"}, False),
        ({"workflow_state": "pending_review", "end_at": None}, False),
    ],
)
def test_is_live_attempt(attempt, expected):
    assert live_quiz.is_live_attempt(attempt, now=NOW) is expected


def test_quiz_ids_found_through_assignments_when_quiz_page_disabled():
    client = FakeClient(
        {
            "/api/v1/courses/1596/assignments": [
                {"id": 1, "submission_types": ["online_upload"]},
                {"id": 41171, "submission_types": ["online_quiz"], "quiz_id": 14635},
            ],
            "/api/v1/courses/1596/quizzes": _http_error(404),
        }
    )
    assert live_quiz.find_course_quiz_ids(client, 1596) == [14635]


def test_quiz_ids_include_ungraded_quizzes_from_quiz_list():
    client = FakeClient(
        {
            "/api/v1/courses/7/assignments": [
                {"submission_types": ["online_quiz"], "quiz_id": 5},
            ],
            "/api/v1/courses/7/quizzes": [{"id": 5}, {"id": 6}],
        }
    )
    assert live_quiz.find_course_quiz_ids(client, 7) == [5, 6]


def test_question_image_is_downloaded_and_embedded(tmp_path):
    html = (
        '<p><img src="https://canvas.example/courses/1596/files/2063499/preview?verifier=v"'
        ' alt="Online Quiz 2_Set C.png"'
        ' data-api-endpoint="https://canvas.example/api/v1/courses/1596/files/2063499"></p>'
    )
    client = FakeClient(
        {
            "/api/v1/files/2063499": {
                "display_name": "Online Quiz 2_Set C.png",
                "url": "https://files.example/2063499",
                "size": 3,
            }
        },
        files={"https://files.example/2063499": b"png"},
    )
    downloaded = {}

    out = live_quiz.embed_question_images(html, str(tmp_path), client, downloaded)

    assert "![[Online Quiz 2_Set C.png]]" in out
    assert (tmp_path / "Online Quiz 2_Set C.png").read_bytes() == b"png"
    # A second reference to the same file is not fetched again.
    live_quiz.embed_question_images(html, str(tmp_path), client, downloaded)
    assert client.downloads == ["https://files.example/2063499"]


def test_existing_image_with_same_size_is_not_redownloaded(tmp_path):
    (tmp_path / "q.png").write_bytes(b"png")
    client = FakeClient(
        {"/api/v1/files/9": {"display_name": "q.png", "url": "u", "size": 3}}
    )
    out = live_quiz.embed_question_images(
        '<img src="/courses/1/files/9/preview">', str(tmp_path), client, {}
    )
    assert "![[q.png]]" in out
    assert client.downloads == []


def test_equation_image_becomes_latex(tmp_path):
    html = '<img class="equation_image" src="/equation_images/x" data-equation-content="\\frac{a}{b}">'
    out = live_quiz.embed_question_images(html, str(tmp_path), FakeClient({}), {})
    assert out == "$\\frac{a}{b}$"


def test_format_attempt_markdown(tmp_path):
    client = FakeClient(
        {"/api/v1/files/11": {"display_name": "sheet.png", "url": "u11", "size": 1}},
        files={"u11": b"x"},
    )
    quiz = {
        "id": 14635,
        "title": "Online Quiz 2",
        "allowed_attempts": 1,
        "points_possible": 10.0,
        "due_at": "2026-11-13T15:59:59Z",
        "html_url": "https://canvas.example/courses/1596/quizzes/14635",
    }
    attempt = {
        "id": 519372,
        "attempt": 1,
        "started_at": "2026-11-09T03:09:53Z",
        "end_at": "2026-11-13T15:59:59Z",
        "workflow_state": "untaken",
    }
    questions = [
        {
            "position": 1,
            "question_name": "Question 1",
            "question_type": "file_upload_question",
            "question_text": '<p><img src="/courses/1596/files/11/preview?verifier=v"></p>',
            "answer": None,
        },
        {
            "position": 2,
            "question_name": "Question 2",
            "question_type": "multiple_choice_question",
            "question_text": "<p>Pick one</p>",
            "flagged": True,
            "answers": [{"id": 1, "text": "Yes"}, {"id": 2, "text": "No"}],
            "answer": 2,
        },
        {
            "position": 3,
            "question_name": "Question 3",
            "question_type": "fill_in_multiple_blanks_question",
            "question_text": "<p>[a] and [b]</p>",
            "answer": {"a": "one", "b": None},
        },
    ]

    md = live_quiz.format_live_attempt_md(
        quiz, attempt, questions, "MTH20017", str(tmp_path), client
    )

    assert md.startswith("# Online Quiz 2\n")
    assert "**Attempt:** 1 of 1 (in progress)" in md
    assert "## 1. Question 1 (file upload)" in md
    assert "![[sheet.png]]" in md
    assert "## 2. Question 2 (multiple choice) — flagged" in md
    assert "- [ ] Yes" in md
    assert "- [x] No" in md
    assert "- a: one" in md
    assert "[Open quiz](https://canvas.example/courses/1596/quizzes/14635)" in md


def test_export_writes_one_file_per_live_attempt(tmp_path):
    client = FakeClient(
        {
            "/api/v1/courses/1596/assignments": [
                {"submission_types": ["online_quiz"], "quiz_id": 14635},
                {"submission_types": ["online_quiz"], "quiz_id": 999},
            ],
            "/api/v1/courses/1596/quizzes": _http_error(404),
            "/api/v1/courses/1596/quizzes/14635/submission": {
                "quiz_submissions": [
                    {"id": 519372, "attempt": 1, "workflow_state": "untaken", "end_at": None}
                ]
            },
            "/api/v1/courses/1596/quizzes/999/submission": {
                "quiz_submissions": [{"id": 1, "attempt": 1, "workflow_state": "complete"}]
            },
            "/api/v1/courses/1596/quizzes/14635": {"id": 14635, "title": "Quiz: 2/3"},
            "/api/v1/quiz_submissions/519372/questions": {
                "quiz_submission_questions": [
                    {"position": 1, "question_type": "essay_question", "question_text": "<p>Why?</p>"}
                ]
            },
        }
    )

    written = live_quiz.export_course_live_quizzes(
        client, {"id": 1596, "name": "MTH20017 MATHS"}, str(tmp_path)
    )

    expected = tmp_path / "MTH20017 MATHS" / "Quizzes" / "Quiz 2 3" / "Quiz 2 3 - attempt 1.md"
    assert written == [str(expected)]
    assert "Why?" in expected.read_text(encoding="utf-8")


def test_select_courses_filters_by_id_and_restriction():
    courses = [
        {"id": 1, "name": "A"},
        {"id": 2, "name": "B", "access_restricted_by_date": True},
        {"id": 3, "name": "C"},
    ]
    assert [c["id"] for c in live_quiz.select_courses(courses, None)] == [1, 3]
    assert [c["id"] for c in live_quiz.select_courses(courses, [3, 2])] == [3]

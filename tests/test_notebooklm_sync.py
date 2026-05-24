import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from canvasync.notebooklm_sync import build_plan, discover_sources, is_eligible_source


def write_file(path: Path, content: str = "content") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_source_filter_includes_markdown_and_pdf_but_skips_pdf_extracts(tmp_path):
    normal_note = write_file(tmp_path / "Course A" / "Page.md")
    pdf = write_file(tmp_path / "Course A" / "Lecture.pdf")
    pdf_extract = write_file(tmp_path / "Course A" / "Lecture_pdf.md")
    report = write_file(tmp_path / "Course A" / "Reports" / "announcements.json", "{}")
    office = write_file(tmp_path / "Course A" / "Slides.pptx")
    dependency_readme = write_file(tmp_path / "Course A" / "node_modules" / "pkg" / "README.md")
    submission_pdf = write_file(tmp_path / "Course A" / "Assignments" / "A1" / "submission" / "mine.pdf")

    assert is_eligible_source(normal_note, normal_note.relative_to(tmp_path))
    assert is_eligible_source(pdf, pdf.relative_to(tmp_path))
    assert not is_eligible_source(pdf_extract, pdf_extract.relative_to(tmp_path))
    assert not is_eligible_source(report, report.relative_to(tmp_path))
    assert not is_eligible_source(office, office.relative_to(tmp_path))
    assert not is_eligible_source(dependency_readme, dependency_readme.relative_to(tmp_path))
    assert not is_eligible_source(submission_pdf, submission_pdf.relative_to(tmp_path))


def test_pdf_extracts_can_be_included_explicitly(tmp_path):
    pdf_extract = write_file(tmp_path / "Course A" / "Lecture_pdf.md")

    assert is_eligible_source(
        pdf_extract,
        pdf_extract.relative_to(tmp_path),
        include_pdf_extracts=True,
    )


def test_submissions_can_be_included_explicitly(tmp_path):
    submission_pdf = write_file(tmp_path / "Course A" / "Assignments" / "A1" / "submission" / "mine.pdf")

    assert is_eligible_source(
        submission_pdf,
        submission_pdf.relative_to(tmp_path),
        include_submissions=True,
    )


def test_discover_sources_sets_course_name_and_hash(tmp_path):
    write_file(tmp_path / "Course A" / "Assignments" / "A1.md", "alpha")
    write_file(tmp_path / "Course B" / "Lecture.pdf", "bravo")
    write_file(tmp_path / "Course B" / "Lecture_pdf.md", "duplicate text")

    sources = discover_sources(tmp_path)

    assert [source.rel_path for source in sources] == [
        "Course A/Assignments/A1.md",
        "Course B/Lecture.pdf",
    ]
    assert sources[0].course_name == "Course A"
    assert sources[0].kind == "markdown"
    assert sources[0].sha256
    assert sources[1].course_name == "Course B"
    assert sources[1].kind == "pdf"


def test_discover_sources_can_filter_one_course(tmp_path):
    write_file(tmp_path / "Course A" / "Page.md", "alpha")
    write_file(tmp_path / "Course B" / "Page.md", "bravo")

    sources = discover_sources(tmp_path, course_filter="Course B")

    assert [source.rel_path for source in sources] == ["Course B/Page.md"]


def test_build_plan_marks_new_changed_and_unchanged(tmp_path):
    write_file(tmp_path / "Course A" / "New.md", "new")
    write_file(tmp_path / "Course A" / "Changed.md", "changed")
    write_file(tmp_path / "Course A" / "Same.md", "same")
    sources = discover_sources(tmp_path)
    by_rel = {source.rel_path: source for source in sources}
    state = {
        "files": {
            "Course A/Changed.md": {"sha256": "old-hash"},
            "Course A/Same.md": {"sha256": by_rel["Course A/Same.md"].sha256},
        }
    }

    plan = build_plan(sources, state)

    assert {item.source.rel_path: item.action for item in plan} == {
        "Course A/Changed.md": "changed",
        "Course A/New.md": "new",
        "Course A/Same.md": "unchanged",
    }

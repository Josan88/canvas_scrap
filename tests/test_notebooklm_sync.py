import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from canvasync.notebooklm_sync import apply_plan, build_plan, discover_sources, is_eligible_source, short_upload_name, notebooklm_title


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


def test_apply_plan_copies_and_uploads_incrementally(tmp_path, monkeypatch):
    import canvasync.notebooklm_sync as nbs

    write_file(tmp_path / "Course A" / "Page.md", "hello markdown")
    write_file(tmp_path / "Course A" / "Image.pdf", "hello pdf")

    sources = discover_sources(tmp_path)
    plan = build_plan(sources, {})

    notebook_calls = []
    run_calls = []

    def mock_get_or_create_notebook(course_name, state, prefix):
        notebook_calls.append(course_name)
        return "mock-notebook-id"

    def mock_run_notebooklm(args):
        run_calls.append(args)
        if args[0] == "source" and args[1] == "add":
            return {"source": {"id": "mock-source-id"}}
        return {}

    monkeypatch.setattr(nbs, "get_or_create_notebook", mock_get_or_create_notebook)
    monkeypatch.setattr(nbs, "run_notebooklm", mock_run_notebooklm)

    state = {}
    state_path = tmp_path / "state.json"

    uploaded = apply_plan(plan, state, state_path, "Prefix - ")

    assert uploaded == 2
    assert notebook_calls == ["Course A", "Course A"]
    
    add_calls = [c for c in run_calls if c[0] == "source" and c[1] == "add"]
    wait_calls = [c for c in run_calls if c[0] == "source" and c[1] == "wait"]
    
    assert len(add_calls) == 2
    assert len(wait_calls) == 2
    
    # Check --title is passed with readable course-relative path
    for c in add_calls:
        assert "--title" in c, f"Expected --title in add call args: {c}"
    assert any("Course A/Page.md" in c for c in add_calls)
    assert any("Course A/Image.pdf" in c for c in add_calls)

    # Check temp filenames are short hash-based names (no path separators in basename)
    for c in add_calls:
        basename = c[2].rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        assert "/" not in basename and "\\" not in basename, f"Temp basename should not contain separators: {basename}"
        assert len(basename) < 50, f"Temp basename too long: {basename}"

    # Check markdown gets .txt extension, PDF keeps .pdf
    assert any(c[2].endswith(".txt") for c in add_calls)
    assert any(c[2].endswith(".pdf") for c in add_calls)

    assert state_path.exists()
    assert state["files"]["Course A/Page.md"]["source_id"] == "mock-source-id"
    assert state["files"]["Course A/Image.pdf"]["source_id"] == "mock-source-id"


def test_long_path_uses_short_temp_filename(tmp_path):
    long_rel = "COS20015 FUNDAMENTAL OF DATA MANAGEMENT/Discussions/2023 S1 Week 8 and 9 Lecture and Lab Discussion/2023 S1 Week 8 and 9 Lecture and Lab Discussion.pdf"

    upload_name = short_upload_name(long_rel, "pdf")
    assert len(upload_name) < 50, f"Upload name should be short, got {len(upload_name)}: {upload_name}"
    assert "/" not in upload_name
    assert "\\" not in upload_name
    assert upload_name.endswith(".pdf")

    title = notebooklm_title(long_rel)
    assert "COS20015" in title
    assert "Discussions" in title
    assert title.endswith(".pdf")

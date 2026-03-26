"""Tests for canvasync.utils.obsidian.html_to_obsidian."""

import sys, os
# Ensure the project root is on sys.path so canvasync can be imported.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from canvasync.utils.obsidian import html_to_obsidian


def test_empty_input():
    assert html_to_obsidian("") == ""
    assert html_to_obsidian(None) == ""


def test_plain_text():
    result = html_to_obsidian("<p>Hello world</p>")
    assert "Hello world" in result


def test_internal_page_link_becomes_wikilink():
    html = '<a href="/courses/123/pages/week-1-introduction">Week 1 Introduction</a>'
    result = html_to_obsidian(html)
    assert "[[Week 1 Introduction]]" in result


def test_internal_assignment_link_becomes_wikilink():
    html = '<a href="/courses/456/assignments/my-assignment">My Assignment</a>'
    result = html_to_obsidian(html)
    assert "[[My Assignment]]" in result


def test_internal_discussion_link_becomes_wikilink():
    html = '<a href="/courses/789/discussion_topics/some-topic">Some Topic</a>'
    result = html_to_obsidian(html)
    assert "[[Some Topic]]" in result


def test_external_link_preserved():
    html = '<a href="https://example.com">Example</a>'
    result = html_to_obsidian(html)
    # Should NOT be a wikilink
    assert "[[" not in result
    # Should remain a markdown link
    assert "Example" in result
    assert "https://example.com" in result


def test_mixed_links():
    html = (
        '<p>See <a href="/courses/1/pages/intro">Intro</a> and '
        '<a href="https://google.com">Google</a>.</p>'
    )
    result = html_to_obsidian(html)
    assert "[[Intro]]" in result
    assert "[[" in result  # at least one wikilink
    assert "https://google.com" in result  # external link preserved


def test_bold_and_lists_converted():
    html = "<b>Important</b><ul><li>Item 1</li><li>Item 2</li></ul>"
    result = html_to_obsidian(html)
    assert "**Important**" in result
    assert "Item 1" in result
    assert "Item 2" in result


def test_link_text_with_special_chars_sanitised():
    html = '<a href="/courses/1/pages/week-1">Week 1: Introduction?</a>'
    result = html_to_obsidian(html)
    # sanitize_filename strips ?, :, etc.
    assert "[[Week 1 Introduction ]]" in result or "[[Week 1 Introduction]]" in result


def test_fallback_to_slug_when_no_text():
    html = '<a href="/courses/1/pages/my-page-title"></a>'
    result = html_to_obsidian(html)
    assert "[[my page title]]" in result


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])

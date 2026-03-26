"""Utility to convert Canvas HTML into Obsidian-flavoured Markdown with wikilinks."""

import re
from urllib.parse import unquote

from bs4 import BeautifulSoup, NavigableString
from markdownify import markdownify as md

from canvasync.utils.sanitize import sanitize_filename

# Patterns that indicate an internal Canvas resource link.
# Matches both relative (/courses/...) and absolute (https://...) links.
_INTERNAL_PATH_RE = re.compile(
    r"(?:https?://[^/]+)?/(?:courses/\d+/)?"
    r"(?:pages|wiki|assignments|discussion_topics|quizzes|modules(?:/items)?|files)"
    r"/([^#?\s/]+)",
    re.IGNORECASE,
)


def html_to_obsidian(html_content: str) -> str:
    """Convert Canvas HTML to Obsidian Markdown with ``[[wikilinks]]``.

    1. Internal Canvas links (pages, assignments, discussions, quizzes,
       modules) are replaced with ``[[Sanitised Title]]`` wikilinks.
    2. All remaining HTML is converted to clean Markdown via *markdownify*.

    Args:
        html_content: Raw HTML string from the Canvas API.

    Returns:
        Obsidian-ready Markdown string.
    """
    if not html_content:
        return ""

    soup = BeautifulSoup(html_content, "html.parser")

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        if not isinstance(href, str):
            continue

        match = _INTERNAL_PATH_RE.search(href)
        if not match:
            continue

        # Prefer the visible link text; fall back to the URL slug.
        link_text = anchor.get_text(strip=True)
        if not link_text:
            link_text = unquote(match.group(1)).replace("-", " ")

        sanitised = sanitize_filename(link_text)
        # If it's a PDF link, append _pdf to match our extraction naming convention.
        is_pdf_link = (
            href.lower().split("?")[0].endswith(".pdf") or
            link_text.lower().endswith(".pdf") or
            "/files/" in href.lower() and ".pdf" in link_text.lower()
        )

        if is_pdf_link:
            # Remove .pdf extension if present and append _pdf
            base_name = re.sub(r"\.pdf$", "", sanitised, flags=re.IGNORECASE).strip()
            wikilink = f"[[{base_name}_pdf]]"
        else:
            wikilink = f"[[{sanitised}]]"

        print(f"  - Converted internal link: '{link_text}' -> {wikilink}")
        anchor.replace_with(NavigableString(wikilink))

    markdown_content = md(str(soup), strip=["img"]).strip()
    # Unescape underscores that markdownify might have escaped inside wikilinks
    return markdown_content.replace(r"\_", "_")

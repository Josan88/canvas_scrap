"""Utility to convert Canvas HTML into Obsidian-flavoured Markdown with wikilinks."""

import os
import re
from urllib.parse import unquote

from bs4 import BeautifulSoup, NavigableString
from markdownify import markdownify as md

from canvasync.utils.sanitize import sanitize_filename
from canvasync.utils.youtube import extract_youtube_ids, get_youtube_transcript

# Patterns that indicate an internal Canvas resource link.
# Matches both relative (/courses/...) and absolute (https://...) links.
_INTERNAL_PATH_RE = re.compile(
    r"(?:https?://[^/]+)?/(?:courses/\d+/)?"
    r"(?:pages|wiki|assignments|discussion_topics|quizzes|modules(?:/items)?|files)"
    r"/([^#?\s/]+)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Module-level registry of known wikilink targets.
# When set (not None), html_to_obsidian will only create [[wikilinks]] for
# targets that exist in this set.  Unrecognised targets are kept as plain
# text so that no broken links are ever written to disk.
# ---------------------------------------------------------------------------
_known_wikilink_targets = None  # type: set | None

# ---------------------------------------------------------------------------
# Module-level mapping of Canvas page slugs to sanitised page titles.
# This allows html_to_obsidian to resolve the correct wikilink target when
# the visible link text differs from the actual page title (e.g. an anchor
# that says "list of approved topics" pointing to a page whose real title
# is "[S1 2026] List of Approved Topics for D HD Projects").
# ---------------------------------------------------------------------------
_slug_to_title = None  # type: dict | None


def set_known_wikilink_targets(targets):
    """Set (or clear) the registry of valid wikilink target names.

    Call with a ``set`` of sanitised basenames (without ``.md`` extension)
    before running any HTML-to-Obsidian conversion.  Call with ``None``
    to disable target checking (all wikilinks are created unconditionally).
    """
    global _known_wikilink_targets
    _known_wikilink_targets = targets


def set_slug_to_title_map(mapping):
    """Set (or clear) the Canvas page slug → sanitised-title mapping.

    Call with a ``dict`` mapping URL slugs (e.g.
    ``"s1-2026-list-of-approved-topics-for-d-hd-projects"``) to sanitised
    page titles (e.g. ``"[S1 2026] List of Approved Topics for D HD Projects"``).
    Call with ``None`` to clear the mapping.
    """
    global _slug_to_title
    _slug_to_title = mapping


def _resolve_slug_to_title(slug):
    """Look up a Canvas URL slug in the slug-to-title mapping.

    Returns the sanitised page title if found, otherwise ``None``.
    """
    if _slug_to_title is None or not slug:
        return None
    return _slug_to_title.get(slug)


def is_known_wikilink_target(target):
    """Return *True* if *target* is a recognised wikilink destination.

    When the registry is ``None`` (not initialised), this always returns
    ``True`` so that the converter behaves as before.
    """
    return _known_wikilink_targets is None or target in _known_wikilink_targets


def html_to_obsidian(html_content: str, file_id_map: dict = None, output_dir: str = None) -> str:
    """Convert Canvas HTML to Obsidian Markdown with ``[[wikilinks]]``.

    1. Internal Canvas links (pages, assignments, discussions, quizzes,
       modules, files) are replaced with ``[[Sanitised Title]]`` wikilinks.
    2. YouTube links and iframes generate transcript wikilinks and write transcript files.
    3. All remaining HTML is converted to clean Markdown via *markdownify*.

    Args:
        html_content: Raw HTML string from the Canvas API.
        file_id_map: Optional mapping of file_id -> filename.

    Returns:
        Obsidian-ready Markdown string.
    """
    if not html_content:
        return ""

    if file_id_map is None:
        file_id_map = {}

    soup = BeautifulSoup(html_content, "html.parser")

    # Handle YouTube iframes and links
    for tag in soup.find_all(["a", "iframe"]):
        src_or_href = tag.get("href") or tag.get("src") or ""
        if isinstance(src_or_href, list):
            src_or_href = src_or_href[0]
            
        vids = extract_youtube_ids(src_or_href)
        if not vids:
            continue
            
        vid = vids[0]
        # Only fetch if we have an output_dir
        if output_dir:
            transcript_filename = f"YouTube_Transcript_{vid}.md"
            transcript_path = os.path.join(output_dir, transcript_filename)
            if not os.path.exists(transcript_path):
                print(f"  - Fetching YouTube transcript for {vid}...")
                transcript_text = get_youtube_transcript(vid)
                if transcript_text.startswith("_") and transcript_text.endswith("_"):
                    print(f"  -> Skipping transcript file creation: {transcript_text.strip('_')}")
                else:
                    try:
                        os.makedirs(output_dir, exist_ok=True)
                        with open(transcript_path, "w", encoding="utf-8") as tf:
                            tf.write(f"# YouTube Transcript ({vid})\n\n{transcript_text}\n")
                    except Exception as e:
                        print(f"  -> Error saving transcript: {e}")
                    
        wikilink = f" [[YouTube_Transcript_{vid}]]"
        if tag.name == "iframe":
            replacement = soup.new_tag("p")
            watch_url = f"https://www.youtube.com/watch?v={vid}"
            a_tag = soup.new_tag("a", href=watch_url)
            a_tag.string = f"Watch Video ({vid})"
            replacement.append(a_tag)
            replacement.append(NavigableString(wikilink))
            tag.replace_with(replacement)
        else:
            # Fix existing anchor tags that might point to embed links
            if tag.name == "a":
                watch_url = f"https://www.youtube.com/watch?v={vid}"
                tag['href'] = watch_url

            # Important: Make sure not to double add if we process twice
            if wikilink not in tag.get_text():
                tag.append(NavigableString(wikilink))

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        if not isinstance(href, str):
            continue

        match = _INTERNAL_PATH_RE.search(href)
        if not match:
            continue

        # Prefer the visible link text; fall back to the URL slug.
        link_text = anchor.get_text(strip=True)

        # Check if it's a file link by ID and if we have a mapped filename
        is_file_link = "/files/" in href.lower()
        if is_file_link:
            file_id_match = re.search(r"/files/(\d+)", href)
            if file_id_match:
                file_id = file_id_match.group(1)
                if file_id in file_id_map:
                    # Target the actual downloaded filename!
                    link_text = file_id_map[file_id]

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
            wikilink_target = f"{base_name}_pdf"
        else:
            wikilink_target = sanitised

        # Only create a wikilink when the target is known to exist.
        if not is_known_wikilink_target(wikilink_target):
            # The visible link text didn't match — try resolving the actual
            # page title from the URL slug (e.g. the anchor text might be
            # "list of approved topics" while the real page title is
            # "[S1 2026] List of Approved Topics for D HD Projects").
            url_slug = unquote(match.group(1))
            resolved_title = _resolve_slug_to_title(url_slug)
            if resolved_title and is_known_wikilink_target(resolved_title):
                wikilink_target = resolved_title
            else:
                print(f"  - Skipped broken link (target not found): '{link_text}'")
                anchor.replace_with(NavigableString(link_text))
                continue

        wikilink = f"[[{wikilink_target}]]"
        print(f"  - Converted internal link: '{link_text}' -> {wikilink}")
        anchor.replace_with(NavigableString(wikilink))

    markdown_content = md(str(soup), strip=["img"]).strip()
    # Unescape underscores that markdownify might have escaped inside wikilinks
    return markdown_content.replace(r"\_", "_")



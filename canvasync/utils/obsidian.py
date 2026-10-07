"""Utility to convert Canvas HTML into Obsidian-flavoured Markdown with wikilinks."""

import json
import os
import re
from urllib.parse import unquote

from bs4 import BeautifulSoup, NavigableString
from markdownify import markdownify as md

from canvasync.utils.sanitize import sanitize_filename
from canvasync.utils.youtube import (
    extract_youtube_ids,
    format_youtube_details,
    format_youtube_frontmatter,
    get_youtube_metadata,
    get_youtube_transcript,
)

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


def register_wikilink_target(name):
    """Register a file saved during this run so same-run links can resolve."""
    if _known_wikilink_targets is None or not name:
        return
    clean = sanitize_filename(str(name))
    if not clean:
        return
    _known_wikilink_targets.add(clean)
    stem, ext = os.path.splitext(clean)
    if stem:
        _known_wikilink_targets.add(stem)
    if ext.lower() == ".pdf" and stem:
        _known_wikilink_targets.add(f"{stem}_pdf")


_VIDEO_DETAILS_MARKER = "## Video Details"
_FRONTMATTER_TITLE_RE = re.compile(r'^title:\s*(".*")\s*$', re.MULTILINE)


def _write_youtube_transcript(vid: str, transcript_path: str):
    """Create (or backfill details into) a transcript file. Returns the video title if known."""
    if os.path.exists(transcript_path):
        try:
            with open(transcript_path, "r", encoding="utf-8") as tf:
                existing = tf.read()
        except Exception as e:
            print(f"  -> Error reading transcript: {e}")
            return None

        if _VIDEO_DETAILS_MARKER in existing:
            m = _FRONTMATTER_TITLE_RE.search(existing)
            if m:
                try:
                    return json.loads(m.group(1))
                except ValueError:
                    return None
            return None

        # Older transcript file without details: prepend them, keep the transcript text.
        print(f"  - Adding video details to existing transcript for {vid}...")
        meta = get_youtube_metadata(vid)
        body = re.sub(r"^# YouTube Transcript \([^)]*\)\s*", "", existing, count=1)
        _save_transcript_file(transcript_path, vid, meta, body.strip())
        return meta.get("title")

    print(f"  - Fetching YouTube transcript for {vid}...")
    transcript_text = get_youtube_transcript(vid)
    if transcript_text.startswith("_") and transcript_text.endswith("_"):
        print(f"  -> Skipping transcript file creation: {transcript_text.strip('_')}")
        return None

    meta = get_youtube_metadata(vid)
    _save_transcript_file(transcript_path, vid, meta, transcript_text)
    return meta.get("title")


def _save_transcript_file(transcript_path: str, vid: str, meta: dict, transcript_text: str) -> None:
    heading = meta.get("title") or f"YouTube Video ({vid})"
    content = (
        f"{format_youtube_frontmatter(meta)}\n"
        f"# {heading}\n\n"
        f"{format_youtube_details(meta)}\n"
        f"## Transcript\n\n{transcript_text}\n"
    )
    try:
        os.makedirs(os.path.dirname(transcript_path) or ".", exist_ok=True)
        with open(transcript_path, "w", encoding="utf-8") as tf:
            tf.write(content)
    except Exception as e:
        print(f"  -> Error saving transcript: {e}")


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
        video_title = None
        # Only fetch if we have an output_dir
        if output_dir:
            transcript_filename = f"YouTube_Transcript_{vid}.md"
            transcript_path = os.path.join(output_dir, transcript_filename)
            video_title = _write_youtube_transcript(vid, transcript_path)

        wikilink = f" [[YouTube_Transcript_{vid}]]"
        if tag.name == "iframe":
            replacement = soup.new_tag("p")
            watch_url = f"https://www.youtube.com/watch?v={vid}"
            a_tag = soup.new_tag("a", href=watch_url)
            a_tag.string = f"Watch Video: {video_title}" if video_title else f"Watch Video ({vid})"
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



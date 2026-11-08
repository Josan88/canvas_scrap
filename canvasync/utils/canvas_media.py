"""Find and resolve Canvas-hosted videos embedded in course HTML.

Canvas embeds uploaded videos (lecture recordings etc.) as media player iframes:

* ``/media_attachments_iframe/<file id>?verifier=...`` (current Canvas) - backed
  by a normal Canvas file, so the Files API gives its name and download URL.
* ``/media_objects_iframe/<m-media id>`` (older content) - keyed only by the
  media id. Its player sources are DASH streams rather than video files, so
  these are kept as a link to the Canvas player instead of being downloaded.

The Files API reports a placeholder ``size`` for these videos, so change
detection must rely on ``updated_at`` only (which ``process_canvas_file`` does).
"""

import os
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

_ATTACHMENT_RE = re.compile(r"/media_attachments(?:_iframe)?/(\d+)")
_MEDIA_OBJECT_RE = re.compile(r"/media_objects(?:_iframe)?/(m-[A-Za-z0-9]+)")
_PLAYER_PREFIX_RE = re.compile(r"^(?:video|audio|media) player for\s+", re.IGNORECASE)

_downloads_enabled = True


def set_embedded_video_downloads(enabled: bool) -> None:
    """Turn downloading of embedded Canvas videos on or off for this run."""
    global _downloads_enabled
    _downloads_enabled = bool(enabled)


def embedded_video_downloads_enabled() -> bool:
    return _downloads_enabled


def media_embed_key(tag) -> Optional[str]:
    """Return the attachment id (``"123"``) or media id (``"m-..."``) of a Canvas media embed tag."""
    if tag.name not in ("iframe", "video", "audio", "a"):
        return None
    src = tag.get("src") or tag.get("href") or ""
    if isinstance(src, list):
        src = src[0] if src else ""
    match = _ATTACHMENT_RE.search(src)
    if match:
        return match.group(1)
    match = _MEDIA_OBJECT_RE.search(src)
    if match:
        return match.group(1)
    media_id = tag.get("data-media-id")
    if tag.name != "a" and media_id and str(media_id).startswith("m-"):
        return str(media_id)
    return None


def media_embed_title(tag) -> str:
    """Human-readable title from an embed's ``title`` attribute ("Video player for X.mp4" -> "X.mp4")."""
    title = (tag.get("title") or "").strip()
    return _PLAYER_PREFIX_RE.sub("", title).strip()


def find_media_embeds(html_content: Optional[str]) -> List[Dict[str, str]]:
    """List Canvas media embeds in *html_content* (unique by key, in document order)."""
    if not html_content:
        return []
    soup = BeautifulSoup(html_content, "html.parser")
    embeds: List[Dict[str, str]] = []
    seen = set()
    for tag in soup.find_all(["iframe", "video", "audio", "a"]):
        key = media_embed_key(tag)
        if not key or key in seen:
            continue
        seen.add(key)
        embeds.append({"key": key, "title": media_embed_title(tag)})
    return embeds


def resolve_media_file_info(key: str, canvas_api_url: str, canvas_headers: dict, session, timeout: int) -> Optional[dict]:
    """Return the Canvas file record (id, display_name, url, updated_at) behind a media embed.

    Only attachment-backed embeds can be downloaded; media-id-only embeds return
    ``None`` because their player sources are DASH streams, not video files.
    Raises ``requests.RequestException`` on HTTP failures.
    """
    if not key.isdigit():
        return None
    base = (canvas_api_url or "").rstrip("/")
    resp = session.get(f"{base}/api/v1/files/{key}", headers=canvas_headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def needs_media_embed_retry(html_content: Optional[str], md_path: str, file_id_map: Optional[dict] = None) -> bool:
    """True when an existing Markdown file is missing a Canvas video embed found in *html_content*.

    A downloaded video must appear as ``![[name]]``; otherwise the Markdown must at
    least carry the Canvas player link (which contains the embed key).
    """
    if not html_content or not md_path or not os.path.exists(md_path):
        return False
    embeds = find_media_embeds(html_content)
    if not embeds:
        return False
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return True
    folder = os.path.dirname(md_path)
    for embed in embeds:
        name = (file_id_map or {}).get(embed["key"])
        if name and os.path.exists(os.path.join(folder, name)):
            if f"![[{name}]]" not in text:
                return True
        elif embed["key"] not in text:
            return True
    return False

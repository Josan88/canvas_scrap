import json
import re
from typing import Dict, List, Optional
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from youtube_transcript_api._errors import IpBlocked

# Regex to match youtube video IDs from common URLs
YOUTUBE_URL_RE = re.compile(
    r"(?:youtube(?:-nocookie)?\.com/(?:watch\?v=|embed/|shorts/|v/)|youtu\.be/)([a-zA-Z0-9_-]{11})"
)

def extract_youtube_ids(text: str) -> List[str]:
    """Extract all YouTube video IDs from a given text or HTML snippet."""
    if not text:
        return []
    
    matches = YOUTUBE_URL_RE.findall(text)
    # Return unique values while preserving order
    unique_ids = []
    for match in matches:
        if isinstance(match, tuple):
            vid = match[0]
        else:
            vid = match
            
        if vid and vid not in unique_ids:
            unique_ids.append(vid)
            
    return unique_ids


_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def _extract_json_string(page: str, key: str) -> Optional[str]:
    """Pull the first ``"key":"value"`` JSON string out of a watch page."""
    m = re.search(r'"%s":"((?:[^"\\]|\\.)*)"' % re.escape(key), page)
    if not m:
        return None
    try:
        return json.loads(f'"{m.group(1)}"')
    except ValueError:
        return m.group(1)


def get_youtube_metadata(video_id: str) -> Dict[str, str]:
    """Fetch video details (title, channel, duration, upload date, views, description).

    Title and channel come from YouTube's oEmbed endpoint, which is stable and
    needs no API key. The remaining fields are best-effort scraped from the
    watch page and are simply omitted if YouTube changes its markup.
    """
    import requests

    url = f"https://www.youtube.com/watch?v={video_id}"
    meta: Dict[str, str] = {"video_id": video_id, "url": url}
    session = requests.Session()
    session.headers.update(_BROWSER_HEADERS)

    try:
        resp = session.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
            timeout=15,
        )
        if resp.ok:
            data = resp.json()
            meta["title"] = data.get("title", "")
            meta["channel"] = data.get("author_name", "")
            meta["channel_url"] = data.get("author_url", "")
            meta["thumbnail"] = data.get("thumbnail_url", "")
    except Exception:
        pass

    try:
        resp = session.get(url, timeout=15)
        if resp.ok:
            page = resp.text
            if not meta.get("title"):
                meta["title"] = _extract_json_string(page, "title") or ""
            if not meta.get("channel"):
                meta["channel"] = _extract_json_string(page, "author") or ""
            length = _extract_json_string(page, "lengthSeconds")
            if length and length.isdigit():
                secs = int(length)
                h, rem = divmod(secs, 3600)
                m, s = divmod(rem, 60)
                meta["duration"] = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
            publish = _extract_json_string(page, "publishDate") or _extract_json_string(page, "uploadDate")
            if publish:
                meta["published"] = publish[:10]
            views = _extract_json_string(page, "viewCount")
            if views and views.isdigit():
                meta["views"] = f"{int(views):,}"
            desc = _extract_json_string(page, "shortDescription")
            if desc:
                meta["description"] = desc
    except Exception:
        pass

    return {k: v for k, v in meta.items() if v}


def format_youtube_frontmatter(meta: Dict[str, str]) -> str:
    """Render video metadata as YAML frontmatter (values JSON-quoted, which is valid YAML)."""
    front = ["---"]
    for key in ("title", "channel", "url", "published", "duration", "views", "video_id"):
        if key in meta:
            front.append(f"{key}: {json.dumps(meta[key], ensure_ascii=False)}")
    front.append("---")
    return "\n".join(front) + "\n"


def format_youtube_details(meta: Dict[str, str]) -> str:
    """Render video metadata as a Markdown ``## Video Details`` section."""
    rows = ["## Video Details", ""]
    if "title" in meta:
        rows.append(f"- **Title:** [{meta['title']}]({meta['url']})")
    else:
        rows.append(f"- **URL:** {meta['url']}")
    if "channel" in meta:
        channel = f"[{meta['channel']}]({meta['channel_url']})" if "channel_url" in meta else meta["channel"]
        rows.append(f"- **Channel:** {channel}")
    if "published" in meta:
        rows.append(f"- **Published:** {meta['published']}")
    if "duration" in meta:
        rows.append(f"- **Duration:** {meta['duration']}")
    if "views" in meta:
        rows.append(f"- **Views:** {meta['views']}")
    if "description" in meta:
        quoted = "\n".join(f"> {line}" if line else ">" for line in meta["description"].splitlines())
        rows += ["", "### Description", "", quoted]

    return "\n".join(rows) + "\n"


def get_youtube_transcript(video_id: str) -> str:
    """Fetch transcript for a given YouTube video ID.
    
    Tries to get a manual English transcript. If missing, attempts to grab
    an auto-generated English transcript, or translates another available language to English.
    """
    import time
    time.sleep(1.5)  # Pace requests to avoid rapid-fire IP bans
    try:
        import requests
        session = requests.Session()
        session.headers.update(_BROWSER_HEADERS)
        api = YouTubeTranscriptApi(http_client=session)
        transcript_list = api.list(video_id)
        transcript = None
        
        try:
            # First, look for a manual English transcript
            transcript = transcript_list.find_manually_created_transcript(['en'])
        except NoTranscriptFound:
            # Next, look for an auto-generated English one
            try:
                transcript = transcript_list.find_generated_transcript(['en'])
            except NoTranscriptFound:
                # Finally, pick any transcript and attempt to translate it to English
                for t in transcript_list:
                    if t.is_translatable and 'en' in [lang.language_code for lang in t.translation_languages]:
                        transcript = t.translate('en')
                        break
        
        if transcript:
            data = transcript.fetch()
            lines = [item.text.replace('\n', ' ') for item in data]
            return "\n".join(lines)
            
        return "_No English transcript could be retrieved for this video._"

    except IpBlocked:
        return "_Could not fetch transcript: YouTube API rate limit exceeded (IP blocked due to too many rapid requests). Please try again later._"
    except TranscriptsDisabled:
        return "_Transcripts are disabled for this video._"
    except Exception as e:
        return f"_Could not fetch transcript: {e}_"

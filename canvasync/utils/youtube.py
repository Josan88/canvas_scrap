import re
from typing import List
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
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        })
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

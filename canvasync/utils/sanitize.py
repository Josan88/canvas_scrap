import re


def sanitize_filename(name):
    """Removes invalid characters from a string to make it a valid filename, and collapses multiple spaces."""
    clean_name = re.sub(r'[\\/*?:"<>|]', " ", name).strip()
    return re.sub(r"\s+", " ", clean_name)


def sanitize_folder_name(name: str, fallback: str = "Unnamed") -> str:
    """Sanitizes folder names and guarantees a non-empty result."""
    safe_name = sanitize_filename(name or "")
    return safe_name if safe_name else fallback

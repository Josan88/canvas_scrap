import os

PERSONAL_NOTES_MARKER = "## Personal Notes\n"
PERSONAL_NOTES_INSTRUCTION = "<!-- Add your local notes and edits below this line. They will be preserved during sync. -->"

def extract_personal_notes(filepath: str) -> str:
    """Extracts existing personal notes from a markdown file."""
    if not os.path.exists(filepath):
        return ""
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            
        if PERSONAL_NOTES_MARKER in content:
            parts = content.split(PERSONAL_NOTES_MARKER)
            notes_content = parts[-1].strip()
            if notes_content == PERSONAL_NOTES_INSTRUCTION:
                return ""
            return notes_content
    except Exception as e:
        print(f"Warning: Could not read personal notes from {filepath}: {e}")
    return ""

def get_personal_notes_section(existing_notes: str) -> str:
    """Returns the formatted personal notes section to be appended to markdown."""
    section = f"\n---\n{PERSONAL_NOTES_MARKER}"
    if existing_notes:
        section += f"{existing_notes}\n"
    else:
        section += f"{PERSONAL_NOTES_INSTRUCTION}\n"
    return section

def append_personal_notes_to_file(filepath: str, existing_notes: str):
    """Appends personal notes section directly to a markdown file on disk."""
    if not os.path.exists(filepath):
        return
        
    try:
        section = get_personal_notes_section(existing_notes)
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(section)
    except Exception as e:
        print(f"Warning: Could not append personal notes to {filepath}: {e}")

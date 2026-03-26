import datetime
import os


def get_existing_file_metadata_local(folder_path, filename):
    """Gets metadata of an existing file in local folder."""
    if not folder_path or not filename:
        return None
    path = os.path.join(folder_path, filename)
    if os.path.exists(path):
        try:
            return {
                "size": os.path.getsize(path),
                "modified_time": os.path.getmtime(path),
            }
        except OSError as error:
            print(f"Error getting metadata for '{path}': {error}")
    return None


def has_file_changed(existing_metadata, canvas_size=None, canvas_updated_at=None):
    """Checks if file has changed based on metadata."""
    if not existing_metadata:
        return True  # New file
    if canvas_size is not None and existing_metadata["size"] != canvas_size:
        return True
    if canvas_updated_at and existing_metadata["modified_time"]:
        try:
            canvas_time = datetime.datetime.fromisoformat(
                canvas_updated_at.replace("Z", "+00:00")
            )
            existing_mod = existing_metadata["modified_time"]
            if isinstance(existing_mod, (int, float)):
                existing_time = datetime.datetime.fromtimestamp(
                    float(existing_mod), tz=canvas_time.tzinfo
                )
            else:
                existing_time = datetime.datetime.fromisoformat(
                    str(existing_mod).replace("Z", "+00:00")
                )
            # If the timestamps differ by more than 2 seconds, either Canvas updated 
            # or the user edited the file locally. We return True to overwrite.
            if abs(canvas_time.timestamp() - existing_time.timestamp()) > 2.0:
                return True
        except (ValueError, TypeError):
            pass  # If parsing fails, assume changed
    return False

import os
import shutil

from canvasync.utils.sanitize import sanitize_folder_name


def get_or_create_local_folder(local_root_dir, folder_name, parent_path=None):
    """Creates a local folder if it doesn't exist. Returns the full path."""
    folder_name = sanitize_folder_name(folder_name)
    if parent_path:
        folder_path = os.path.join(parent_path, folder_name)
    else:
        folder_path = os.path.join(local_root_dir, folder_name)

    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print(f"Created local folder: '{folder_path}'")
    return folder_path


def get_existing_files_in_local_folder(folder_path):
    """Returns a set of filenames that already exist in a local folder."""
    if not os.path.exists(folder_path):
        return set()
    try:
        return {
            f
            for f in os.listdir(folder_path)
            if os.path.isfile(os.path.join(folder_path, f))
        }
    except OSError as error:
        print(f"Error reading local folder '{folder_path}': {error}")
        return set()


def save_file_locally(local_path, filename, folder_path):
    """Moves a file from temp directory to the specified local folder."""
    if not os.path.exists(local_path):
        return False
    try:
        import stat
        destination_path = os.path.join(folder_path, filename)
        
        # Ensure the destination is writable if it already exists
        if os.path.exists(destination_path):
            try:
                os.chmod(destination_path, stat.S_IWRITE)
            except OSError:
                pass
                
        shutil.move(local_path, destination_path)
        
        # Make markdown files read-only to prevent accidental edits
        if filename.lower().endswith(".md"):
            try:
                os.chmod(destination_path, stat.S_IREAD)
            except OSError:
                pass
                
        print(f"Saved '{filename}' to local storage: '{folder_path}'")
        return True
    except OSError as error:
        print(f"An error occurred saving file locally: {error}")
        return False

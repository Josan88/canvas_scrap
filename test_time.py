from canvasync.storage.metadata import has_file_changed, get_existing_file_metadata_local
from canvasync.storage.local_fs import set_file_mtime
import os
import datetime

folder = "."
filename = "test_time.md"
path = os.path.join(folder, filename)

with open(path, "w") as f:
    f.write("test")

# Canvas updated string
updated_at = "2024-01-01T12:00:00Z"

# 1. Set the time
print(f"Setting mtime to {updated_at}")
set_file_mtime(path, updated_at)

# 2. Get metadata
metadata = get_existing_file_metadata_local(folder, filename)
print(f"Metadata read: {metadata}")

# 3. Check if file has changed
changed = has_file_changed(metadata, canvas_updated_at=updated_at)
print(f"has_file_changed returned: {changed}")

# Let's see the times directly
import time
canvas_time = datetime.datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
existing_mod = metadata["modified_time"]
existing_time = datetime.datetime.fromtimestamp(
    float(existing_mod), tz=canvas_time.tzinfo
)

print(f"Canvas timestamp: {canvas_time.timestamp()}")
print(f"Existing timestamp: {existing_time.timestamp()}")
print(f"Diff: {abs(canvas_time.timestamp() - existing_time.timestamp())}")

os.remove(path)

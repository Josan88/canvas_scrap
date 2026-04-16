"""Quick smoke test for the known-targets wikilink prevention."""
from canvasync.utils.obsidian import (
    html_to_obsidian,
    set_known_wikilink_targets,
    is_known_wikilink_target,
)

html = '<a href="/courses/123/pages/my-page">My Page</a>'

# Test 1: No registry (backwards compatible)
set_known_wikilink_targets(None)
result = html_to_obsidian(html)
print(f"Test 1 (no registry):    {repr(result)}")
assert "[[My Page]]" in result, f"Expected wikilink, got: {result}"

# Test 2: Known target → wikilink created
set_known_wikilink_targets({"My Page", "Other Page"})
result = html_to_obsidian(html)
print(f"Test 2 (known target):   {repr(result)}")
assert "[[My Page]]" in result, f"Expected wikilink, got: {result}"

# Test 3: Unknown target → plain text
set_known_wikilink_targets({"Other Page"})
result = html_to_obsidian(html)
print(f"Test 3 (unknown target): {repr(result)}")
assert "[[" not in result, f"Expected no wikilink, got: {result}"
assert "My Page" in result, f"Expected plain text preserved, got: {result}"

# Test 4: Helper function
set_known_wikilink_targets({"Assignment 1"})
assert is_known_wikilink_target("Assignment 1") is True
assert is_known_wikilink_target("Assignment 2") is False
print("Test 4 (helper):         OK")

# Test 5: PDF link with known target
set_known_wikilink_targets({"MyDoc_pdf"})
pdf_html = '<a href="/courses/123/files/456/download?download_frd=1">MyDoc.pdf</a>'
result = html_to_obsidian(pdf_html, file_id_map={"456": "MyDoc.pdf"})
print(f"Test 5 (PDF known):      {repr(result)}")
assert "[[MyDoc_pdf]]" in result, f"Expected PDF wikilink, got: {result}"

# Test 6: PDF link with unknown target
set_known_wikilink_targets({"Other_pdf"})
result = html_to_obsidian(pdf_html, file_id_map={"456": "MyDoc.pdf"})
print(f"Test 6 (PDF unknown):    {repr(result)}")
assert "[[" not in result, f"Expected no wikilink, got: {result}"

set_known_wikilink_targets(None)
print("\nAll tests passed!")

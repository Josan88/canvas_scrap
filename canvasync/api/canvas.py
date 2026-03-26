import os
import re
import shutil
import subprocess
from typing import Optional

import opendataloader_pdf
import requests


def get_paginated_canvas_items(
    url,
    headers,
    session: Optional[requests.Session],
    timeout: int,
    per_page: int,
    suppress_errors: bool = False,
):
    """Handles Canvas API pagination to retrieve all items from an endpoint using a shared session, with per_page sizing."""
    if session is None:
        session = requests.Session()
    # Append per_page if not already present
    if "per_page=" not in url:
        url += ("&" if "?" in url else "?") + f"per_page={per_page}"
    items, next_url = [], url
    while next_url:
        try:
            response = session.get(next_url, headers=headers, timeout=timeout)
            response.raise_for_status()
            items.extend(response.json())
            next_url = None
            if "Link" in response.headers:
                links = requests.utils.parse_header_links(response.headers["Link"])
                next_url = next(
                    (link["url"] for link in links if link.get("rel") == "next"), None
                )
        except requests.exceptions.RequestException as e:
            if not suppress_errors:
                print(f"Error fetching data from Canvas: {e}")
            break
    return items


def download_canvas_file(
    file_url, local_path, headers, session: Optional[requests.Session], timeout: int
):
    """Downloads a file from a Canvas URL to a local path."""
    if session is None:
        session = requests.Session()
    try:
        with session.get(file_url, headers=headers, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return True
    except requests.exceptions.RequestException as e:
        print(f"Failed to download {file_url}: {e}")
        return False


def check_java_environment():
    """Check if Java is available and verify minimum version (Java 11+).

    Returns:
        tuple: (is_available: bool, version_info: str, error_message: str or None)
    """
    try:
        # Check if java command exists
        result = subprocess.run(
            ["java", "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        # Parse version from stderr or stdout (java -version writes to stderr)
        version_output = result.stderr or result.stdout
        if not version_output:
            return False, "", "java -version produced no output"

        # Extract version number (e.g., "11", "17", etc.)
        version_match = re.search(r'version "([0-9]+)', version_output)
        if version_match:
            major_version = int(version_match.group(1))
            version_info = f"Java {major_version}"

            if major_version < 11:
                return (
                    False,
                    version_info,
                    f"Java 11 or higher required, found {version_info}",
                )
            return True, version_info, None

        return True, "(version unknown)", None
    except FileNotFoundError:
        return False, "", "Java command not found. Please install Java 11 or higher."
    except subprocess.TimeoutExpired:
        return False, "", "Java version check timed out"
    except Exception as e:
        return False, "", f"Error checking Java: {e}"


def extract_pdf_with_diagnostics(pdf_path: str, output_dir: str) -> tuple[bool, str]:
    """Extract PDF to Markdown with enhanced error capture and diagnostics.

    Returns:
        tuple: (success: bool, error_message: str or empty string)
    """
    try:
        # Attempt extraction using opendataloader_pdf
        opendataloader_pdf.convert(
            input_path=[pdf_path],
            output_dir=output_dir,
            format="markdown",
            hybrid="docling-fast",
            hybrid_mode="full",
            hybrid_fallback=True,
            use_struct_tree=True,
            quiet=True,
            # image_output="embedded",
        )

        # Rename the auto-generated "{stem}_images" folder to "images"
        pdf_stem = os.path.splitext(os.path.basename(pdf_path))[0]
        old_images_dir = os.path.join(output_dir, f"{pdf_stem}_images")
        new_images_dir = os.path.join(output_dir, "images")

        if os.path.isdir(old_images_dir):
            # If "images" dir already exists, merge contents into it
            if os.path.isdir(new_images_dir):
                for item in os.listdir(old_images_dir):
                    src = os.path.join(old_images_dir, item)
                    dst = os.path.join(new_images_dir, item)
                    shutil.move(src, dst)
                shutil.rmtree(old_images_dir)
            else:
                os.rename(old_images_dir, new_images_dir)

            # Update image references in the generated markdown file
            md_path = os.path.join(output_dir, f"{pdf_stem}.md")
            if os.path.isfile(md_path):
                with open(md_path, "r", encoding="utf-8") as f:
                    content = f.read()
                updated = content.replace(f"{pdf_stem}_images/", "images/")
                if updated != content:
                    with open(md_path, "w", encoding="utf-8") as f:
                        f.write(updated)

        return True, ""
    except subprocess.CalledProcessError as e:
        # Capture Java subprocess errors
        stderr_output = e.stderr if hasattr(e, "stderr") else ""
        stdout_output = e.stdout if hasattr(e, "stdout") else ""
        error_lines = []

        if stderr_output:
            error_lines.append(f"Java stderr: {stderr_output[:500]}")
        if stdout_output:
            error_lines.append(f"Java stdout: {stdout_output[:500]}")
        if e.returncode:
            error_lines.append(f"Exit code: {e.returncode}")

        error_msg = " | ".join(error_lines) if error_lines else f"Subprocess error: {e}"
        return False, error_msg
    except Exception as e:
        error_msg = str(e)

        # Provide better diagnostics for common errors
        if "No such file" in error_msg or "cannot find" in error_msg.lower():
            return False, f"PDF file not found at {pdf_path}: {error_msg}"
        elif "Permission denied" in error_msg:
            return False, f"Permission denied reading {pdf_path}: {error_msg}"
        elif "Java" in error_msg or "opendataloader" in error_msg:
            return False, f"PDF extraction tool error: {error_msg}"

        return False, f"PDF extraction failed: {error_msg}"

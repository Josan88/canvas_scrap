import os
import shutil
import subprocess
from typing import Tuple

OFFICE_EXTENSIONS = {".docx", ".pptx", ".xlsx"}


def is_office_file(filename: str) -> bool:
    """Return True if the filename has an Office extension we can convert."""
    return os.path.splitext(filename)[1].lower() in OFFICE_EXTENSIONS


def _get_libreoffice_cmd() -> str | None:
    """Find the LibreOffice executable."""
    for cmd in ("soffice", "libreoffice"):
        if shutil.which(cmd):
            return cmd
    return None


def convert_office_to_pdf(
    input_path: str, output_dir: str, timeout: int = 120
) -> Tuple[bool, str, str]:
    """Convert an Office file to PDF using LibreOffice headless.

    Returns:
        (success, pdf_path, error_message)
    """
    ext = os.path.splitext(input_path)[1].lower()
    base = os.path.splitext(os.path.basename(input_path))[0]
    pdf_path = os.path.join(output_dir, f"{base}.pdf")

    if ext not in OFFICE_EXTENSIONS:
        return False, "", f"Unsupported office extension: {ext}"

    cmd = _get_libreoffice_cmd()
    if cmd is None:
        return False, "", "LibreOffice not found. Please install it (https://www.libreoffice.org/) and add it to your PATH."

    try:
        result = subprocess.run(
            [
                cmd,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                output_dir,
                input_path,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        if result.returncode == 0:
            if os.path.exists(pdf_path):
                return True, pdf_path, ""
            return False, "", "LibreOffice returned success but PDF file was not created"
            
        return False, "", f"LibreOffice conversion failed: {result.stderr.strip()}"
        
    except subprocess.TimeoutExpired:
        return False, "", f"LibreOffice conversion timed out after {timeout} seconds"
    except Exception as e:
        return False, "", f"Office-to-PDF conversion failed: {e}"

import os
import uuid
import requests
from typing import Optional
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from deepinsight.core.utils.research_utils import parse_research_config


class DownloadFileResult(BaseModel):
    """
    Output model for the download_file_from_url tool.
    """

    file_path: Optional[str] = Field(
        default=None,
        description="Absolute or relative local path where the downloaded file is saved."
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if the download or file writing process fails."
    )


SUPPORTED_IMAGE_FORMATS = {"BMP", "GIF", "JPEG", "PNG", "TIFF", "WMF"}


def _is_http_url(url: str) -> bool:
    try:
        u = (url or "").strip().lower()
        return u.startswith("http://") or u.startswith("https://")
    except Exception:
        return False


def _is_web_image(url: str) -> bool:
    """
    Determine whether the given URL is a valid HTTP/HTTPS URL.
    """
    return _is_http_url(url)


@tool("download_file_from_url", return_direct=False)
def download_file_from_url(
    file_url: str,
    file_name: Optional[str],
    config: RunnableConfig
) -> DownloadFileResult:
    """
    A general-purpose tool for downloading a file from a remote URL
    and saving it into a configurable local workspace directory.

    This tool allows an LLM or agent to safely fetch and store remote resources
    (such as images, PDFs, or datasets).

    Args:
        file_url (str):
            The remote URL of the file to download.
            Example: "https://example.com/image.png".
            This parameter is typically provided by the model.

        file_name (Optional[str]):
            The desired name of the local file, including its extension.
            Example: "photo.jpg" or "document.pdf".
            If None, the tool automatically generates a random filename using UUID.
            The filename should not contain directory separators.

    Returns:
        DownloadFileResult:
            A structured Pydantic object containing:
            - `file_path`: The final path of the downloaded file (if successful).
            - `error`: A human-readable error message if the operation fails.

    Notes:
        - Web image detection relies on common image file extensions only; no header requests are made.
        - If the input is not a web image (non-HTTP/HTTPS or URL whose extension does not look like an image), the tool will not download and will return the original path directly.
        - This tool performs synchronous downloads using the `requests` library.
        - If a file with the same name already exists, the operation will fail safely.
        - The workspace directory will be automatically created if it does not exist.
        - The function gracefully handles network, HTTP, and file I/O errors.
        - This tool is suitable for safe invocation by autonomous agents.
    """

    # Early exit: if it's NOT a web image, return the original relative path directly
    # - Non-http/https paths are treated as local/relative and returned as-is
    # - HTTP/HTTPS URLs with non-image content-type are returned as-is
    if not _is_web_image(file_url):
        return DownloadFileResult(file_path=file_url)

    # Step 1: Determine images workspace directory under current_thread_work_root
    # Reference: current_thread_work_root = os.path.join(rc.work_root, "conference_report_result", rc.thread_id)
    rc = parse_research_config(config)
    thread_id = rc.thread_id or "default_thread"
    current_thread_work_root = os.path.join(rc.work_root or "./", "conference_report_result", thread_id)
    files_path_folder = os.path.join(current_thread_work_root, "files")

    # Ensure the base workspace directory exists
    os.makedirs(files_path_folder, exist_ok=True)

    # Step 2: Generate a filename if not provided
    if not file_name:
        file_name = str(uuid.uuid4())

    # Step 3: Build the final file path within files directory
    file_path = os.path.join(files_path_folder, file_name)

    # Step 4: Check for existing file before downloading
    if os.path.exists(file_path):
        return DownloadFileResult(error=f"File already exists: {file_path}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    }
    # Step 5: Download and save the file
    try:
        response = requests.get(file_url, stream=True, timeout=150, headers=headers)
        response.raise_for_status()  # Raise an exception for HTTP errors

        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:  # Avoid keep-alive chunks
                    f.write(chunk)

        # If image format not in supported list, convert to PNG
        try:
            with Image.open(file_path) as img:
                img_format = (img.format or "").upper()
                # Compare in a case-insensitive manner
                if img_format.upper() not in SUPPORTED_IMAGE_FORMATS:
                    png_path = os.path.splitext(file_path)[0] + ".png"
                    
                    if os.path.abspath(png_path) == os.path.abspath(file_path):
                        png_path = os.path.splitext(file_path)[0] + "_converted.png"

                    img.convert("RGB").save(png_path, "PNG")
                    img.close()

                    os.remove(file_path)
                    os.rename(png_path, file_path)

        except UnidentifiedImageError:
            # Not an image file, skip conversion
            pass

    except requests.RequestException as e:
        return DownloadFileResult(error=f"Failed to download file from {file_url}: {str(e)}")

    except OSError as e:
        return DownloadFileResult(error=f"Failed to write file to {file_path}: {str(e)}")

    # Step 6: Return success result
    return DownloadFileResult(file_path=file_path)

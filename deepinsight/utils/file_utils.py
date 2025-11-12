import hashlib
from typing import Optional


def compute_md5(path: str, chunk_size: int = 8192) -> str:
    """
    Compute the MD5 checksum of a file.

    Args:
        path: Absolute or relative file path.
        chunk_size: Size of chunks to read to avoid high memory usage.

    Returns:
        Hexadecimal MD5 digest string.

    Raises:
        FileNotFoundError: If the file does not exist.
        OSError: For I/O errors while reading the file.
    """
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


__all__ = ["compute_md5"]
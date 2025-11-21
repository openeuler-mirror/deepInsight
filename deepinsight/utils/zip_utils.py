import io
import logging
import zipfile
from typing import TypeAlias


_FileTree: TypeAlias = "dict[str, bytes | _FileTree]"


def unzip(file: bytes | io.BytesIO) -> _FileTree:
    buf = io.BytesIO(file) if isinstance(file, bytes) else file
    result_dict = {}
    with zipfile.ZipFile(buf, "r") as archive:
        for filename in archive.namelist():
            is_dir = filename.endswith("/")
            parts = [
                p for p in filename.rstrip("/").split("/")
                if p and (p not in (".", ".."))
            ]
            if len(parts) < 1:
                logging.warning(f"Illegal filename {filename}")
                continue
            if is_dir:
                _set_new_value(result_dict, {}, parts)
            else:
                _set_new_value(result_dict, archive.read(filename), parts)
    return result_dict


def _set_new_value(d: dict, v, key_list: list[str]) -> None:
    for key in key_list[:-1]:
        if key not in d:
            d[key] = {}
        d = d[key]
        if not isinstance(d, dict):
            logging.warning(f"Try creating {key} in a dict which is already exists.")
            return
    key = key_list[-1]
    if key in d:
        logging.warning(f"Try creating {key} in a dict which is already exists.")
        return
    d[key] = v

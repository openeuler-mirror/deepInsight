import os
import threading
import time
from typing import Dict, List, Optional, Any

from deepagents.backends.protocol import BackendProtocol, WriteResult, EditResult
from deepagents.backends.utils import FileInfo, GrepMatch


# -----------------------------
# Exceptions
# -----------------------------
class MCPFsError(Exception):
    pass


class FileNotFoundErrorMCP(MCPFsError):
    pass


class NotADirectoryErrorMCP(MCPFsError):
    pass


class ExistsErrorMCP(MCPFsError):
    pass


class PermissionErrorMCP(MCPFsError):
    pass


class InvalidOperationErrorMCP(MCPFsError):
    pass


# -----------------------------
# Utilities
# -----------------------------
def _split_parts(path: str) -> List[str]:
    return [seg for seg in path.replace("\\", "/").split("/") if seg]


def _join_parts(parts: List[str]) -> str:
    return "/" + "/".join(parts) if parts else "/"


def _norm_path(path: str, base: Optional[str] = None) -> str:
    if not path:
        raise InvalidOperationErrorMCP("Empty path")
    if path.startswith("/"):
        parts = _split_parts(path)
    else:
        base_parts = _split_parts(base or "/")
        parts = base_parts + _split_parts(path)
    out = []
    for seg in parts:
        if seg == ".":
            continue
        elif seg == "..":
            if out:
                out.pop()
        else:
            out.append(seg)
    return _join_parts(out)


def _normalize_path(path: str) -> str:
    """Centralized path normalization."""
    return _norm_path(path)


class MemoryFilesystem(BackendProtocol):
    """Optimized in-memory virtual filesystem simulating Linux-like operations."""

    _instance: Optional["MemoryFilesystem"] = None
    _lock = threading.RLock()

    def __new__(cls, allowed_roots: Optional[List[str]] = None):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self, allowed_roots: Optional[List[str]] = None):
        if getattr(self, "_initialized", False):
            return  # Already initialized
        self.lock = threading.RLock()
        self.allowed_roots = allowed_roots or ["/"]
        self.files: Dict[str, Dict[str, Any]] = {}
        self.dirs = {"/"}
        self._create_default_directories()
        self._initialized = True

    def _create_default_directories(self):
        default_dirs = ["/"]
        for dir_path in default_dirs:
            self._ensure_dir_exists(dir_path)

    def _ensure_allowed(self, path: str):
        for root in self.allowed_roots:
            if path.startswith(root):
                return
        raise PermissionErrorMCP(f"Path {path} outside allowed roots")

    def _ensure_dir_exists(self, dir_path: str):
        """Ensure the directory exists, if not create it."""
        if dir_path not in self.dirs:
            self.dirs.add(dir_path)

    def exists(self, path: str) -> bool:
        """Check if the file or directory exists in the memory filesystem."""
        with self.lock:
            normalized_path = _normalize_path(path)
            self._ensure_allowed(normalized_path)

            # Check if it's a directory
            if normalized_path in self.dirs:
                return True

            # Check if it's a file
            if normalized_path in self.files:
                return True

            return False

    def write(
            self,
            file_path: str,
            content: str,
    ) -> WriteResult:
        """Create a new file. Returns WriteResult; error populated on failure."""
        with self.lock:
            try:
                normalized_path = _normalize_path(file_path)
                self._ensure_allowed(normalized_path)
                parent_dir = _join_parts(_split_parts(normalized_path)[:-1])
                if parent_dir not in self.dirs:
                    self._ensure_dir_exists(parent_dir)

                if normalized_path in self.files:
                    return WriteResult(error="File exists")

                self.files[normalized_path] = {"content": content, "created_at": time.time(), "modified_at":
                    time.time()}
                return WriteResult(path=normalized_path)
            except MCPFsError as e:
                return WriteResult(error=str(e))

    def read(self, file_path: str, offset: int = 0, limit: int = 8000) -> str:
        with self.lock:
            normalized_path = _normalize_path(file_path)
            self._ensure_allowed(normalized_path)

            if normalized_path not in self.files:
                return f"Error: {file_path} not found."

            file_data = self.files[normalized_path]
            content = file_data["content"][offset:offset + limit]
            return content

    def edit(
            self,
            file_path: str,
            old_string: str,
            new_string: str,
            replace_all: bool = False,
    ) -> EditResult:
        """Edit a file by replacing string occurrences. Returns EditResult."""
        with self.lock:
            normalized_path = _normalize_path(file_path)
            self._ensure_allowed(normalized_path)

            if normalized_path not in self.files:
                return EditResult(error=f"File {file_path} not found.")

            file_data = self.files[normalized_path]
            content = file_data["content"]
            occurrences = 0

            for i, line in enumerate(content):
                new_line = line.replace(old_string, new_string)
                if new_line != line:
                    content[i] = new_line
                    occurrences += 1
                    if not replace_all:
                        break

            file_data["content"] = content
            file_data["modified_at"] = time.time()

            return EditResult(path=normalized_path, occurrences=occurrences)

    def ls_info(self, path: str) -> List[FileInfo]:
        """Structured listing with file metadata."""
        with self.lock:
            normalized_path = _normalize_path(path)
            self._ensure_allowed(normalized_path)

            if normalized_path not in self.dirs:
                return []

            files_info = [
                FileInfo(
                    path=file_path,
                    is_dir=False,
                    size=len(file_data["content"]),
                    modified_at=time.ctime(file_data["modified_at"])
                )
                for file_path, file_data in self.files.items() if file_path.startswith(normalized_path)
            ]
            return files_info

    def glob_info(self, pattern: str, path: str = "/") -> List[FileInfo]:
        """Structured glob matching returning FileInfo dicts."""
        with self.lock:
            normalized_path = _normalize_path(path)
            self._ensure_allowed(normalized_path)

            matching_files = [
                file_path for file_path in self.files if file_path.startswith(normalized_path) and pattern in file_path
            ]
            return [
                FileInfo(
                    path=file_path,
                    is_dir=False,
                    size=len(self.files[file_path]["content"]),
                    modified_at=time.ctime(self.files[file_path]["modified_at"])
                )
                for file_path in matching_files
            ]

    def grep_raw(self, pattern: str, path: Optional[str] = None, glob: Optional[str] = None) -> List[GrepMatch] | str:
        """Structured search results or error string for invalid input."""
        with self.lock:
            normalized_path = _normalize_path(path or "/")
            self._ensure_allowed(normalized_path)

            matches = []
            for file_path, file_data in self.files.items():
                if normalized_path in file_path and (glob is None or glob in file_path):
                    for line_num, line in enumerate(file_data["content"]):
                        if pattern in line:
                            matches.append(GrepMatch(path=file_path, line=line_num, text=line))

            return matches if matches else "No matches found."

    def list_files_in_directory(self, dir_path: str) -> List[str]:
        """List all files in the specified directory."""
        with self.lock:
            normalized_path = _normalize_path(dir_path)
            self._ensure_allowed(normalized_path)

            if normalized_path not in self.dirs:
                return []  # If the directory does not exist, return an empty list

            # Return a list of files in the directory
            return [file_path for file_path in self.files if
                    file_path.startswith(normalized_path) and file_path != normalized_path]

    def sync_with_real_fs(self, real_dir: str, folder_path: Optional[str] = None,
                          import_file: Optional[str] = None) -> str:
        """
        Sync memory filesystem with real filesystem.
        1. If `import_file` is None: Export memory FS (folder_path or all) to real_dir.
        2. If `import_file` is provided: Export that in-memory file to real filesystem.

        Args:
            real_dir (str): Target real filesystem directory.
            folder_path (Optional[str]): Memory FS folder path (for export scope).
            import_file (Optional[str]): In-memory file path to export.

        Returns:
            str: Success message
        """
        with self.lock:
            # ---------------------------
            # 导出单个内存文件
            # ---------------------------
            if import_file:
                normalized_import_path = _normalize_path(import_file)

                # 判断该文件是否存在于内存文件系统
                if normalized_import_path not in self.files:
                    raise FileNotFoundErrorMCP(f"In-memory file {import_file} does not exist.")

                # 写入到真实磁盘
                os.makedirs(real_dir, exist_ok=True)
                export_path = os.path.join(real_dir, os.path.basename(normalized_import_path))

                with open(export_path, "w", encoding="utf-8") as f:
                    f.write(self.files[normalized_import_path]["content"])

                return f"✅ Exported in-memory file {normalized_import_path} to {export_path}"

            # ---------------------------
            # 导出整个文件系统或目录
            # ---------------------------
            if not os.path.exists(real_dir):
                os.makedirs(real_dir)

            if folder_path:
                folder_norm = _norm_path(folder_path)
                if folder_norm not in self.dirs:
                    raise NotADirectoryErrorMCP(f"Folder '{folder_norm}' not found")

                folder_prefix = folder_norm if folder_norm.endswith("/") else folder_norm + "/"
                export_files = {
                    p: meta for p, meta in self.files.items()
                    if p.startswith(folder_prefix)
                }
            else:
                export_files = dict(self.files)
                folder_prefix = "/"

            # 执行文件落盘
            for file_path, meta in export_files.items():
                relative_path = file_path[len(folder_prefix):]
                real_file_path = os.path.join(real_dir, relative_path)
                os.makedirs(os.path.dirname(real_file_path), exist_ok=True)
                with open(real_file_path, "w", encoding="utf-8") as f:
                    f.write(meta["content"])

            return f"✅ {len(export_files)} files exported to {real_dir} (from folder: {folder_path or '/'})"


mem_file_system_instance = MemoryFilesystem()

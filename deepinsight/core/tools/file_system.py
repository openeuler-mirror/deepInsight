import logging
import os
import threading
import time
from typing import Dict, List, Optional, Any
from langchain.tools import tool


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


# -----------------------------
# Stateful Memory Filesystem
# -----------------------------
# -----------------------------
# Stateful Memory Filesystem (Singleton)
# -----------------------------
class MemoryMCPFilesystem:
    """In-memory virtual filesystem for agent integration (Singleton)."""

    _instance: Optional["MemoryMCPFilesystem"] = None
    _lock = threading.RLock()  # 单例锁

    def __new__(cls, allowed_roots: Optional[List[str]] = None):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self, allowed_roots: Optional[List[str]] = None):
        if getattr(self, "_initialized", False):
            return  # 已经初始化过，直接返回
        self.lock = threading.RLock()
        self.allowed_roots = ["/"] if not allowed_roots else allowed_roots
        self.files: Dict[str, Dict[str, Any]] = {}
        self.dirs = set(["/"])
        self._create_default_directories()
        self._initialized = True

    # -----------------------------
    # 下面保留你原来的方法
    # -----------------------------
    def _create_default_directories(self):
        default_dirs = ["/"]
        for dir_path in default_dirs:
            self._ensure_dir_exists(dir_path)

    def _ensure_allowed(self, path: str):
        for root in self.allowed_roots:
            if root == "/" or path.startswith(root):
                return
        raise PermissionErrorMCP(f"Path {path} outside allowed roots")

    def _ensure_dir_exists(self, dir_path: str):
        parts = _split_parts(dir_path)
        cur = []
        for seg in parts:
            cur.append(seg)
            p = _join_parts(cur)
            if p not in self.dirs:
                self.dirs.add(p)

    def write_file(self, file_path: str, content: str, create_dirs: bool = True) -> str:
        with self.lock:
            if not file_path.lower().endswith(".md"):
                file_path += ".md"
            normalized_path = _norm_path(file_path)
            self._ensure_allowed(normalized_path)
            path_parts = _split_parts(normalized_path)
            if not path_parts:
                raise InvalidOperationErrorMCP("Invalid file path")
            parent_dir = _join_parts(path_parts[:-1])
            if parent_dir not in self.dirs:
                if create_dirs:
                    self._ensure_dir_exists(parent_dir)
                else:
                    raise NotADirectoryErrorMCP(f"Parent directory '{parent_dir}' not found")
            self.files[normalized_path] = {"content": content, "mtime": time.time()}
            return f"✅ File written: {normalized_path}"

    def read_all_files_in_dir(self, folder_path: str) -> Dict[str, str]:
        with self.lock:
            folder_norm = _norm_path(folder_path)
            self._ensure_allowed(folder_norm)

            if folder_norm not in self.dirs:
                logging.error(f"Folder '{folder_norm}' not found， dirs:{self.dirs}")
                # ❌ 不抛异常，直接返回空 Map
                return {}

            prefix = folder_norm if folder_norm.endswith("/") else folder_norm + "/"

            result: Dict[str, str] = {}

            # ✅ 查找所有文件（递归）
            for file_path, meta in self.files.items():
                if file_path.startswith(prefix):
                    result[file_path] = meta["content"]

            return result

    def read_file(self, file_path: str) -> str:
        with self.lock:
            p = _norm_path(file_path)
            if p not in self.files:
                return f""
            return self.files[p]["content"]

    def list_directory(self, path: str) -> str:
        with self.lock:
            try:
                p = _norm_path(path)
                self._ensure_allowed(p)

                # ✅ 如果目录不存在但路径合法，则自动创建，不提示
                if p not in self.dirs:
                    self._ensure_dir_exists(p)

                prefix = p if p.endswith("/") else p + "/"
                children = []

                # 查找子文件
                for f in self.files:
                    if f.startswith(prefix):
                        rest = f[len(prefix):]
                        part = rest.split("/", 1)[0]
                        children.append(part)

                # 查找子目录
                for d in self.dirs:
                    if d.startswith(prefix):
                        rest = d[len(prefix):]
                        if rest:
                            part = rest.split("/", 1)[0]
                            children.append(part)

                # 构建子目录层级结构
                def format_child(child, level=1):
                    return "  " * level + child

                # 将子目录排序并按照层级格式化
                formatted_children = []
                for child in sorted(children):
                    formatted_children.append(format_child(child))

                return f"List of {p}: \n" + "\n".join(formatted_children)

            except Exception as e:
                print(f"❌ Exception in list_directory('{path}'): {e}")
                return f"❌ Error listing directory '{path}': {e}"

    def export_to_real_fs(self, real_dir: str, folder_path: Optional[str] = None) -> str:
        """
        Export files from a specific folder (including subfolders) in memory FS
        to a real filesystem directory, but DO NOT include folder_path as a top-level directory.

        Args:
            real_dir (str): Target real filesystem directory
            folder_path (Optional[str]): Folder path in memory FS to export.
                                         If None, export everything.

        Returns:
            str: Success message
        """
        with self.lock:
            if not os.path.exists(real_dir):
                os.makedirs(real_dir)

            if folder_path:
                folder_norm = _norm_path(folder_path)
                if folder_norm not in self.dirs:
                    raise NotADirectoryErrorMCP(f"Folder '{folder_norm}' not found")

                folder_prefix = folder_norm if folder_norm.endswith("/") else folder_norm + "/"

                # ✅ 找出 folder_path 下的所有文件
                export_files = {
                    p: meta for p, meta in self.files.items()
                    if p.startswith(folder_prefix)
                }
            else:
                export_files = dict(self.files)
                folder_prefix = "/"  # ✅ 用于统一处理 relative path

            # ✅ 执行文件落盘（去掉 folder_path 前缀）
            for file_path, meta in export_files.items():
                relative_path = file_path[len(folder_prefix):]  # <-- ✅ Trim folder path
                real_file_path = os.path.join(real_dir, relative_path)

                os.makedirs(os.path.dirname(real_file_path), exist_ok=True)
                with open(real_file_path, "w", encoding="utf-8") as f:
                    f.write(meta["content"])

            return f"✅ {len(export_files)} files exported to {real_dir} (from folder: {folder_path or '/'})"

    def create_folders(self, parent_folder: str, subfolders: List[str]) -> str:
        with self.lock:
            # 规范化父文件夹路径
            normalized_parent_folder = _norm_path(parent_folder)
            self._ensure_allowed(normalized_parent_folder)

            # 确保父文件夹存在
            self._ensure_dir_exists(normalized_parent_folder)

            # 创建子文件夹
            for subfolder in subfolders:
                subfolder_path = _join_parts([normalized_parent_folder, subfolder])
                self._ensure_dir_exists(subfolder_path)

            return f"✅ Folders created under {normalized_parent_folder}: {', '.join(subfolders)}"


# -----------------------------
# Tool registration via closures
# -----------------------------
def register_fs_tools(fs: MemoryMCPFilesystem):
    """Register fs methods as LangChain tools with correct instance binding."""

    @tool("write_file", return_direct=False)
    def write_file_tool(file_path: str, content: str) -> str:
        """
        Write content to a file in the OS filesystem.

        This function writes the given content to the specified file path. If the directory
        does not exist, it will be automatically created before writing the content to the file.

        Parameters:
        - file_path (str): The full path of the file where the content will be written.
        - content (str): The content to be written to the file.

        Returns:
        - str: A message indicating the result of the write operation, such as success or error.
        """
        # Ensure the directory exists, creating it if necessary
        return fs.write_file(file_path, content)

    @tool("read_file", return_direct=False)
    def read_file_tool(file_path: str) -> str:
        """Read the contents of a file."""
        return fs.read_file(file_path)

    @tool("list_directory", return_direct=False)
    def list_directory_tool(path: str) -> str:
        """
        List files and directories under a given path, returning a tree-like structure.

        Directories are listed without extensions, while files retain their extensions.
        The output is structured in a way that represents a hierarchical directory structure.

        Parameters:
        - path (str): The path of the directory to list.

        Returns:
        - str: A string representing the directory tree, with directories shown without extensions
          and files shown with their extensions.
        """
        return fs.list_directory(path)

        # Generate the tree string

    # return [write_file_tool, read_file_tool, list_directory_tool]
    return [write_file_tool, list_directory_tool]


fs_instance = MemoryMCPFilesystem()

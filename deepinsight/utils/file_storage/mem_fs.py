import logging
import pathlib
import re
import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Literal

from deepagents.backends.protocol import BackendProtocol, EditResult, FileInfo, GrepMatch, WriteResult
from langchain_core.tools import tool, BaseTool
import wcmatch.glob as wc_glob

from deepinsight.utils.trace_utils import tracepoint


DISK_DIR_MODE = 0o755


class IsBinaryError(RuntimeError):
    def __init__(self):
        super().__init__("This file is a binary file that cannot be decoded to a string.")


class DeepAgentsBackend(BackendProtocol):
    def __init__(self, fs: "MemFileSystem"):
        self.__fs = fs

    def ls_info(self, path: str) -> list[FileInfo]:
        return self.__fs.ls_info(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        """`offset` and `limit` is line number."""
        try:
            content = self.__fs.read(file_path)
        except (IsADirectoryError, PermissionError, FileNotFoundError, IsBinaryError) as e:
            return f"{type(e).__name__}: {e}"
        lines = content.splitlines(keepends=True)[offset:offset + limit]
        return "".join(lines)

    def grep_raw(self, pattern: str, path: str | None = None, glob: str | None = None) -> list[GrepMatch] | str:
        try:
            return self.__fs.grep(pattern, path, glob)
        except (re.error, NotADirectoryError, PermissionError, FileNotFoundError) as e:
            return f"{type(e).__name__}: {e}"

    def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
        return self.__fs.glob(pattern, path)

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            self.__fs.write(file_path, content)
            return WriteResult(path=file_path)
        except (re.error, FileExistsError, IsADirectoryError, PermissionError) as e:
            return WriteResult(error=f"{type(e).__name__}: {e}")

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        try:
            return EditResult(path=file_path,
                              occurrences=self.__fs.edit(file_path, old_string, new_string, replace_all))
        except (IsADirectoryError, PermissionError, FileNotFoundError, IsBinaryError) as e:
            return EditResult(error=f"{type(e).__name__}: {e}")


class MemFileSystem(ABC):
    @abstractmethod
    def ls_info(self, path: str = "/") -> list[FileInfo]:
        """Structured listing with file metadata."""

    @abstractmethod
    def read_raw(self, file_path: str) -> str | bytes:
        """Read file content."""

    @abstractmethod
    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> list[GrepMatch]:
        """Structured search results or error string for invalid input."""

    @abstractmethod
    def glob(self, pattern: str, path: str = "/") -> list[FileInfo]:
        """Structured glob matching returning FileInfo dicts."""

    @abstractmethod
    def write(self, file_path: str, content: str | bytes, allow_overwrite: bool = False) -> None:
        """Create a new file. Returns WriteResult; error populated on failure."""

    @abstractmethod
    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> int:
        """Edit a file by replacing string occurrences. Returns number of replacement and raise on failure."""

    @abstractmethod
    def child(self, relative_path: str) -> "ChildFileSystem":
        """Get a child file system regarding `relative_path` (related to this instance) as root."""

    @abstractmethod
    def make_dir(self, path: str) -> None:
        """Make a new directory."""

    @abstractmethod
    def exists(self, path: str) -> Literal[False, "file", "dir"]:
        """Check if `path` exist and type of path."""

    def read(self, file_path: str) -> str:
        """Read text file content."""
        content = self.read_raw(file_path)
        if isinstance(content, bytes):
            raise IsBinaryError()
        return content

    def is_file(self, path: str) -> bool:
        return self.exists(path) == "file"

    def is_dir(self, path: str) -> bool:
        return self.exists(path) == "dir"

    def read_all(self, path: str) -> dict[str, str]:
        path = _norm_path(path, allow_root=True)
        files = [f["path"] for f in self.ls_info(path) if not f.get("is_dir", False)]
        ret = {}
        for filename in files:
            try:
                ret[filename] = self.read(_norm_path(path, filename, allow_root=False))
            except IsBinaryError:
                continue
        return ret

    def deep_agent_backend(self) -> BackendProtocol:
        return DeepAgentsBackend(self)

    def tools(self, ls: bool = True, read: bool = True, write: bool = True,
              edit: bool = False, grep: bool = False, glob: bool = False) -> list[BaseTool]:
        @tool("write_file", return_direct=True)
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
            try:
                self.write(file_path, content)
                return "ok"
            except Exception as e:
                logging.error(f"Agent tool write to {file_path!r} failed with {type(e).__name__}: {e}", exc_info=True)
                return f"Write failed with {type(e).__name__}: {e}"

        @tool("read_file", return_direct=True)
        def read_file_tool(file_path: str) -> str:
            """Read the contents of a file."""
            return self.read(file_path)

        @tool("list_directory", return_direct=True)
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
            return "\n".join(obj["path"] for obj in self.ls_info(path))

        tools = []
        if ls:
            tools.append(list_directory_tool)
        if read:
            tools.append(read_file_tool)
        if write:
            tools.append(write_file_tool)
        if edit or glob or grep:
            raise NotImplementedError("No available tool for edit/glob/grep yet.")
        return tools


class RootFileSystem(MemFileSystem):
    __dirs: set[str]
    """Always ends with a '/'."""
    __files: dict[str, bytes | str]
    """Key is its absolute path."""
    __unchanged: set[str]

    def __init__(self,
                 file_list: dict[str, bytes],
                 directories: list[str],
                 mem_root_prefix: str):
        root = _norm_path(mem_root_prefix, allow_root=True)
        self.__root_dir_name = root
        self.__item_path_prefix = root if root == "/" else root + "/"
        dirs = set(directories).union(str(pathlib.Path(f).parent) for f in file_list)
        self.__dirs = {dir_name.rstrip("/") + "/" for dir_name in dirs}

        decoded_files = {}
        for name, binary in file_list.items():
            try:
                decoded_files[name] = binary.decode("utf8")
            except UnicodeDecodeError:
                decoded_files[name] = binary

        self.__files = decoded_files
        self.__unchanged = set(decoded_files)
        self.__lock = threading.Lock()

    @staticmethod
    def __dir_name(path: str) -> str:
        if "/" not in path:
            return "/"
        return path.rsplit("/", 1)[0] + "/"

    @classmethod
    def from_empty(cls) -> "RootFileSystem":
        return cls({}, [], mem_root_prefix="/")

    @classmethod
    def from_storage(cls, file_list: dict[str, bytes], empty_dirs: list[str],
                     root_prefix: str = "/") -> "RootFileSystem":
        raise NotImplementedError("Currently memfs can only be imported from local disk.")

    @classmethod
    def from_local_disk(cls, real_dir: str,
                        root_prefix: str = "/") -> "RootFileSystem":
        real_path = pathlib.Path(real_dir).resolve(strict=False)
        if not real_path.exists():
            real_path.mkdir(mode=DISK_DIR_MODE, exist_ok=True)
        if not real_path.is_dir():
            raise RuntimeError("Destination is not a legal directory.")

        file_lists = {}
        dirs = set()

        for item in real_path.rglob("*"):
            mem_path = _norm_path(item.relative_to(real_path), allow_root=False)
            if item.is_dir():
                dirs.add(mem_path)
            else:
                with open(item, mode="rb") as f:
                    file_lists[mem_path] = f.read()

        return cls(file_lists, list(dirs), root_prefix)

    @tracepoint("fs_ls", self=lambda root: root.trace_dump())
    def ls_info(self, path: str = "/") -> list[FileInfo]:
        with self.__lock:
            path = self.__norm_path(path, allow_root=True, check_exists=True)
            if path in self.__files:
                return [FileInfo(path=path.rsplit("/", 1)[-1], is_dir=False)]
            path_len = len(path)
            return [
                FileInfo(path=filename[path_len:], is_dir=False) for filename in self.__files
                if filename.startswith(path) and "/" not in filename[path_len:]
            ] + [
                FileInfo(path=dir_name[path_len:], is_dir=True) for dir_name in self.__dirs
                if (dir_name != path) and dir_name.startswith(path) and ("/" not in dir_name[path_len:-1])
            ]

    @tracepoint("fs_read_raw", self=lambda root: root.trace_dump())
    def read_raw(self, file_path: str) -> str | bytes:
        with self.__lock:
            path = self.__norm_path(file_path, allow_root=False, check_exists=True, must_be_file=True)
            content = self.__files[path]
            return content

    @tracepoint("fs_grep", self=lambda root: root.trace_dump())
    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> list[GrepMatch]:
        regex = re.compile(pattern)
        with self.__lock:
            path = self.__norm_path(path, allow_root=True, check_exists=True)
            if not path.endswith("/"):
                raise NotADirectoryError(path)
            filtered = [name for name in self.__files if name.startswith(path)]
            if glob:
                path_len = len(path)
                filtered = [
                    name for name in filtered
                    if wc_glob.globmatch(name[path_len:], glob, flags=wc_glob.BRACE | wc_glob.GLOBSTAR)
                ]
            out = []
            for abs_path in filtered:
                file = self.__files[abs_path]
                if isinstance(file, bytes):
                    continue
                out.extend(
                    GrepMatch(path=abs_path, line=line_index, text=line_text)
                    for line_index, line_text in enumerate(file.splitlines(), 1)
                    if regex.search(line_text)
                )
            return out

    @tracepoint("fs_glob", self=lambda root: root.trace_dump())
    def glob(self, pattern: str, path: str = "/") -> list[FileInfo]:
        with self.__lock:
            path = self.__norm_path(path, allow_root=True, check_exists=True) if path else "/"
            if not path.endswith("/"):  # normalize will add '/' to a found dir
                raise NotADirectoryError(path)
            path_len = len(path)
            files = {name for name in self.__files if name.startswith(path)}.union(
                {name for name in self.__dirs if name.startswith(path) and name != path}
            )
            matches = [
                FileInfo(path=name, is_dir=name.endswith("/")) for name in files
                if wc_glob.globmatch(name[path_len:], pattern, flags=wc_glob.BRACE | wc_glob.GLOBSTAR)
            ]
            return sorted(matches, key=lambda item: item["path"])

    @tracepoint("fs_write", self=lambda root: root.trace_dump())
    def write(self, file_path: str, content: str | bytes, allow_overwrite: bool = False) -> None:
        with self.__lock:
            path = self.__norm_path(file_path, allow_root=False, check_exists=False)
            if path in self.__files and not allow_overwrite:
                raise FileExistsError(path)
            if path + "/" in self.__dirs:
                raise IsADirectoryError(path)
            dir_name = self.__dir_name(path)
            self._mkdir_no_lock(dir_name)
            self.__files[path] = content
            self.__unchanged -= {path}

    @tracepoint("fs_edit", self=lambda root: root.trace_dump())
    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> int:
        with self.__lock:
            path = self.__norm_path(file_path, allow_root=False, check_exists=True, must_be_file=True)
            content = self.__files[path]
            if isinstance(content, bytes):
                raise IsBinaryError()
            count = content.count(old_string)
            if not count:
                return 0
            count = count if replace_all else 1
            self.__files[path] = content.replace(old_string, new_string, count)
            self.__unchanged -= {path}
            return count

    def child(self, relative_path: str) -> "ChildFileSystem":
        return ChildFileSystem(self, self.__norm_path(relative_path, allow_root=False, check_exists=False))

    def make_dir(self, path: str) -> None:
        with self.__lock:
            self._mkdir_no_lock(path)

    def exists(self, path: str) -> Literal[False, "file", "dir"]:
        with self.__lock:
            path = self.__norm_path(path, allow_root=True, check_exists=False)
            if path in self.__files:
                return "file"
            if path == self.__root_dir_name:
                return "dir"
            if path + "/" in self.__dirs:
                return "dir"
            return False

    def export_to_local_disk(self, export_to: str) -> None:
        """return number of make directory / write of files."""
        export_path = pathlib.Path(export_to).resolve(strict=True)
        if not export_path.exists():
            export_path.mkdir(mode=DISK_DIR_MODE, parents=True, exist_ok=True)
        if not export_path.is_dir():
            raise NotADirectoryError(export_path)
        export_path_str = str(export_path)
        if not export_path_str.endswith("/"):
            export_path_str += "/"
        with self.__lock:
            root_prefix_len = len(self.__item_path_prefix)
            for dir_name in self.__dirs:
                if dir_name == self.__item_path_prefix:
                    continue
                sub_dir = pathlib.Path(export_path, dir_name[root_prefix_len:]).resolve()
                if not str(sub_dir).startswith(export_path_str):
                    logging.error(f"Assertion error: {str(sub_dir)!r} is not a sub directory of {export_path_str!r},"
                                  f" skip!")
                    continue
                if not sub_dir.exists():
                    sub_dir.mkdir(mode=DISK_DIR_MODE, parents=True, exist_ok=True)
                if not sub_dir.is_dir():
                    logging.error(f"Assertion error: {sub_dir!r} is not a directory, skip!")
            for filename, file_content in self.__files.items():
                abs_path = pathlib.Path(export_path, filename[root_prefix_len:]).resolve()
                if not str(abs_path).startswith(export_path_str):
                    logging.error(f"Assertion error: {str(abs_path)!r} is not a sub directory of {export_path_str!r},"
                                  f" skip!")
                    continue
                if not abs_path.parent.exists():
                    abs_path.parent.mkdir(mode=DISK_DIR_MODE, parents=True, exist_ok=True)
                if not abs_path.parent.is_dir():
                    logging.error(f"Assertion error: {str(abs_path.parent)!r} is not a directory, skip exporting "
                                  f"{str(abs_path)!r}!")
                    continue
                if filename in self.__unchanged and abs_path.exists():
                    logging.debug(f"File {filename!r} not changed. skip.")
                    continue
                try:
                    with open(abs_path, mode="wb") as f:
                        f.write(file_content if isinstance(file_content, bytes) else file_content.encode("utf8"))
                        self.__unchanged.add(filename)
                except (FileNotFoundError, PermissionError) as e:
                    logging.warning(f"Export {filename!r} to {abs_path!r} failed with {type(e).__name__}: {e}",
                                    exc_info=True)

    def export_as_storage(self, include_unchanged: bool = False) -> Any:
        """Return a dict that match all files (metadata included) """
        raise NotImplementedError("Currently memfs can only be exported to local disk.")

    def trace_dump(self):
        with self.__lock:
            return dict(
                files={k: f"{type(content).__name__} (len={len(content)})" for k, content in self.__files.items()},
                directories=sorted(self.__dirs),
                root=self.__root_dir_name
            )

    def _mkdir_no_lock(self, path: str) -> None:
        path = self.__norm_path(path, allow_root=True, check_exists=False)
        if path in self.__dirs:
            return
        if path in self.__files:
            raise FileExistsError(path)
        parts = path[len(self.__item_path_prefix):].split("/")
        current = self.__item_path_prefix
        for part in parts:
            if not part:
                continue
            current += part + "/"
            self.__dirs.add(current)

    def __norm_path(self, path: str, *, allow_root: bool, check_exists: bool, must_be_file=False) -> str:
        path = _norm_path(path, allow_root=allow_root)  # root dir name may not be '/'
        if not (path == self.__root_dir_name or path.startswith(self.__item_path_prefix)):
            raise PermissionError(path)
        if path in (self.__root_dir_name, self.__item_path_prefix):
            if not allow_root:
                raise PermissionError(path)
            return path
        if check_exists:
            if path in self.__files:
                return path
            if must_be_file:
                if path + "/" in self.__dirs:
                    raise IsADirectoryError(path)
                raise FileNotFoundError(path)
            path += "/"
            if path not in self.__dirs:
                raise FileNotFoundError(path)
        return path


class ChildFileSystem(MemFileSystem):
    """Never directly create instance of this class without another MemFileSystem instance."""
    @staticmethod
    @contextmanager
    def __forward_to(path: str):
        path = _norm_path(path, allow_root=True)
        try:
            yield
        except (NotADirectoryError, FileExistsError, IsADirectoryError, PermissionError, FileNotFoundError) as e:
            raise type(e)(path) from e

    def __init__(self, root: RootFileSystem, sub_path: str):
        self.__root = root
        sub_path = _norm_path(sub_path, allow_root=False)
        self.__sub_path = sub_path
        self.__prefix_len = len(sub_path)
        self.__root.make_dir(sub_path)

    def ls_info(self, path: str = "/") -> list[FileInfo]:
        with self.__forward_to(path):
            return self.__root.ls_info(self.__abs_path(path, allow_root=True))

    def read_raw(self, file_path: str) -> str | bytes:
        with self.__forward_to(file_path):
            return self.__root.read_raw(self.__abs_path(file_path, allow_root=False))

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> list[GrepMatch]:
        with self.__forward_to(path or "/"):
            ret = self.__root.grep(pattern, self.__abs_path(path or "/", allow_root=True), glob)
            for m in ret:
                m["path"] = m["path"][self.__prefix_len:]
            return ret

    def glob(self, pattern: str, path: str = "/") -> list[FileInfo]:
        with self.__forward_to(path):
            ret = self.__root.glob(pattern, self.__abs_path(path, allow_root=True))
            for f in ret:
                f["path"] = f["path"][self.__prefix_len:]
            return ret

    def write(self, file_path: str, content: str | bytes, allow_overwrite: bool = False) -> None:
        with self.__forward_to(file_path):
            self.__root.write(self.__abs_path(file_path, allow_root=False), content, allow_overwrite)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> int:
        with self.__forward_to(file_path):
            return self.__root.edit(self.__abs_path(file_path, allow_root=False), old_string, new_string, replace_all)

    def child(self, relative_path: str) -> "ChildFileSystem":
        return ChildFileSystem(self.__root, self.__abs_path(relative_path, allow_root=False))

    def make_dir(self, path: str) -> None:
        self.__root.make_dir(self.__abs_path(path, allow_root=False))

    def exists(self, path: str) -> Literal[False, "file", "dir"]:
        with self.__forward_to(path):
            return self.__root.exists(self.__abs_path(path, allow_root=True))

    def __abs_path(self, path: str, allow_root: bool) -> str:
        return self.__sub_path + _norm_path(path, allow_root=allow_root)


def _norm_path(path: str | pathlib.Path, *sub: str, allow_root: bool) -> str:
    path = str(pathlib.Path(path, *sub))
    parts = []
    for seg in path.split("/"):
        parts.extend(seg.split("\\"))
    out = []
    for seg in parts:
        if seg == ".":
            continue
        elif seg == "..":
            if out:
                out.pop()
        elif seg:
            out.append(seg)
    if not out and not allow_root:
        raise PermissionError(path)
    return "/" + "/".join(out)

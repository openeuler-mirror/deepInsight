import io
import logging
import os.path
import pathlib
from typing import Any

from pydantic import ConfigDict

from deepinsight.config.config import Config
from deepinsight.utils.file_storage.base import BaseFileStorage, StorageError, StorageOp


logger = logging.getLogger(__name__)


class LocalStorage(BaseFileStorage):
    """Storage implementation via local disk storage."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    root_dir: str

    @classmethod
    def from_config(cls, config: Config) -> "LocalStorage":
        return LocalStorage(
            root_dir=config.file_storage.local.root_dir or config.workspace.work_root
        )

    def model_post_init(self, context: Any, /) -> None:
        path = pathlib.Path(self.root_dir)
        if path.exists():
            if not path.is_dir():
                raise RuntimeError(f"Path {self.root_dir!r} want to be a directory but actually not.")
        else:
            os.makedirs(path, exist_ok=True)

    async def bucket_create(self, bucket: str, *, exist_ok: bool = False) -> None:
        exist = self._check_bucket_exists(StorageOp.CREATE, bucket, allow_miss=True)
        if exist:
            if exist_ok:
                return
            raise StorageError(StorageOp.CREATE, bucket, reason=StorageError.Reason.ALREADY_EXISTS)
        os.makedirs(pathlib.Path(self.root_dir, bucket), exist_ok=True)

    async def list_buckets(self) -> list[str]:
        return [
            item.name
            for item in pathlib.Path(self.root_dir).iterdir()
            if item.is_dir()
        ]

    async def list_files(self, bucket: str, prefix: str = None) -> list[str]:
        self._check_bucket_exists(StorageOp.LIST, bucket, prefix)
        bucket_path = pathlib.Path(self.root_dir, bucket)
        if prefix:
            path = pathlib.Path(self._path_of(StorageOp.LIST, bucket, prefix))
        else:
            path = pathlib.Path(bucket_path)
        if not path.is_dir():
            return []
        return [
            str(item.relative_to(bucket_path))
            for item in path.rglob("*")
            if item.is_file()
        ]

    async def file_add(self, bucket: str, filename: str, content: bytes) -> None:
        self._check_bucket_exists(StorageOp.CREATE, bucket, filename)
        with self._open_file(StorageOp.CREATE, bucket, filename) as f:
            f.write(content)

    async def file_delete(self, bucket: str, filename: str, allow_not_exists: bool = False) -> None:
        self._check_bucket_exists(StorageOp.DELETE, bucket, filename)
        path = self._path_of(StorageOp.GET, bucket, filename)
        try:
            os.remove(path)
        except FileNotFoundError:
            if allow_not_exists:
                return
            raise StorageError(StorageOp.DELETE, bucket, filename, reason=StorageError.Reason.FILE_NOT_FOUND) from None

    async def file_get(self, bucket: str, filename: str) -> bytes:
        self._check_bucket_exists(StorageOp.GET, bucket, filename)
        with self._open_file(StorageOp.GET, bucket, filename) as f:
            return f.read()

    def _check_bucket_exists(self, op: StorageOp, bucket: str, file: str | None = None, *,
                             allow_miss: bool = False) -> bool:
        bucket_path = pathlib.PurePath(bucket)
        if any(["\\" in bucket,
                len(bucket_path.parts) > 1,
                any(part in {"..", ".", ""} for part in bucket_path.parts)]):
            raise StorageError(op, bucket, reason=StorageError.Reason.NAME_ILLEGAL)

        path = pathlib.PurePath(self.root_dir, bucket)
        if os.path.isdir(path):
            return True
        if os.path.exists(path):
            logger.error(f"Local storage want {str(path)!r} is a directory but got a file.")
            raise StorageError(op, bucket, file, reason=StorageError.Reason.NAME_ILLEGAL)
        if not allow_miss:
            raise StorageError(op, bucket, file, reason=StorageError.Reason.BUCKET_NOT_FOUND)
        return False

    def _open_file(self, op: StorageOp, bucket: str, filename: str) -> io.BufferedReader | io.BufferedWriter:
        path = self._path_of(op, bucket, filename)

        directory = path.parent
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        elif not os.path.isdir(directory):
            logger.error(f"Local storage want {str(directory)!r} is a directory but got a file.")
            raise StorageError(op, bucket, filename, reason=StorageError.Reason.NAME_ILLEGAL)

        if op == StorageOp.GET:
            try:
                return open(path, mode="rb")
            except (FileNotFoundError, IsADirectoryError):
                raise StorageError(op, bucket, filename, reason=StorageError.Reason.FILE_NOT_FOUND) from None
        elif op == StorageOp.CREATE:
            try:
                return open(path, mode="xb")
            except FileExistsError:
                pass
            logger.warning(f"File {str(path)!r} conflicts with exiting, next writing will overwrite its content.")
            return open(path, mode="wb")
        raise AssertionError("Illegal execute path. DeepInsight has a bug on local file storage.")

    def _path_of(self, op: StorageOp, bucket: str, filename: str) -> pathlib.PurePath:
        filename_path = pathlib.PurePath(filename)
        if "\\" in filename or any(part in {"..", ".", ""} for part in filename_path.parts):
            raise StorageError(op, bucket, filename, reason=StorageError.Reason.NAME_ILLEGAL)
        return pathlib.PurePath(self.root_dir, bucket, filename)

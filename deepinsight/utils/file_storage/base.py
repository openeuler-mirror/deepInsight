"""Interface definition (compatible with AWS S3 OBS) and storage mapping definition for any files."""
__all__ = ["StorageError", "StorageOp", "BaseFileStorage"]

import asyncio
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Type, TypeVar, TYPE_CHECKING

from pydantic import BaseModel, PrivateAttr

from deepinsight.utils.file_storage.identify import BaseIdentifier

if TYPE_CHECKING:
    from deepinsight.config.config import Config
else:
    from pydantic import BaseModel as Config


_Self = TypeVar("_Self")
logger = logging.getLogger(__name__)


class StorageOp(str, Enum):
    CREATE = "create"
    DELETE = "delete"
    GET = "get"
    LIST = "list"
    CONFIG = "config"


class StorageError(RuntimeError):

    class Reason(str, Enum):
        BUCKET_NOT_FOUND = "bucket_not_found"
        FILE_NOT_FOUND = "file_not_found"
        ALREADY_EXISTS = "already_exists"
        PERMISSION = "permission_denied"
        SPACE_LIMITED = "space_limited"
        NETWORK = "network_error"
        NAME_ILLEGAL = "name_illegal"
        BUCKET_NOT_EMPTY = "bucket_not_empty"
        OTHER = "other"

    op: StorageOp
    bucket: str
    filename: str | None
    """May be a file prefix"""
    reason: Reason

    def __init__(self, op: StorageOp, bucket: str, filename: str | None = None, *, reason: Reason):
        self.op = op
        self.bucket = bucket
        self.filename = filename
        self.reason = reason
        if filename:
            task = f"{op.value} object {filename!r} on bucket {bucket!r}"
        else:
            task = f"{op.value} bucket {bucket!r}"
        super().__init__(f"Storage subsystem failed with code {reason.value!r} when going to {task}.")


class BaseFileStorage(ABC, BaseModel):
    """
    Defines these necessary interfaces (all subclass should implement these methods):
    - Create a bucket.
    - List buckets.
    - List files in specified bucket (can with prefix).
    - Add / Get / Delete a file from specified bucket.

    Implements useful methods:
    - Store images for a document.
    - Store images for a report.
    """
    _warned_unsupported_method: set[str] = PrivateAttr(default_factory=set)

    def __aenter__(self):
        return self

    def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    @classmethod
    @abstractmethod
    def from_config(cls: Type[_Self], config: "Config") -> _Self:
        raise NotImplementedError(f"{cls.__name__}.from_config")

    # bucket operations begin
    @abstractmethod
    async def bucket_create(self, bucket: str, *, exist_ok: bool = False) -> bool:
        """Return True if actually create this bucket and False if bucket already exists."""
        raise NotImplementedError("bucket_create")

    @abstractmethod
    async def list_buckets(self) -> list[str]:
        raise NotImplementedError("list_buckets")

    @abstractmethod
    async def list_files(self, bucket: str, prefix: str = None) -> list[str]:
        raise NotImplementedError("list_files")

    # file operations begin
    @abstractmethod
    async def file_add(self, bucket: str, filename: str, content: bytes) -> None:
        raise NotImplementedError("file_add")

    @abstractmethod
    async def file_delete(self, bucket: str, filename: str, allow_not_exists: bool = True) -> None:
        raise NotImplementedError("file_delete")

    @abstractmethod
    async def file_get(self, bucket: str, filename: str) -> bytes:
        raise NotImplementedError("file_get")

    # unnecessary interfaces definition

    async def bucket_allow_anonymous_get(self, bucket: str) -> None:
        if "set_anonymous_get" not in self._warned_unsupported_method:
            self._warned_unsupported_method.add("set_anonymous_get")
            logger.warning(f"'{type(self).__name__}.allow_anonymous_get({bucket!r})' is not implemented"
                           f" and has no efforts.")

    # utils begin
    async def object_put(self, identify: BaseIdentifier, content: bytes | dict[str, bytes],
                         auto_create_bucket=False, set_allow_anonymous: bool = False) -> None:
        bucket = identify.bucket_name()
        obj = identify.object_name()
        if isinstance(content, bytes):
            try:
                await self.file_add(bucket, obj, content)
                return
            except StorageError as e:
                if not auto_create_bucket or e.reason != e.Reason.BUCKET_NOT_FOUND:
                    raise
            await self.object_init_bucket(identify, exist_ok=True, set_allow_anonymous=set_allow_anonymous)
            await self.file_add(bucket, obj, content)
            return

        if obj and not obj.endswith("/"):
            obj += "/"
        tasks = list(content.items())
        if not tasks:
            return
        first_task = tasks[0]
        try:
            await self.file_add(bucket, obj + first_task[0], first_task[1])
        except StorageError as e:
            if not auto_create_bucket or e.reason != e.Reason.BUCKET_NOT_FOUND:
                raise
            await self.object_init_bucket(identify, exist_ok=True, set_allow_anonymous=set_allow_anonymous)
            await self.file_add(bucket, obj + first_task[0], first_task[1])
        tasks = tasks[1:]
        if not tasks:
            return
        await asyncio.gather(*(self.file_add(bucket, obj + name, binary) for name, binary in tasks))

    async def object_get(self, identify: BaseIdentifier) -> bytes:
        bucket = identify.bucket_name()
        obj = identify.object_name()
        return await self.file_get(bucket, obj)

    async def object_init_bucket(self, identify: BaseIdentifier,
                                 exist_ok: bool = True, set_allow_anonymous: bool = False):
        bucket = identify.bucket_name()
        if await self.bucket_create(bucket, exist_ok=exist_ok) and set_allow_anonymous:
            await self.bucket_allow_anonymous_get(bucket)

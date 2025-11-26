"""Interface definition (compatible with AWS S3 OBS) and storage mapping definition for any files."""
__all__ = ["StorageError", "StorageOp", "BaseFileStorage"]

import asyncio
from abc import ABC, abstractmethod
from enum import Enum
from typing import Type, TypeVar, TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from deepinsight.config.config import Config
else:
    from pydantic import BaseModel as Config


_Self = TypeVar("_Self")


class StorageOp(str, Enum):
    CREATE = "create"
    DELETE = "delete"
    GET = "get"
    LIST = "list"


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
    @classmethod
    @abstractmethod
    def from_config(cls: Type[_Self], config: "Config") -> _Self:
        raise NotImplementedError(f"{cls.__name__}.from_config")

    # bucket operations begin
    @abstractmethod
    async def bucket_create(self, bucket: str, *, exist_ok: bool = False) -> None:
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
    async def file_delete(self, bucket: str, filename: str, allow_not_exists: bool = False) -> None:
        raise NotImplementedError("file_delete")

    @abstractmethod
    async def file_get(self, bucket: str, filename: str) -> bytes:
        raise NotImplementedError("file_get")

    # utils begin
    async def store_document_images(self, knowledge_base_id: int, document_id: str, images: dict[str, bytes]) -> None:
        await self.bucket_create(str(knowledge_base_id), exist_ok=True)
        if not images:
            return
        bucket = str(knowledge_base_id)
        upload_tasks = [
            self.file_add(bucket, f"{document_id}/{name}", content)
        for name, content in images.items()
        ]
        await asyncio.gather(*upload_tasks)

    async def store_chart(self, name: str, content: bytes) -> None:
        bucket = "charts"
        try:
            await self.file_add(bucket, name, content)
            return
        except StorageError as e:
            if e.reason != e.Reason.BUCKET_NOT_FOUND:
                raise
        await self.bucket_create(bucket, exist_ok=True)
        await self.file_add(bucket, name, content)

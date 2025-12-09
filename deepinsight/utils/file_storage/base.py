"""Interface definition (compatible with AWS S3 OBS) and storage mapping definition for any files."""
__all__ = ["StorageError", "StorageOp", "BaseFileStorage"]

import asyncio
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Type, TypeVar, TYPE_CHECKING

from pydantic import BaseModel, PrivateAttr

from deepinsight.config.file_storage_config import ObsMappingConfig

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
    keymap: ObsMappingConfig = ObsMappingConfig()
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
    async def document_images_init_bucket(self, knowledge_base_id: str, exist_ok: bool = True,
                                          set_allow_anonymous: bool = False) -> None:
        bucket = self.keymap.kb_doc_image.bucket.format(kb_id=knowledge_base_id)
        if await self.bucket_create(bucket, exist_ok=exist_ok) and set_allow_anonymous:
            await self.bucket_allow_anonymous_get(bucket)

    async def document_images_store(self, knowledge_base_id: str, document_id: str,
                                    images: dict[str, bytes]) -> dict[str, str]:
        """Store images and returns a mapping from origin image path to its stored path as {bucket}/{object}."""
        if not images:
            return {}
        bucket = self.keymap.kb_doc_image.bucket.format(kb_id=knowledge_base_id)
        obj_names = {
            name: self.keymap.kb_doc_image.object.format(kb_id=knowledge_base_id, doc_id=document_id, img_path=name)
            for name in images
        }
        upload_tasks = [self.file_add(bucket, obj_names[name], content) for name, content in images.items()]
        await asyncio.gather(*upload_tasks)
        return {k: f"{bucket}/{v}" for k, v in obj_names.items()}

    async def chart_store(self, name: str, content: bytes) -> None:
        bucket = self.keymap.report_image.bucket
        obj_name = self.keymap.report_image.object.format(img_path=name)
        try:
            await self.file_add(bucket, obj_name, content)
            return
        except StorageError as e:
            if e.reason != e.Reason.BUCKET_NOT_FOUND:
                raise
        await self.bucket_create(bucket, exist_ok=True)
        await self.file_add(bucket, obj_name, content)

    async def knowledge_file_init_bucket(self, knowledge_base_id: str, owner_type: str, owner_id: str,
                                         exist_ok: bool = True):
        bucket = self.keymap.kb_doc_binary.bucket.format(kb_id=knowledge_base_id, owner_type=owner_type,
                                                         owner_id=owner_id)
        await self.bucket_create(bucket, exist_ok=exist_ok)

    async def knowledge_file_get(self, knowledge_base_id: str,
                                 owner_type: str, owner_id: str,
                                 doc_id: str, doc_name: str) -> bytes:
        bucket, obj_name = self._knowledge_file_info(knowledge_base_id, owner_type, owner_id, doc_id, doc_name)
        return await self.file_get(bucket, obj_name)

    async def knowledge_file_put(self, knowledge_base_id: str,
                                 owner_type: str, owner_id: str,
                                 doc_id: str, doc_name: str, binary: bytes) -> None:
        bucket, obj_name = self._knowledge_file_info(knowledge_base_id, owner_type, owner_id, doc_id, doc_name)
        await self.file_add(bucket, obj_name, binary)

    def _knowledge_file_info(self, knowledge_base_id: str, owner_type: str, owner_id: str,
                                 doc_id: str, doc_name: str) -> tuple[str, str]:
        bucket_args = dict(kb_id=knowledge_base_id, owner_type=owner_type, owner_id=owner_id)
        object_args = dict(kb_id=knowledge_base_id, owner_type=owner_type, owner_id=owner_id,
                           doc_id=doc_id, doc_name=doc_name)
        bucket = self.keymap.kb_doc_binary.bucket.format_map(bucket_args)
        obj_name = self.keymap.kb_doc_binary.object.format_map(object_args)
        return bucket, obj_name

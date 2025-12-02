"""Configuration about how to store files referenced by Markdown text."""
import os
from enum import Enum
from typing import Annotated, Any, ClassVar, Type

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator, ValidationError
from pydantic_core import ErrorDetails, InitErrorDetails


class StorageType(str, Enum):
    LOCAL = "local"
    """Storage on local disk."""
    S3_OBS = "s3"
    """AWS S3 compatible OBS(Object Storage Service)."""


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigS3(_ConfigModel):
    """Configuration for AWS S3 compatible OBS(Object Storage Service) client."""
    endpoint: str
    ak: SecretStr
    sk: SecretStr


class ListenConfig(_ConfigModel):
    """How to start an HTTP server to handle get file request. Currently, HTTPS is unsupported."""
    attach: bool = True
    """Whether attach to deepinsight main service."""
    path_prefix: Annotated[str, Field(default_factory=lambda: ...)]
    """If `attach` is `True`, default is '/resources'. Otherwise, default is '/' to compatible with S3 OBS."""

    name: str = "DeepInsight file accessor"
    """Server progress name. Only take efforts when `attach` is `False`."""
    host: str = None
    """Server listen IP. Only take efforts when `attach` is `False`."""
    port: int = None
    """Server listen Port. Only take efforts when `attach` is `False`."""

    def model_post_init(self, context: Any, /) -> None:
        if self.path_prefix is ...:
            self.path_prefix = "/resources" if self.attach else "/"

class ConfigLocal(_ConfigModel):
    """Configuration for how to store files in disk."""

    root_dir: str | None = None
    """The base directory to store files.
    If it's `None`, DeepInsight workspace root (`workspace.work_root`) is used.
    """

    def actual_root_dir(self, workspace_root: str) -> str:
        return self.root_dir or workspace_root


class MappingItem(_ConfigModel):
    """Specify how to map a storage request to OBS bucket name and filename.

    `bucket` and `object` are in Python str.format() style. Available keys differs from every usage.
    """
    model_config = ConfigDict(frozen=True)
    bucket: str
    object: str


_MAPPING_AVAILABLE_KEYS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = dict(
    kb_doc_image=(("kb_id",), ("kb_id", "doc_id","img_path")),
    kb_doc_binary=(("kb_id", "owner_type", "owner_id"), ("kb_id", "owner_type", "owner_id", "doc_id", "doc_name")),
    report_image=((), ("img_path",))
)


class ObsMappingConfig(_ConfigModel):
    model_config = ConfigDict(frozen=True)

    kb_doc_image: MappingItem = MappingItem(bucket="rag_storage", object="{kb_id}/{doc_id}/{img_path}")
    kb_doc_binary: MappingItem = MappingItem(bucket="original_files", object="{owner_type}/{owner_id}/{doc_name}")
    report_image: MappingItem = MappingItem(bucket="charts", object="{img_path}")

    @model_validator(mode="after")
    def _check_mapping_keys(self):
        errors: list[tuple[str, tuple, str]] = []
        for rule_name, key_rules in _MAPPING_AVAILABLE_KEYS.items():
            mapping: MappingItem = getattr(self, rule_name)
            for field, keys in zip(("bucket", "object"), key_rules):
                try:
                    getattr(mapping, field).format(**{k: "" for k in keys})
                except KeyError as e:
                    key_msg = "', '".join(keys)
                    errors.append((f"Rule has a unsupported key {e}. Available: '{key_msg}'.",
                                   (rule_name, field), mapping.bucket))
                except ValueError as e:
                    errors.append((str(e), (rule_name, field), mapping.bucket))
        if errors:
            raise ValidationError.from_exception_data(
                type(self).__name__,
                [
                    InitErrorDetails(loc=loc, type="value_error", input=inputs, ctx=dict(error=msg))
                    for msg, loc, inputs in errors
                ]
            )
        return self


class FileStorageConfig(_ConfigModel):
    type: Annotated[
        StorageType,
        Field(default_factory=lambda: StorageType(os.getenv("STORAGE_TYPE") or StorageType.LOCAL))
    ]
    s3: ConfigS3 | None = None
    local: Annotated[ConfigLocal | None, Field(default_factory=ConfigLocal)]
    remote_access: bool | ListenConfig = False

    def model_post_init(self, context: Any, /) -> None:
        if self.remote_access is True:
            self.remote_access = ListenConfig()

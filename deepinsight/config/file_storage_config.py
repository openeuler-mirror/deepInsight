"""Configuration about how to store files referenced by Markdown text."""
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


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


class FileStorageConfig(_ConfigModel):
    type: StorageType = StorageType.LOCAL
    s3: ConfigS3 | None = None
    local: Annotated[ConfigLocal | None, Field(default_factory=ConfigLocal)]
    remote_access: bool | ListenConfig = False

    _REQUIRED_FIELD_MAP: ClassVar[dict[StorageType, str]] = {
        StorageType.LOCAL: "local",
        StorageType.S3_OBS: "s3"
    }

    def model_post_init(self, context: Any, /) -> None:
        if self.remote_access is True:
            self.remote_access = ListenConfig()

    @model_validator(mode="after")
    def _check_configs(self):
        required_config = self._REQUIRED_FIELD_MAP[self.type]
        if getattr(self, required_config) is None:
            raise ValueError(f"For storage type '{self.type}', config field '{required_config}' is required.")
        return self

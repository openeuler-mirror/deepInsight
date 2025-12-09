"""Init storage or get existing storage implementation."""
__all__ = ["get_storage_impl"]

from typing import TYPE_CHECKING

from deepinsight.config.file_storage_config import StorageType
from deepinsight.utils.file_storage.base import BaseFileStorage

if TYPE_CHECKING:
    from deepinsight.config.config import Config
else:
    from typing import Any as Config


_current: BaseFileStorage | None = None


def get_storage_impl(config: Config = None) -> BaseFileStorage:
    """Init storage or get existing storage implementation."""
    global _current
    if config is None:
        if not _current:
            raise RuntimeError("Deepinsight file storage subsystem not fully inited.")
        return _current
    if config.file_storage.type == StorageType.LOCAL:
        from deepinsight.utils.file_storage.local import LocalStorage
        _current = LocalStorage.from_config(config)
    elif config.file_storage.type == StorageType.S3_OBS:
        from deepinsight.utils.file_storage.s3_compatible import S3CompatibleObsClient
        _current = S3CompatibleObsClient.from_config(config)
    else:
        raise NotImplementedError(f"Unsupported storage type {config.file_storage.type}")
    return _current

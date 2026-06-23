"""Storage subpackage: dual-adapter repository pattern (Delta Lake / Snowflake)."""

from sprintshield.storage.base import (
    SCHEMA_REGISTRY,
    StorageAdapter,
    StorageError,
    TimeTravelResult,
)
from sprintshield.storage.factory import get_storage_adapter, list_backends

__all__ = [
    "StorageAdapter",
    "StorageError",
    "TimeTravelResult",
    "SCHEMA_REGISTRY",
    "get_storage_adapter",
    "list_backends",
]

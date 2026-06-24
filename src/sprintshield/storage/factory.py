"""Storage factory - selects the backend from configuration with zero friction.

``STORAGE_BACKEND=deltalake`` (default) gives a fully local ACID lakehouse with
no external services. ``STORAGE_BACKEND=snowflake`` switches to the cloud
warehouse. If Snowflake is requested but credentials are absent, the factory
transparently falls back to Delta Lake so the pipeline never hard-fails.
"""

from __future__ import annotations

import os
from typing import Any

from sprintshield.config import SprintShieldConfig, load_config
from sprintshield.storage.base import StorageAdapter
from sprintshield.storage.delta_adapter import DeltaLakeAdapter

_BACKENDS = ("deltalake", "snowflake")


def list_backends() -> list[str]:
    """Return the adapter identifiers this package understands."""
    return list(_BACKENDS)


def _snowflake_credentials_present() -> bool:
    required = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD")
    return all(os.environ.get(name) for name in required)


def get_storage_adapter(
    config: SprintShieldConfig | None = None,
    backend: str | None = None,
    **kwargs: Any,
) -> StorageAdapter:
    """Build the storage adapter named by ``backend`` or the configuration.

    Parameters
    ----------
    config:
        Pre-loaded configuration. Loaded from disk when omitted.
    backend:
        Explicit override (``deltalake`` or ``snowflake``). Falls back to
        ``STORAGE_BACKEND`` then the configuration default.
    """
    cfg = config or load_config()
    selected = (backend or os.environ.get("STORAGE_BACKEND") or cfg.storage_backend).strip().lower()

    if selected not in _BACKENDS:
        raise ValueError(f"Unknown STORAGE_BACKEND={selected!r}; expected one of {_BACKENDS}")

    if selected == "snowflake":
        if not _snowflake_credentials_present():
            # Graceful degradation: keep the pipeline running locally.
            return DeltaLakeAdapter(cfg.storage.deltalake, root_path=kwargs.pop("root_path", None))
        from sprintshield.storage.snowflake_adapter import SnowflakeAdapter

        return SnowflakeAdapter(cfg.storage.snowflake, credentials=kwargs.pop("credentials", None))

    return DeltaLakeAdapter(cfg.storage.deltalake, root_path=kwargs.pop("root_path", None))

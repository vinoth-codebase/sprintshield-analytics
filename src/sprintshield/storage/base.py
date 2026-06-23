"""Abstract storage contract shared by every SprintShield backend adapter.

The repository pattern here lets the analytics engine, reporting exporters and
dashboard read identical pandas DataFrames whether the underlying engine is a
local Delta Lake lakehouse or the Snowflake cloud warehouse. Swapping backends
is a configuration change, never a code change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import pandas as pd

# --------------------------------------------------------------------------- #
# Canonical schemas
# --------------------------------------------------------------------------- #
# Ordered column contract per logical table. Adapters validate and reorder
# incoming frames against these, so downstream code can rely on a stable shape.

SPRINT_SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "tower_id",
    "sprint_id",
    "sprint_name",
    "start_date",
    "end_date",
    "committed_points",
    "completed_points",
    "scope_change_points",
    "gross_hours",
    "planned_feature_hours",
    "unplanned_support_hours",
    "incident_count",
    "support_tax",
    "flow_efficiency",
    "recorded_at",
)

SUPPORT_TICKET_COLUMNS: tuple[str, ...] = (
    "ticket_id",
    "tower_id",
    "sprint_id",
    "asset",
    "summary",
    "issue_type",
    "priority",
    "status",
    "created_at",
    "resolved_at",
    "resolution_hours",
    "story_points",
    "is_planned",
    "is_release_regression",
    "labels",
)

SCHEMA_REGISTRY: Mapping[str, tuple[str, ...]] = {
    "sprint_snapshots": SPRINT_SNAPSHOT_COLUMNS,
    "support_tickets": SUPPORT_TICKET_COLUMNS,
}


class StorageError(RuntimeError):
    """Raised when a storage adapter cannot satisfy a request."""


@dataclass(frozen=True)
class TimeTravelResult:
    """A historical snapshot of a table at a specific Delta version."""

    version: int
    timestamp: str
    table: str
    frame: pd.DataFrame

    def __len__(self) -> int:
        return int(len(self.frame))


class StorageAdapter(ABC):
    """Backend-agnostic persistence contract.

    Concrete adapters must implement every abstract method. ``table_name``
    arguments always refer to the *logical* table (``sprint_snapshots`` or
    ``support_tickets``); each adapter maps that to its own physical location.
    """

    #: Human-readable identifier used by the factory and CLI output.
    backend: str = "abstract"

    # -- writes -------------------------------------------------------------- #
    @abstractmethod
    def save_sprint_snapshots(self, frame: pd.DataFrame, mode: str = "append") -> int:
        """Persist sprint snapshot rows. Returns the number of rows written."""

    @abstractmethod
    def save_support_tickets(self, frame: pd.DataFrame, mode: str = "append") -> int:
        """Persist support ticket rows. Returns the number of rows written."""

    # -- reads --------------------------------------------------------------- #
    @abstractmethod
    def get_sprint_history(
        self,
        tower_id: str | None = None,
        sprint_ids: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        """Return sprint snapshots, optionally filtered by tower and/or sprints."""

    @abstractmethod
    def get_support_tickets(
        self,
        tower_id: str | None = None,
        sprint_ids: Sequence[str] | None = None,
        resolved_only: bool = False,
    ) -> pd.DataFrame:
        """Return support tickets, optionally filtered by tower and/or sprints."""

    # -- analytics ----------------------------------------------------------- #
    @abstractmethod
    def query_tower_metrics(self, tower_id: str | None = None) -> pd.DataFrame:
        """Aggregate per-tower operational metrics from stored telemetry."""

    # -- housekeeping -------------------------------------------------------- #
    @abstractmethod
    def table_exists(self, table: str) -> bool:
        """Whether the logical table has any persisted data."""

    @abstractmethod
    def list_tables(self) -> list[str]:
        """Logical tables known to this adapter."""

    def close(self) -> None:
        """Release any held resources. Safe to call multiple times."""

    # -- shared validation --------------------------------------------------- #
    @staticmethod
    def validate_frame(frame: pd.DataFrame, table: str) -> pd.DataFrame:
        """Check and reorder a frame against the canonical schema.

        Missing optional columns are added as nulls; columns outside the schema
        are dropped so the physical store stays stable across releases.
        """
        if table not in SCHEMA_REGISTRY:
            raise StorageError(f"Unknown logical table: {table!r}")
        expected = SCHEMA_REGISTRY[table]
        if frame is None or not isinstance(frame, pd.DataFrame):
            raise StorageError(f"Expected a pandas DataFrame for {table}, got {type(frame)!r}")

        out = frame.copy()
        for column in expected:
            if column not in out.columns:
                out[column] = pd.NA
        extra = [c for c in out.columns if c not in expected]
        if extra:
            out = out.drop(columns=extra)
        return out[list(expected)]

    def __enter__(self) -> "StorageAdapter":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def empty_frame(table: str) -> pd.DataFrame:
    """Return an empty frame carrying the canonical columns for ``table``."""
    if table not in SCHEMA_REGISTRY:
        raise StorageError(f"Unknown logical table: {table!r}")
    return pd.DataFrame({column: pd.Series(dtype="object") for column in SCHEMA_REGISTRY[table]})

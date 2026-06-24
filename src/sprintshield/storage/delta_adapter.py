"""Delta Lake storage adapter - the zero-setup default backend.

Uses the Rust-backed ``deltalake`` library to persist telemetry as partitioned
Parquet with full ACID guarantees. Every write creates a new table version, so
historical sprint audits can be replayed with time-travel queries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import pandas as pd
import pyarrow as pa

from sprintshield.config import DeltaLakeConfig
from sprintshield.storage.base import (
    StorageAdapter,
    StorageError,
    TimeTravelResult,
    empty_frame,
)

try:  # pragma: no cover - import guard mirrors the optional dependency
    from deltalake import DeltaTable, write_deltalake

    _DELTA_AVAILABLE = True
except ImportError:  # pragma: no cover
    DeltaTable = None  # type: ignore[assignment]
    write_deltalake = None  # type: ignore[assignment]
    _DELTA_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Explicit Arrow schemas keep the physical store stable across releases and
# prevent schema-merge surprises when appending frames built from scratch.
# --------------------------------------------------------------------------- #
_SPRINT_SNAPSHOT_ARROW = pa.schema(
    [
        pa.field("tower_id", pa.string()),
        pa.field("sprint_id", pa.string()),
        pa.field("sprint_name", pa.string()),
        pa.field("start_date", pa.string()),
        pa.field("end_date", pa.string()),
        pa.field("committed_points", pa.float64()),
        pa.field("completed_points", pa.float64()),
        pa.field("scope_change_points", pa.float64()),
        pa.field("gross_hours", pa.float64()),
        pa.field("planned_feature_hours", pa.float64()),
        pa.field("unplanned_support_hours", pa.float64()),
        pa.field("incident_count", pa.float64()),
        pa.field("support_tax", pa.float64()),
        pa.field("flow_efficiency", pa.float64()),
        pa.field("recorded_at", pa.string()),
    ]
)

_SUPPORT_TICKET_ARROW = pa.schema(
    [
        pa.field("ticket_id", pa.string()),
        pa.field("tower_id", pa.string()),
        pa.field("sprint_id", pa.string()),
        pa.field("asset", pa.string()),
        pa.field("summary", pa.string()),
        pa.field("issue_type", pa.string()),
        pa.field("priority", pa.string()),
        pa.field("status", pa.string()),
        pa.field("created_at", pa.string()),
        pa.field("resolved_at", pa.string()),
        pa.field("resolution_hours", pa.float64()),
        pa.field("story_points", pa.float64()),
        pa.field("is_planned", pa.bool_()),
        pa.field("is_release_regression", pa.bool_()),
        pa.field("labels", pa.string()),
    ]
)

_ARROW_SCHEMAS: dict[str, pa.Schema] = {
    "sprint_snapshots": _SPRINT_SNAPSHOT_ARROW,
    "support_tickets": _SUPPORT_TICKET_ARROW,
}

_STRING_COLUMNS = {
    "sprint_snapshots": (
        "tower_id",
        "sprint_id",
        "sprint_name",
        "start_date",
        "end_date",
        "recorded_at",
    ),
    "support_tickets": (
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
        "labels",
    ),
}


def _to_arrow(frame: pd.DataFrame, table: str) -> pa.Table:
    """Coerce a validated frame into the fixed Arrow schema for ``table``."""
    schema = _ARROW_SCHEMAS[table]
    data: dict[str, Any] = {}
    for field in schema:
        name = field.name
        column = frame[name]
        if pa.types.is_string(field.type):
            data[name] = [None if v is None or pd.isna(v) else str(v) for v in column]
        elif pa.types.is_boolean(field.type):
            data[name] = [bool(v) if not pd.isna(v) else False for v in column]
        else:
            data[name] = pd.to_numeric(column, errors="coerce").astype("float64")
    return pa.table(data, schema=schema)


class DeltaLakeAdapter(StorageAdapter):
    """Local ACID lakehouse adapter backed by Delta Lake."""

    backend = "deltalake"

    def __init__(self, config: DeltaLakeConfig, root_path: str | Path | None = None) -> None:
        if not _DELTA_AVAILABLE:  # pragma: no cover
            raise StorageError(
                "The 'deltalake' package is required for the Delta Lake adapter. "
                "Install it with `pip install deltalake`."
            )
        self.config = config
        self.root = Path(root_path) if root_path is not None else Path(config.root_path)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- path helpers -------------------------------------------------------- #
    def _table_path(self, table: str) -> Path:
        if table == "sprint_snapshots":
            return self.root / self.config.snapshot_table
        if table == "support_tickets":
            return self.root / self.config.ticket_table
        raise StorageError(f"Unknown logical table: {table!r}")

    def _partition_by(self, table: str) -> list[str]:
        if table == "sprint_snapshots":
            return list(self.config.snapshot_partition_by)
        if table == "support_tickets":
            return list(self.config.ticket_partition_by)
        raise StorageError(f"Unknown logical table: {table!r}")

    # -- writes -------------------------------------------------------------- #
    def _write(self, table: str, frame: pd.DataFrame, mode: str) -> int:
        validated = self.validate_frame(frame, table)
        if validated.empty:
            return 0
        arrow_table = _to_arrow(validated, table)
        write_deltalake(
            str(self._table_path(table)),
            arrow_table,
            mode=mode,
            partition_by=self._partition_by(table),
            schema_mode="merge",
        )
        return int(arrow_table.num_rows)

    def save_sprint_snapshots(self, frame: pd.DataFrame, mode: str = "append") -> int:
        return self._write("sprint_snapshots", frame, mode)

    def save_support_tickets(self, frame: pd.DataFrame, mode: str = "append") -> int:
        return self._write("support_tickets", frame, mode)

    # -- reads --------------------------------------------------------------- #
    def _read(self, table: str, version: int | None = None) -> pd.DataFrame:
        path = self._table_path(table)
        if not path.exists():
            return empty_frame(table)
        try:
            dt = DeltaTable(str(path), version=version) if version is not None else DeltaTable(str(path))
            frame = dt.to_pandas()
        except Exception as exc:  # pragma: no cover - defensive
            raise StorageError(f"Failed to read Delta table {table!r}: {exc}") from exc
        return self.validate_frame(frame, table)

    def get_sprint_history(
        self,
        tower_id: str | None = None,
        sprint_ids: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        frame = self._read("sprint_snapshots")
        return _filter(frame, tower_id, sprint_ids)

    def get_support_tickets(
        self,
        tower_id: str | None = None,
        sprint_ids: Sequence[str] | None = None,
        resolved_only: bool = False,
    ) -> pd.DataFrame:
        frame = self._read("support_tickets")
        frame = _filter(frame, tower_id, sprint_ids)
        if resolved_only and not frame.empty:
            frame = frame[frame["resolution_hours"].notna()]
        return frame

    # -- time travel --------------------------------------------------------- #
    def table_versions(self, table: str) -> list[int]:
        """Return the ascending list of Delta versions available for ``table``."""
        path = self._table_path(table)
        if not path.exists():
            return []
        dt = DeltaTable(str(path))
        history = dt.history()
        return sorted(int(entry["version"]) for entry in history)

    def time_travel(self, table: str, version: int | None = None) -> TimeTravelResult:
        """Return the table as it existed at ``version`` (latest when omitted)."""
        path = self._table_path(table)
        if not path.exists():
            return TimeTravelResult(version=-1, timestamp="", table=table, frame=empty_frame(table))
        dt = DeltaTable(str(path)) if version is None else DeltaTable(str(path), version=version)
        resolved_version = int(dt.version())
        metadata = dt.metadata()
        timestamp = ""
        try:
            history = dt.history()
            if history:
                match = [h for h in history if int(h["version"]) == resolved_version]
                if match:
                    timestamp = str(match[0].get("timestamp", ""))
        except Exception:  # pragma: no cover - history is best-effort
            timestamp = ""
        frame = self.validate_frame(dt.to_pandas(), table)
        _ = metadata
        return TimeTravelResult(
            version=resolved_version, timestamp=timestamp, table=table, frame=frame
        )

    def get_sprint_history_at_version(self, version: int) -> pd.DataFrame:
        """Audit helper: sprint snapshots exactly as recorded at ``version``."""
        return self.time_travel("sprint_snapshots", version).frame

    def compare_versions(
        self,
        table: str,
        version_a: int,
        version_b: int,
    ) -> pd.DataFrame:
        """Diff row counts between two historical versions (audit trail)."""
        frame_a = self.time_travel(table, version_a).frame
        frame_b = self.time_travel(table, version_b).frame
        return pd.DataFrame(
            {
                "metric": ["row_count"],
                "version_a": [len(frame_a)],
                "version_b": [len(frame_b)],
                "delta": [len(frame_b) - len(frame_a)],
            }
        )

    # -- analytics ----------------------------------------------------------- #
    def query_tower_metrics(self, tower_id: str | None = None) -> pd.DataFrame:
        snapshots = self.get_sprint_history(tower_id=tower_id)
        if snapshots.empty:
            return pd.DataFrame(
                columns=[
                    "tower_id",
                    "sprints",
                    "avg_support_tax",
                    "total_unplanned_hours",
                    "avg_incidents",
                    "avg_velocity",
                    "delivery_predictability",
                ]
            )
        tickets = self.get_support_tickets(tower_id=tower_id, resolved_only=True)
        return _aggregate_tower_metrics(snapshots, tickets)

    # -- housekeeping -------------------------------------------------------- #
    def table_exists(self, table: str) -> bool:
        return self._table_path(table).exists()

    def list_tables(self) -> list[str]:
        return [t for t in ("sprint_snapshots", "support_tickets") if self.table_exists(t)]


# --------------------------------------------------------------------------- #
# Shared helpers (module level so the Snowflake adapter can reuse them)
# --------------------------------------------------------------------------- #
def _filter(
    frame: pd.DataFrame,
    tower_id: str | None,
    sprint_ids: Sequence[str] | None,
) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame
    if tower_id is not None:
        out = out[out["tower_id"] == tower_id]
    if sprint_ids is not None:
        wanted = set(sprint_ids)
        out = out[out["sprint_id"].isin(wanted)]
    return out.reset_index(drop=True)


def _aggregate_tower_metrics(snapshots: pd.DataFrame, tickets: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for tower, group in snapshots.groupby("tower_id", dropna=False):
        support_tax = pd.to_numeric(group["support_tax"], errors="coerce").dropna()
        unplanned = pd.to_numeric(group["unplanned_support_hours"], errors="coerce").fillna(0.0)
        incidents = pd.to_numeric(group["incident_count"], errors="coerce").fillna(0.0)
        velocity = pd.to_numeric(group["completed_points"], errors="coerce").fillna(0.0)

        if len(velocity) > 1 and float(velocity.mean()) > 0:
            predictability = float(1.0 - (velocity.std(ddof=0) / velocity.mean()))
        else:
            predictability = 1.0

        mttr = float("nan")
        if not tickets.empty and "resolution_hours" in tickets.columns:
            tower_tickets = tickets[tickets["tower_id"] == tower]
            hours = pd.to_numeric(tower_tickets["resolution_hours"], errors="coerce").dropna()
            if not hours.empty:
                mttr = float(hours.mean())

        rows.append(
            {
                "tower_id": tower,
                "sprints": int(group["sprint_id"].nunique()),
                "avg_support_tax": float(support_tax.mean()) if not support_tax.empty else 0.0,
                "total_unplanned_hours": float(unplanned.sum()),
                "avg_incidents": float(incidents.mean()) if not incidents.empty else 0.0,
                "avg_velocity": float(velocity.mean()) if not velocity.empty else 0.0,
                "mttr_hours": mttr,
                "delivery_predictability": predictability,
            }
        )
    return pd.DataFrame(rows)

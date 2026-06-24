"""Snowflake cloud warehouse adapter.

Talks to enterprise tables under ``SPRINTSHIELD_DB.TELEMETRY`` using
``snowflake-connector-python`` (parameter binding, connection reuse and query
retries). ``snowflake-sqlalchemy`` is used for the SQLAlchemy engine path when
available. The connector is imported lazily so the package installs and imports
cleanly on machines that only ever use the Delta Lake backend.
"""

from __future__ import annotations

import time
from typing import Any, Sequence

import pandas as pd

from sprintshield.config import SnowflakeConfig
from sprintshield.storage.base import StorageAdapter, StorageError, empty_frame
from sprintshield.storage.delta_adapter import _aggregate_tower_metrics, _filter


class SnowflakeAdapter(StorageAdapter):
    """Cloud warehouse adapter for the Snowflake backend."""

    backend = "snowflake"

    def __init__(
        self,
        config: SnowflakeConfig,
        credentials: dict[str, Any] | None = None,
    ) -> None:
        self.config = config
        self._credentials = credentials or {}
        self._connection: Any = None
        self._engine: Any = None

    # -- connection management ---------------------------------------------- #
    def _connect_params(self) -> dict[str, Any]:
        import os

        params: dict[str, Any] = {
            "account": self._credentials.get("account") or os.environ.get("SNOWFLAKE_ACCOUNT"),
            "user": self._credentials.get("user") or os.environ.get("SNOWFLAKE_USER"),
            "password": self._credentials.get("password") or os.environ.get("SNOWFLAKE_PASSWORD"),
            "warehouse": self._credentials.get("warehouse")
            or os.environ.get("SNOWFLAKE_WAREHOUSE", self.config.warehouse),
            "database": self._credentials.get("database")
            or os.environ.get("SNOWFLAKE_DATABASE", self.config.database),
            "schema": self._credentials.get("schema")
            or os.environ.get("SNOWFLAKE_SCHEMA", self.config.schema),
            "role": self._credentials.get("role") or os.environ.get("SNOWFLAKE_ROLE", self.config.role),
            "login_timeout": self.config.connect_timeout,
        }
        return {k: v for k, v in params.items() if v}

    @property
    def connection(self) -> Any:
        """Lazily establish (and cache) the Snowflake connection."""
        if self._connection is None:
            try:
                import snowflake.connector  # noqa: F401
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise StorageError(
                    "snowflake-connector-python is required for the Snowflake adapter. "
                    "Install the extra with `pip install sprintshield-analytics[snowflake]`."
                ) from exc
            import snowflake.connector

            try:
                self._connection = snowflake.connector.connect(**self._connect_params())
            except Exception as exc:  # pragma: no cover - network path
                raise StorageError(f"Snowflake connection failed: {exc}") from exc
        return self._connection

    @property
    def engine(self) -> Any:  # pragma: no cover - SQLAlchemy path
        """SQLAlchemy engine built on the shared connection parameters."""
        if self._engine is None:
            try:
                from snowflake.sqlalchemy import URL  # noqa: F401
                from sqlalchemy import create_engine
            except ImportError as exc:
                raise StorageError(
                    "snowflake-sqlalchemy and sqlalchemy are required for the engine path."
                ) from exc
            from snowflake.sqlalchemy import URL
            from sqlalchemy import create_engine

            url = URL(**self._connect_params())
            self._engine = create_engine(url, pool_pre_ping=True, pool_recycle=1800)
        return self._engine

    def _fqn(self, table: str) -> str:
        physical = (
            self.config.snapshot_table if table == "sprint_snapshots" else self.config.ticket_table
        )
        return f"{self.config.database}.{self.config.schema}.{physical}"

    def _run_with_retry(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple]:
        """Execute SQL with exponential backoff retries on transient failures."""
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries):
            try:
                cursor = self.connection.cursor()
                try:
                    cursor.execute(sql, params or [])
                    if cursor.description is not None:
                        return cursor.fetchall()
                    return []
                finally:
                    cursor.close()
            except Exception as exc:  # pragma: no cover - network path
                last_error = exc
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_backoff_seconds * (2**attempt))
        raise StorageError(f"Snowflake query failed after retries: {last_error}")

    # -- writes -------------------------------------------------------------- #
    def _write(self, table: str, frame: pd.DataFrame, mode: str) -> int:
        validated = self.validate_frame(frame, table)
        if validated.empty:
            return 0
        try:  # pragma: no cover - network path
            from snowflake.connector.pandas_tools import write_pandas

            overwrite = mode == "overwrite"
            success, _chunks, rows, _output = write_pandas(
                self.connection,
                validated,
                table_name=self._fqn(table).split(".")[-1],
                database=self.config.database,
                schema=self.config.schema,
                overwrite=overwrite,
                auto_create_table=True,
                quote_identifiers=False,
            )
            if not success:
                raise StorageError(f"Snowflake write_pandas reported failure for {table}")
            return int(rows)
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"Failed to write to Snowflake table {table}: {exc}") from exc

    def save_sprint_snapshots(self, frame: pd.DataFrame, mode: str = "append") -> int:
        return self._write("sprint_snapshots", frame, mode)

    def save_support_tickets(self, frame: pd.DataFrame, mode: str = "append") -> int:
        return self._write("support_tickets", frame, mode)

    # -- reads --------------------------------------------------------------- #
    def _read(self, table: str) -> pd.DataFrame:
        try:  # pragma: no cover - network path
            columns = list(self.validate_frame(empty_frame(table), table).columns)
            sql = f"SELECT {', '.join(columns)} FROM {self._fqn(table)}"
            rows = self._run_with_retry(sql)
            frame = pd.DataFrame(rows, columns=columns)
            return self.validate_frame(frame, table)
        except StorageError:
            return empty_frame(table)

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

    def query_tower_metrics(self, tower_id: str | None = None) -> pd.DataFrame:
        snapshots = self.get_sprint_history(tower_id=tower_id)
        if snapshots.empty:
            return empty_frame("sprint_snapshots").iloc[0:0].assign(
                sprints=pd.Series(dtype="int64"),
                avg_support_tax=pd.Series(dtype="float64"),
                total_unplanned_hours=pd.Series(dtype="float64"),
                avg_incidents=pd.Series(dtype="float64"),
                avg_velocity=pd.Series(dtype="float64"),
                mttr_hours=pd.Series(dtype="float64"),
                delivery_predictability=pd.Series(dtype="float64"),
            )
        tickets = self.get_support_tickets(tower_id=tower_id, resolved_only=True)
        return _aggregate_tower_metrics(snapshots, tickets)

    # -- housekeeping -------------------------------------------------------- #
    def table_exists(self, table: str) -> bool:
        try:  # pragma: no cover - network path
            rows = self._run_with_retry(
                f"SELECT COUNT(*) FROM {self._fqn(table)}",
            )
            return bool(rows) and int(rows[0][0]) > 0
        except StorageError:
            return False

    def list_tables(self) -> list[str]:
        return [t for t in ("sprint_snapshots", "support_tickets") if self.table_exists(t)]

    def close(self) -> None:
        if self._connection is not None:  # pragma: no cover - network path
            try:
                self._connection.close()
            finally:
                self._connection = None
        if self._engine is not None:  # pragma: no cover - network path
            try:
                self._engine.dispose()
            finally:
                self._engine = None

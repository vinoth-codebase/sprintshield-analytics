"""Tests for the dual-adapter storage engine (Delta Lake, Snowflake, factory)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from sprintshield.config import SprintShieldConfig
from sprintshield.storage.base import (
    SCHEMA_REGISTRY,
    SPRINT_SNAPSHOT_COLUMNS,
    SUPPORT_TICKET_COLUMNS,
    StorageAdapter,
    StorageError,
    empty_frame,
)
from sprintshield.storage.delta_adapter import DeltaLakeAdapter
from sprintshield.storage.factory import get_storage_adapter, list_backends


# --------------------------------------------------------------------------- #
# Contract / validation
# --------------------------------------------------------------------------- #
def test_schema_registry_covers_both_tables():
    assert set(SCHEMA_REGISTRY) == {"sprint_snapshots", "support_tickets"}
    assert SCHEMA_REGISTRY["sprint_snapshots"] == SPRINT_SNAPSHOT_COLUMNS
    assert SCHEMA_REGISTRY["support_tickets"] == SUPPORT_TICKET_COLUMNS


def test_validate_frame_fills_missing_and_drops_extra():
    frame = pd.DataFrame({"tower_id": ["DE"], "made_up": [1]})
    out = StorageAdapter.validate_frame(frame, "sprint_snapshots")
    assert list(out.columns) == list(SPRINT_SNAPSHOT_COLUMNS)
    assert "made_up" not in out.columns
    assert out["sprint_id"].isna().all()


def test_validate_frame_rejects_unknown_table():
    with pytest.raises(StorageError):
        StorageAdapter.validate_frame(pd.DataFrame(), "nope")


def test_validate_frame_rejects_non_frame():
    with pytest.raises(StorageError):
        StorageAdapter.validate_frame([1, 2, 3], "sprint_snapshots")  # type: ignore[arg-type]


def test_empty_frame_has_canonical_columns():
    frame = empty_frame("support_tickets")
    assert list(frame.columns) == list(SUPPORT_TICKET_COLUMNS)
    assert frame.empty


def test_empty_frame_rejects_unknown_table():
    with pytest.raises(StorageError):
        empty_frame("nope")


def test_storage_adapter_is_abstract():
    with pytest.raises(TypeError):
        StorageAdapter()  # type: ignore[abstract]


# --------------------------------------------------------------------------- #
# Delta Lake adapter
# --------------------------------------------------------------------------- #
def test_save_and_read_round_trip(populated_adapter: DeltaLakeAdapter, snapshots, tickets):
    read_snaps = populated_adapter.get_sprint_history()
    read_ticks = populated_adapter.get_support_tickets()
    assert len(read_snaps) == len(snapshots)
    assert len(read_ticks) == len(tickets)
    assert set(read_snaps.columns) == set(SPRINT_SNAPSHOT_COLUMNS)


def test_tables_are_partitioned_on_disk(delta_adapter, snapshots, tickets, delta_root):
    delta_adapter.save_sprint_snapshots(snapshots, mode="overwrite")
    delta_adapter.save_support_tickets(tickets, mode="overwrite")
    # Hive-style partitioning: tower_id=.../sprint_id=...
    names = [p.name for p in (delta_root / "sprint_snapshots").rglob("*")]
    assert any(n.startswith("tower_id=") for n in names), "expected tower_id partition directories"
    assert any(n.startswith("sprint_id=") for n in names), "expected sprint_id partition directories"
    # And the same partitioning for the ticket table.
    ticket_names = [p.name for p in (delta_root / "support_tickets").rglob("*")]
    assert any(n.startswith("tower_id=") for n in ticket_names)


def test_get_sprint_history_filters(populated_adapter, snapshots):
    by_tower = populated_adapter.get_sprint_history(tower_id="DE")
    assert set(by_tower["tower_id"]) == {"DE"}

    first_sprint = sorted(snapshots["sprint_id"].unique())[0]
    by_sprint = populated_adapter.get_sprint_history(sprint_ids=[first_sprint])
    assert set(by_sprint["sprint_id"]) == {first_sprint}

    both = populated_adapter.get_sprint_history(tower_id="DE", sprint_ids=[first_sprint])
    assert len(both) == 1


def test_get_support_tickets_resolved_only(populated_adapter, tickets):
    resolved = populated_adapter.get_support_tickets(resolved_only=True)
    assert resolved["resolution_hours"].notna().all()
    assert len(resolved) <= len(tickets)


def test_get_support_tickets_filters(populated_adapter):
    filtered = populated_adapter.get_support_tickets(tower_id="DS")
    assert set(filtered["tower_id"]) == {"DS"}


def test_read_missing_table_returns_empty(delta_adapter):
    assert delta_adapter.get_sprint_history().empty
    assert delta_adapter.get_support_tickets().empty
    assert delta_adapter.table_exists("sprint_snapshots") is False
    assert delta_adapter.list_tables() == []


def test_list_tables_after_write(populated_adapter):
    assert set(populated_adapter.list_tables()) == {"sprint_snapshots", "support_tickets"}
    assert populated_adapter.table_exists("sprint_snapshots") is True


def test_append_mode_accumulates_rows(delta_adapter, snapshots):
    delta_adapter.save_sprint_snapshots(snapshots, mode="overwrite")
    n_first = len(delta_adapter.get_sprint_history())
    delta_adapter.save_sprint_snapshots(snapshots, mode="append")
    assert len(delta_adapter.get_sprint_history()) == n_first * 2


def test_overwrite_mode_replaces_rows(delta_adapter, snapshots):
    delta_adapter.save_sprint_snapshots(snapshots, mode="overwrite")
    delta_adapter.save_sprint_snapshots(snapshots, mode="overwrite")
    assert len(delta_adapter.get_sprint_history()) == len(snapshots)


def test_writing_empty_frame_is_a_noop(delta_adapter):
    assert delta_adapter.save_sprint_snapshots(empty_frame("sprint_snapshots")) == 0
    assert not delta_adapter.table_exists("sprint_snapshots")


def test_unknown_table_path_raises(delta_adapter):
    with pytest.raises(StorageError):
        delta_adapter._table_path("mystery")
    with pytest.raises(StorageError):
        delta_adapter._partition_by("mystery")


# --------------------------------------------------------------------------- #
# Time travel
# --------------------------------------------------------------------------- #
def test_time_travel_versions_and_history(delta_adapter, snapshots):
    delta_adapter.save_sprint_snapshots(snapshots, mode="overwrite")
    delta_adapter.save_sprint_snapshots(snapshots, mode="append")
    versions = delta_adapter.table_versions("sprint_snapshots")
    assert len(versions) >= 2
    assert versions == sorted(versions)


def test_time_travel_returns_historical_row_counts(delta_adapter, snapshots):
    delta_adapter.save_sprint_snapshots(snapshots, mode="overwrite")
    delta_adapter.save_sprint_snapshots(snapshots, mode="append")
    versions = delta_adapter.table_versions("sprint_snapshots")

    first = delta_adapter.time_travel("sprint_snapshots", versions[0])
    latest = delta_adapter.time_travel("sprint_snapshots")
    assert len(first) == len(snapshots)
    assert len(latest) == len(snapshots) * 2
    assert latest.version >= first.version
    assert isinstance(latest.timestamp, str)


def test_get_sprint_history_at_version(delta_adapter, snapshots):
    delta_adapter.save_sprint_snapshots(snapshots, mode="overwrite")
    delta_adapter.save_sprint_snapshots(snapshots, mode="append")
    version = delta_adapter.table_versions("sprint_snapshots")[0]
    historical = delta_adapter.get_sprint_history_at_version(version)
    assert len(historical) == len(snapshots)


def test_compare_versions_audit(delta_adapter, snapshots):
    delta_adapter.save_sprint_snapshots(snapshots, mode="overwrite")
    delta_adapter.save_sprint_snapshots(snapshots, mode="append")
    versions = delta_adapter.table_versions("sprint_snapshots")
    diff = delta_adapter.compare_versions("sprint_snapshots", versions[0], versions[-1])
    assert diff.loc[0, "delta"] == len(snapshots)


def test_time_travel_on_missing_table(delta_adapter):
    result = delta_adapter.time_travel("sprint_snapshots")
    assert result.version == -1
    assert len(result) == 0


# --------------------------------------------------------------------------- #
# Aggregations
# --------------------------------------------------------------------------- #
def test_query_tower_metrics(populated_adapter):
    metrics = populated_adapter.query_tower_metrics()
    assert not metrics.empty
    assert {"tower_id", "avg_support_tax", "avg_incidents", "mttr_hours"} <= set(metrics.columns)
    assert len(metrics) == 4


def test_query_tower_metrics_single_tower(populated_adapter):
    metrics = populated_adapter.query_tower_metrics(tower_id="DA")
    assert len(metrics) == 1
    assert metrics.loc[0, "tower_id"] == "DA"


def test_query_tower_metrics_empty(delta_adapter):
    metrics = delta_adapter.query_tower_metrics()
    assert metrics.empty


def test_adapter_context_manager(populated_adapter):
    with populated_adapter as adapter:
        assert adapter.backend == "deltalake"


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def test_list_backends():
    assert set(list_backends()) == {"deltalake", "snowflake"}


def test_factory_defaults_to_delta(config: SprintShieldConfig, tmp_path):
    adapter = get_storage_adapter(config, root_path=tmp_path / "d")
    assert isinstance(adapter, DeltaLakeAdapter)
    assert adapter.backend == "deltalake"


def test_factory_explicit_backend_argument(config: SprintShieldConfig, tmp_path):
    adapter = get_storage_adapter(config, backend="deltalake", root_path=tmp_path / "d")
    assert isinstance(adapter, DeltaLakeAdapter)


def test_factory_reads_env_variable(config: SprintShieldConfig, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "deltalake")
    adapter = get_storage_adapter(config, root_path=tmp_path / "d")
    assert adapter.backend == "deltalake"


def test_factory_rejects_unknown_backend(config: SprintShieldConfig):
    with pytest.raises(ValueError):
        get_storage_adapter(config, backend="sqlite")


def test_factory_falls_back_to_delta_without_snowflake_credentials(config, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "snowflake")
    adapter = get_storage_adapter(config, root_path=tmp_path / "d")
    assert adapter.backend == "deltalake"


# --------------------------------------------------------------------------- #
# Snowflake adapter (no live connection)
# --------------------------------------------------------------------------- #
def test_snowflake_adapter_construction_and_fqn(config):
    from sprintshield.storage.snowflake_adapter import SnowflakeAdapter

    adapter = SnowflakeAdapter(config.storage.snowflake)
    assert adapter.backend == "snowflake"
    assert adapter._fqn("sprint_snapshots").endswith("SPRINT_SNAPSHOTS")
    assert adapter._fqn("support_tickets").endswith("SUPPORT_TICKETS")
    assert adapter._fqn("sprint_snapshots").startswith(f"{config.storage.snowflake.database}.")
    adapter.close()


def test_snowflake_adapter_connect_params(config, monkeypatch):
    from sprintshield.storage.snowflake_adapter import SnowflakeAdapter

    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
    monkeypatch.setenv("SNOWFLAKE_USER", "svc")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "pw")
    params = SnowflakeAdapter(config.storage.snowflake)._connect_params()
    assert params["account"] == "acct"
    assert params["user"] == "svc"
    assert params["warehouse"] == config.storage.snowflake.warehouse


def test_snowflake_connection_without_driver_raises(config):
    import importlib.util

    if importlib.util.find_spec("snowflake") is not None:
        pytest.skip("snowflake-connector-python is installed")
    from sprintshield.storage.snowflake_adapter import SnowflakeAdapter

    adapter = SnowflakeAdapter(config.storage.snowflake, credentials={"account": "x", "user": "y", "password": "z"})
    with pytest.raises(StorageError):
        _ = adapter.connection


# --------------------------------------------------------------------------- #
# Configuration loader
# --------------------------------------------------------------------------- #
def test_load_config_reads_all_sections(config):
    assert config.storage_backend in {"deltalake", "snowflake"}
    assert len(config.towers) == 4
    assert config.simulation.runs > 0
    assert config.tower("DE").name == "Data Engineering"
    assert config.tower_ids() == ("DE", "DA", "DS", "PMO")
    assert config.total_gross_hours > 0


def test_load_config_tower_lookup_errors(config):
    with pytest.raises(KeyError):
        config.tower("NOPE")


def test_load_config_env_overrides(config, tmp_path):
    from sprintshield.config import load_config

    env_file = tmp_path / ".env"
    env_file.write_text(
        "STORAGE_BACKEND=snowflake\nSIMULATION_RUNS=1234\nDELTA_LAKE_PATH=/tmp/custom-delta\n",
        encoding="utf-8",
    )
    cfg = load_config(project_root=config.project_root, env_file=env_file)
    assert cfg.storage_backend == "snowflake"
    assert cfg.simulation.runs == 1234
    assert cfg.storage.deltalake.root_path == "/tmp/custom-delta"


def test_load_config_ignores_malformed_env_numbers(config, tmp_path):
    from sprintshield.config import load_config

    env_file = tmp_path / ".env"
    env_file.write_text("SIMULATION_RUNS=not-a-number\n", encoding="utf-8")
    cfg = load_config(project_root=config.project_root, env_file=env_file)
    assert isinstance(cfg.simulation.runs, int)


def test_load_config_missing_directory_raises(tmp_path):
    from sprintshield.config import load_config

    with pytest.raises(FileNotFoundError):
        load_config(config_dir=tmp_path / "nope", project_root=tmp_path)


def test_resolve_path_is_relative_to_project_root(config):
    from sprintshield.config import resolve_path

    assert resolve_path(config, "data/delta").is_absolute()
    assert resolve_path(config, Path("/tmp/x")).name == "x"


def test_planned_hours_property(config):
    assert config.simulation.planned_hours == (
        config.simulation.planned_points_per_sprint * config.simulation.hours_per_point
    )


def test_tower_profile_asset_names(config):
    names = config.tower("DS").asset_names()
    assert "feature_store" in names

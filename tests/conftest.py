"""Shared pytest fixtures for the SprintShield test-suite."""

from __future__ import annotations

import os
import resource
import sys
from pathlib import Path

import pandas as pd
import pytest

# --------------------------------------------------------------------------- #
# Container-friendly hygiene.
#
# delta-rs spawns a small thread pool per write. In tightly memory-capped CI
# containers that can trip the process address-space soft limit and abort the
# interpreter. The hard limit is normally unlimited, so relax the soft limit
# when we are allowed to; this is a no-op on an ordinary workstation.
# --------------------------------------------------------------------------- #
try:  # pragma: no cover - environment dependent
    _soft, _hard = resource.getrlimit(resource.RLIMIT_AS)
    if _hard == resource.RLIM_INFINITY and _soft != resource.RLIM_INFINITY:
        resource.setrlimit(resource.RLIMIT_AS, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
except (ValueError, OSError):  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sprintshield.analytics.arrival_model import fit_arrival_model  # noqa: E402
from sprintshield.analytics.effort_distribution import fit_effort_distribution  # noqa: E402
from sprintshield.client.synthetic_generator import generate_telemetry  # noqa: E402
from sprintshield.config import SprintShieldConfig, load_config  # noqa: E402
from sprintshield.storage.delta_adapter import DeltaLakeAdapter  # noqa: E402

# Keep test runs quick and deterministic.
TEST_SPRINTS = 6
TEST_SEED = 20260724


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def config(project_root: Path) -> SprintShieldConfig:
    return load_config(project_root=project_root)


@pytest.fixture()
def telemetry(config: SprintShieldConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A compact synthetic telemetry pair (6 sprints across 4 towers)."""
    return generate_telemetry(sprints=TEST_SPRINTS, config=config, seed=TEST_SEED)


@pytest.fixture()
def snapshots(telemetry: tuple[pd.DataFrame, pd.DataFrame]) -> pd.DataFrame:
    return telemetry[0]


@pytest.fixture()
def tickets(telemetry: tuple[pd.DataFrame, pd.DataFrame]) -> pd.DataFrame:
    return telemetry[1]


@pytest.fixture()
def delta_root(tmp_path: Path) -> Path:
    return tmp_path / "delta"


@pytest.fixture()
def delta_adapter(config: SprintShieldConfig, delta_root: Path) -> DeltaLakeAdapter:
    return DeltaLakeAdapter(config.storage.deltalake, root_path=delta_root)


@pytest.fixture()
def populated_adapter(
    delta_adapter: DeltaLakeAdapter,
    snapshots: pd.DataFrame,
    tickets: pd.DataFrame,
) -> DeltaLakeAdapter:
    delta_adapter.save_sprint_snapshots(snapshots, mode="overwrite")
    delta_adapter.save_support_tickets(tickets, mode="overwrite")
    return delta_adapter


@pytest.fixture()
def arrival_fit(config: SprintShieldConfig, snapshots: pd.DataFrame):
    counts = snapshots.loc[snapshots["tower_id"] == "DE", "incident_count"].tolist()
    return fit_arrival_model(counts, config.arrival_model)


@pytest.fixture()
def effort_fit(config: SprintShieldConfig, tickets: pd.DataFrame):
    hours = tickets.loc[tickets["tower_id"] == "DE", "resolution_hours"].dropna().tolist()
    return fit_effort_distribution(hours, config=config.effort_model)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ambient configuration from leaking between tests."""
    for name in (
        "STORAGE_BACKEND",
        "DELTA_LAKE_PATH",
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PASSWORD",
        "REPORT_OUTPUT_DIR",
        "SIMULATION_RUNS",
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
        "JIRA_PAT",
    ):
        monkeypatch.delenv(name, raising=False)
    _ = os.environ.get("PATH")

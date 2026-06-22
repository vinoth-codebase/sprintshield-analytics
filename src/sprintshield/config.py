"""Typed configuration loader.

Reads ``config/config.yaml``, ``config/storage.yaml`` and ``config/towers.yaml``
into frozen dataclasses, merging environment overrides from a ``.env`` file or
the process environment. Every other module consumes configuration through
:class:`SprintShieldConfig`, never by touching YAML or ``os.environ`` directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

try:  # python-dotenv is a soft dependency; degrade gracefully when absent.
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - exercised only without the package
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:  # type: ignore[misc]
        return False


# --------------------------------------------------------------------------- #
# Repository-relative defaults
# --------------------------------------------------------------------------- #
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = DEFAULT_PROJECT_ROOT / "config"


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SimulationConfig:
    """Monte Carlo and capacity planning parameters."""

    runs: int = 10_000
    random_seed: int = 20260724
    confidence_target: float = 0.85
    gross_hours_per_sprint: float = 320.0
    planned_points_per_sprint: int = 80
    hours_per_point: float = 2.5

    @property
    def planned_hours(self) -> float:
        """Planned commitment expressed in engineering hours."""
        return self.planned_points_per_sprint * self.hours_per_point


@dataclass(frozen=True)
class ArrivalModelConfig:
    """Thresholds controlling Poisson vs Negative Binomial selection."""

    poisson_lower_bound: float = 0.85
    poisson_upper_bound: float = 1.20
    negbinom_max_iter: int = 500
    negbinom_tol: float = 1.0e-6


@dataclass(frozen=True)
class EffortModelConfig:
    """Resolution-effort fitting controls."""

    candidates: tuple[str, ...] = ("lognormal", "weibull")
    min_samples: int = 8
    min_hours: float = 0.25
    max_hours: float = 240.0


@dataclass(frozen=True)
class NoiseConfig:
    """Corporate operational noise parameters for the synthetic generator."""

    release_regression_probability: float = 0.18
    end_of_quarter_uplift: float = 1.65
    holiday_capacity_drop: float = 0.55
    black_swan_probability: float = 0.04


@dataclass(frozen=True)
class ReportingConfig:
    """Report thresholds and destination."""

    output_dir: str = "reports"
    support_tax_warning_threshold: float = 0.20
    support_tax_critical_threshold: float = 0.30


@dataclass(frozen=True)
class DeltaLakeConfig:
    """Local ACID lakehouse settings."""

    root_path: str = "data/delta"
    snapshot_table: str = "sprint_snapshots"
    ticket_table: str = "support_tickets"
    snapshot_partition_by: tuple[str, ...] = ("tower_id", "sprint_id")
    ticket_partition_by: tuple[str, ...] = ("tower_id", "sprint_id")
    enable_time_travel: bool = True


@dataclass(frozen=True)
class SnowflakeConfig:
    """Snowflake cloud warehouse settings."""

    database: str = "SPRINTSHIELD_DB"
    schema: str = "TELEMETRY"
    snapshot_table: str = "SPRINT_SNAPSHOTS"
    ticket_table: str = "SUPPORT_TICKETS"
    warehouse: str = "COMPUTE_WH"
    role: str = "SYSADMIN"
    connect_timeout: int = 30
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0


@dataclass(frozen=True)
class StorageConfig:
    """Container for both storage adapters plus the active backend selector."""

    default_backend: str = "deltalake"
    deltalake: DeltaLakeConfig = field(default_factory=DeltaLakeConfig)
    snowflake: SnowflakeConfig = field(default_factory=SnowflakeConfig)


@dataclass(frozen=True)
class AssetProfile:
    """A business data asset that generates support demand."""

    asset: str
    weight: float
    severity: float


@dataclass(frozen=True)
class TowerArrivalProfile:
    """Arrival-process signature for one organizational tower."""

    mean_incidents: float
    dispersion: float
    burst_probability: float
    burst_multiplier: float


@dataclass(frozen=True)
class TowerEffortProfile:
    """Resolution-effort signature for one organizational tower."""

    lognormal_mu: float
    lognormal_sigma: float
    weibull_shape: float
    weibull_scale: float


@dataclass(frozen=True)
class TowerProfile:
    """Full operational profile for a single data tower."""

    tower_id: str
    name: str
    headcount: int
    story_points_per_sprint: int
    gross_hours_per_sprint: float
    arrival: TowerArrivalProfile
    effort: TowerEffortProfile
    assets: tuple[AssetProfile, ...]

    def asset_names(self) -> tuple[str, ...]:
        return tuple(a.asset for a in self.assets)


@dataclass(frozen=True)
class SprintShieldConfig:
    """Root configuration object handed to every component."""

    project_root: Path
    storage_backend: str
    simulation: SimulationConfig
    arrival_model: ArrivalModelConfig
    effort_model: EffortModelConfig
    noise: NoiseConfig
    reporting: ReportingConfig
    storage: StorageConfig
    towers: tuple[TowerProfile, ...]

    # -- convenience accessors ------------------------------------------------ #
    def tower(self, tower_id: str) -> TowerProfile:
        for profile in self.towers:
            if profile.tower_id == tower_id:
                return profile
        raise KeyError(f"Unknown tower_id: {tower_id!r}")

    def tower_ids(self) -> tuple[str, ...]:
        return tuple(t.tower_id for t in self.towers)

    @property
    def total_gross_hours(self) -> float:
        return float(sum(t.gross_hours_per_sprint for t in self.towers))


# --------------------------------------------------------------------------- #
# Loading helpers
# --------------------------------------------------------------------------- #
def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a mapping at the root of {path}, got {type(payload)!r}")
    return payload


def _as_tuple(value: Any, default: Iterable[str]) -> tuple[str, ...]:
    if value is None:
        return tuple(default)
    return tuple(str(v) for v in value)


def _env_float(name: str, fallback: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return fallback
    try:
        return float(raw)
    except ValueError:
        return fallback


def _env_int(name: str, fallback: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return fallback
    try:
        return int(float(raw))
    except ValueError:
        return fallback


def _build_tower(raw: dict[str, Any]) -> TowerProfile:
    arrival = raw.get("arrival", {}) or {}
    effort = raw.get("effort", {}) or {}
    assets = tuple(
        AssetProfile(
            asset=str(a["asset"]),
            weight=float(a.get("weight", 1.0)),
            severity=float(a.get("severity", 1.0)),
        )
        for a in (raw.get("assets") or [])
    )
    return TowerProfile(
        tower_id=str(raw["tower_id"]),
        name=str(raw.get("name", raw["tower_id"])),
        headcount=int(raw.get("headcount", 0)),
        story_points_per_sprint=int(raw.get("story_points_per_sprint", 0)),
        gross_hours_per_sprint=float(raw.get("gross_hours_per_sprint", 320.0)),
        arrival=TowerArrivalProfile(
            mean_incidents=float(arrival.get("mean_incidents", 6.0)),
            dispersion=float(arrival.get("dispersion", 1.0)),
            burst_probability=float(arrival.get("burst_probability", 0.2)),
            burst_multiplier=float(arrival.get("burst_multiplier", 2.5)),
        ),
        effort=TowerEffortProfile(
            lognormal_mu=float(effort.get("lognormal_mu", 3.0)),
            lognormal_sigma=float(effort.get("lognormal_sigma", 0.9)),
            weibull_shape=float(effort.get("weibull_shape", 1.3)),
            weibull_scale=float(effort.get("weibull_scale", 30.0)),
        ),
        assets=assets,
    )


def load_config(
    config_dir: str | Path | None = None,
    project_root: str | Path | None = None,
    env_file: str | Path | None = None,
) -> SprintShieldConfig:
    """Load and merge the full SprintShield configuration.

    Parameters
    ----------
    config_dir:
        Directory holding ``config.yaml``, ``storage.yaml`` and ``towers.yaml``.
    project_root:
        Repository root used for relative paths.
    env_file:
        Optional ``.env`` path. Defaults to ``<project_root>/.env`` when present.
    """

    root = Path(project_root) if project_root is not None else DEFAULT_PROJECT_ROOT
    cfg_dir = Path(config_dir) if config_dir is not None else root / "config"

    dotenv_path = Path(env_file) if env_file is not None else root / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path, override=False)

    base = _read_yaml(cfg_dir / "config.yaml")
    storage_raw = _read_yaml(cfg_dir / "storage.yaml")
    towers_raw = _read_yaml(cfg_dir / "towers.yaml")

    sim_raw = base.get("simulation", {}) or {}
    sim = SimulationConfig(
        runs=_env_int("SIMULATION_RUNS", int(sim_raw.get("runs", 10_000))),
        random_seed=_env_int("RANDOM_SEED", int(sim_raw.get("random_seed", 20260724))),
        confidence_target=float(sim_raw.get("confidence_target", 0.85)),
        gross_hours_per_sprint=float(sim_raw.get("gross_hours_per_sprint", 320.0)),
        planned_points_per_sprint=int(sim_raw.get("planned_points_per_sprint", 80)),
        hours_per_point=float(sim_raw.get("hours_per_point", 2.5)),
    )

    arr_raw = base.get("arrival_model", {}) or {}
    arrival = ArrivalModelConfig(
        poisson_lower_bound=float(arr_raw.get("poisson_lower_bound", 0.85)),
        poisson_upper_bound=float(arr_raw.get("poisson_upper_bound", 1.20)),
        negbinom_max_iter=int(arr_raw.get("negbinom_max_iter", 500)),
        negbinom_tol=float(arr_raw.get("negbinom_tol", 1.0e-6)),
    )

    eff_raw = base.get("effort_model", {}) or {}
    effort = EffortModelConfig(
        candidates=_as_tuple(eff_raw.get("candidates"), ("lognormal", "weibull")),
        min_samples=int(eff_raw.get("min_samples", 8)),
        min_hours=float(eff_raw.get("min_hours", 0.25)),
        max_hours=float(eff_raw.get("max_hours", 240.0)),
    )

    noise_raw = base.get("noise", {}) or {}
    noise = NoiseConfig(
        release_regression_probability=float(noise_raw.get("release_regression_probability", 0.18)),
        end_of_quarter_uplift=float(noise_raw.get("end_of_quarter_uplift", 1.65)),
        holiday_capacity_drop=float(noise_raw.get("holiday_capacity_drop", 0.55)),
        black_swan_probability=float(noise_raw.get("black_swan_probability", 0.04)),
    )

    rep_raw = base.get("reporting", {}) or {}
    reporting = ReportingConfig(
        output_dir=os.environ.get("REPORT_OUTPUT_DIR", str(rep_raw.get("output_dir", "reports"))),
        support_tax_warning_threshold=float(rep_raw.get("support_tax_warning_threshold", 0.20)),
        support_tax_critical_threshold=float(rep_raw.get("support_tax_critical_threshold", 0.30)),
    )

    storage_section = storage_raw.get("storage", {}) or {}
    delta_raw = storage_section.get("deltalake", {}) or {}
    snow_raw = storage_section.get("snowflake", {}) or {}
    storage = StorageConfig(
        default_backend=str(storage_section.get("default_backend", "deltalake")),
        deltalake=DeltaLakeConfig(
            root_path=os.environ.get("DELTA_LAKE_PATH", str(delta_raw.get("root_path", "data/delta"))),
            snapshot_table=str(delta_raw.get("snapshot_table", "sprint_snapshots")),
            ticket_table=str(delta_raw.get("ticket_table", "support_tickets")),
            snapshot_partition_by=_as_tuple(
                delta_raw.get("snapshot_partition_by"), ("tower_id", "sprint_id")
            ),
            ticket_partition_by=_as_tuple(
                delta_raw.get("ticket_partition_by"), ("tower_id", "sprint_id")
            ),
            enable_time_travel=bool(delta_raw.get("enable_time_travel", True)),
        ),
        snowflake=SnowflakeConfig(
            database=os.environ.get("SNOWFLAKE_DATABASE", str(snow_raw.get("database", "SPRINTSHIELD_DB"))),
            schema=os.environ.get("SNOWFLAKE_SCHEMA", str(snow_raw.get("schema", "TELEMETRY"))),
            snapshot_table=str(snow_raw.get("snapshot_table", "SPRINT_SNAPSHOTS")),
            ticket_table=str(snow_raw.get("ticket_table", "SUPPORT_TICKETS")),
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", str(snow_raw.get("warehouse", "COMPUTE_WH"))),
            role=os.environ.get("SNOWFLAKE_ROLE", str(snow_raw.get("role", "SYSADMIN"))),
            connect_timeout=int(snow_raw.get("connect_timeout", 30)),
            max_retries=int(snow_raw.get("max_retries", 3)),
            retry_backoff_seconds=float(snow_raw.get("retry_backoff_seconds", 2.0)),
        ),
    )

    backend = os.environ.get("STORAGE_BACKEND", storage.default_backend).strip().lower()

    towers = tuple(_build_tower(t) for t in (towers_raw.get("towers") or []))
    if not towers:
        raise ValueError("No towers defined in towers.yaml")

    return SprintShieldConfig(
        project_root=root,
        storage_backend=backend,
        simulation=sim,
        arrival_model=arrival,
        effort_model=effort,
        noise=noise,
        reporting=reporting,
        storage=storage,
        towers=towers,
    )


def resolve_path(config: SprintShieldConfig, relative: str | Path) -> Path:
    """Resolve a possibly-relative path against the project root."""
    path = Path(relative)
    return path if path.is_absolute() else (config.project_root / path)

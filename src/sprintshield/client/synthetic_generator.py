"""High-fidelity synthetic multi-tower telemetry generator.

Simulates 24 two-week sprints (one full calendar year) of engineering telemetry
across the Data Engineering, Data Analytics, Data Science and PMO towers. The
generator reproduces the real-world statistical signatures that make unplanned
support demand hard to plan for:

* **Overdispersion** - incident counts are drawn from a Negative Binomial when
  the tower's dispersion index exceeds 1.2, so variance exceeds the mean exactly
  as it does around batch releases.
* **Right-skewed effort** - resolution hours are drawn from a Log-Normal with
  per-asset severity multipliers, producing the long tail of pipeline lockups
  and model-drift investigations.
* **Corporate noise** - platform release regressions, end-of-quarter audit
  surges, holiday capacity drops and rare black-swan incidents.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

from sprintshield.config import SprintShieldConfig, TowerProfile, load_config
from sprintshield.storage.base import SPRINT_SNAPSHOT_COLUMNS, SUPPORT_TICKET_COLUMNS

# Default anchor: a Monday, so every two-week sprint starts on a Monday.
# Anchored late December so the 24-sprint window spans a full calendar year and
# the winter-shutdown capacity drop is exercised by the generator.
DEFAULT_START_DATE = date(2025, 12, 22)

SPRINT_LENGTH_DAYS = 14

_SUMMARY_TEMPLATES: dict[str, list[str]] = {
    "DE": [
        "Airflow DAG {dag} stuck in running state, blocking downstream load",
        "Schema drift detected on {asset} breaking nightly batch",
        "Kafka consumer lag spike on ingestion topic {topic}",
        "Snowflake loader failing on duplicate primary key for {asset}",
        "dbt model {model} failing incremental merge after upstream change",
    ],
    "DA": [
        "Executive dashboard {asset} showing stale metric values",
        "Metric mismatch between Looker and Power BI for {asset}",
        "Row-level security filter dropping regional rows in {asset}",
        "Scheduled refresh failure on {asset} before board review",
        "Semantic layer measure computing wrong period-over-period delta",
    ],
    "DS": [
        "Model drift alert raised for {asset} beyond PSI threshold",
        "Feature store null spike corrupting training set for {asset}",
        "Scoring latency regression on {asset} breaching SLA",
        "Churn prediction {asset} producing out-of-range probabilities",
        "Recommendation {asset} cold-start returning empty candidate sets",
    ],
    "PMO": [
        "Portfolio governance report {asset} missing sprint rollup",
        "Regulatory audit evidence gap flagged for {asset}",
        "SteerCo pack generation failed for {asset}",
        "Resource forecast model out of sync with {asset}",
        "Change-control ticket raised against {asset} baseline",
    ],
}

_RELEASE_REGRESSION_SUMMARY = (
    "Release regression: {asset} broke immediately after platform release {release}"
)


@dataclass
class SyntheticTelemetryGenerator:
    """Deterministic generator of sprint snapshots and support tickets."""

    config: SprintShieldConfig
    seed: int | None = None
    start_date: date = DEFAULT_START_DATE

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed if self.seed is not None else self.config.simulation.random_seed)

    # -- public API ---------------------------------------------------------- #
    def generate(self, sprints: int = 24) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Generate ``(sprint_snapshots, support_tickets)`` frames."""
        sprint_calendar = self._build_calendar(sprints)
        snapshot_rows: list[dict[str, Any]] = []
        ticket_rows: list[dict[str, Any]] = []

        for tower in self.config.towers:
            for index, (sprint_id, sprint_name, start, end) in enumerate(sprint_calendar, start=1):
                snap, tickets = self._simulate_sprint(
                    tower=tower,
                    sprint_id=sprint_id,
                    sprint_name=sprint_name,
                    start=start,
                    end=end,
                    index=index,
                    total=sprints,
                )
                snapshot_rows.append(snap)
                ticket_rows.extend(tickets)

        snapshots = pd.DataFrame(snapshot_rows, columns=list(SPRINT_SNAPSHOT_COLUMNS))
        tickets = pd.DataFrame(ticket_rows, columns=list(SUPPORT_TICKET_COLUMNS))
        snapshots = snapshots.sort_values(["sprint_id", "tower_id"]).reset_index(drop=True)
        if not tickets.empty:
            tickets = tickets.sort_values(["sprint_id", "tower_id", "created_at"]).reset_index(drop=True)
        return snapshots, tickets

    # -- calendar ------------------------------------------------------------ #
    def _build_calendar(self, sprints: int) -> list[tuple[str, str, date, date]]:
        calendar: list[tuple[str, str, date, date]] = []
        cursor = self.start_date
        for index in range(1, sprints + 1):
            start = cursor
            end = start + timedelta(days=SPRINT_LENGTH_DAYS - 1)
            sprint_id = f"S{index:02d}"
            sprint_name = f"{start.year} Sprint {index:02d}"
            calendar.append((sprint_id, sprint_name, start, end))
            cursor = end + timedelta(days=1)
        return calendar

    def _is_end_of_quarter(self, end: date) -> bool:
        return end.month in {3, 6, 9, 12} and end.day >= 22

    def _is_holiday_sprint(self, start: date, end: date) -> bool:
        # Late-December sprints lose capacity to the winter shutdown.
        return (start.month == 12 and start.day >= 20) or (end.month == 1 and end.day <= 5)

    # -- per-sprint simulation ---------------------------------------------- #
    def _simulate_sprint(
        self,
        tower: TowerProfile,
        sprint_id: str,
        sprint_name: str,
        start: date,
        end: date,
        index: int,
        total: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        rng = self._rng
        noise = self.config.noise

        # ---- 1. Effective demand mean, after corporate noise ---------------- #
        mu = tower.arrival.mean_incidents
        if self._is_end_of_quarter(end):
            mu *= 1.0 + (noise.end_of_quarter_uplift - 1.0) * 0.45

        burst = rng.random() < tower.arrival.burst_probability
        if burst:
            mu *= tower.arrival.burst_multiplier

        # ---- 2. Sample the incident count ----------------------------------- #
        incident_count = self._sample_arrival(mu, tower.arrival.dispersion)

        # ---- 3. Gross capacity, after holiday drops ------------------------- #
        gross_hours = tower.gross_hours_per_sprint
        if self._is_holiday_sprint(start, end):
            gross_hours *= noise.holiday_capacity_drop

        # ---- 4. Sample resolution effort per incident ----------------------- #
        tickets: list[dict[str, Any]] = []
        support_hours = 0.0
        assets = list(tower.assets)
        weights = np.array([a.weight for a in assets], dtype=float)
        weights = weights / weights.sum()

        release_regression = burst and rng.random() < noise.release_regression_probability

        for i in range(int(incident_count)):
            asset = assets[int(rng.choice(len(assets), p=weights))]
            severity = asset.severity
            black_swan = rng.random() < noise.black_swan_probability

            hours = float(
                rng.lognormal(
                    mean=tower.effort.lognormal_mu,
                    sigma=tower.effort.lognormal_sigma,
                )
            )
            hours *= severity
            if black_swan:
                hours *= 3.0
            hours = float(np.clip(hours, self.config.effort_model.min_hours, self.config.effort_model.max_hours))

            created = datetime(
                start.year, start.month, start.day, tzinfo=timezone.utc
            ) + timedelta(
                days=float(rng.integers(0, SPRINT_LENGTH_DAYS)),
                hours=float(rng.uniform(6, 20)),
            )
            # A small share of incidents is still open at sprint close (active WIP).
            still_open = rng.random() < 0.12
            resolved = None if still_open else created + timedelta(hours=hours)

            ticket_id = f"{tower.tower_id}-{sprint_id}-{i + 1:03d}"
            summary = self._summary_for(tower, asset.asset, release_regression, index, rng)
            priority = self._priority_for(hours, rng)
            labels = [f"asset:{asset.asset}", f"tower:{tower.tower_id.lower()}"]
            if release_regression:
                labels.append("release-regression")
            if black_swan:
                labels.append("black-swan")

            tickets.append(
                {
                    "ticket_id": ticket_id,
                    "tower_id": tower.tower_id,
                    "sprint_id": sprint_id,
                    "asset": asset.asset,
                    "summary": summary,
                    "issue_type": self._issue_type(tower, rng),
                    "priority": priority,
                    "status": "Open" if still_open else "Done",
                    "created_at": created.isoformat(),
                    "resolved_at": resolved.isoformat() if resolved else None,
                    "resolution_hours": round(hours, 3),
                    "story_points": float(rng.choice([0.0, 1.0, 2.0, 3.0], p=[0.55, 0.2, 0.15, 0.1])),
                    "is_planned": False,
                    "is_release_regression": bool(release_regression),
                    "labels": ",".join(labels),
                }
            )
            support_hours += hours

        # ---- 5. Derive the sprint snapshot ---------------------------------- #
        planned_feature_hours = max(gross_hours - support_hours, 0.0)
        flow_efficiency = planned_feature_hours / gross_hours if gross_hours else 0.0
        support_tax = support_hours / gross_hours if gross_hours else 0.0

        committed = float(tower.story_points_per_sprint)
        committed *= float(rng.normal(1.0, 0.06))
        committed = round(max(committed, 0.0), 1)
        scope_change = round(float(rng.normal(2.5, 1.5)), 1)
        scope_change = float(np.clip(scope_change, 0.0, committed * 0.25))

        delivery_ratio = float(np.clip(flow_efficiency * rng.normal(1.0, 0.08), 0.05, 1.05))
        completed = round(committed * delivery_ratio, 1)

        snapshot = {
            "tower_id": tower.tower_id,
            "sprint_id": sprint_id,
            "sprint_name": sprint_name,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "committed_points": committed,
            "completed_points": completed,
            "scope_change_points": scope_change,
            "gross_hours": round(gross_hours, 2),
            "planned_feature_hours": round(planned_feature_hours, 2),
            "unplanned_support_hours": round(support_hours, 2),
            "incident_count": float(incident_count),
            "support_tax": round(support_tax, 4),
            "flow_efficiency": round(flow_efficiency, 4),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        return snapshot, tickets

    # -- sampling helpers ---------------------------------------------------- #
    def _sample_arrival(self, mu: float, dispersion: float) -> int:
        """Draw an incident count from the distribution the dispersion implies."""
        rng = self._rng
        mu = max(mu, 0.0)
        if mu == 0:
            return 0
        if dispersion > 1.2:
            # Negative Binomial: var = mu + mu^2 / r  =>  r = mu / (D - 1)
            r = mu / (dispersion - 1.0)
            p = r / (r + mu)
            return int(rng.negative_binomial(r, p))
        return int(rng.poisson(mu))

    def _summary_for(
        self,
        tower: TowerProfile,
        asset: str,
        release_regression: bool,
        index: int,
        rng: np.random.Generator,
    ) -> str:
        if release_regression:
            return _RELEASE_REGRESSION_SUMMARY.format(asset=asset, release=f"R{2026}.{index % 12 + 1}")
        templates = _SUMMARY_TEMPLATES.get(tower.tower_id, _SUMMARY_TEMPLATES["DE"])
        template = templates[int(rng.integers(0, len(templates)))]
        return template.format(
            asset=asset,
            dag=f"dag_{asset}",
            model=f"model_{asset}",
            topic=f"topic.{asset}",
        )

    @staticmethod
    def _priority_for(hours: float, rng: np.random.Generator) -> str:
        if hours >= 40:
            return "P1"
        if hours >= 16:
            return "P2"
        if hours >= 6:
            return "P3"
        return "P4" if rng.random() < 0.7 else "P3"

    @staticmethod
    def _issue_type(tower: TowerProfile, rng: np.random.Generator) -> str:
        options = {
            "DE": ["Incident", "Bug", "Hotfix", "Service Request"],
            "DA": ["Bug", "Incident", "Service Request"],
            "DS": ["Incident", "Bug", "Support"],
            "PMO": ["Service Request", "Support"],
        }.get(tower.tower_id, ["Bug", "Incident"])
        return str(rng.choice(options))


def generate_telemetry(
    sprints: int = 24,
    config: SprintShieldConfig | None = None,
    seed: int | None = None,
    start_date: date | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convenience wrapper returning ``(sprint_snapshots, support_tickets)``."""
    cfg = config or load_config()
    generator = SyntheticTelemetryGenerator(
        config=cfg,
        seed=seed,
        start_date=start_date or DEFAULT_START_DATE,
    )
    return generator.generate(sprints=sprints)

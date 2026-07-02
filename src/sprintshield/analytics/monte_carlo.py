"""Vectorised Monte Carlo sprint-capacity simulator.

For each of the ``runs`` independent sprint iterations the engine executes the
four-step generative process:

1. sample the incoming incident count       ``N ~ NegBinom(r, p)``  (or Poisson)
2. sample the resolution effort per incident ``E_i ~ LogNormal(mu, sigma)``
3. sum the unplanned overhead                ``H_support = sum_i E_i``
4. net the support burn off gross capacity   ``H_net = H_gross - H_support``

The resulting distribution of ``H_net`` yields calibrated delivery-confidence
curves: the probability the team still clears its planned commitment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from sprintshield.analytics.arrival_model import ArrivalFit, fit_arrival_model
from sprintshield.analytics.effort_distribution import EffortFit, fit_effort_distribution
from sprintshield.config import NoiseConfig, SprintShieldConfig, TowerProfile


@dataclass
class SimulationResult:
    """Outcome of a Monte Carlo sprint simulation."""

    runs: int
    gross_hours: float
    planned_hours: float
    support_hours: np.ndarray
    net_hours: np.ndarray
    incident_counts: np.ndarray
    seed: int | None = None
    tower_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    # -- point statistics ---------------------------------------------------- #
    @property
    def mean_support_hours(self) -> float:
        return float(np.mean(self.support_hours))

    @property
    def mean_net_hours(self) -> float:
        return float(np.mean(self.net_hours))

    @property
    def mean_support_tax(self) -> float:
        return self.mean_support_hours / self.gross_hours if self.gross_hours else 0.0

    @property
    def mean_incidents(self) -> float:
        return float(np.mean(self.incident_counts))

    def percentile(self, q: float, of: str = "support") -> float:
        """Return a percentile of the support or net-hours distribution."""
        series = self.support_hours if of == "support" else self.net_hours
        return float(np.percentile(series, q))

    # -- delivery confidence ------------------------------------------------- #
    def delivery_confidence(self, planned_hours: float | None = None) -> float:
        """P(net capacity >= planned commitment)."""
        target = self.planned_hours if planned_hours is None else planned_hours
        return float(np.mean(self.net_hours >= target))

    def delivery_risk(self, planned_hours: float | None = None) -> float:
        """Complement of :meth:`delivery_confidence`."""
        return 1.0 - self.delivery_confidence(planned_hours)

    def confidence_curve(self, grid: Sequence[float] | None = None) -> pd.DataFrame:
        """Delivery-confidence curve across a grid of planned-hour commitments."""
        if grid is None:
            grid = np.linspace(0.0, self.gross_hours * 1.1, 60)
        planned = np.asarray(grid, dtype=float)
        confidence = np.array([self.delivery_confidence(p) for p in planned])
        return pd.DataFrame(
            {
                "planned_hours": planned,
                "delivery_confidence": confidence,
                "delivery_risk": 1.0 - confidence,
            }
        )

    def buffer_hours(self, target_confidence: float = 0.85) -> float:
        """Support-hours buffer required to hit ``target_confidence``.

        The buffer is the (1 - confidence) percentile of the support-hours
        distribution: the contingency a planner must ring-fence.
        """
        percentile = float(np.clip(target_confidence, 0.0, 1.0)) * 100.0
        return float(np.percentile(self.support_hours, percentile))

    def max_committed_hours(self, target_confidence: float = 0.85) -> float:
        """Largest planned commitment still cleared at ``target_confidence``."""
        return float(np.percentile(self.net_hours, (1.0 - target_confidence) * 100.0))

    def summary(self, target_confidence: float = 0.85) -> dict[str, Any]:
        """Condensed, JSON-friendly summary of the simulation."""
        return {
            "tower_id": self.tower_id,
            "runs": self.runs,
            "gross_hours": self.gross_hours,
            "planned_hours": self.planned_hours,
            "mean_incidents": self.mean_incidents,
            "mean_support_hours": self.mean_support_hours,
            "mean_net_hours": self.mean_net_hours,
            "mean_support_tax": self.mean_support_tax,
            "p50_support_hours": self.percentile(50),
            "p85_support_hours": self.percentile(85),
            "p95_support_hours": self.percentile(95),
            "p50_net_hours": self.percentile(50, of="net"),
            "p15_net_hours": self.percentile(15, of="net"),
            "delivery_confidence": self.delivery_confidence(),
            "delivery_risk": self.delivery_risk(),
            "buffer_hours_at_target": self.buffer_hours(target_confidence),
            "max_committed_hours_at_target": self.max_committed_hours(target_confidence),
            "target_confidence": target_confidence,
        }


class MonteCarloSprintSimulator:
    """Composes an arrival model and an effort model into sprint simulations."""

    def __init__(
        self,
        config: SprintShieldConfig,
        arrival_fit: ArrivalFit,
        effort_fit: EffortFit,
        tower_id: str | None = None,
        seed: int | None = None,
    ) -> None:
        self.config = config
        self.arrival_fit = arrival_fit
        self.effort_fit = effort_fit
        self.tower_id = tower_id
        self.seed = seed if seed is not None else config.simulation.random_seed

    # -- constructors -------------------------------------------------------- #
    @classmethod
    def from_tower(
        cls,
        config: SprintShieldConfig,
        tower: TowerProfile | str,
        seed: int | None = None,
    ) -> "MonteCarloSprintSimulator":
        """Build a simulator from a tower's declared operational profile."""
        profile = config.tower(tower) if isinstance(tower, str) else tower
        return cls(
            config=config,
            arrival_fit=arrival_fit_from_profile(profile, config.noise),
            effort_fit=effort_fit_from_profile(profile, config.noise),
            tower_id=profile.tower_id,
            seed=seed,
        )

    @classmethod
    def from_history(
        cls,
        config: SprintShieldConfig,
        snapshots: pd.DataFrame,
        tickets: pd.DataFrame,
        tower_id: str,
        seed: int | None = None,
    ) -> "MonteCarloSprintSimulator":
        """Build a simulator by fitting both models to stored telemetry."""
        if snapshots is None or snapshots.empty:
            raise ValueError("No sprint history supplied for simulation")
        tower_snaps = snapshots[snapshots["tower_id"] == tower_id]
        if tower_snaps.empty:
            raise ValueError(f"No sprint snapshots for tower {tower_id!r}")

        counts = pd.to_numeric(tower_snaps["incident_count"], errors="coerce").dropna().tolist()
        arrival = fit_arrival_model(counts, config.arrival_model)

        tower_tickets = tickets[tickets["tower_id"] == tower_id] if tickets is not None else None
        hours: list[float] = []
        if tower_tickets is not None and not tower_tickets.empty:
            hours = (
                pd.to_numeric(tower_tickets["resolution_hours"], errors="coerce")
                .dropna()
                .loc[lambda s: s > 0]
                .tolist()
            )
        if hours:
            effort = fit_effort_distribution(hours, config=config.effort_model)
        else:
            effort = effort_fit_from_profile(config.tower(tower_id), config.noise)

        return cls(config=config, arrival_fit=arrival, effort_fit=effort, tower_id=tower_id, seed=seed)

    # -- simulation ---------------------------------------------------------- #
    def simulate(
        self,
        gross_hours: float | None = None,
        planned_hours: float | None = None,
        runs: int | None = None,
        seed: int | None = None,
    ) -> SimulationResult:
        """Run the vectorised Monte Carlo simulation."""
        n_runs = int(runs if runs is not None else self.config.simulation.runs)
        gross = float(gross_hours if gross_hours is not None else self.config.simulation.gross_hours_per_sprint)
        planned = float(planned_hours if planned_hours is not None else self.config.simulation.planned_hours)
        rng = np.random.default_rng(seed if seed is not None else self.seed)

        counts = np.asarray(self.arrival_fit.rvs(n_runs, rng), dtype=float)
        counts = np.clip(counts, 0, None)

        support = self._sample_total_support(counts, rng)
        net = gross - support

        return SimulationResult(
            runs=n_runs,
            gross_hours=gross,
            planned_hours=planned,
            support_hours=support,
            net_hours=net,
            incident_counts=counts,
            seed=seed if seed is not None else self.seed,
            tower_id=self.tower_id,
            metadata={
                "arrival_family": self.arrival_fit.family,
                "effort_family": self.effort_fit.family,
                "arrival_params": dict(self.arrival_fit.params),
                "effort_params": dict(self.effort_fit.params),
            },
        )

    def _sample_total_support(self, counts: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """Vectorised sampling of ``H_support = sum_i E_i`` for every run."""
        n_runs = counts.size
        max_n = int(counts.max()) if n_runs else 0
        if max_n <= 0:
            return np.zeros(n_runs, dtype=float)

        effort_draws = np.asarray(self.effort_fit.rvs(n_runs * max_n, rng), dtype=float)
        effort_draws = effort_draws.reshape(n_runs, max_n)
        effort_draws = np.clip(effort_draws, self._min_hours, self._max_hours)

        mask = np.arange(max_n)[None, :] < counts[:, None]
        return np.sum(effort_draws * mask, axis=1)

    @property
    def _min_hours(self) -> float:
        return float(self.config.effort_model.min_hours)

    @property
    def _max_hours(self) -> float:
        return float(self.config.effort_model.max_hours)

    # -- planning helpers ---------------------------------------------------- #
    def confidence_for_planned(self, planned_hours: float, **simulate_kwargs: Any) -> float:
        """Convenience: simulate then return confidence for one commitment."""
        return self.simulate(**simulate_kwargs).delivery_confidence(planned_hours)

    def recommend_commitment(
        self,
        target_confidence: float | None = None,
        **simulate_kwargs: Any,
    ) -> dict[str, Any]:
        """Return the largest commitment that clears ``target_confidence``."""
        target = target_confidence if target_confidence is not None else self.config.simulation.confidence_target
        result = self.simulate(**simulate_kwargs)
        safe_hours = result.max_committed_hours(target)
        hours_per_point = self.config.simulation.hours_per_point or 1.0
        return {
            "tower_id": result.tower_id,
            "target_confidence": target,
            "gross_hours": result.gross_hours,
            "recommended_hours": safe_hours,
            "recommended_points": round(safe_hours / hours_per_point, 1),
            "buffer_hours": result.buffer_hours(target),
            "mean_support_tax": result.mean_support_tax,
        }


# --------------------------------------------------------------------------- #
# Profile -> fitted-model constructors
# --------------------------------------------------------------------------- #
def arrival_fit_from_profile(
    profile: TowerProfile,
    noise: NoiseConfig | None = None,
) -> ArrivalFit:
    """Construct an analytic arrival fit from a tower's declared profile.

    The declared ``mean_incidents`` describes the *steady-state* rate. Batch
    platform releases superimpose a burst regime, so the effective moments of
    the resulting two-component mixture are derived analytically here. This
    keeps profile-driven simulations calibrated against telemetry-driven ones.
    """
    base_mu = max(profile.arrival.mean_incidents, 1e-6)
    dispersion = max(profile.arrival.dispersion, 1.0)
    burst = max(profile.arrival.burst_probability, 0.0)
    b = max(profile.arrival.burst_multiplier, 1.0)

    # Moments of the base (non-burst) component.
    if dispersion > 1.2:
        r = base_mu / (dispersion - 1.0)
        base_var = base_mu + base_mu**2 / r
    else:
        base_var = base_mu

    # Mixture: with prob ``burst`` the mean scales by ``b`` (dispersion held).
    mean = base_mu * (1.0 + burst * (b - 1.0))
    var_burst = base_mu * b * (1.0 + b * (dispersion - 1.0)) if dispersion > 1.2 else base_mu * b
    second_moment = (1.0 - burst) * (base_var + base_mu**2) + burst * (var_burst + (base_mu * b) ** 2)
    variance = max(second_moment - mean**2, mean + 1e-9)
    effective_dispersion = variance / mean

    if effective_dispersion > 1.2:
        r_eff = mean / (effective_dispersion - 1.0)
        p_eff = mean / (r_eff + mean)
        return ArrivalFit(
            family="negative_binomial",
            params={"r": r_eff, "p": p_eff},
            dispersion=effective_dispersion,
            mean=mean,
            variance=variance,
            sample_size=0,
            diagnostics={"source": "profile", "burst_probability": burst, "burst_multiplier": b},
        )
    return ArrivalFit(
        family="poisson",
        params={"lam": mean},
        dispersion=effective_dispersion,
        mean=mean,
        variance=mean,
        sample_size=0,
        diagnostics={"source": "profile"},
    )


def effort_fit_from_profile(
    profile: TowerProfile,
    noise: NoiseConfig | None = None,
) -> EffortFit:
    """Construct an analytic Log-Normal effort fit from a tower profile.

    The declared ``lognormal_mu`` describes a baseline ticket; the expected
    per-asset severity and the rare black-swan escalation are folded in as a
    multiplicative shift on ``mu`` (which leaves the shape ``sigma`` intact).
    """
    mu = profile.effort.lognormal_mu
    sigma = profile.effort.lognormal_sigma

    weights = np.array([a.weight for a in profile.assets], dtype=float) if profile.assets else np.array([1.0])
    severities = np.array([a.severity for a in profile.assets], dtype=float) if profile.assets else np.array([1.0])
    if weights.sum() > 0:
        expected_severity = float(np.sum(weights / weights.sum() * severities))
    else:
        expected_severity = 1.0

    p_swan = float(np.clip(noise.black_swan_probability, 0.0, 1.0)) if noise else 0.0
    swan_uplift = (1.0 - p_swan) + p_swan * 3.0  # generator escalates black swans x3

    uplift = max(expected_severity * swan_uplift, 1e-6)
    mu_eff = mu + float(np.log(uplift))
    mean = float(np.exp(mu_eff + sigma**2 / 2.0))
    variance = float((np.exp(sigma**2) - 1.0) * np.exp(2 * mu_eff + sigma**2))
    return EffortFit(
        family="lognormal",
        params={"mu": mu_eff, "sigma": sigma},
        sample_size=0,
        mean=mean,
        variance=variance,
        diagnostics={
            "source": "profile",
            "expected_severity": expected_severity,
            "weibull_shape": profile.effort.weibull_shape,
        },
    )


def simulate_all_towers(
    config: SprintShieldConfig,
    snapshots: pd.DataFrame | None = None,
    tickets: pd.DataFrame | None = None,
    runs: int | None = None,
    target_confidence: float | None = None,
) -> tuple[dict[str, SimulationResult], pd.DataFrame]:
    """Run the simulator for every configured tower.

    Returns the per-tower results plus a consolidated summary table.
    """
    results: dict[str, SimulationResult] = {}
    rows: list[dict[str, Any]] = []
    for tower in config.towers:
        if snapshots is not None and not snapshots.empty:
            simulator = MonteCarloSprintSimulator.from_history(
                config, snapshots, tickets if tickets is not None else pd.DataFrame(), tower.tower_id
            )
        else:
            simulator = MonteCarloSprintSimulator.from_tower(config, tower)
        result = simulator.simulate(
            gross_hours=tower.gross_hours_per_sprint,
            planned_hours=tower.story_points_per_sprint * config.simulation.hours_per_point,
            runs=runs,
        )
        results[tower.tower_id] = result
        summary = result.summary(
            target_confidence if target_confidence is not None else config.simulation.confidence_target
        )
        summary["tower_name"] = tower.name
        rows.append(summary)
    return results, pd.DataFrame(rows)

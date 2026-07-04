"""Tests for the Monte Carlo engine and operational metric calculators."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sprintshield.analytics.arrival_model import fit_arrival_model
from sprintshield.analytics.metrics import (
    asset_metrics_table,
    delivery_predictability_index,
    executive_summary,
    flow_efficiency,
    incident_rate,
    mttr,
    mttr_percentile,
    sprint_metrics_table,
    support_hours_per_engineer,
    support_tax,
    tower_metrics_table,
)
from sprintshield.analytics.monte_carlo import (
    MonteCarloSprintSimulator,
    SimulationResult,
    arrival_fit_from_profile,
    effort_fit_from_profile,
    simulate_all_towers,
)
from sprintshield.config import SprintShieldConfig

RUNS = 2000


# --------------------------------------------------------------------------- #
# Profile -> fitted models
# --------------------------------------------------------------------------- #
def test_arrival_fit_from_profile_captures_burst_inflation(config):
    tower = config.tower("DE")
    fit = arrival_fit_from_profile(tower, config.noise)
    assert fit.family == "negative_binomial"
    # Effective mean must exceed the declared steady-state rate because bursts
    # are folded into the mixture.
    assert fit.mean > tower.arrival.mean_incidents
    assert fit.variance > fit.mean
    assert fit.diagnostics["source"] == "profile"


def test_arrival_fit_from_profile_without_noise():
    from sprintshield.config import TowerArrivalProfile, TowerEffortProfile, TowerProfile

    tower = TowerProfile(
        tower_id="X",
        name="X",
        headcount=1,
        story_points_per_sprint=1,
        gross_hours_per_sprint=10.0,
        arrival=TowerArrivalProfile(2.0, 1.0, 0.0, 1.0),
        effort=TowerEffortProfile(1.0, 0.5, 1.2, 5.0),
        assets=(),
    )
    fit = arrival_fit_from_profile(tower)
    assert fit.family == "poisson"
    assert fit.mean == pytest.approx(2.0)


def test_effort_fit_from_profile_folds_in_severity(config):
    tower = config.tower("DS")
    fit = effort_fit_from_profile(tower, config.noise)
    assert fit.family == "lognormal"
    assert fit.diagnostics["expected_severity"] > 1.0
    assert fit.mean > np.exp(tower.effort.lognormal_mu)


def test_effort_fit_from_profile_without_assets_or_noise():
    from sprintshield.config import TowerArrivalProfile, TowerEffortProfile, TowerProfile

    tower = TowerProfile(
        tower_id="Y",
        name="Y",
        headcount=1,
        story_points_per_sprint=1,
        gross_hours_per_sprint=10.0,
        arrival=TowerArrivalProfile(2.0, 1.0, 0.1, 2.0),
        effort=TowerEffortProfile(1.0, 0.5, 1.2, 5.0),
        assets=(),
    )
    fit = effort_fit_from_profile(tower)
    assert fit.family == "lognormal"
    assert fit.diagnostics["expected_severity"] == 1.0


# --------------------------------------------------------------------------- #
# Simulator construction
# --------------------------------------------------------------------------- #
def test_from_tower_builds_a_simulator(config):
    sim = MonteCarloSprintSimulator.from_tower(config, "DE")
    assert sim.tower_id == "DE"
    assert sim.arrival_fit.family in {"poisson", "negative_binomial"}
    assert sim.effort_fit.family == "lognormal"


def test_from_tower_accepts_a_profile_object(config):
    sim = MonteCarloSprintSimulator.from_tower(config, config.tower("DA"))
    assert sim.tower_id == "DA"


def test_from_history_fits_from_telemetry(config, snapshots, tickets):
    sim = MonteCarloSprintSimulator.from_history(config, snapshots, tickets, "DE")
    assert sim.tower_id == "DE"
    assert sim.arrival_fit.sample_size > 0
    assert sim.effort_fit.sample_size > 0


def test_from_history_falls_back_when_no_tickets(config, snapshots):
    empty = snapshots.iloc[0:0]
    sim = MonteCarloSprintSimulator.from_history(config, snapshots, empty, "DE")
    assert sim.effort_fit.family == "lognormal"


def test_from_history_raises_on_empty_snapshots(config, tickets):
    with pytest.raises(ValueError):
        MonteCarloSprintSimulator.from_history(config, pd.DataFrame(), tickets, "DE")


def test_from_history_raises_for_unknown_tower(config, snapshots, tickets):
    with pytest.raises(ValueError):
        MonteCarloSprintSimulator.from_history(config, snapshots, tickets, "ZZ")


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #
@pytest.fixture()
def de_result(config) -> SimulationResult:
    sim = MonteCarloSprintSimulator.from_tower(config, "DE", seed=42)
    return sim.simulate(gross_hours=320.0, planned_hours=240.0, runs=RUNS)


def test_simulation_shapes_and_ranges(de_result):
    assert de_result.runs == RUNS
    assert de_result.support_hours.shape == (RUNS,)
    assert de_result.net_hours.shape == (RUNS,)
    assert de_result.incident_counts.shape == (RUNS,)
    assert (de_result.support_hours >= 0).all()
    assert (de_result.incident_counts >= 0).all()
    assert np.allclose(de_result.net_hours, de_result.gross_hours - de_result.support_hours)


def test_simulation_is_reproducible_with_a_seed(config):
    a = MonteCarloSprintSimulator.from_tower(config, "DE", seed=7).simulate(runs=500)
    b = MonteCarloSprintSimulator.from_tower(config, "DE", seed=7).simulate(runs=500)
    assert np.allclose(a.support_hours, b.support_hours)


def test_simulation_differs_across_seeds(config):
    a = MonteCarloSprintSimulator.from_tower(config, "DE", seed=1).simulate(runs=500)
    b = MonteCarloSprintSimulator.from_tower(config, "DE", seed=2).simulate(runs=500)
    assert not np.allclose(a.support_hours, b.support_hours)


def test_simulate_honours_run_override(config):
    sim = MonteCarloSprintSimulator.from_tower(config, "DE", seed=11)
    assert sim.simulate(runs=137).runs == 137


def test_mean_statistics_are_consistent(de_result):
    assert de_result.mean_support_hours == pytest.approx(float(np.mean(de_result.support_hours)))
    assert de_result.mean_net_hours == pytest.approx(de_result.gross_hours - de_result.mean_support_hours)
    assert 0 < de_result.mean_support_tax < 1
    assert de_result.mean_incidents > 0


def test_percentile_ordering(de_result):
    p50 = de_result.percentile(50)
    p85 = de_result.percentile(85)
    p95 = de_result.percentile(95)
    assert p50 <= p85 <= p95
    # Net capacity is gross minus support, so its percentiles mirror around gross.
    assert de_result.percentile(50, of="net") == pytest.approx(de_result.gross_hours - p50)


def test_delivery_confidence_is_monotonic_in_commitment(de_result):
    low = de_result.delivery_confidence(0.0)
    mid = de_result.delivery_confidence(de_result.gross_hours * 0.5)
    high = de_result.delivery_confidence(de_result.gross_hours * 2)
    # Committing nothing is almost always deliverable; committing double the
    # gross capacity never is.
    assert low >= 0.99
    assert high == 0.0
    assert low >= mid >= high


def test_delivery_confidence_and_risk_complement(de_result):
    assert de_result.delivery_confidence(240.0) + de_result.delivery_risk(240.0) == pytest.approx(1.0)


def test_confidence_curve_shape(de_result):
    curve = de_result.confidence_curve()
    assert list(curve.columns) == ["planned_hours", "delivery_confidence", "delivery_risk"]
    assert curve["delivery_confidence"].is_monotonic_decreasing
    assert ((curve["delivery_confidence"] + curve["delivery_risk"]).round(9) == 1.0).all()


def test_confidence_curve_accepts_custom_grid(de_result):
    curve = de_result.confidence_curve([0, 100, 200, 300])
    assert len(curve) == 4


def test_buffer_and_max_committed(de_result):
    buffer = de_result.buffer_hours(0.85)
    safe = de_result.max_committed_hours(0.85)
    assert buffer > 0
    assert safe < de_result.gross_hours
    # The safe commitment should itself clear the target confidence.
    assert de_result.delivery_confidence(safe) >= 0.80


def test_summary_payload(de_result):
    summary = de_result.summary()
    for key in (
        "runs",
        "gross_hours",
        "mean_support_hours",
        "mean_support_tax",
        "p85_support_hours",
        "delivery_confidence",
        "buffer_hours_at_target",
        "max_committed_hours_at_target",
    ):
        assert key in summary
    assert summary["runs"] == RUNS


def test_simulation_metadata(de_result):
    assert de_result.metadata["arrival_family"] in {"poisson", "negative_binomial"}
    assert de_result.metadata["effort_family"] == "lognormal"


def test_zero_demand_produces_zero_support():
    from sprintshield.analytics.arrival_model import ArrivalFit
    from sprintshield.analytics.effort_distribution import EffortFit
    from sprintshield.config import load_config

    config = load_config()
    arrival = ArrivalFit(family="poisson", params={"lam": 0.0}, dispersion=0.0, mean=0.0, variance=0.0, sample_size=0)
    effort = EffortFit(family="lognormal", params={"mu": 1.0, "sigma": 0.5}, sample_size=0, mean=3.0, variance=5.0)
    sim = MonteCarloSprintSimulator(config, arrival, effort, tower_id="TEST", seed=0)
    result = sim.simulate(gross_hours=100.0, planned_hours=50.0, runs=200)
    assert (result.support_hours == 0).all()
    assert result.delivery_confidence(50.0) == 1.0


def test_effort_samples_respect_config_clamps(config):
    from sprintshield.analytics.arrival_model import ArrivalFit
    from sprintshield.analytics.effort_distribution import EffortFit

    arrival = ArrivalFit(family="poisson", params={"lam": 10.0}, dispersion=1.0, mean=10.0, variance=10.0, sample_size=0)
    # An absurdly wide Log-Normal must still be clamped by the config bounds.
    effort = EffortFit(family="lognormal", params={"mu": 10.0, "sigma": 3.0}, sample_size=0, mean=1e4, variance=1e8)
    sim = MonteCarloSprintSimulator(config, arrival, effort, seed=1)
    result = sim.simulate(gross_hours=320.0, runs=300)
    assert result.support_hours.max() <= config.effort_model.max_hours * 300


def test_confidence_for_planned_helper(config):
    sim = MonteCarloSprintSimulator.from_tower(config, "DE", seed=3)
    value = sim.confidence_for_planned(200.0, runs=500)
    assert 0.0 <= value <= 1.0


def test_recommend_commitment(config):
    sim = MonteCarloSprintSimulator.from_tower(config, "DE", seed=3)
    recommendation = sim.recommend_commitment(runs=1000)
    assert recommendation["recommended_hours"] > 0
    assert recommendation["recommended_points"] > 0
    assert recommendation["target_confidence"] == config.simulation.confidence_target
    assert recommendation["buffer_hours"] > 0


def test_simulate_all_towers(config, snapshots, tickets):
    results, summary = simulate_all_towers(config, snapshots, tickets, runs=500)
    assert set(results) == set(config.tower_ids())
    assert len(summary) == len(config.towers)
    assert "max_committed_hours_at_target" in summary.columns
    assert (summary["delivery_confidence"] >= 0).all()


def test_simulate_all_towers_without_history(config):
    results, summary = simulate_all_towers(config, None, None, runs=400)
    assert len(results) == len(config.towers)
    assert not summary.empty


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def test_support_tax_accepts_frame_and_series(snapshots):
    from_frame = support_tax(snapshots)
    assert isinstance(from_frame, pd.Series)
    scalar = support_tax(snapshots["unplanned_support_hours"], snapshots["gross_hours"])
    assert 0 < scalar < 1


def test_flow_efficiency_frame_and_series(snapshots):
    assert isinstance(flow_efficiency(snapshots), pd.Series)
    scalar = flow_efficiency(snapshots["planned_feature_hours"], snapshots["gross_hours"])
    assert 0 <= scalar <= 1


def test_ratio_helpers_handle_zero_gross():
    result = support_tax([10.0], [0.0])
    assert np.isnan(result)


def test_delivery_predictability_index_bounds():
    steady = delivery_predictability_index([80, 81, 79, 80])
    volatile = delivery_predictability_index([10, 90, 20, 80])
    assert steady > volatile
    assert 0.0 <= volatile <= 1.0
    blended = delivery_predictability_index([80, 81, 79, 80], [100, 100, 100, 100])
    assert 0.0 <= blended <= 1.0


def test_delivery_predictability_index_edge_cases():
    assert np.isnan(delivery_predictability_index([]))
    assert delivery_predictability_index([0, 0, 0]) == 0.0
    assert delivery_predictability_index([50]) == 1.0


def test_mttr_variants(tickets):
    assert mttr(tickets) > 0
    assert mttr(tickets["resolution_hours"].dropna()) > 0
    assert mttr_percentile(tickets, 0.9) >= mttr(tickets)


def test_mttr_edge_cases():
    assert np.isnan(mttr(pd.DataFrame({"resolution_hours": []})))
    assert np.isnan(mttr_percentile(pd.DataFrame({"resolution_hours": []})))


def test_incident_rate_and_hours_per_engineer(snapshots):
    assert incident_rate(snapshots) > 0
    assert support_hours_per_engineer(snapshots, 14) > 0
    assert np.isnan(support_hours_per_engineer(snapshots, 0))
    assert np.isnan(incident_rate(pd.DataFrame()))


def test_sprint_metrics_table(snapshots):
    frame = sprint_metrics_table(snapshots)
    assert {"support_tax", "flow_efficiency", "commitment_reliability", "scope_change_rate"} <= set(frame.columns)
    assert len(frame) == len(snapshots)


def test_sprint_metrics_table_empty():
    assert sprint_metrics_table(pd.DataFrame()).empty


def test_tower_metrics_table(snapshots, tickets):
    table = tower_metrics_table(snapshots, tickets)
    assert len(table) == 4
    assert {"tower_id", "support_tax_pct", "mttr_hours", "delivery_predictability"} <= set(table.columns)
    # Sorted by support tax descending.
    assert table["support_tax_pct"].is_monotonic_decreasing


def test_tower_metrics_table_without_tickets(snapshots):
    table = tower_metrics_table(snapshots, None)
    assert len(table) == 4
    assert table["mttr_hours"].isna().all()


def test_tower_metrics_table_empty():
    assert tower_metrics_table(pd.DataFrame()).empty


def test_asset_metrics_table(tickets):
    table = asset_metrics_table(tickets)
    assert not table.empty
    assert {"tower_id", "asset", "hours_burned", "mttr_hours", "open_incidents"} <= set(table.columns)
    assert table["hours_burned"].is_monotonic_decreasing


def test_asset_metrics_table_empty():
    assert asset_metrics_table(pd.DataFrame()).empty


def test_executive_summary(snapshots, tickets, config):
    names = {t.tower_id: t.name for t in config.towers}
    summary = executive_summary(snapshots, tickets, names)
    assert summary["towers"] == 4
    assert summary["sprints"] == snapshots["sprint_id"].nunique()
    assert 0 < summary["overall_support_tax_pct"] < 100
    assert summary["mttr_hours"] > 0
    assert summary["tower_names"]["DE"] == "Data Engineering"


def test_executive_summary_without_tickets(snapshots):
    summary = executive_summary(snapshots)
    assert np.isnan(summary["mttr_hours"])


def test_executive_summary_empty():
    assert executive_summary(pd.DataFrame()) == {}

"""Tests for stochastic demand and resolution-effort modelling."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sprintshield.analytics.arrival_model import (
    ArrivalFit,
    arrival_summary,
    chi_square_goodness_of_fit,
    compare_arrival_models,
    dispersion_index,
    fit_arrival_model,
    fit_negative_binomial,
    fit_poisson,
    select_arrival_family,
)
from sprintshield.analytics.effort_distribution import (
    EffortFit,
    compare_effort_models,
    effort_fits_to_frame,
    fit_effort_by_group,
    fit_effort_distribution,
    fit_lognormal,
    fit_weibull,
)
from sprintshield.config import ArrivalModelConfig


# --------------------------------------------------------------------------- #
# Dispersion
# --------------------------------------------------------------------------- #
def test_dispersion_index_of_poisson_like_sample_is_near_one():
    rng = np.random.default_rng(0)
    counts = rng.poisson(9, size=4000)
    assert dispersion_index(counts) == pytest.approx(1.0, abs=0.12)


def test_dispersion_index_of_overdispersed_sample_exceeds_one():
    rng = np.random.default_rng(1)
    # Mixture of two Poisson rates produces variance >> mean.
    counts = np.concatenate([rng.poisson(3, 500), rng.poisson(25, 500)])
    assert dispersion_index(counts) > 1.2


def test_dispersion_index_edge_cases():
    assert np.isnan(dispersion_index([]))
    assert np.isnan(dispersion_index([0, 0, 0]))
    assert dispersion_index([5]) == 0.0


def test_select_arrival_family_thresholds():
    cfg = ArrivalModelConfig(poisson_upper_bound=1.2)
    assert select_arrival_family(0.98, cfg) == "poisson"
    assert select_arrival_family(1.55, cfg) == "negative_binomial"
    assert select_arrival_family(float("nan"), cfg) == "poisson"


# --------------------------------------------------------------------------- #
# Poisson fitting
# --------------------------------------------------------------------------- #
def test_fit_poisson_recovers_the_rate():
    rng = np.random.default_rng(2)
    counts = rng.poisson(7.5, size=5000)
    fit = fit_poisson(counts)
    assert fit.family == "poisson"
    assert fit.params["lam"] == pytest.approx(7.5, abs=0.25)
    assert fit.variance == pytest.approx(fit.mean)
    assert np.isfinite(fit.aic) and np.isfinite(fit.bic)


def test_fit_poisson_rejects_empty():
    with pytest.raises(ValueError):
        fit_poisson([])


def test_poisson_pmf_sums_to_one_and_rvs_shape():
    fit = fit_poisson([4] * 50)
    pmf = fit.pmf(np.arange(0, 40))
    assert pmf.sum() == pytest.approx(1.0, abs=1e-6)
    assert fit.pmf(4)[0] > fit.pmf(20)[0]
    draws = fit.rvs(100, np.random.default_rng(0))
    assert draws.shape == (100,)
    assert (draws >= 0).all()


# --------------------------------------------------------------------------- #
# Negative Binomial fitting
# --------------------------------------------------------------------------- #
def test_fit_negative_binomial_matches_observed_moments():
    rng = np.random.default_rng(3)
    counts = np.concatenate([rng.poisson(2, 400), rng.poisson(18, 400)])
    fit = fit_negative_binomial(counts)
    assert fit.family == "negative_binomial"
    assert fit.params["r"] > 0
    assert 0 < fit.params["p"] < 1
    assert fit.mean == pytest.approx(counts.mean(), rel=0.25)
    assert fit.variance > fit.mean  # captures the overdispersion


def test_fit_negative_binomial_rejects_empty():
    with pytest.raises(ValueError):
        fit_negative_binomial([])


def test_negative_binomial_pmf_and_rvs():
    rng = np.random.default_rng(4)
    counts = np.concatenate([rng.poisson(3, 300), rng.poisson(20, 300)])
    fit = fit_negative_binomial(counts)
    pmf = fit.pmf(np.arange(0, 400))
    assert pmf.sum() == pytest.approx(1.0, abs=1e-6)
    draws = fit.rvs(200, np.random.default_rng(1))
    assert draws.shape == (200,)
    assert draws.mean() == pytest.approx(counts.mean(), rel=0.35)


def test_negative_binomial_on_underdispersed_sample_stays_valid():
    fit = fit_negative_binomial([5] * 30)
    assert fit.params["r"] > 0
    assert 0 < fit.params["p"] < 1


def test_arrival_fit_as_dict():
    fit = fit_poisson([1, 2, 3, 4])
    payload = fit.as_dict()
    assert payload["family"] == "poisson"
    assert "param_lam" in payload
    assert payload["sample_size"] == 4


def test_arrival_fit_std():
    fit = fit_poisson([4] * 20)
    assert fit.std == pytest.approx(np.sqrt(4.0))


# --------------------------------------------------------------------------- #
# Model selection & comparison
# --------------------------------------------------------------------------- #
def test_fit_arrival_model_selects_negbinom_for_bursty_counts():
    rng = np.random.default_rng(5)
    counts = np.concatenate([rng.poisson(2, 300), rng.poisson(24, 300)])
    assert fit_arrival_model(counts).family == "negative_binomial"


def test_fit_arrival_model_selects_poisson_for_steady_counts():
    rng = np.random.default_rng(6)
    assert fit_arrival_model(rng.poisson(6, 3000)).family == "poisson"


def test_fit_arrival_model_rejects_empty():
    with pytest.raises(ValueError):
        fit_arrival_model([])


def test_compare_arrival_models_ranks_by_aic():
    rng = np.random.default_rng(7)
    counts = np.concatenate([rng.poisson(2, 300), rng.poisson(20, 300)])
    comparison = compare_arrival_models(counts)
    assert set(comparison["family"]) == {"poisson", "negative_binomial"}
    assert comparison.loc[0, "preferred"] is True or bool(comparison.loc[0, "preferred"]) is True
    assert comparison["aic"].is_monotonic_increasing
    assert comparison.loc[0, "family"] == "negative_binomial"


def test_compare_arrival_models_rejects_empty():
    with pytest.raises(ValueError):
        compare_arrival_models([])


def test_chi_square_goodness_of_fit():
    rng = np.random.default_rng(8)
    counts = rng.poisson(8, size=2000)
    fit = fit_poisson(counts)
    result = chi_square_goodness_of_fit(counts, fit)
    assert "chi_square" in result and "p_value" in result
    assert result["dof"] >= 1
    # A correctly specified Poisson should not be rejected.
    assert result["p_value"] > 0.01


def test_chi_square_rejects_empty():
    fit = fit_poisson([1, 2, 3])
    with pytest.raises(ValueError):
        chi_square_goodness_of_fit([], fit)


def test_arrival_summary_bundle():
    rng = np.random.default_rng(9)
    summary = arrival_summary(rng.poisson(5, 400))
    assert summary["family"] in {"poisson", "negative_binomial"}
    assert isinstance(summary["fit"], ArrivalFit)
    assert "p_value" in summary["goodness_of_fit"]


# --------------------------------------------------------------------------- #
# Effort distributions
# --------------------------------------------------------------------------- #
def test_fit_lognormal_recovers_parameters():
    rng = np.random.default_rng(10)
    hours = rng.lognormal(mean=1.4, sigma=0.8, size=4000)
    fit = fit_lognormal(hours)
    assert fit.family == "lognormal"
    assert fit.params["mu"] == pytest.approx(1.4, abs=0.06)
    assert fit.params["sigma"] == pytest.approx(0.8, abs=0.06)
    assert fit.mean > 0 and fit.std > 0


def test_fit_weibull_recovers_parameters():
    rng = np.random.default_rng(11)
    hours = rng.weibull(1.5, size=4000) * 20.0
    fit = fit_weibull(hours)
    assert fit.family == "weibull"
    assert fit.params["shape"] == pytest.approx(1.5, abs=0.1)
    assert fit.params["scale"] == pytest.approx(20.0, rel=0.12)


def test_effort_fitters_reject_empty():
    with pytest.raises(ValueError):
        fit_lognormal([])
    with pytest.raises(ValueError):
        fit_weibull([])


def test_effort_fit_pdf_cdf_rvs():
    rng = np.random.default_rng(12)
    fit = fit_lognormal(rng.lognormal(1.0, 0.7, 500))
    assert fit.pdf(5.0)[0] > 0
    assert 0 <= fit.cdf(5.0)[0] <= 1
    draws = fit.rvs(50, np.random.default_rng(0))
    assert draws.shape == (50,)
    assert (draws > 0).all()


def test_weibull_effort_fit_pdf_cdf_rvs():
    rng = np.random.default_rng(13)
    fit = fit_weibull(rng.weibull(1.3, 800) * 12)
    assert fit.pdf(6.0)[0] > 0
    assert 0 <= fit.cdf(6.0)[0] <= 1
    assert fit.rvs(10, np.random.default_rng(0)).shape == (10,)


def test_effort_fit_as_dict_and_std():
    fit = fit_lognormal([1.0, 2.0, 4.0, 8.0, 16.0])
    payload = fit.as_dict()
    assert payload["family"] == "lognormal"
    assert "param_mu" in payload and "param_sigma" in payload
    assert fit.std == pytest.approx(np.sqrt(fit.variance))


def test_effort_fit_unknown_family_pdf_raises():
    fit = EffortFit(family="gamma", params={}, sample_size=1, mean=1.0, variance=1.0)
    with pytest.raises(ValueError):
        fit.pdf(1.0)


def test_fit_effort_distribution_picks_best_by_aic():
    rng = np.random.default_rng(14)
    hours = rng.lognormal(1.2, 0.9, 3000)
    fit = fit_effort_distribution(hours)
    assert fit.family in {"lognormal", "weibull"}
    assert np.isfinite(fit.aic)


def test_fit_effort_distribution_small_sample_defaults_to_lognormal():
    fit = fit_effort_distribution([1.0, 2.0, 3.0])
    assert fit.family == "lognormal"


def test_fit_effort_distribution_honours_candidate_list():
    rng = np.random.default_rng(15)
    hours = rng.weibull(1.4, 2000) * 15
    fit = fit_effort_distribution(hours, candidates=["weibull"])
    assert fit.family == "weibull"


def test_fit_effort_distribution_rejects_empty():
    with pytest.raises(ValueError):
        fit_effort_distribution([])


def test_compare_effort_models():
    rng = np.random.default_rng(16)
    frame = compare_effort_models(rng.lognormal(1.0, 0.8, 1500))
    assert set(frame["family"]) == {"lognormal", "weibull"}
    assert frame["aic"].is_monotonic_increasing
    assert bool(frame.loc[0, "preferred"]) is True


def test_compare_effort_models_rejects_empty():
    with pytest.raises(ValueError):
        compare_effort_models([])


# --------------------------------------------------------------------------- #
# Panel fitting
# --------------------------------------------------------------------------- #
def test_fit_effort_by_group(tickets):
    fits = fit_effort_by_group(tickets, "tower_id")
    assert set(fits) == {"DE", "DA", "DS", "PMO"}
    assert all(isinstance(f, EffortFit) for f in fits.values())


def test_fit_effort_by_group_on_empty_frame():
    assert fit_effort_by_group(pd.DataFrame()) == {}


def test_effort_fits_to_frame(tickets):
    fits = fit_effort_by_group(tickets, "tower_id")
    frame = effort_fits_to_frame(fits, key_name="tower_id")
    assert "tower_id" in frame.columns
    assert "family" in frame.columns
    assert len(frame) == len(fits)

"""Stochastic arrival-demand modelling.

Unplanned production support does not arrive like clockwork. When it is
steady-state it is well described by a Poisson process; when batch platform
releases dump correlated incidents into the same sprint it becomes
*overdispersed* and the Negative Binomial is the correct family.

The **dispersion index**

    D = sigma^2 / mu

decides which. ``D ~ 1`` -> Poisson. ``D > 1.2`` -> Negative Binomial.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd
from scipy import optimize, stats
from scipy.special import gammaln

from sprintshield.config import ArrivalModelConfig

# Dispersion thresholds used when no explicit config is supplied.
POISSON_UPPER_BOUND = 1.20
POISSON_LOWER_BOUND = 0.85


@dataclass(frozen=True)
class ArrivalFit:
    """A fitted arrival distribution for one demand stream."""

    family: str  # "poisson" | "negative_binomial"
    params: Mapping[str, float]
    dispersion: float
    mean: float
    variance: float
    sample_size: int
    log_likelihood: float = float("nan")
    aic: float = float("nan")
    bic: float = float("nan")
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    # -- moments ------------------------------------------------------------- #
    @property
    def std(self) -> float:
        return float(np.sqrt(self.variance))

    def pmf(self, k: Iterable[int] | int) -> np.ndarray:
        """Probability mass function evaluated at ``k``."""
        k_arr = np.atleast_1d(np.asarray(k, dtype=float))
        if self.family == "poisson":
            return stats.poisson.pmf(k_arr, self.params["lam"])
        r = self.params["r"]
        p = self.params["p"]
        return np.exp(
            gammaln(k_arr + r) - gammaln(r) - gammaln(k_arr + 1) + r * np.log(1 - p) + k_arr * np.log(p)
        )

    def rvs(self, size: int, rng: np.random.Generator | None = None) -> np.ndarray:
        """Draw ``size`` random incident counts from the fitted distribution."""
        generator = rng if rng is not None else np.random.default_rng()
        if self.family == "poisson":
            return generator.poisson(self.params["lam"], size=size)
        # numpy parameterises by the *failure* probability q = 1 - p.
        r = self.params["r"]
        p = self.params["p"]
        return generator.negative_binomial(r, 1.0 - p, size=size)

    def as_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "mean": self.mean,
            "variance": self.variance,
            "dispersion": self.dispersion,
            "sample_size": self.sample_size,
            **{f"param_{k}": v for k, v in self.params.items()},
            "log_likelihood": self.log_likelihood,
            "aic": self.aic,
            "bic": self.bic,
        }


# --------------------------------------------------------------------------- #
# Dispersion
# --------------------------------------------------------------------------- #
def dispersion_index(counts: Iterable[float]) -> float:
    """Return ``D = variance / mean`` for a sequence of incident counts.

    A value near 1 indicates a Poisson process; values above 1.2 indicate the
    overdispersion expected from clustered release-driven incidents.
    """
    arr = np.asarray(list(counts), dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return float("nan")
    mu = float(arr.mean())
    if mu <= 0:
        return float("nan")
    var = float(arr.var(ddof=1)) if arr.size > 1 else 0.0
    return var / mu


def select_arrival_family(
    dispersion: float,
    config: ArrivalModelConfig | None = None,
) -> str:
    """Choose the arrival family implied by the dispersion index."""
    upper = config.poisson_upper_bound if config else POISSON_UPPER_BOUND
    if np.isnan(dispersion):
        return "poisson"
    return "negative_binomial" if dispersion > upper else "poisson"


# --------------------------------------------------------------------------- #
# Fitting
# --------------------------------------------------------------------------- #
def fit_poisson(counts: Iterable[float]) -> ArrivalFit:
    """Maximum-likelihood Poisson fit (closed form: ``lambda = mean``)."""
    arr = np.asarray(list(counts), dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        raise ValueError("Cannot fit a Poisson distribution to an empty sample")
    lam = float(arr.mean())
    log_lik = float(np.sum(stats.poisson.logpmf(arr, lam)))
    aic = 2 * 1 - 2 * log_lik
    bic = 1 * np.log(arr.size) - 2 * log_lik
    return ArrivalFit(
        family="poisson",
        params={"lam": lam},
        dispersion=dispersion_index(arr),
        mean=lam,
        variance=lam,
        sample_size=int(arr.size),
        log_likelihood=log_lik,
        aic=float(aic),
        bic=float(bic),
    )


def _negbinom_loglik(theta: np.ndarray, counts: np.ndarray) -> float:
    r, p = float(theta[0]), float(theta[1])
    if r <= 0 or not (0.0 < p < 1.0):
        return float("inf")
    ll = np.sum(gammaln(counts + r) - gammaln(r) - gammaln(counts + 1) + r * np.log(1 - p) + counts * np.log(p))
    return float(-ll)


def fit_negative_binomial(
    counts: Iterable[float],
    config: ArrivalModelConfig | None = None,
) -> ArrivalFit:
    """Fit a Negative Binomial to overdispersed counts.

    Seeded with the method-of-moments estimate then refined by maximum
    likelihood; falls back to the closed-form estimate if the optimiser fails.
    """
    arr = np.asarray(list(counts), dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        raise ValueError("Cannot fit a Negative Binomial to an empty sample")

    mu = float(arr.mean())
    var = float(arr.var(ddof=1)) if arr.size > 1 else mu
    if var <= mu:
        # No overdispersion in the sample: push r to a large value so the
        # distribution approaches the Poisson limit.
        r_mom = max(mu * 10.0, 1.0)
    else:
        r_mom = mu * mu / (var - mu)
    p_mom = mu / (r_mom + mu)

    max_iter = config.negbinom_max_iter if config else 500
    tol = config.negbinom_tol if config else 1.0e-6

    fitted_r, fitted_p = r_mom, p_mom
    try:
        result = optimize.minimize(
            _negbinom_loglik,
            x0=np.array([max(r_mom, 1e-3), min(max(p_mom, 1e-4), 1 - 1e-4)]),
            args=(arr,),
            method="Nelder-Mead",
            options={"maxiter": max_iter, "xatol": tol, "fatol": tol},
        )
        if result.success or np.isfinite(result.fun):
            cand_r, cand_p = float(result.x[0]), float(result.x[1])
            if cand_r > 0 and 0 < cand_p < 1:
                fitted_r, fitted_p = cand_r, cand_p
    except Exception:  # pragma: no cover - optimisation is best-effort
        pass

    r, p = fitted_r, fitted_p
    fitted_var = r * p / (1 - p) ** 2
    fitted_mean = r * p / (1 - p)
    ll = -_negbinom_loglik(np.array([r, p]), arr)
    aic = 2 * 2 - 2 * ll
    bic = 2 * np.log(arr.size) - 2 * ll

    return ArrivalFit(
        family="negative_binomial",
        params={"r": r, "p": p},
        dispersion=dispersion_index(arr),
        mean=fitted_mean,
        variance=fitted_var,
        sample_size=int(arr.size),
        log_likelihood=float(ll),
        aic=float(aic),
        bic=float(bic),
        diagnostics={"r_method_of_moments": r_mom, "p_method_of_moments": p_mom},
    )


def fit_arrival_model(
    counts: Iterable[float],
    config: ArrivalModelConfig | None = None,
) -> ArrivalFit:
    """Fit the arrival family the dispersion index selects."""
    arr = np.asarray(list(counts), dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        raise ValueError("Cannot fit an arrival model to an empty sample")
    family = select_arrival_family(dispersion_index(arr), config)
    return fit_negative_binomial(arr, config) if family == "negative_binomial" else fit_poisson(arr)


def compare_arrival_models(
    counts: Iterable[float],
    config: ArrivalModelConfig | None = None,
) -> pd.DataFrame:
    """Fit both families and compare them on log-likelihood, AIC and BIC."""
    arr = np.asarray(list(counts), dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        raise ValueError("Cannot compare arrival models on an empty sample")

    poisson = fit_poisson(arr)
    negbinom = fit_negative_binomial(arr, config)
    rows = []
    for fit in (poisson, negbinom):
        rows.append(
            {
                "family": fit.family,
                "log_likelihood": fit.log_likelihood,
                "aic": fit.aic,
                "bic": fit.bic,
                "fitted_mean": fit.mean,
                "fitted_variance": fit.variance,
                "observed_mean": float(arr.mean()),
                "observed_variance": float(arr.var(ddof=1)) if arr.size > 1 else 0.0,
            }
        )
    frame = pd.DataFrame(rows).sort_values("aic").reset_index(drop=True)
    frame["preferred"] = False
    if not frame.empty:
        frame.loc[0, "preferred"] = True
    return frame


def chi_square_goodness_of_fit(
    counts: Iterable[float],
    fit: ArrivalFit,
    min_expected: float = 5.0,
) -> dict[str, float]:
    """Pearson chi-square goodness-of-fit for a fitted arrival distribution.

    Bins are ``0, 1, ..., K`` with the two low-expectation tails merged until
    every cell holds at least ``min_expected`` observations, which is the
    condition under which the chi-square approximation is valid.
    """
    arr = np.asarray(list(counts), dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        raise ValueError("Cannot test goodness of fit on an empty sample")

    n = arr.size
    k_max = int(max(arr.max(), 1))
    observed, _ = np.histogram(arr, bins=np.arange(0, k_max + 2))
    expected = np.asarray(fit.pmf(np.arange(0, k_max + 1)), dtype=float) * n

    # Merge low-expectation cells from the right, then from the left.
    while len(expected) > 2 and expected[-1] < min_expected:
        expected[-2] += expected[-1]
        observed[-2] += observed[-1]
        expected, observed = expected[:-1], observed[:-1]
    while len(expected) > 2 and expected[0] < min_expected:
        expected[1] += expected[0]
        observed[1] += observed[0]
        expected, observed = expected[1:], observed[1:]

    expected = np.where(expected <= 0, 1e-12, expected)
    stat = float(np.sum((observed - expected) ** 2 / expected))
    dof = max(len(observed) - 1 - len(fit.params), 1)
    p_value = float(1.0 - stats.chi2.cdf(stat, dof))
    return {
        "chi_square": stat,
        "dof": float(dof),
        "p_value": p_value,
        "cells": float(len(observed)),
    }


def arrival_summary(counts: Iterable[float], config: ArrivalModelConfig | None = None) -> dict[str, Any]:
    """Convenience bundle: dispersion, selected family, fit and GOF test."""
    fit = fit_arrival_model(counts, config)
    gof = chi_square_goodness_of_fit(counts, fit)
    return {"fit": fit, "dispersion": fit.dispersion, "family": fit.family, "goodness_of_fit": gof}

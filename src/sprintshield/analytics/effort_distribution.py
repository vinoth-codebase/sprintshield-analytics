"""Resolution-effort distribution fitting.

Support tickets do not resolve in average time. Most close in a couple of
hours; a stubborn few consume entire days. That right-skew is captured by the
**Log-Normal** and **Weibull** families, fitted here per tower and per business
asset so the Monte Carlo engine samples from the real shape of the tail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd
from scipy import stats
from scipy.special import gammaln

from sprintshield.config import EffortModelConfig

SUPPORTED_FAMILIES = ("lognormal", "weibull")


@dataclass(frozen=True)
class EffortFit:
    """A fitted resolution-effort distribution."""

    family: str
    params: Mapping[str, float]
    sample_size: int
    mean: float
    variance: float
    log_likelihood: float = float("nan")
    aic: float = float("nan")
    bic: float = float("nan")
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def std(self) -> float:
        return float(np.sqrt(self.variance))

    def _scipy_args(self) -> tuple[float, ...]:
        if self.family == "lognormal":
            return (self.params["sigma"], 0.0, float(np.exp(self.params["mu"])))
        if self.family == "weibull":
            return (self.params["shape"], 0.0, self.params["scale"])
        raise ValueError(f"Unsupported family: {self.family!r}")

    def pdf(self, x: Iterable[float] | float) -> np.ndarray:
        """Probability density evaluated at ``x``."""
        arr = np.atleast_1d(np.asarray(x, dtype=float))
        dist = stats.lognorm if self.family == "lognormal" else stats.weibull_min
        return dist.pdf(arr, *self._scipy_args())

    def cdf(self, x: Iterable[float] | float) -> np.ndarray:
        """Cumulative distribution evaluated at ``x``."""
        arr = np.atleast_1d(np.asarray(x, dtype=float))
        dist = stats.lognorm if self.family == "lognormal" else stats.weibull_min
        return dist.cdf(arr, *self._scipy_args())

    def rvs(self, size: int, rng: np.random.Generator | None = None) -> np.ndarray:
        """Draw ``size`` resolution-hour samples from the fitted distribution."""
        generator = rng if rng is not None else np.random.default_rng()
        if self.family == "lognormal":
            return generator.lognormal(self.params["mu"], self.params["sigma"], size=size)
        return generator.weibull(self.params["shape"], size=size) * self.params["scale"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "mean": self.mean,
            "std": self.std,
            "sample_size": self.sample_size,
            **{f"param_{k}": v for k, v in self.params.items()},
            "log_likelihood": self.log_likelihood,
            "aic": self.aic,
            "bic": self.bic,
        }


# --------------------------------------------------------------------------- #
# Individual fitters
# --------------------------------------------------------------------------- #
def _clean(hours: Iterable[float]) -> np.ndarray:
    arr = np.asarray(list(hours), dtype=float)
    arr = arr[np.isfinite(arr) & (arr > 0)]
    return arr


def fit_lognormal(hours: Iterable[float]) -> EffortFit:
    """Fit a Log-Normal distribution by maximum likelihood (``loc`` pinned to 0)."""
    arr = _clean(hours)
    if arr.size == 0:
        raise ValueError("Cannot fit a Log-Normal distribution to an empty sample")
    sigma, _loc, scale = stats.lognorm.fit(arr, floc=0.0)
    mu = float(np.log(scale))
    sigma = float(sigma)
    log_lik = float(np.sum(stats.lognorm.logpdf(arr, sigma, 0.0, scale)))
    mean = float(np.exp(mu + sigma**2 / 2.0))
    variance = float((np.exp(sigma**2) - 1.0) * np.exp(2 * mu + sigma**2))
    k = 2
    return EffortFit(
        family="lognormal",
        params={"mu": mu, "sigma": sigma},
        sample_size=int(arr.size),
        mean=mean,
        variance=variance,
        log_likelihood=log_lik,
        aic=float(2 * k - 2 * log_lik),
        bic=float(k * np.log(arr.size) - 2 * log_lik),
    )


def fit_weibull(hours: Iterable[float]) -> EffortFit:
    """Fit a 2-parameter Weibull distribution (``loc`` pinned to 0)."""
    arr = _clean(hours)
    if arr.size == 0:
        raise ValueError("Cannot fit a Weibull distribution to an empty sample")
    shape, _loc, scale = stats.weibull_min.fit(arr, floc=0.0)
    shape = float(shape)
    scale = float(scale)
    log_lik = float(np.sum(stats.weibull_min.logpdf(arr, shape, 0.0, scale)))
    mean = float(scale * np.exp(gammaln(1.0 + 1.0 / shape)))
    variance = float(scale**2 * (np.exp(gammaln(1.0 + 2.0 / shape)) - np.exp(gammaln(1.0 + 1.0 / shape)) ** 2))
    k = 2
    return EffortFit(
        family="weibull",
        params={"shape": shape, "scale": scale},
        sample_size=int(arr.size),
        mean=mean,
        variance=variance,
        log_likelihood=log_lik,
        aic=float(2 * k - 2 * log_lik),
        bic=float(k * np.log(arr.size) - 2 * log_lik),
    )


_FITTERS = {"lognormal": fit_lognormal, "weibull": fit_weibull}


def fit_effort_distribution(
    hours: Iterable[float],
    candidates: Iterable[str] | None = None,
    config: EffortModelConfig | None = None,
) -> EffortFit:
    """Fit every candidate family and return the best one by AIC.

    Falls back to the Log-Normal when the sample is too small to discriminate
    (fewer than ``min_samples`` observations) or when the preferred fit fails.
    """
    arr = _clean(hours)
    if arr.size == 0:
        raise ValueError("Cannot fit an effort distribution to an empty sample")

    families = list(candidates) if candidates else list(config.candidates if config else SUPPORTED_FAMILIES)
    min_samples = config.min_samples if config else 8

    if arr.size < min_samples:
        try:
            return fit_lognormal(arr)
        except Exception:  # pragma: no cover - defensive
            return fit_weibull(arr)

    fits: list[EffortFit] = []
    for family in families:
        fitter = _FITTERS.get(family)
        if fitter is None:
            continue
        try:
            fits.append(fitter(arr))
        except Exception:  # pragma: no cover - a candidate may fail to converge
            continue
    if not fits:
        return fit_lognormal(arr)
    return min(fits, key=lambda f: f.aic)


def compare_effort_models(
    hours: Iterable[float],
    candidates: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Compare candidate effort families on AIC/BIC for a sample."""
    arr = _clean(hours)
    if arr.size == 0:
        raise ValueError("Cannot compare effort models on an empty sample")
    families = list(candidates) if candidates else list(SUPPORTED_FAMILIES)
    rows = []
    for family in families:
        fitter = _FITTERS.get(family)
        if fitter is None:
            continue
        try:
            fit = fitter(arr)
        except Exception:  # pragma: no cover
            continue
        rows.append(
            {
                "family": fit.family,
                "log_likelihood": fit.log_likelihood,
                "aic": fit.aic,
                "bic": fit.bic,
                "fitted_mean": fit.mean,
                "fitted_std": fit.std,
            }
        )
    frame = pd.DataFrame(rows).sort_values("aic").reset_index(drop=True)
    frame["preferred"] = False
    if not frame.empty:
        frame.loc[0, "preferred"] = True
    return frame


# --------------------------------------------------------------------------- #
# Panel fitting across towers / assets
# --------------------------------------------------------------------------- #
def fit_effort_by_group(
    tickets: pd.DataFrame,
    group_column: str = "tower_id",
    hours_column: str = "resolution_hours",
    config: EffortModelConfig | None = None,
) -> dict[str, EffortFit]:
    """Fit one effort distribution per value of ``group_column``."""
    if tickets is None or tickets.empty:
        return {}
    working = tickets.copy()
    working[hours_column] = pd.to_numeric(working[hours_column], errors="coerce")
    working = working[working[hours_column].notna() & (working[hours_column] > 0)]
    fits: dict[str, EffortFit] = {}
    for key, group in working.groupby(group_column, dropna=False):
        try:
            fits[str(key)] = fit_effort_distribution(
                group[hours_column].tolist(),
                config=config,
            )
        except ValueError:
            continue
    return fits


def effort_fits_to_frame(fits: Mapping[str, EffortFit], key_name: str = "group") -> pd.DataFrame:
    """Flatten a mapping of fits into a tidy DataFrame for reporting."""
    rows = []
    for key, fit in fits.items():
        row = {key_name: key}
        row.update(fit.as_dict())
        rows.append(row)
    return pd.DataFrame(rows)

"""Operational metric calculators.

The KPIs a delivery leader actually asks for: how much capacity did unplanned
support eat (Support Tax), how much of the sprint went to planned work (Flow
Efficiency), how repeatable is delivery (Delivery Predictability Index), and
how long do incidents take to resolve (MTTR).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Primitive metrics
# --------------------------------------------------------------------------- #
def _numeric(series: Iterable[float] | pd.Series) -> np.ndarray:
    return pd.to_numeric(pd.Series(series), errors="coerce").to_numpy(dtype=float)


def support_tax(
    unplanned_support_hours: Iterable[float] | pd.Series,
    gross_hours: Iterable[float] | pd.Series | None = None,
) -> float | pd.Series:
    """Support Tax %: share of gross capacity lost to unplanned support.

    Accepts either two aligned series (returns a series) or a single DataFrame
    with the canonical ``unplanned_support_hours``/``gross_hours`` columns.
    """
    if isinstance(unplanned_support_hours, pd.DataFrame):
        frame = unplanned_support_hours
        support = _numeric(frame["unplanned_support_hours"])
        gross = _numeric(frame["gross_hours"])
        return _safe_ratio(support, gross)
    support = _numeric(unplanned_support_hours)
    gross = _numeric(gross_hours)  # type: ignore[arg-type]
    return float(_safe_ratio(support, gross).mean()) if gross.size else float("nan")


def flow_efficiency(
    planned_feature_hours: Iterable[float] | pd.Series | pd.DataFrame,
    gross_hours: Iterable[float] | pd.Series | None = None,
) -> float | pd.Series:
    """Flow Efficiency: share of gross capacity delivered as planned feature work."""
    if isinstance(planned_feature_hours, pd.DataFrame):
        frame = planned_feature_hours
        planned = _numeric(frame["planned_feature_hours"])
        gross = _numeric(frame["gross_hours"])
        return _safe_ratio(planned, gross)
    planned = _numeric(planned_feature_hours)
    gross = _numeric(gross_hours)  # type: ignore[arg-type]
    return float(_safe_ratio(planned, gross).mean()) if gross.size else float("nan")


def _safe_ratio(numerator: np.ndarray, denominator: np.ndarray) -> pd.Series:
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(denominator > 0, numerator / denominator, np.nan)
    return pd.Series(ratio)


def delivery_predictability_index(
    completed_points: Iterable[float] | pd.Series,
    committed_points: Iterable[float] | pd.Series | None = None,
) -> float:
    """Delivery Predictability Index in ``[0, 1]``.

    Blends velocity stability (``1 - coefficient of variation``) with
    commitment reliability (mean delivered/committed ratio). Higher is better.
    """
    completed = _numeric(completed_points)
    completed = completed[~np.isnan(completed)]
    if completed.size == 0:
        return float("nan")

    mean = float(completed.mean())
    if mean <= 0:
        return 0.0
    cv = float(completed.std(ddof=0) / mean) if completed.size > 1 else 0.0
    stability = float(np.clip(1.0 - cv, 0.0, 1.0))

    if committed_points is None:
        return stability

    committed = _numeric(committed_points)
    committed = committed[~np.isnan(committed)]
    if committed.size == 0:
        return stability
    n = min(completed.size, committed.size)
    ratio = np.clip(completed[:n] / np.where(committed[:n] > 0, committed[:n], np.nan), 0.0, 1.0)
    ratio = ratio[~np.isnan(ratio)]
    reliability = float(ratio.mean()) if ratio.size else 0.0
    return float(np.clip(0.5 * stability + 0.5 * reliability, 0.0, 1.0))


def mttr(
    tickets: pd.DataFrame | Iterable[float],
    hours_column: str = "resolution_hours",
) -> float:
    """Mean Time To Resolution (hours) across resolved tickets."""
    if isinstance(tickets, pd.DataFrame):
        hours = _numeric(tickets[hours_column]) if hours_column in tickets.columns else np.array([])
    else:
        hours = _numeric(tickets)
    hours = hours[~np.isnan(hours)]
    return float(hours.mean()) if hours.size else float("nan")


def mttr_percentile(
    tickets: pd.DataFrame,
    q: float = 0.9,
    hours_column: str = "resolution_hours",
) -> float:
    """Percentile Time To Resolution, e.g. the P90 tail an SRE cares about."""
    hours = _numeric(tickets[hours_column]) if hours_column in tickets.columns else np.array([])
    hours = hours[~np.isnan(hours)]
    return float(np.percentile(hours, q * 100)) if hours.size else float("nan")


def incident_rate(snapshots: pd.DataFrame) -> float:
    """Average incidents raised per sprint."""
    if snapshots is None or snapshots.empty:
        return float("nan")
    return float(_numeric(snapshots["incident_count"]).mean())


def support_hours_per_engineer(snapshots: pd.DataFrame, headcount: int) -> float:
    """Unplanned support hours absorbed per engineer across the window."""
    if snapshots is None or snapshots.empty or headcount <= 0:
        return float("nan")
    return float(_numeric(snapshots["unplanned_support_hours"]).sum() / headcount)


# --------------------------------------------------------------------------- #
# Aggregated tables
# --------------------------------------------------------------------------- #
def sprint_metrics_table(snapshots: pd.DataFrame) -> pd.DataFrame:
    """Add derived KPI columns to a sprint-snapshot frame."""
    if snapshots is None or snapshots.empty:
        return pd.DataFrame(
            columns=[
                "tower_id",
                "sprint_id",
                "support_tax",
                "flow_efficiency",
                "commitment_reliability",
                "scope_change_rate",
            ]
        )
    frame = snapshots.copy()
    frame["support_tax"] = _safe_ratio(
        _numeric(frame["unplanned_support_hours"]), _numeric(frame["gross_hours"])
    )
    frame["flow_efficiency"] = _safe_ratio(
        _numeric(frame["planned_feature_hours"]), _numeric(frame["gross_hours"])
    )
    frame["commitment_reliability"] = _safe_ratio(
        _numeric(frame["completed_points"]), _numeric(frame["committed_points"])
    )
    frame["scope_change_rate"] = _safe_ratio(
        _numeric(frame["scope_change_points"]), _numeric(frame["committed_points"])
    )
    return frame


def tower_metrics_table(
    snapshots: pd.DataFrame,
    tickets: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One consolidated KPI row per tower."""
    if snapshots is None or snapshots.empty:
        return pd.DataFrame()

    frame = sprint_metrics_table(snapshots)
    rows: list[dict[str, Any]] = []
    for tower_id, group in frame.groupby("tower_id", dropna=False):
        tower_tickets = (
            tickets[tickets["tower_id"] == tower_id]
            if tickets is not None and not tickets.empty
            else pd.DataFrame()
        )
        resolved = (
            tower_tickets[tower_tickets["resolution_hours"].notna()]
            if not tower_tickets.empty
            else pd.DataFrame()
        )
        rows.append(
            {
                "tower_id": tower_id,
                "sprints": int(group["sprint_id"].nunique()),
                "support_tax_pct": round(float(group["support_tax"].mean()) * 100, 2),
                "flow_efficiency_pct": round(float(group["flow_efficiency"].mean()) * 100, 2),
                "delivery_predictability": round(
                    delivery_predictability_index(
                        group["completed_points"], group["committed_points"]
                    ),
                    4,
                ),
                "total_unplanned_hours": round(float(_numeric(group["unplanned_support_hours"]).sum()), 1),
                "total_incidents": int(_numeric(group["incident_count"]).sum()),
                "avg_incidents_per_sprint": round(float(_numeric(group["incident_count"]).mean()), 2),
                "mttr_hours": round(mttr(resolved), 2) if not resolved.empty else float("nan"),
                "p90_resolution_hours": round(mttr_percentile(resolved), 2) if not resolved.empty else float("nan"),
                "avg_velocity": round(float(_numeric(group["completed_points"]).mean()), 1),
                "avg_committed": round(float(_numeric(group["committed_points"]).mean()), 1),
                "avg_scope_change": round(float(_numeric(group["scope_change_points"]).mean()), 1),
            }
        )
    table = pd.DataFrame(rows).sort_values("support_tax_pct", ascending=False).reset_index(drop=True)
    return table


def asset_metrics_table(tickets: pd.DataFrame) -> pd.DataFrame:
    """Per-asset support burden: hours burned, incident count and MTTR."""
    if tickets is None or tickets.empty:
        return pd.DataFrame()
    working = tickets.copy()
    working["resolution_hours"] = _numeric(working["resolution_hours"])
    rows: list[dict[str, Any]] = []
    for (tower_id, asset), group in working.groupby(["tower_id", "asset"], dropna=False):
        resolved = group[group["resolution_hours"].notna()]
        rows.append(
            {
                "tower_id": tower_id,
                "asset": asset,
                "incidents": int(len(group)),
                "hours_burned": round(float(group["resolution_hours"].sum()), 1),
                "mttr_hours": round(float(resolved["resolution_hours"].mean()), 2) if not resolved.empty else float("nan"),
                "p90_resolution_hours": round(float(np.percentile(resolved["resolution_hours"], 90)), 2)
                if not resolved.empty
                else float("nan"),
                "open_incidents": int((group["status"] != "Done").sum()),
            }
        )
    table = pd.DataFrame(rows).sort_values("hours_burned", ascending=False).reset_index(drop=True)
    return table


def executive_summary(
    snapshots: pd.DataFrame,
    tickets: pd.DataFrame | None = None,
    tower_names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Headline numbers for the executive KPI summary."""
    if snapshots is None or snapshots.empty:
        return {}
    frame = sprint_metrics_table(snapshots)
    gross = float(_numeric(frame["gross_hours"]).sum())
    unplanned = float(_numeric(frame["unplanned_support_hours"]).sum())
    planned = float(_numeric(frame["planned_feature_hours"]).sum())
    committed = float(_numeric(frame["committed_points"]).sum())
    completed = float(_numeric(frame["completed_points"]).sum())
    return {
        "towers": int(frame["tower_id"].nunique()),
        "sprints": int(frame["sprint_id"].nunique()),
        "total_gross_hours": round(gross, 1),
        "total_unplanned_hours": round(unplanned, 1),
        "total_planned_feature_hours": round(planned, 1),
        "overall_support_tax_pct": round(unplanned / gross * 100, 2) if gross else 0.0,
        "overall_flow_efficiency_pct": round(planned / gross * 100, 2) if gross else 0.0,
        "total_committed_points": round(committed, 1),
        "total_completed_points": round(completed, 1),
        "commitment_reliability_pct": round(completed / committed * 100, 2) if committed else 0.0,
        "delivery_predictability": round(
            delivery_predictability_index(frame["completed_points"], frame["committed_points"]), 4
        ),
        "total_incidents": int(_numeric(frame["incident_count"]).sum()),
        "mttr_hours": round(mttr(tickets), 2) if tickets is not None and not tickets.empty else float("nan"),
        "tower_names": dict(tower_names or {}),
    }

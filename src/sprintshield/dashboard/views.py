"""Pure view-layer helpers for the Streamlit cockpit.

Everything in this module is framework-agnostic: it loads telemetry, runs the
Monte Carlo engine and returns Plotly figures. ``app.py`` is then a thin
Streamlit shell over these functions, which keeps the visual layer unit
testable without a running Streamlit server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
import plotly.graph_objects as go

from sprintshield.analytics.metrics import (
    asset_metrics_table,
    executive_summary,
    tower_metrics_table,
)
from sprintshield.analytics.monte_carlo import (
    MonteCarloSprintSimulator,
    SimulationResult,
    simulate_all_towers,
)
from sprintshield.client.synthetic_generator import generate_telemetry
from sprintshield.config import SprintShieldConfig, load_config
from sprintshield.storage.factory import get_storage_adapter

# Shared colour language with the SVG screenshots and Office exports.
TOWER_COLORS = {
    "DE": "#2C9FB5",
    "DA": "#4CAF50",
    "DS": "#F4A261",
    "PMO": "#9C6ADE",
}
PALETTE = ["#2C9FB5", "#4CAF50", "#F4A261", "#9C6ADE", "#D9534F", "#5A6B72"]
NAVY = "#0F2027"


@dataclass
class DashboardData:
    """Everything the cockpit needs, loaded once per render."""

    config: SprintShieldConfig
    snapshots: pd.DataFrame
    tickets: pd.DataFrame
    tower_metrics: pd.DataFrame
    asset_metrics: pd.DataFrame
    summary: dict[str, Any]
    simulations: dict[str, SimulationResult]

    def tower_names(self) -> dict[str, str]:
        return {t.tower_id: t.name for t in self.config.towers}


def load_dashboard_data(
    config: SprintShieldConfig | None = None,
    sprints: int = 24,
    runs: int | None = None,
) -> DashboardData:
    """Load telemetry from storage (or synthesise it) and run the simulations."""
    cfg = config or load_config()
    adapter = get_storage_adapter(cfg)
    snapshots = adapter.get_sprint_history()
    tickets = adapter.get_support_tickets()
    if snapshots.empty:
        snapshots, tickets = generate_telemetry(sprints=sprints, config=cfg)

    names = {t.tower_id: t.name for t in cfg.towers}
    simulations, _ = simulate_all_towers(cfg, snapshots, tickets, runs=runs)
    return DashboardData(
        config=cfg,
        snapshots=snapshots,
        tickets=tickets,
        tower_metrics=tower_metrics_table(snapshots, tickets),
        asset_metrics=asset_metrics_table(tickets),
        summary=executive_summary(snapshots, tickets, names),
        simulations=simulations,
    )


def _style(fig: go.Figure, title: str, height: int = 380) -> go.Figure:
    fig.update_layout(
        title=title,
        height=height,
        margin=dict(l=30, r=20, t=50, b=30),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#EAF4F6", size=12),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25),
    )
    fig.update_xaxes(gridcolor="rgba(255,255,255,0.08)")
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.08)")
    return fig


def build_confidence_curve(
    result: SimulationResult,
    planned_hours: float | None = None,
    target_confidence: float = 0.85,
) -> go.Figure:
    """Delivery-confidence curve for a single tower's simulation."""
    curve = result.confidence_curve()
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=curve["planned_hours"],
            y=curve["delivery_confidence"] * 100,
            mode="lines",
            name="Delivery confidence",
            line=dict(color="#2C9FB5", width=3),
            fill="tozeroy",
            fillcolor="rgba(44,159,181,0.18)",
        )
    )
    fig.add_hline(
        y=target_confidence * 100,
        line=dict(color="#F4A261", dash="dash"),
        annotation_text=f"target {target_confidence:.0%}",
        annotation_position="top left",
    )
    target_hours = planned_hours if planned_hours is not None else result.planned_hours
    confidence = result.delivery_confidence(target_hours) * 100
    fig.add_trace(
        go.Scatter(
            x=[target_hours],
            y=[confidence],
            mode="markers+text",
            marker=dict(color="#4CAF50" if confidence >= target_confidence * 100 else "#D9534F", size=14),
            text=[f"{confidence:.0f}%"],
            textposition="top center",
            name="Planned commitment",
        )
    )
    fig.update_xaxes(title="Planned commitment (hours)")
    fig.update_yaxes(title="Delivery confidence (%)", range=[0, 105])
    return _style(fig, "Delivery Confidence Curve")


def build_support_tax_bar(tower_metrics: pd.DataFrame, names: dict[str, str] | None = None) -> go.Figure:
    """Support-tax ranking bar chart across towers."""
    names = names or {}
    fig = go.Figure()
    if tower_metrics.empty:
        return _style(fig, "Support Tax by Tower")
    labels = [f"{t} - {names.get(str(t), '')}".strip(" -") for t in tower_metrics["tower_id"]]
    values = [float(v) for v in tower_metrics["support_tax_pct"]]
    colours = [
        "#D9534F" if v > 30 else "#F4A261" if v > 20 else "#4CAF50" for v in values
    ]
    fig.add_trace(
        go.Bar(x=labels, y=values, marker_color=colours, text=[f"{v:.1f}%" for v in values], textposition="outside")
    )
    fig.add_hline(y=20, line=dict(color="#F4A261", dash="dot"))
    fig.add_hline(y=30, line=dict(color="#D9534F", dash="dot"))
    fig.update_yaxes(title="Support tax (%)")
    return _style(fig, "Support Tax by Tower")


def build_tower_radar(tower_metrics: pd.DataFrame) -> go.Figure:
    """Radar chart comparing normalised KPIs across towers."""
    fig = go.Figure()
    if tower_metrics.empty:
        return _style(fig, "Tower Capacity Radar")
    metrics = [
        ("support_tax_pct", "Support Tax"),
        ("flow_efficiency_pct", "Flow Efficiency"),
        ("delivery_predictability", "Predictability"),
    ]
    categories = [label for _, label in metrics]
    for _, row in tower_metrics.iterrows():
        tower = str(row["tower_id"])
        raw = []
        for key, _ in metrics:
            value = float(row.get(key, 0) or 0)
            if key == "delivery_predictability":
                value *= 100
            raw.append(value)
        fig.add_trace(
            go.Scatterpolar(
                r=raw + [raw[0]],
                theta=categories + [categories[0]],
                fill="toself",
                name=tower,
                line=dict(color=TOWER_COLORS.get(tower, PALETTE[0])),
                opacity=0.6,
            )
        )
    fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 100], gridcolor="rgba(255,255,255,0.15)")))
    return _style(fig, "Tower Capacity Radar")


def build_capacity_donut(tower_metrics: pd.DataFrame, tower_id: str | None = None) -> go.Figure:
    """Donut showing planned feature work vs. unplanned support capacity."""
    fig = go.Figure()
    if tower_metrics.empty:
        return _style(fig, "Capacity Absorption")
    if tower_id is not None:
        row = tower_metrics[tower_metrics["tower_id"] == tower_id]
        if row.empty:
            row = tower_metrics.head(1)
    else:
        row = tower_metrics.head(1)
    support = float(row["support_tax_pct"].iloc[0])
    planned = max(100.0 - support, 0.0)
    fig.add_trace(
        go.Pie(
            labels=["Planned feature work", "Unplanned support"],
            values=[planned, support],
            hole=0.62,
            marker=dict(colors=["#4CAF50", "#D9534F"]),
            textinfo="label+percent",
        )
    )
    fig.add_annotation(
        text=f"<b>{support:.1f}%</b><br>support tax",
        x=0.5, y=0.5, showarrow=False, font=dict(size=14, color="#EAF4F6"),
    )
    return _style(fig, "Capacity Absorption", height=340)


def build_velocity_trend(snapshots: pd.DataFrame, tower_id: str | None = None) -> go.Figure:
    """Committed vs delivered velocity across sprints."""
    fig = go.Figure()
    if snapshots is None or snapshots.empty:
        return _style(fig, "Velocity Trend")
    frame = snapshots if tower_id is None else snapshots[snapshots["tower_id"] == tower_id]
    frame = frame.sort_values("sprint_id")
    if tower_id is None:
        frame = frame.groupby("sprint_id", as_index=False)[["committed_points", "completed_points"]].sum()
    fig.add_trace(
        go.Bar(x=frame["sprint_id"], y=frame["committed_points"], name="Committed", marker_color="#134E5E")
    )
    fig.add_trace(
        go.Bar(x=frame["sprint_id"], y=frame["completed_points"], name="Delivered", marker_color="#2C9FB5")
    )
    fig.update_layout(barmode="group")
    fig.update_yaxes(title="Story points")
    return _style(fig, "Committed vs Delivered Velocity")


def build_active_wip_table(tickets: pd.DataFrame) -> pd.DataFrame:
    """Rows for the Active Support WIP monitor (open blockers only)."""
    if tickets is None or tickets.empty:
        return pd.DataFrame(columns=["ticket_id", "tower_id", "asset", "priority", "summary"])
    open_tickets = tickets[tickets["status"] != "Done"].copy()
    if open_tickets.empty:
        return pd.DataFrame(columns=["ticket_id", "tower_id", "asset", "priority", "summary"])
    order = {"P1": 0, "P2": 1, "P3": 2, "P4": 3, "P5": 4}
    open_tickets["_rank"] = open_tickets["priority"].map(order).fillna(9)
    columns = ["ticket_id", "tower_id", "asset", "priority", "issue_type", "summary", "sprint_id"]
    return open_tickets.sort_values(["_rank", "sprint_id"])[columns].reset_index(drop=True)


def simulate_what_if(
    data: DashboardData,
    tower_id: str,
    planned_points: float,
    gross_hours: float | None = None,
) -> SimulationResult:
    """Re-run a single tower's simulation for a what-if planned commitment."""
    tower = data.config.tower(tower_id)
    simulator = MonteCarloSprintSimulator.from_tower(data.config, tower)
    hours_per_point = data.config.simulation.hours_per_point
    return simulator.simulate(
        gross_hours=gross_hours if gross_hours is not None else tower.gross_hours_per_sprint,
        planned_hours=planned_points * hours_per_point,
    )

"""SprintShield Streamlit cockpit.

Designed to be opened straight from VS Code: ``Cmd/Ctrl+Shift+P`` ->
``Simple Browser: Show`` -> ``http://localhost:8501``.

    streamlit run src/sprintshield/dashboard/app.py --server.port=8501

Three panels:
* **What-If Planning Simulator** - sliders update delivery confidence live.
* **Tower Capacity Radar** - donut and bar charts of absorbed capacity.
* **Active Support WIP Monitor** - ongoing blockers threatening sprint goals.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import streamlit as st

# Allow `streamlit run src/sprintshield/dashboard/app.py` without installation.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sprintshield.analytics.monte_carlo import simulate_all_towers  # noqa: E402
from sprintshield.config import load_config  # noqa: E402
from sprintshield.dashboard.views import (  # noqa: E402
    build_active_wip_table,
    build_capacity_donut,
    build_confidence_curve,
    build_support_tax_bar,
    build_tower_radar,
    build_velocity_trend,
    load_dashboard_data,
    simulate_what_if,
)
from sprintshield.reporting.excel_exporter import export_support_ledger  # noqa: E402
from sprintshield.reporting.pptx_exporter import export_steerco_deck  # noqa: E402

st.set_page_config(
    page_title="SprintShield Analytics",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
  .stApp { background: radial-gradient(circle at 20% 0%, #16323d 0%, #0f2027 55%, #0a161b 100%); }
  .kpi-card {
      background: linear-gradient(160deg, #16323d 0%, #12262e 100%);
      border: 1px solid rgba(44,159,181,0.35);
      border-radius: 14px; padding: 16px 18px; height: 118px;
  }
  .kpi-value { font-size: 30px; font-weight: 700; color: #2C9FB5; margin: 0; }
  .kpi-label { font-size: 12px; letter-spacing: .06em; text-transform: uppercase; color: #9fb6bd; margin: 0; }
  .warn { color: #F4A261 !important; }
  .crit { color: #D9534F !important; }
  .good { color: #4CAF50 !important; }
  h1, h2, h3 { color: #EAF4F6 !important; }
  .block-container { padding-top: 1.4rem; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner="Loading sprint telemetry and running simulations...")
def _load(runs: int):
    config = load_config()
    data = load_dashboard_data(config=config, runs=runs)
    return data


def _kpi(label: str, value: str, tone: str = "") -> str:
    return (
        f'<div class="kpi-card"><p class="kpi-value {tone}">{value}</p>'
        f'<p class="kpi-label">{label}</p></div>'
    )


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
st.sidebar.title("🛡️ SprintShield")
st.sidebar.caption("Unplanned Support Demand & Capacity Forecaster")

with st.sidebar:
    runs = st.select_slider("Monte Carlo runs", options=[1000, 2500, 5000, 10000, 20000], value=10000)
    confidence_target = st.slider("Target delivery confidence", 0.50, 0.99, 0.85, 0.01)
    if st.button("🔄 Refresh telemetry", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    st.markdown("**Storage backend**")
    data = _load(int(runs))
    st.code(data.config.storage_backend, language=None)
    st.caption(
        f"{data.snapshots['sprint_id'].nunique()} sprints · "
        f"{len(data.tickets):,} tickets · {data.snapshots['tower_id'].nunique()} towers"
    )

tower_names = data.tower_names()
tower_ids = list(tower_names.keys())

st.title("SprintShield Analytics")
st.caption(
    "Quantifying engineering capacity lost to unplanned production support, and forecasting "
    "delivery confidence with stochastic demand modelling + Monte Carlo simulation."
)

# --------------------------------------------------------------------------- #
# KPI strip
# --------------------------------------------------------------------------- #
summary = data.summary
tax = float(summary.get("overall_support_tax_pct", 0.0))
tax_tone = "crit" if tax > 30 else "warn" if tax > 20 else "good"
cols = st.columns(5)
cards = [
    ("Overall Support Tax", f"{tax:.1f}%", tax_tone),
    ("Unplanned Hours", f"{summary.get('total_unplanned_hours', 0):,.0f}", ""),
    ("Flow Efficiency", f"{summary.get('overall_flow_efficiency_pct', 0):.1f}%", "good"),
    ("Incidents Absorbed", f"{summary.get('total_incidents', 0):,.0f}", ""),
    ("MTTR", f"{summary.get('mttr_hours', 0):.1f} h", ""),
]
for col, (label, value, tone) in zip(cols, cards):
    col.markdown(_kpi(label, value, tone), unsafe_allow_html=True)

st.divider()

selected_tower = tower_ids[0]

tab_whatif, tab_radar, tab_wip, tab_export = st.tabs(
    ["🎯 What-If Planning Simulator", "📊 Tower Capacity Radar", "🚨 Active Support WIP", "📥 Reports"]
)

# --------------------------------------------------------------------------- #
# Tab 1 - What-If
# --------------------------------------------------------------------------- #
with tab_whatif:
    left, right = st.columns([1, 2])
    with left:
        tower_id = st.selectbox("Tower", tower_ids, format_func=lambda t: f"{t} — {tower_names[t]}")
        selected_tower = tower_id
        tower = data.config.tower(tower_id)
        planned_points = st.slider(
            "Planned sprint commitment (story points)",
            min_value=0,
            max_value=int(tower.story_points_per_sprint * 2),
            value=int(tower.story_points_per_sprint),
            step=2,
        )
        gross_hours = st.slider(
            "Gross capacity (hours)",
            min_value=int(tower.gross_hours_per_sprint * 0.5),
            max_value=int(tower.gross_hours_per_sprint * 1.5),
            value=int(tower.gross_hours_per_sprint),
            step=5,
        )
        hours_per_point = data.config.simulation.hours_per_point
        st.metric("Planned hours", f"{planned_points * hours_per_point:,.0f} h")
        st.metric("Gross capacity", f"{gross_hours:,} h")

    result = simulate_what_if(data, tower_id, planned_points, gross_hours)
    confidence = result.delivery_confidence() * 100
    with right:
        c1, c2, c3 = st.columns(3)
        c1.metric(
            "Delivery confidence",
            f"{confidence:.1f}%",
            delta=f"{confidence - confidence_target * 100:+.1f} pts vs target",
        )
        c2.metric("Mean support burn", f"{result.mean_support_hours:,.1f} h")
        c3.metric("Support tax", f"{result.mean_support_tax * 100:.1f}%")
        st.plotly_chart(
            build_confidence_curve(result, planned_hours=planned_points * hours_per_point,
                                   target_confidence=confidence_target),
            width="stretch",
        )
        safe_points = result.max_committed_hours(confidence_target) / hours_per_point
        st.info(
            f"To clear **{confidence_target:.0%}** delivery confidence, commit at most "
            f"**{safe_points:.0f} story points** and ring-fence a "
            f"**{result.buffer_hours(confidence_target):.0f} h** support contingency."
        )

# --------------------------------------------------------------------------- #
# Tab 2 - Radar
# --------------------------------------------------------------------------- #
with tab_radar:
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(build_support_tax_bar(data.tower_metrics, tower_names), width="stretch")
    with c2:
        st.plotly_chart(build_capacity_donut(data.tower_metrics, selected_tower),
                        width="stretch")
    c3, c4 = st.columns(2)
    with c3:
        st.plotly_chart(build_tower_radar(data.tower_metrics), width="stretch")
    with c4:
        st.plotly_chart(build_velocity_trend(data.snapshots), width="stretch")
    st.subheader("Tower KPI matrix")
    st.dataframe(data.tower_metrics, width="stretch", hide_index=True)
    st.subheader("Support burden by business asset")
    st.dataframe(data.asset_metrics, width="stretch", hide_index=True)

# --------------------------------------------------------------------------- #
# Tab 3 - WIP monitor
# --------------------------------------------------------------------------- #
with tab_wip:
    wip = build_active_wip_table(data.tickets)
    st.subheader("Active support WIP threatening sprint goals")
    if wip.empty:
        st.success("No open support blockers — every ticket is resolved.")
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Open blockers", f"{len(wip):,}")
        m2.metric("P1 / P2 blockers", f"{int(wip['priority'].isin(['P1', 'P2']).sum()):,}")
        m3.metric("Assets impacted", f"{wip['asset'].nunique():,}")
        priority = st.multiselect(
            "Filter by priority",
            options=["P1", "P2", "P3", "P4", "P5"],
            default=["P1", "P2", "P3"],
        )
        filtered = wip[wip["priority"].isin(priority)] if priority else wip
        st.dataframe(filtered, width="stretch", hide_index=True, height=420)

# --------------------------------------------------------------------------- #
# Tab 4 - Reports
# --------------------------------------------------------------------------- #
with tab_export:
    st.subheader("Generate executive reports")
    st.caption("Same engines as `sprintshield export --excel --pptx`.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("📊 Build Excel support ledger", width="stretch"):
            buffer = io.BytesIO()
            export_support_ledger(data.config, data.snapshots, data.tickets, output_path="/tmp/_ledger.xlsx")
            buffer.write(Path("/tmp/_ledger.xlsx").read_bytes())
            st.download_button(
                "⬇️ Download sprint_support_ledger.xlsx",
                buffer.getvalue(),
                file_name="sprint_support_ledger.xlsx",
                width="stretch",
            )
    with c2:
        if st.button("📽️ Build SteerCo deck", width="stretch"):
            _, sim_summary = simulate_all_towers(data.config, data.snapshots, data.tickets)
            export_steerco_deck(
                data.config,
                data.snapshots,
                data.tickets,
                simulation_summary=sim_summary,
                output_path="/tmp/_deck.pptx",
            )
            st.download_button(
                "⬇️ Download stakeholder_steerco_deck.pptx",
                Path("/tmp/_deck.pptx").read_bytes(),
                file_name="stakeholder_steerco_deck.pptx",
                width="stretch",
            )

st.divider()
st.caption(
    "SprintShield Analytics v1.0.0 · open in VS Code Simple Browser at "
    "http://localhost:8501 · dual-adapter storage (Delta Lake / Snowflake)"
)

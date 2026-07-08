# %% [markdown]
# # SprintShield - Interactive Sprint Capacity Audit
#
# Run this file cell-by-cell in the **VS Code Python Interactive Window**:
#
#   1. Open `scripts/interactive_analysis.py`
#   2. `Cmd/Ctrl+Shift+P` -> *Python: Run Current File in Interactive Window*
#      (or click **Run Cell** above any `# %%` marker)
#   3. Output renders inline: filterable DataFrames, Plotly charts and the
#      Monte Carlo confidence curves.
#
# Every cell below is self-contained once the setup cell has run.

# %%
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

try:
    # Normal case: this file lives at <repo>/scripts/interactive_analysis.py
    _ROOT = Path(__file__).resolve().parents[1]
except NameError:  # pragma: no cover - some interactive kernels omit __file__
    _ROOT = Path.cwd()

_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sprintshield.analytics.arrival_model import compare_arrival_models, dispersion_index
from sprintshield.analytics.effort_distribution import (
    compare_effort_models,
    effort_fits_to_frame,
    fit_effort_by_group,
)
from sprintshield.analytics.metrics import (
    asset_metrics_table,
    executive_summary,
    tower_metrics_table,
)
from sprintshield.analytics.monte_carlo import (
    MonteCarloSprintSimulator,
    simulate_all_towers,
)
from sprintshield.client.synthetic_generator import generate_telemetry
from sprintshield.config import load_config
from sprintshield.dashboard.views import (
    build_confidence_curve,
    build_support_tax_bar,
    build_tower_radar,
)
from sprintshield.storage.factory import get_storage_adapter

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 40)

CONFIG = load_config(project_root=_ROOT)
print(f"SprintShield ready | towers: {', '.join(CONFIG.tower_ids())}")
print(f"Storage backend: {CONFIG.storage_backend}")

# %% [markdown]
# ## 1. Load telemetry
#
# Reads from the configured storage backend; if the lakehouse is empty the
# synthetic generator fills it so this notebook always runs end-to-end.

# %%
adapter = get_storage_adapter(CONFIG)
snapshots = adapter.get_sprint_history()
tickets = adapter.get_support_tickets()

if snapshots.empty:
    print("Lakehouse empty -> generating synthetic telemetry...")
    snapshots, tickets = generate_telemetry(sprints=24, config=CONFIG)

print(f"Sprint snapshots : {snapshots.shape}")
print(f"Support tickets  : {tickets.shape}")
snapshots.head()

# %% [markdown]
# ## 2. Executive KPI summary
#
# Support Tax, Flow Efficiency, MTTR and the Delivery Predictability Index.

# %%
names = {t.tower_id: t.name for t in CONFIG.towers}
summary = executive_summary(snapshots, tickets, names)
pd.Series(summary).to_frame("value")

# %%
tower_metrics = tower_metrics_table(snapshots, tickets)
tower_metrics  # filterable inline DataFrame

# %% [markdown]
# ## 3. Support tax across towers
#
# Amber > 20%, red > 30% - the thresholds used throughout the reports.

# %%
build_support_tax_bar(tower_metrics, names)

# %%
build_tower_radar(tower_metrics)

# %% [markdown]
# ## 4. Stochastic demand modelling
#
# Dispersion index `D = sigma^2 / mu` decides Poisson vs. Negative Binomial.

# %%
rows = []
for tower_id, group in snapshots.groupby("tower_id"):
    counts = pd.to_numeric(group["incident_count"], errors="coerce").dropna().tolist()
    comparison = compare_arrival_models(counts, CONFIG.arrival_model)
    preferred = comparison.iloc[0]
    rows.append(
        {
            "tower_id": tower_id,
            "dispersion": round(dispersion_index(counts), 3),
            "selected_family": preferred["family"],
            "fitted_mean": round(float(preferred["fitted_mean"]), 2),
            "observed_mean": round(float(preferred["observed_mean"]), 2),
            "aic": round(float(preferred["aic"]), 2),
        }
    )
arrival_table = pd.DataFrame(rows)
arrival_table

# %% [markdown]
# ## 5. Resolution-effort distributions (Log-Normal / Weibull)

# %%
fits = fit_effort_by_group(tickets, "tower_id", config=CONFIG.effort_model)
effort_table = effort_fits_to_frame(fits, key_name="tower_id")
effort_table[["tower_id", "family", "param_mu", "param_sigma", "mean", "std", "sample_size"]]

# %%
compare_effort_models(
    tickets.loc[tickets["tower_id"] == "DE", "resolution_hours"].dropna().tolist()
)

# %% [markdown]
# ## 6. Monte Carlo sprint capacity simulation
#
# 10,000 vectorised runs per tower: sample arrivals, sample effort per incident,
# sum the support burn, net it off gross capacity.

# %%
simulations, simulation_summary = simulate_all_towers(CONFIG, snapshots, tickets, runs=10_000)
simulation_summary[
    [
        "tower_id",
        "mean_incidents",
        "mean_support_hours",
        "mean_support_tax",
        "p85_support_hours",
        "delivery_confidence",
        "max_committed_hours_at_target",
    ]
].round(3)

# %%
de_result = simulations["DE"]
build_confidence_curve(
    de_result,
    planned_hours=CONFIG.tower("DE").story_points_per_sprint * CONFIG.simulation.hours_per_point,
    target_confidence=CONFIG.simulation.confidence_target,
)

# %% [markdown]
# ## 7. What-if planning
#
# Change `PLANNED_POINTS` and re-run to see delivery confidence move.

# %%
TOWER = "DE"
PLANNED_POINTS = 80

simulator = MonteCarloSprintSimulator.from_tower(CONFIG, TOWER)
result = simulator.simulate(
    gross_hours=CONFIG.tower(TOWER).gross_hours_per_sprint,
    planned_hours=PLANNED_POINTS * CONFIG.simulation.hours_per_point,
)
pd.Series(result.summary()).to_frame("value")

# %% [markdown]
# ## 8. Root cause - assets draining developer hours

# %%
asset_metrics_table(tickets).head(12)

# %% [markdown]
# ## 9. Active support WIP
#
# Open blockers threatening the current sprint goal.

# %%
open_wip = tickets[tickets["status"] != "Done"]
print(f"Open blockers: {len(open_wip)}")
open_wip.sort_values("priority")[
    ["ticket_id", "tower_id", "asset", "priority", "issue_type", "summary"]
].head(20)

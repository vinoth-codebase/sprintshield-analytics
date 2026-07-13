"""SprintShield command-line interface.

A Rich-powered Click CLI binding the full pipeline together: extraction /
generation, model training, Monte Carlo simulation, Office reporting, the
Streamlit cockpit and the VS Code screenshot renderer.

    sprintshield ingest --sprints 24
    sprintshield train
    sprintshield simulate --gross-hours 320 --planned-points 80
    sprintshield export --excel --pptx
    sprintshield dashboard
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import click
import pandas as pd

from sprintshield import __version__
from sprintshield.analytics.arrival_model import compare_arrival_models, dispersion_index
from sprintshield.analytics.effort_distribution import (
    effort_fits_to_frame,
    fit_effort_by_group,
)
from sprintshield.analytics.metrics import executive_summary, tower_metrics_table
from sprintshield.analytics.monte_carlo import (
    MonteCarloSprintSimulator,
    simulate_all_towers,
)
from sprintshield.client.synthetic_generator import generate_telemetry
from sprintshield.config import SprintShieldConfig, load_config
from sprintshield.reporting.excel_exporter import export_support_ledger
from sprintshield.reporting.pptx_exporter import export_steerco_deck
from sprintshield.storage.factory import get_storage_adapter

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

    _RICH = True
except ImportError:  # pragma: no cover - rich is a declared dependency
    _RICH = False

    class Console:  # type: ignore[no-redef]
        def print(self, *args: Any, **kwargs: Any) -> None:
            click.echo(" ".join(str(a) for a in args))

    class Table:  # type: ignore[no-redef]
        def __init__(self, *a: Any, **k: Any) -> None:
            self.rows: list[list[str]] = []
            self.title = k.get("title", "")

        def add_column(self, *a: Any, **k: Any) -> None:
            pass

        def add_row(self, *values: Any) -> None:
            self.rows.append([str(v) for v in values])

    class Panel:  # type: ignore[no-redef]
        def __init__(self, body: str, *a: Any, **k: Any) -> None:
            self.body = body

    class Progress:  # type: ignore[no-redef]
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def __enter__(self) -> "Progress":
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def add_task(self, *a: Any, **k: Any) -> int:
            return 0

        def advance(self, *a: Any, **k: Any) -> None:
            pass

    class SpinnerColumn:  # type: ignore[no-redef]
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

    class TextColumn:  # type: ignore[no-redef]
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

    class BarColumn:  # type: ignore[no-redef]
        def __init__(self, *a: Any, **k: Any) -> None:
            pass


console = Console()

# Storage backend identifiers accepted by the CLI (matches the factory).
_VALID_BACKENDS = ("deltalake", "snowflake")


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _load_config(ctx: click.Context) -> SprintShieldConfig:
    obj = ctx.ensure_object(dict)
    if "config" not in obj:
        obj["config"] = load_config(
            project_root=obj.get("project_root"),
            env_file=obj.get("env_file"),
        )
    return obj["config"]


def _load_frames(config: SprintShieldConfig, sprints: int = 24) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read telemetry from storage, generating synthetic data when empty."""
    adapter = get_storage_adapter(config)
    snapshots = adapter.get_sprint_history()
    tickets = adapter.get_support_tickets()
    if snapshots.empty:
        snapshots, tickets = generate_telemetry(sprints=sprints, config=config)
    return snapshots, tickets


def _render_table(title: str, frame: pd.DataFrame, columns: list[str] | None = None) -> None:
    columns = columns or list(frame.columns)
    table = Table(title=title)
    for column in columns:
        table.add_column(str(column))
    for _, row in frame.iterrows():
        table.add_row(*[str(row.get(c, "")) for c in columns])
    console.print(table)


# --------------------------------------------------------------------------- #
# Root group
# --------------------------------------------------------------------------- #
@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="sprintshield")
@click.option("--project-root", default=None, help="Repository root for config resolution.")
@click.option("--env-file", default=None, help="Explicit .env file to load.")
def cli(project_root: str | None, env_file: str | None) -> None:
    """SprintShield Analytics - unplanned support demand & capacity forecaster."""
    ctx = click.get_current_context()
    obj = ctx.ensure_object(dict)
    obj["project_root"] = project_root
    obj["env_file"] = env_file


# --------------------------------------------------------------------------- #
# ingest
# --------------------------------------------------------------------------- #
@cli.command()
@click.option("--sprints", default=24, show_default=True, help="Number of two-week sprints to generate.")
@click.option("--seed", default=None, type=int, help="Random seed for reproducible telemetry.")
@click.option(
    "--source",
    type=click.Choice(["synthetic", "jira"]),
    default="synthetic",
    show_default=True,
    help="Telemetry source.",
)
@click.option("--mode", type=click.Choice(["append", "overwrite"]), default="overwrite", show_default=True)
@click.option(
    "--backend",
    type=click.Choice(_VALID_BACKENDS),
    default=None,
    help="Override the storage backend for this write.",
)
def ingest(sprints: int, seed: int | None, source: str, mode: str, backend: str | None) -> None:
    """Generate (or extract) telemetry and persist it to the storage backend."""
    ctx = click.get_current_context()
    config = _load_config(ctx)
    adapter = get_storage_adapter(config, backend=backend)

    if source == "jira":
        console.print(Panel("Jira extraction requested - falling back to synthetic telemetry when credentials are absent."))
    snapshots, tickets = generate_telemetry(sprints=sprints, config=config, seed=seed)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn()) as progress:
        task = progress.add_task("Persisting telemetry...", total=2)
        n_snap = adapter.save_sprint_snapshots(snapshots, mode=mode)
        progress.advance(task)
        n_tick = adapter.save_support_tickets(tickets, mode=mode)
        progress.advance(task)

    console.print(
        Panel(
            f"Backend: {adapter.backend}\n"
            f"Sprint snapshots written: {n_snap}\n"
            f"Support tickets written: {n_tick}\n"
            f"Sprints: {snapshots['sprint_id'].nunique()} | Towers: {snapshots['tower_id'].nunique()}",
            title="Ingestion complete",
        )
    )


# --------------------------------------------------------------------------- #
# train
# --------------------------------------------------------------------------- #
@cli.command()
@click.option("--tower", default=None, help="Restrict training output to a single tower.")
def train(tower: str | None) -> None:
    """Fit arrival (Poisson / Negative Binomial) and effort (Log-Normal / Weibull) models."""
    ctx = click.get_current_context()
    config = _load_config(ctx)
    snapshots, tickets = _load_frames(config)
    if tower:
        snapshots = snapshots[snapshots["tower_id"] == tower]
        tickets = tickets[tickets["tower_id"] == tower]

    # Arrival model comparison per tower.
    rows = []
    for tower_id, group in snapshots.groupby("tower_id"):
        counts = pd.to_numeric(group["incident_count"], errors="coerce").dropna().tolist()
        comparison = compare_arrival_models(counts, config.arrival_model)
        preferred = comparison.iloc[0]
        rows.append(
            {
                "tower_id": tower_id,
                "dispersion": round(dispersion_index(counts), 3),
                "selected": preferred["family"],
                "fitted_mean": round(float(preferred["fitted_mean"]), 3),
                "observed_mean": round(float(preferred["observed_mean"]), 3),
                "aic": round(float(preferred["aic"]), 2),
            }
        )
    _render_table("Arrival Demand Models", pd.DataFrame(rows))

    # Effort fits per tower.
    fits = fit_effort_by_group(tickets, "tower_id", config=config.effort_model)
    frame = effort_fits_to_frame(fits, key_name="tower_id")
    if not frame.empty:
        frame["mean"] = frame["mean"].round(2)
        frame["std"] = frame["std"].round(2)
        _render_table(
            "Resolution Effort Models",
            frame[["tower_id", "family", "mean", "std", "sample_size"]],
        )
    console.print(Panel("Model training complete. Run `sprintshield simulate` next.", title="train"))


# --------------------------------------------------------------------------- #
# simulate
# --------------------------------------------------------------------------- #
@cli.command()
@click.option("--gross-hours", default=320.0, show_default=True, type=float, help="Gross capacity per sprint.")
@click.option("--planned-points", default=80, show_default=True, type=int, help="Planned sprint commitment in points.")
@click.option("--runs", default=None, type=int, help="Monte Carlo iterations (defaults to config).")
@click.option("--tower", default=None, help="Simulate a single tower.")
@click.option("--target-confidence", default=None, type=float, help="Delivery-confidence target (default 0.85).")
@click.option("--json-out", is_flag=True, default=False, help="Emit machine-readable JSON instead of tables.")
def simulate(
    gross_hours: float,
    planned_points: int,
    runs: int | None,
    tower: str | None,
    target_confidence: float | None,
    json_out: bool,
) -> None:
    """Run the vectorised Monte Carlo sprint-capacity simulation."""
    ctx = click.get_current_context()
    config = _load_config(ctx)
    snapshots, tickets = _load_frames(config)
    hours_per_point = config.simulation.hours_per_point
    planned_hours = planned_points * hours_per_point
    target = target_confidence if target_confidence is not None else config.simulation.confidence_target

    results, summary = simulate_all_towers(
        config,
        snapshots,
        tickets,
        runs=runs,
        target_confidence=target,
    )

    if tower:
        if tower not in results:
            raise click.ClickException(f"Unknown tower {tower!r}. Known: {list(results)}")
        result = results[tower]
        if json_out:
            console.print(json.dumps(result.summary(target), indent=2))
            return
        _render_table(
            f"Monte Carlo Summary - {tower}",
            pd.DataFrame([result.summary(target)]).T.reset_index().rename(
                columns={"index": "metric", 0: "value"}
            ),
        )
        curve = result.confidence_curve()
        _render_table(
            "Delivery Confidence Curve (sampled)",
            curve.iloc[:: max(len(curve) // 10, 1)].round(4),
        )
        return

    if json_out:
        console.print(json.dumps(summary.to_dict(orient="records"), indent=2))
        return

    display = summary[
        [
            "tower_id",
            "mean_incidents",
            "mean_support_hours",
            "mean_support_tax",
            "p85_support_hours",
            "delivery_confidence",
            "max_committed_hours_at_target",
        ]
    ].copy()
    display["mean_incidents"] = display["mean_incidents"].round(2)
    display["mean_support_hours"] = display["mean_support_hours"].round(1)
    display["mean_support_tax"] = (display["mean_support_tax"] * 100).round(1)
    display["p85_support_hours"] = display["p85_support_hours"].round(1)
    display["delivery_confidence"] = (display["delivery_confidence"] * 100).round(1)
    display["max_committed_hours_at_target"] = display["max_committed_hours_at_target"].round(1)
    display.columns = [
        "Tower",
        "Mean Incidents",
        "Mean Support Hrs",
        "Support Tax %",
        "P85 Support Hrs",
        "Delivery Conf %",
        f"Safe Hrs @ {target:.0%}",
    ]
    _render_table(f"Monte Carlo Sprint Simulation ({runs or config.simulation.runs} runs)", display)
    console.print(
        Panel(
            f"Gross hours: {gross_hours:.0f} | Planned: {planned_points} pts "
            f"({planned_hours:.0f} h @ {hours_per_point} h/pt) | Target confidence: {target:.0%}",
            title="simulate",
        )
    )


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #
@cli.command()
@click.option("--excel", "do_excel", is_flag=True, default=False, help="Generate the Excel support ledger.")
@click.option("--pptx", "do_pptx", is_flag=True, default=False, help="Generate the SteerCo deck.")
@click.option("--all", "do_all", is_flag=True, default=False, help="Generate every report.")
@click.option("--out-dir", default=None, help="Override the report output directory.")
def export(do_excel: bool, do_pptx: bool, do_all: bool, out_dir: str | None) -> None:
    """Generate the Excel ledger and/or the PowerPoint SteerCo deck."""
    ctx = click.get_current_context()
    config = _load_config(ctx)
    snapshots, tickets = _load_frames(config)

    if not (do_excel or do_pptx or do_all):
        do_excel = do_pptx = True

    output_dir = Path(out_dir) if out_dir else config.project_root / config.reporting.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    _, sim_summary = simulate_all_towers(config, snapshots, tickets)
    names = {t.tower_id: t.name for t in config.towers}

    produced: list[str] = []
    if do_excel or do_all:
        result = export_support_ledger(
            config, snapshots, tickets, output_path=output_dir / "sprint_support_ledger.xlsx", tower_names=names
        )
        produced.append(f"{result.path}  ({len(result.sheets)} tabs)")
    if do_pptx or do_all:
        result = export_steerco_deck(
            config,
            snapshots,
            tickets,
            simulation_summary=sim_summary,
            output_path=output_dir / "stakeholder_steerco_deck.pptx",
            tower_names=names,
        )
        produced.append(f"{result.path}  ({result.slides} slides)")

    console.print(Panel("\n".join(produced), title="Reports generated"))


# --------------------------------------------------------------------------- #
# report / dashboard / screenshots
# --------------------------------------------------------------------------- #
@cli.command()
def report() -> None:
    """Print the executive KPI summary to the terminal."""
    ctx = click.get_current_context()
    config = _load_config(ctx)
    snapshots, tickets = _load_frames(config)
    summary = executive_summary(snapshots, tickets, {t.tower_id: t.name for t in config.towers})
    console.print(Panel(json.dumps(summary, indent=2, default=str), title="Executive KPI Summary"))
    _render_table("Tower Metrics", tower_metrics_table(snapshots, tickets))


@cli.command()
@click.option("--port", default=8501, show_default=True, type=int)
@click.option("--headless/--no-headless", default=True, show_default=True)
def dashboard(port: int, headless: bool) -> None:  # pragma: no cover - launches a subprocess
    """Launch the Streamlit cockpit (view it in the VS Code Simple Browser)."""
    ctx = click.get_current_context()
    config = _load_config(ctx)
    app_path = Path(__file__).parent / "dashboard" / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        f"--server.port={port}",
        "--server.headless=" + ("true" if headless else "false"),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(config.project_root / "src")
    console.print(Panel(f"Starting Streamlit on http://localhost:{port}\nOpen it via VS Code Simple Browser.", title="dashboard"))
    subprocess.run(cmd, env=env, check=False)


@cli.command()
@click.option("--out-dir", default=None, help="Directory for the SVG assets.")
def screenshots(out_dir: str | None) -> None:  # pragma: no cover - renders SVG assets
    """Render the VS Code-themed SVG screenshots."""
    ctx = click.get_current_context()
    config = _load_config(ctx)
    from scripts.generate_vscode_screenshots import generate_all  # type: ignore

    target = Path(out_dir) if out_dir else config.project_root / "docs" / "screenshots"
    paths = generate_all(target)
    console.print(Panel("\n".join(str(p) for p in paths), title="Screenshots rendered"))


def main() -> None:
    """Console-script entry point."""
    cli(obj={})


if __name__ == "__main__":  # pragma: no cover
    main()

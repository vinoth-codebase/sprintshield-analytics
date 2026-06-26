#!/usr/bin/env python3
"""Generate synthetic multi-tower sprint telemetry and persist it.

Simulates ``--sprints`` two-week sprints (default 24 = one full calendar year)
across the Data Engineering, Analytics, Data Science and PMO towers, then
writes the results to the configured storage backend.

    python scripts/generate_sprint_telemetry.py --sprints 24
    python scripts/generate_sprint_telemetry.py --sprints 24 --backend deltalake
    python scripts/generate_sprint_telemetry.py --no-write --csv-out data/exports
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

# Allow running straight from a checkout without installation.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sprintshield.analytics.metrics import executive_summary, tower_metrics_table  # noqa: E402
from sprintshield.client.synthetic_generator import generate_telemetry  # noqa: E402
from sprintshield.config import load_config  # noqa: E402
from sprintshield.storage.factory import get_storage_adapter  # noqa: E402

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    def _table(title: str, frame) -> None:
        table = Table(title=title)
        for column in frame.columns:
            table.add_column(str(column))
        for _, row in frame.iterrows():
            table.add_row(*[str(row[c]) for c in frame.columns])
        console.print(table)

    def _panel(text: str, title: str) -> None:
        console.print(Panel(text, title=title))

except ImportError:  # pragma: no cover
    def _table(title: str, frame) -> None:
        print(f"\n== {title} ==")
        print(frame.to_string(index=False))

    def _panel(text: str, title: str) -> None:
        print(f"\n== {title} ==\n{text}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sprints", type=int, default=24, help="Number of two-week sprints (default 24).")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility.")
    parser.add_argument(
        "--start-date",
        type=lambda s: date.fromisoformat(s),
        default=None,
        help="Anchor date for sprint 1 (YYYY-MM-DD, default 2026-01-05).",
    )
    parser.add_argument(
        "--backend",
        choices=["deltalake", "snowflake"],
        default=None,
        help="Storage backend override.",
    )
    parser.add_argument(
        "--mode",
        choices=["append", "overwrite"],
        default="overwrite",
        help="Write mode against the storage backend.",
    )
    parser.add_argument("--no-write", action="store_true", help="Generate only; skip persistence.")
    parser.add_argument("--csv-out", type=Path, default=None, help="Also export the frames as CSV here.")
    parser.add_argument("--out-dir", type=Path, default=None, help="Override the Delta Lake root path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(project_root=_ROOT)

    _panel(
        f"Sprints: {args.sprints} | Towers: {', '.join(config.tower_ids())}\n"
        f"Arrival: Poisson/NegBinom | Effort: Log-Normal/Weibull | Seed: {args.seed or config.simulation.random_seed}",
        "SprintShield synthetic telemetry",
    )

    snapshots, tickets = generate_telemetry(
        sprints=args.sprints,
        config=config,
        seed=args.seed,
        start_date=args.start_date,
    )

    names = {t.tower_id: t.name for t in config.towers}
    metrics = tower_metrics_table(snapshots, tickets)
    _table("Tower operational metrics", metrics)

    summary = executive_summary(snapshots, tickets, names)
    _panel(
        "\n".join(
            [
                f"Total sprints          : {summary['sprints']}",
                f"Total tickets          : {summary['total_incidents']}",
                f"Total unplanned hours  : {summary['total_unplanned_hours']:,.0f}",
                f"Overall support tax    : {summary['overall_support_tax_pct']:.1f}%",
                f"Overall flow efficiency: {summary['overall_flow_efficiency_pct']:.1f}%",
                f"MTTR                   : {summary['mttr_hours']:.2f} h",
            ]
        ),
        "Executive summary",
    )

    if args.csv_out is not None:
        args.csv_out.mkdir(parents=True, exist_ok=True)
        snapshots.to_csv(args.csv_out / "sprint_snapshots.csv", index=False)
        tickets.to_csv(args.csv_out / "support_tickets.csv", index=False)
        _panel(f"CSV exports written to {args.csv_out}", "CSV export")

    if args.no_write:
        _panel("Persistence skipped (--no-write).", "Done")
        return 0

    kwargs = {}
    if args.out_dir is not None:
        kwargs["root_path"] = args.out_dir
    adapter = get_storage_adapter(config, backend=args.backend, **kwargs)
    n_snap = adapter.save_sprint_snapshots(snapshots, mode=args.mode)
    n_tick = adapter.save_support_tickets(tickets, mode=args.mode)
    adapter.close()

    _panel(
        f"Backend      : {adapter.backend}\n"
        f"Snapshots    : {n_snap} rows\n"
        f"Tickets      : {n_tick} rows",
        "Persisted to storage",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

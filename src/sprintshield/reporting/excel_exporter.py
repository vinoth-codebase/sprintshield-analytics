"""Excel Sprint Support Ledger generator (``openpyxl``).

Produces ``sprint_support_ledger.xlsx`` with three styled tabs:

* **Executive KPI Summary** - Support Tax %, total unplanned hours and planned
  vs. actual velocity.
* **Tower Breakdown Matrix** - MTTR, incident count and hours burned per tower
  and business asset.
* **Support Tax Heatmap** - a tower x sprint grid with conditional formatting:
  soft amber above 20%, red above 30%.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from sprintshield.analytics.metrics import (
    asset_metrics_table,
    executive_summary,
    sprint_metrics_table,
    tower_metrics_table,
)
from sprintshield.config import ReportingConfig, SprintShieldConfig

# --- palette ----------------------------------------------------------------- #
NAVY = "0F2027"
TEAL = "134E5E"
LIGHT = "EAF4F6"
AMBER = "FFD8A8"
RED = "FFC9C9"
GREEN = "C3F0CA"
WHITE = "FFFFFF"
GREY_BORDER = "B7C9CE"

THIN = Side(style="thin", color=GREY_BORDER)
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


@dataclass
class ExcelExportResult:
    """Summary of a generated workbook."""

    path: Path
    sheets: list[str]
    rows_written: int
    generated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sheets": self.sheets,
            "rows_written": self.rows_written,
            "generated_at": self.generated_at,
        }


class SprintSupportLedgerExporter:
    """Builds the multi-tab Sprint Support Ledger workbook."""

    def __init__(
        self,
        config: SprintShieldConfig,
        reporting: ReportingConfig | None = None,
    ) -> None:
        self.config = config
        self.reporting = reporting or config.reporting
        self._rows_written = 0

    # -- public API ---------------------------------------------------------- #
    def export(
        self,
        snapshots: pd.DataFrame,
        tickets: pd.DataFrame,
        output_path: str | Path | None = None,
        tower_names: Mapping[str, str] | None = None,
    ) -> ExcelExportResult:
        """Write the workbook and return a result descriptor."""
        destination = self._resolve_output(output_path)
        names = dict(tower_names or {t.tower_id: t.name for t in self.config.towers})

        wb = Workbook()
        wb.remove(wb.active)

        summary = executive_summary(snapshots, tickets, names)
        tower_table = tower_metrics_table(snapshots, tickets)
        asset_table = asset_metrics_table(tickets)
        sprint_table = sprint_metrics_table(snapshots)

        self._write_kpi_summary(wb, summary, tower_table, names)
        self._write_tower_matrix(wb, tower_table, asset_table, names)
        self._write_heatmap(wb, sprint_table, names)
        self._write_sprint_detail(wb, sprint_table)

        wb.save(destination)
        return ExcelExportResult(
            path=destination,
            sheets=list(wb.sheetnames),
            rows_written=self._rows_written,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    # -- helpers ------------------------------------------------------------- #
    def _resolve_output(self, output_path: str | Path | None) -> Path:
        if output_path is not None:
            path = Path(output_path)
        else:
            path = self.config.project_root / self.reporting.output_dir / "sprint_support_ledger.xlsx"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _title(self, ws: Worksheet, text: str, subtitle: str, span: int) -> int:
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(span, 4))
        cell = ws.cell(row=1, column=1, value=text)
        cell.font = Font(size=16, bold=True, color=WHITE)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(vertical="center", horizontal="left", indent=1)
        ws.row_dimensions[1].height = 30

        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max(span, 4))
        sub = ws.cell(row=2, column=1, value=subtitle)
        sub.font = Font(size=9, italic=True, color=TEAL)
        sub.fill = PatternFill("solid", fgColor=LIGHT)
        sub.alignment = Alignment(vertical="center", horizontal="left", indent=1)
        ws.row_dimensions[2].height = 18
        return 4

    def _header_row(self, ws: Worksheet, row: int, headers: list[str]) -> None:
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=col, value=header)
            cell.font = Font(bold=True, color=WHITE, size=10)
            cell.fill = PatternFill("solid", fgColor=TEAL)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = BORDER
        ws.row_dimensions[row].height = 26

    def _write_row(self, ws: Worksheet, row: int, values: list[Any], formats: list[str] | None = None) -> None:
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col, value=value)
            cell.border = BORDER
            cell.alignment = Alignment(horizontal="left" if col == 1 else "right", vertical="center")
            if formats and col - 1 < len(formats) and formats[col - 1]:
                cell.number_format = formats[col - 1]
        self._rows_written += 1

    def _autosize(self, ws: Worksheet, widths: Mapping[int, int]) -> None:
        for col, width in widths.items():
            ws.column_dimensions[get_column_letter(col)].width = width

    # -- tab 1: executive KPI summary ---------------------------------------- #
    def _write_kpi_summary(
        self,
        wb: Workbook,
        summary: dict[str, Any],
        tower_table: pd.DataFrame,
        names: Mapping[str, str],
    ) -> None:
        ws = wb.create_sheet("Executive KPI Summary")
        ws.sheet_view.showGridLines = False
        row = self._title(
            ws,
            "SprintShield - Executive KPI Summary",
            f"Multi-tower unplanned support governance | generated {datetime.now(timezone.utc):%d %b %Y %H:%M UTC}",
            span=6,
        )

        kpis = [
            ("Overall Support Tax %", summary.get("overall_support_tax_pct", 0.0) / 100.0, "0.0%"),
            ("Overall Flow Efficiency %", summary.get("overall_flow_efficiency_pct", 0.0) / 100.0, "0.0%"),
            ("Total Unplanned Support Hours", summary.get("total_unplanned_hours", 0.0), "#,##0.0"),
            ("Total Gross Capacity Hours", summary.get("total_gross_hours", 0.0), "#,##0.0"),
            ("Total Incidents Absorbed", summary.get("total_incidents", 0), "#,##0"),
            ("Mean Time To Resolution (h)", summary.get("mttr_hours", float("nan")), "#,##0.00"),
            ("Commitment Reliability %", summary.get("commitment_reliability_pct", 0.0) / 100.0, "0.0%"),
            ("Delivery Predictability Index", summary.get("delivery_predictability", 0.0), "0.000"),
        ]

        self._header_row(ws, row, ["Executive KPI", "Value", "", "Planning Context", "Value", ""])
        row += 1
        for i, (label, value, fmt) in enumerate(kpis):
            r = row + i
            ws.cell(row=r, column=1, value=label).font = Font(bold=True, size=10)
            ws.cell(row=r, column=1).border = BORDER
            vcell = ws.cell(row=r, column=2, value=value if not (isinstance(value, float) and np.isnan(value)) else "n/a")
            vcell.number_format = fmt
            vcell.border = BORDER
            vcell.alignment = Alignment(horizontal="right")
            vcell.fill = PatternFill("solid", fgColor=LIGHT)
        row += len(kpis) + 1

        # Planned vs actual velocity by tower.
        ws.cell(row=row, column=1, value="Planned vs. Actual Velocity by Tower").font = Font(
            bold=True, size=12, color=TEAL
        )
        row += 1
        headers = ["Tower", "Tower Name", "Avg Committed (pts)", "Avg Delivered (pts)", "Reliability", "Support Tax %"]
        self._header_row(ws, row, headers)
        row += 1
        start_data = row
        for _, r in tower_table.iterrows():
            committed = float(r.get("avg_committed", 0) or 0)
            delivered = float(r.get("avg_velocity", 0) or 0)
            reliability = delivered / committed if committed else 0.0
            self._write_row(
                ws,
                row,
                [
                    r.get("tower_id", ""),
                    names.get(str(r.get("tower_id")), ""),
                    committed,
                    delivered,
                    reliability,
                    float(r.get("support_tax_pct", 0)) / 100.0,
                ],
                [None, None, "#,##0.0", "#,##0.0", "0.0%", "0.0%"],
            )
            row += 1
        end_data = row - 1

        if end_data < start_data:  # no tower rows -> nothing to format or chart
            ws.cell(row=row, column=1, value="No tower telemetry available.")
            self._autosize(ws, {1: 30, 2: 20, 3: 20, 4: 22, 5: 14, 6: 16})
            return

        # Conditional formatting on the Support Tax % column.
        tax_col = get_column_letter(6)
        warn = self.reporting.support_tax_warning_threshold
        crit = self.reporting.support_tax_critical_threshold
        rng = f"{tax_col}{start_data}:{tax_col}{end_data}"
        ws.conditional_formatting.add(
            rng, CellIsRule(operator="greaterThan", formula=[str(crit)], fill=PatternFill("solid", fgColor=RED))
        )
        ws.conditional_formatting.add(
            rng,
            CellIsRule(
                operator="between",
                formula=[str(warn), str(crit)],
                fill=PatternFill("solid", fgColor=AMBER),
            ),
        )

        # Native bar chart: committed vs delivered.
        chart = BarChart()
        chart.type = "col"
        chart.title = "Committed vs Delivered Story Points"
        chart.y_axis.title = "Story points"
        chart.height, chart.width = 7.5, 16
        data = Reference(ws, min_col=3, max_col=4, min_row=start_data - 1, max_row=end_data)
        cats = Reference(ws, min_col=1, min_row=start_data, max_row=end_data)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(cats)
        ws.add_chart(chart, f"A{end_data + 3}")

        self._autosize(ws, {1: 30, 2: 20, 3: 20, 4: 22, 5: 14, 6: 16})

    # -- tab 2: tower breakdown matrix --------------------------------------- #
    def _write_tower_matrix(
        self,
        wb: Workbook,
        tower_table: pd.DataFrame,
        asset_table: pd.DataFrame,
        names: Mapping[str, str],
    ) -> None:
        ws = wb.create_sheet("Tower Breakdown Matrix")
        ws.sheet_view.showGridLines = False
        row = self._title(
            ws,
            "Tower Breakdown Matrix",
            "MTTR, incident volume and hours burned per tower and business asset",
            span=10,
        )

        headers = [
            "Tower",
            "Tower Name",
            "Sprints",
            "Incidents",
            "Incidents/Sprint",
            "Hours Burned",
            "MTTR (h)",
            "P90 Resolution (h)",
            "Support Tax %",
            "Predictability",
        ]
        self._header_row(ws, row, headers)
        row += 1
        start = row
        for _, r in tower_table.iterrows():
            self._write_row(
                ws,
                row,
                [
                    r.get("tower_id", ""),
                    names.get(str(r.get("tower_id")), ""),
                    int(r.get("sprints", 0)),
                    int(r.get("total_incidents", 0)),
                    float(r.get("avg_incidents_per_sprint", 0)),
                    float(r.get("total_unplanned_hours", 0)),
                    r.get("mttr_hours", float("nan")),
                    r.get("p90_resolution_hours", float("nan")),
                    float(r.get("support_tax_pct", 0)) / 100.0,
                    float(r.get("delivery_predictability", 0)),
                ],
                [None, None, "#,##0", "#,##0", "#,##0.00", "#,##0.0", "#,##0.00", "#,##0.00", "0.0%", "0.000"],
            )
            row += 1
        end = row - 1
        row += 1

        if end >= start:
            tax_col = get_column_letter(9)
            warn = self.reporting.support_tax_warning_threshold
            crit = self.reporting.support_tax_critical_threshold
            rng = f"{tax_col}{start}:{tax_col}{end}"
            ws.conditional_formatting.add(
                rng, CellIsRule(operator="greaterThan", formula=[str(crit)], fill=PatternFill("solid", fgColor=RED))
            )
            ws.conditional_formatting.add(
                rng,
                CellIsRule(operator="between", formula=[str(warn), str(crit)], fill=PatternFill("solid", fgColor=AMBER)),
            )

        # Per-asset breakdown.
        ws.cell(row=row, column=1, value="Support Burden by Business Asset").font = Font(
            bold=True, size=12, color=TEAL
        )
        row += 1
        asset_headers = ["Tower", "Asset", "Incidents", "Hours Burned", "MTTR (h)", "P90 (h)", "Open Incidents"]
        self._header_row(ws, row, asset_headers)
        row += 1
        for _, r in asset_table.iterrows():
            self._write_row(
                ws,
                row,
                [
                    r.get("tower_id", ""),
                    r.get("asset", ""),
                    int(r.get("incidents", 0)),
                    float(r.get("hours_burned", 0)),
                    r.get("mttr_hours", float("nan")),
                    r.get("p90_resolution_hours", float("nan")),
                    int(r.get("open_incidents", 0)),
                ],
                [None, None, "#,##0", "#,##0.0", "#,##0.00", "#,##0.00", "#,##0"],
            )
            row += 1

        self._autosize(ws, {i: w for i, w in enumerate([10, 20, 10, 12, 16, 14, 12, 18, 14, 14], start=1)})

    # -- tab 3: heatmap ------------------------------------------------------ #
    def _write_heatmap(
        self,
        wb: Workbook,
        sprint_table: pd.DataFrame,
        names: Mapping[str, str],
    ) -> None:
        ws = wb.create_sheet("Support Tax Heatmap")
        ws.sheet_view.showGridLines = False
        row = self._title(
            ws,
            "Support Tax Heatmap - Tower x Sprint",
            "Amber > 20% | Red > 30% unplanned support tax",
            span=30,
        )

        if sprint_table.empty:
            ws.cell(row=row, column=1, value="No sprint telemetry available.")
            return

        pivot = sprint_table.pivot_table(
            index="tower_id",
            columns="sprint_id",
            values="support_tax",
            aggfunc="mean",
        )
        pivot = pivot.sort_index(axis=1)

        ws.cell(row=row, column=1, value="Tower").font = Font(bold=True, color=WHITE)
        ws.cell(row=row, column=1).fill = PatternFill("solid", fgColor=TEAL)
        ws.cell(row=row, column=1).border = BORDER
        for j, sprint_id in enumerate(pivot.columns, start=2):
            cell = ws.cell(row=row, column=j, value=str(sprint_id))
            cell.font = Font(bold=True, color=WHITE, size=9)
            cell.fill = PatternFill("solid", fgColor=TEAL)
            cell.alignment = Alignment(horizontal="center")
            cell.border = BORDER
        header_row = row
        row += 1
        first_data = row

        for tower_id, series in pivot.iterrows():
            label = ws.cell(row=row, column=1, value=f"{tower_id} - {names.get(str(tower_id), '')}")
            label.font = Font(bold=True, size=9)
            label.border = BORDER
            for j, value in enumerate(series.to_numpy(), start=2):
                cell = ws.cell(row=row, column=j, value=float(value) if not pd.isna(value) else None)
                cell.number_format = "0.0%"
                cell.alignment = Alignment(horizontal="center")
                cell.border = BORDER
                cell.font = Font(size=9)
            row += 1
        last_data = row - 1

        data_range = f"B{first_data}:{get_column_letter(1 + len(pivot.columns))}{last_data}"
        warn = self.reporting.support_tax_warning_threshold
        crit = self.reporting.support_tax_critical_threshold
        ws.conditional_formatting.add(
            data_range, CellIsRule(operator="greaterThan", formula=[str(crit)], fill=PatternFill("solid", fgColor=RED))
        )
        ws.conditional_formatting.add(
            data_range,
            CellIsRule(operator="between", formula=[str(warn), str(crit)], fill=PatternFill("solid", fgColor=AMBER)),
        )
        ws.conditional_formatting.add(
            data_range,
            ColorScaleRule(
                start_type="min", start_color="C3F0CA",
                mid_type="percentile", mid_value=50, mid_color="FFE08A",
                end_type="max", end_color="FF8A8A",
            ),
        )

        # Legend.
        legend_row = last_data + 2
        ws.cell(row=legend_row, column=1, value="Legend").font = Font(bold=True, color=TEAL)
        for offset, (text, colour) in enumerate(
            [("<= 20% healthy", GREEN), ("> 20% watch", AMBER), ("> 30% critical", RED)]
        ):
            cell = ws.cell(row=legend_row + 1 + offset, column=1, value=text)
            cell.fill = PatternFill("solid", fgColor=colour)
            cell.border = BORDER

        ws.column_dimensions["A"].width = 26
        for j in range(2, 2 + len(pivot.columns)):
            ws.column_dimensions[get_column_letter(j)].width = 8
        ws.freeze_panes = f"B{header_row + 1}"

    # -- tab 4: sprint detail ------------------------------------------------ #
    def _write_sprint_detail(self, wb: Workbook, sprint_table: pd.DataFrame) -> None:
        ws = wb.create_sheet("Sprint Detail")
        ws.sheet_view.showGridLines = False
        row = self._title(ws, "Sprint Detail Ledger", "Row-level telemetry backing every KPI", span=9)

        headers = [
            "Tower",
            "Sprint",
            "Start",
            "End",
            "Committed",
            "Completed",
            "Gross Hours",
            "Unplanned Hours",
            "Support Tax",
            "Flow Efficiency",
        ]
        self._header_row(ws, row, headers)
        row += 1
        if sprint_table.empty:
            ws.cell(row=row, column=1, value="No sprint telemetry available.")
            return
        frame = sprint_table.sort_values(["sprint_id", "tower_id"])
        for _, r in frame.iterrows():
            self._write_row(
                ws,
                row,
                [
                    r.get("tower_id", ""),
                    r.get("sprint_id", ""),
                    r.get("start_date", ""),
                    r.get("end_date", ""),
                    float(r.get("committed_points", 0) or 0),
                    float(r.get("completed_points", 0) or 0),
                    float(r.get("gross_hours", 0) or 0),
                    float(r.get("unplanned_support_hours", 0) or 0),
                    float(r.get("support_tax", 0) or 0),
                    float(r.get("flow_efficiency", 0) or 0),
                ],
                [None, None, None, None, "#,##0.0", "#,##0.0", "#,##0.0", "#,##0.0", "0.0%", "0.0%"],
            )
            row += 1

        self._autosize(ws, {i: w for i, w in enumerate([10, 10, 12, 12, 12, 12, 13, 16, 12, 15], start=1)})
        ws.freeze_panes = "A6"


def export_support_ledger(
    config: SprintShieldConfig,
    snapshots: pd.DataFrame,
    tickets: pd.DataFrame,
    output_path: str | Path | None = None,
    tower_names: Mapping[str, str] | None = None,
) -> ExcelExportResult:
    """Convenience wrapper around :class:`SprintSupportLedgerExporter`."""
    return SprintSupportLedgerExporter(config).export(
        snapshots, tickets, output_path=output_path, tower_names=tower_names
    )

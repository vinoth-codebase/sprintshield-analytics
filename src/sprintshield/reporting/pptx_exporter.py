"""SteerCo PowerPoint deck generator (``python-pptx``).

Produces ``stakeholder_steerco_deck.pptx`` - a three-slide executive pack a
delivery manager can present to leadership:

* **Slide 1** - Executive KPI scorecard: planned feature velocity vs. support
  tax across every tower.
* **Slide 2** - Root-cause waterfall: the top three unstable data assets
  draining developer hours.
* **Slide 3** - Monte Carlo capacity recommendation for the next sprint at the
  configured delivery-confidence target (default 85%).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

from sprintshield.analytics.metrics import (
    asset_metrics_table,
    executive_summary,
    tower_metrics_table,
)
from sprintshield.config import ReportingConfig, SprintShieldConfig

# --- palette ----------------------------------------------------------------- #
NAVY = RGBColor(0x0F, 0x20, 0x27)
TEAL = RGBColor(0x13, 0x4E, 0x5E)
AQUA = RGBColor(0x2C, 0x9F, 0xB5)
LIGHT = RGBColor(0xEA, 0xF4, 0xF6)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
AMBER = RGBColor(0xF4, 0xA2, 0x61)
RED = RGBColor(0xD9, 0x53, 0x4F)
GREEN = RGBColor(0x4C, 0xAF, 0x50)
GREY = RGBColor(0x5A, 0x6B, 0x72)

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)


@dataclass
class PptxExportResult:
    """Summary of a generated deck."""

    path: Path
    slides: int
    generated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "slides": self.slides, "generated_at": self.generated_at}


class SteerCoDeckExporter:
    """Builds the three-slide executive SteerCo deck."""

    def __init__(
        self,
        config: SprintShieldConfig,
        reporting: ReportingConfig | None = None,
    ) -> None:
        self.config = config
        self.reporting = reporting or config.reporting

    # -- public API ---------------------------------------------------------- #
    def export(
        self,
        snapshots: pd.DataFrame,
        tickets: pd.DataFrame,
        simulation_summary: pd.DataFrame | None = None,
        output_path: str | Path | None = None,
        tower_names: Mapping[str, str] | None = None,
    ) -> PptxExportResult:
        """Write the deck and return a result descriptor."""
        destination = self._resolve_output(output_path)
        names = dict(tower_names or {t.tower_id: t.name for t in self.config.towers})

        summary = executive_summary(snapshots, tickets, names)
        tower_table = tower_metrics_table(snapshots, tickets)
        asset_table = asset_metrics_table(tickets)

        prs = Presentation()
        prs.slide_width = SLIDE_W
        prs.slide_height = SLIDE_H
        blank = prs.slide_layouts[6]

        self._slide_kpi_scorecard(prs.slides.add_slide(blank), summary, tower_table, names)
        self._slide_root_cause_waterfall(prs.slides.add_slide(blank), asset_table, names)
        self._slide_monte_carlo(
            prs.slides.add_slide(blank),
            simulation_summary if simulation_summary is not None else self._default_simulation(),
            names,
        )

        prs.save(destination)
        return PptxExportResult(
            path=destination,
            slides=len(prs.slides._sldIdLst),  # noqa: SLF001 - python-pptx has no public count
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    # -- helpers ------------------------------------------------------------- #
    def _resolve_output(self, output_path: str | Path | None) -> Path:
        if output_path is not None:
            path = Path(output_path)
        else:
            path = self.config.project_root / self.reporting.output_dir / "stakeholder_steerco_deck.pptx"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _background(self, slide) -> None:
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = NAVY

    def _title_bar(self, slide, title: str, subtitle: str) -> None:
        self._background(slide)
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, SLIDE_W, Inches(1.15))
        bar.fill.solid()
        bar.fill.fore_color.rgb = TEAL
        bar.line.fill.background()
        bar.shadow.inherit = False

        tf = bar.text_frame
        tf.margin_left = Inches(0.4)
        tf.margin_top = Inches(0.12)
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = title
        p.font.size = Pt(26)
        p.font.bold = True
        p.font.color.rgb = WHITE
        p2 = tf.add_paragraph()
        p2.text = subtitle
        p2.font.size = Pt(11)
        p2.font.color.rgb = LIGHT

    def _footer(self, slide, text: str) -> None:
        box = slide.shapes.add_textbox(Inches(0.4), SLIDE_H - Inches(0.45), SLIDE_W - Inches(0.8), Inches(0.3))
        p = box.text_frame.paragraphs[0]
        p.text = text
        p.font.size = Pt(8)
        p.font.color.rgb = GREY

    def _kpi_card(
        self,
        slide,
        left: Emu,
        top: Emu,
        width: Emu,
        height: Emu,
        label: str,
        value: str,
        accent: RGBColor,
    ) -> None:
        card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
        card.fill.solid()
        card.fill.fore_color.rgb = RGBColor(0x16, 0x2B, 0x33)
        card.line.color.rgb = accent
        card.line.width = Pt(1.25)
        card.shadow.inherit = False

        tf = card.text_frame
        tf.word_wrap = True
        tf.margin_left = Inches(0.12)
        tf.margin_top = Inches(0.08)
        p = tf.paragraphs[0]
        p.text = value
        p.font.size = Pt(24)
        p.font.bold = True
        p.font.color.rgb = accent
        p.alignment = PP_ALIGN.LEFT
        p2 = tf.add_paragraph()
        p2.text = label
        p2.font.size = Pt(10)
        p2.font.color.rgb = LIGHT

    # -- slide 1: executive KPI scorecard ------------------------------------ #
    def _slide_kpi_scorecard(
        self,
        slide,
        summary: dict[str, Any],
        tower_table: pd.DataFrame,
        names: Mapping[str, str],
    ) -> None:
        self._title_bar(
            slide,
            "Executive KPI Scorecard",
            "Planned feature velocity vs. unplanned support tax across data towers",
        )

        mttr_value = summary.get("mttr_hours", float("nan"))
        mttr_text = "n/a" if not np.isfinite(mttr_value) else f"{mttr_value:.1f} h"
        tax = float(summary.get("overall_support_tax_pct", 0.0)) / 100.0
        accent = RED if tax > self.reporting.support_tax_critical_threshold else (
            AMBER if tax > self.reporting.support_tax_warning_threshold else GREEN
        )
        cards = [
            ("Overall Support Tax", f"{summary.get('overall_support_tax_pct', 0):.1f}%", accent),
            ("Unplanned Hours", f"{summary.get('total_unplanned_hours', 0):,.0f}", AQUA),
            ("Flow Efficiency", f"{summary.get('overall_flow_efficiency_pct', 0):.1f}%", GREEN),
            ("Incidents Absorbed", f"{summary.get('total_incidents', 0):,.0f}", AMBER),
            ("MTTR", mttr_text, AQUA),
        ]
        left = Inches(0.4)
        gap = Inches(0.18)
        card_w = (SLIDE_W - Inches(0.8) - gap * (len(cards) - 1)) / len(cards)
        for i, (label, value, colour) in enumerate(cards):
            self._kpi_card(slide, left + i * (card_w + gap), Inches(1.45), card_w, Inches(1.15), label, value, colour)

        if tower_table.empty:
            box = slide.shapes.add_textbox(Inches(0.6), Inches(3.2), Inches(11), Inches(1.0))
            box.text_frame.text = "No sprint telemetry available - run `sprintshield ingest` first."
            box.text_frame.paragraphs[0].font.size = Pt(16)
            box.text_frame.paragraphs[0].font.color.rgb = LIGHT
            self._footer(slide, f"SprintShield Analytics | generated {datetime.now(timezone.utc):%d %b %Y}")
            return

        # Committed vs delivered chart.
        chart_data = CategoryChartData()
        chart_data.categories = [str(t) for t in tower_table.get("tower_id", [])]
        committed = [float(v) for v in tower_table.get("avg_committed", [])]
        delivered = [float(v) for v in tower_table.get("avg_velocity", [])]
        chart_data.add_series("Committed (pts)", committed)
        chart_data.add_series("Delivered (pts)", delivered)

        graphic = slide.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED,
            Inches(0.4),
            Inches(2.85),
            Inches(7.4),
            Inches(3.9),
            chart_data,
        ).chart
        graphic.has_legend = True
        graphic.legend.position = XL_LEGEND_POSITION.BOTTOM
        graphic.legend.include_in_layout = False
        graphic.font.size = Pt(10)
        graphic.font.color.rgb = WHITE
        graphic.value_axis.has_major_gridlines = False

        # Support-tax ranking table.
        rows = len(tower_table) + 1
        table_shape = slide.shapes.add_table(rows, 3, Inches(8.1), Inches(2.85), Inches(4.8), Inches(0.4) * rows)
        table = table_shape.table
        table.columns[0].width = Inches(2.7)
        table.columns[1].width = Inches(1.05)
        table.columns[2].width = Inches(1.05)
        headers = ["Tower", "Support Tax", "Predictability"]
        for col, header in enumerate(headers):
            cell = table.cell(0, col)
            cell.text = header
            cell.fill.solid()
            cell.fill.fore_color.rgb = TEAL
            para = cell.text_frame.paragraphs[0]
            para.font.bold = True
            para.font.size = Pt(11)
            para.font.color.rgb = WHITE
        for r, (_, row) in enumerate(tower_table.iterrows(), start=1):
            tax_pct = float(row.get("support_tax_pct", 0))
            values = [
                f"{row.get('tower_id', '')} - {names.get(str(row.get('tower_id')), '')}",
                f"{tax_pct:.1f}%",
                f"{float(row.get('delivery_predictability', 0)):.2f}",
            ]
            for col, value in enumerate(values):
                cell = table.cell(r, col)
                cell.text = value
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor(0x16, 0x2B, 0x33)
                para = cell.text_frame.paragraphs[0]
                para.font.size = Pt(10)
                para.font.color.rgb = WHITE
                if col == 1:
                    para.font.color.rgb = (
                        RED if tax_pct / 100 > self.reporting.support_tax_critical_threshold
                        else AMBER if tax_pct / 100 > self.reporting.support_tax_warning_threshold
                        else GREEN
                    )

        self._footer(slide, f"SprintShield Analytics | generated {datetime.now(timezone.utc):%d %b %Y}")

    # -- slide 2: root-cause waterfall --------------------------------------- #
    def _slide_root_cause_waterfall(
        self,
        slide,
        asset_table: pd.DataFrame,
        names: Mapping[str, str],
    ) -> None:
        self._title_bar(
            slide,
            "Root-Cause Waterfall",
            "Top unstable data assets draining developer hours",
        )

        top = asset_table.head(6).reset_index(drop=True)
        if top.empty:
            box = slide.shapes.add_textbox(Inches(0.6), Inches(3.2), Inches(11), Inches(1.0))
            box.text_frame.text = "No asset-level telemetry available yet."
            box.text_frame.paragraphs[0].font.size = Pt(16)
            box.text_frame.paragraphs[0].font.color.rgb = LIGHT
            self._footer(slide, "Support burden ranked by total resolution hours burned.")
            return

        chart_data = CategoryChartData()
        chart_data.categories = [str(a) for a in top["asset"]]
        chart_data.add_series("Hours burned", [float(v) for v in top["hours_burned"]])
        graphic = slide.shapes.add_chart(
            XL_CHART_TYPE.BAR_CLUSTERED,
            Inches(0.4),
            Inches(1.5),
            Inches(7.3),
            Inches(5.2),
            chart_data,
        ).chart
        graphic.has_legend = False
        graphic.font.size = Pt(10)
        graphic.font.color.rgb = WHITE
        graphic.value_axis.has_major_gridlines = False

        # Narrative callouts for the top three assets.
        top3 = asset_table.head(3).reset_index(drop=True)
        box = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, Inches(8.0), Inches(1.5), Inches(4.9), Inches(5.2)
        )
        box.fill.solid()
        box.fill.fore_color.rgb = RGBColor(0x16, 0x2B, 0x33)
        box.line.color.rgb = AQUA
        box.shadow.inherit = False
        tf = box.text_frame
        tf.word_wrap = True
        tf.margin_left = Inches(0.2)
        tf.margin_top = Inches(0.15)

        heading = tf.paragraphs[0]
        heading.text = "Top 3 hour-draining pipelines"
        heading.font.size = Pt(15)
        heading.font.bold = True
        heading.font.color.rgb = AQUA

        total_hours = float(asset_table["hours_burned"].sum()) or 1.0
        for rank, (_, row) in enumerate(top3.iterrows(), start=1):
            hours = float(row.get("hours_burned", 0))
            share = hours / total_hours * 100
            para = tf.add_paragraph()
            para.text = f"{rank}. {row.get('asset', '')}  ({row.get('tower_id', '')})"
            para.font.size = Pt(12)
            para.font.bold = True
            para.font.color.rgb = WHITE
            detail = tf.add_paragraph()
            detail.text = (
                f"     {hours:,.0f} h burned | {int(row.get('incidents', 0))} incidents | "
                f"MTTR {float(row.get('mttr_hours', 0) or 0):.1f} h | {share:.0f}% of total"
            )
            detail.font.size = Pt(10)
            detail.font.color.rgb = LIGHT

        closing = tf.add_paragraph()
        closing.text = "\nRecommendation: ring-fence a reliability workstream for the assets above."
        closing.font.size = Pt(10)
        closing.font.italic = True
        closing.font.color.rgb = AMBER

        self._footer(slide, "Support burden ranked by total resolution hours burned.")

    # -- slide 3: Monte Carlo recommendation --------------------------------- #
    def _slide_monte_carlo(
        self,
        slide,
        simulation_summary: pd.DataFrame,
        names: Mapping[str, str],
    ) -> None:
        target = self.config.simulation.confidence_target
        self._title_bar(
            slide,
            "Monte Carlo Capacity Recommendation",
            f"10,000-run simulation | commit to the largest plan clearing {target:.0%} delivery confidence",
        )

        if simulation_summary is None or simulation_summary.empty:
            box = slide.shapes.add_textbox(Inches(0.6), Inches(3.2), Inches(11), Inches(1.0))
            box.text_frame.text = "No simulation results available yet."
            box.text_frame.paragraphs[0].font.size = Pt(16)
            box.text_frame.paragraphs[0].font.color.rgb = LIGHT
            self._footer(slide, "Run `sprintshield simulate` to populate this slide.")
            return

        frame = simulation_summary.copy()
        hp = self.config.simulation.hours_per_point or 1.0
        frame["recommended_points"] = frame["max_committed_hours_at_target"] / hp

        chart_data = CategoryChartData()
        chart_data.categories = [str(t) for t in frame["tower_id"]]
        chart_data.add_series(
            "Safe commitment (pts)", [round(float(v), 1) for v in frame["recommended_points"]]
        )
        chart_data.add_series(
            "Contingency buffer (h)", [round(float(v), 0) for v in frame["buffer_hours_at_target"]]
        )
        graphic = slide.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED,
            Inches(0.4),
            Inches(1.5),
            Inches(7.3),
            Inches(5.2),
            chart_data,
        ).chart
        graphic.has_legend = True
        graphic.legend.position = XL_LEGEND_POSITION.BOTTOM
        graphic.legend.include_in_layout = False
        graphic.font.size = Pt(10)
        graphic.font.color.rgb = WHITE
        graphic.value_axis.has_major_gridlines = False

        rows = len(frame) + 1
        table_shape = slide.shapes.add_table(rows, 4, Inches(8.0), Inches(1.7), Inches(4.9), Inches(0.42) * rows)
        table = table_shape.table
        for column, width in zip(table.columns, (Inches(1.3), Inches(1.25), Inches(1.15), Inches(1.2))):
            column.width = width
        headers = ["Tower", "Support Tax", "Safe Pts", "Buffer (h)"]
        for col, header in enumerate(headers):
            cell = table.cell(0, col)
            cell.text = header
            cell.fill.solid()
            cell.fill.fore_color.rgb = TEAL
            para = cell.text_frame.paragraphs[0]
            para.font.bold = True
            para.font.size = Pt(11)
            para.font.color.rgb = WHITE
        for r, (_, row) in enumerate(frame.iterrows(), start=1):
            values = [
                str(row.get("tower_id", "")),
                f"{float(row.get('mean_support_tax', 0)) * 100:.1f}%",
                f"{float(row.get('recommended_points', 0)):.1f}",
                f"{float(row.get('buffer_hours_at_target', 0)):.0f}",
            ]
            for col, value in enumerate(values):
                cell = table.cell(r, col)
                cell.text = value
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor(0x16, 0x2B, 0x33)
                para = cell.text_frame.paragraphs[0]
                para.font.size = Pt(10)
                para.font.color.rgb = WHITE

        note = slide.shapes.add_textbox(Inches(8.0), Inches(1.7) + Inches(0.42) * rows + Inches(0.2), Inches(4.9), Inches(1.2))
        tf = note.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = (
            "Safe Pts = largest sprint commitment that still clears the delivery-confidence "
            "target after sampled unplanned support demand. Buffer is the contingency hours a "
            "planner should ring-fence."
        )
        p.font.size = Pt(9)
        p.font.color.rgb = LIGHT

        self._footer(slide, f"Target delivery confidence: {target:.0%} | simulated runs: {int(frame['runs'].iloc[0])}")

    # -- fallback ------------------------------------------------------------ #
    def _default_simulation(self) -> pd.DataFrame:
        """Run a fresh profile-driven simulation when none is supplied."""
        from sprintshield.analytics.monte_carlo import simulate_all_towers

        _, summary = simulate_all_towers(self.config)
        return summary


def export_steerco_deck(
    config: SprintShieldConfig,
    snapshots: pd.DataFrame,
    tickets: pd.DataFrame,
    simulation_summary: pd.DataFrame | None = None,
    output_path: str | Path | None = None,
    tower_names: Mapping[str, str] | None = None,
) -> PptxExportResult:
    """Convenience wrapper around :class:`SteerCoDeckExporter`."""
    return SteerCoDeckExporter(config).export(
        snapshots,
        tickets,
        simulation_summary=simulation_summary,
        output_path=output_path,
        tower_names=tower_names,
    )

#!/usr/bin/env python3
"""Render VS Code-themed SVG screenshots for the SprintShield README.

Produces four high-resolution SVG assets styled as real VS Code editor
windows (dark theme ``#1e1e1e``, editor tabs, breadcrumbs, activity bar and
window controls):

* ``vscode_interactive_monte_carlo.svg`` - Python Interactive pane showing a
  Monte Carlo summary table beside a delivery-confidence curve.
* ``vscode_support_tax_breakdown.svg`` - split editor: source on the left,
  Interactive output highlighting support tax per tower on the right.
* ``vscode_simple_browser_dashboard.svg`` - the Streamlit cockpit inside an
  embedded "Simple Browser: http://localhost:8501" tab.
* ``vscode_excel_viewer_capacity_ledger.svg`` - the Excel Viewer extension tab
  showing the conditional-formatting heatmap.

The charts are drawn from a real Monte Carlo run, not invented numbers; if the
package cannot be imported the script falls back to baked-in reference values.

    python scripts/generate_vscode_screenshots.py
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path
from typing import Any, Sequence

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# --------------------------------------------------------------------------- #
# VS Code "Dark+" palette
# --------------------------------------------------------------------------- #
BG = "#1e1e1e"
BG_ALT = "#252526"
BG_TITLE = "#3c3c3c"
BG_ACTIVITY = "#333333"
BG_SIDEBAR = "#252526"
BORDER = "#2b2b2b"
STATUS = "#007acc"
TEXT = "#cccccc"
TEXT_DIM = "#858585"
TEXT_BRIGHT = "#ffffff"
ACCENT = "#569cd6"
GREEN = "#4ec9b0"
YELLOW = "#dcdcaa"
ORANGE = "#ce9178"
PURPLE = "#c586c0"
RED = "#f14c4c"
BLUE = "#3794ff"
COMMENT = "#6a9955"
LINE_NO = "#858585"
CURRENT_LINE = "#2a2d2e"

MONO = "'Consolas','Menlo','Monaco','Courier New',monospace"
UI = "'Segoe UI','SF Pro Text',-apple-system,system-ui,sans-serif"

W, H = 1400, 900


# --------------------------------------------------------------------------- #
# SVG primitives
# --------------------------------------------------------------------------- #
def esc(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def rect(x: float, y: float, w: float, h: float, fill: str, rx: float = 0, stroke: str = "", sw: float = 1, opacity: float = 1.0) -> str:
    stroke_attr = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    op = f' opacity="{opacity}"' if opacity != 1.0 else ""
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}"{stroke_attr}{op}/>'


def text(
    x: float,
    y: float,
    value: Any,
    size: float = 13,
    fill: str = TEXT,
    family: str = MONO,
    weight: str = "normal",
    anchor: str = "start",
    opacity: float = 1.0,
    letter_spacing: float = 0,
) -> str:
    ls = f' letter-spacing="{letter_spacing}"' if letter_spacing else ""
    op = f' opacity="{opacity}"' if opacity != 1.0 else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size}" '
        f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}"{ls}{op}>{esc(value)}</text>'
    )


def line(x1: float, y1: float, x2: float, y2: float, stroke: str, sw: float = 1, dash: str = "", opacity: float = 1.0) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    op = f' opacity="{opacity}"' if opacity != 1.0 else ""
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="{sw}"{d}{op}/>'


def circle(cx: float, cy: float, r: float, fill: str, stroke: str = "", sw: float = 0) -> str:
    s = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}"{s}/>'


def path(d: str, fill: str = "none", stroke: str = "", sw: float = 2, opacity: float = 1.0) -> str:
    s = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ""
    op = f' opacity="{opacity}"' if opacity != 1.0 else ""
    return f'<path d="{d}" fill="{fill}"{s}{op}/>'


def polyline(points: Sequence[tuple[float, float]], stroke: str, sw: float = 2.5, fill: str = "none") -> str:
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return f'<polyline points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}" stroke-linejoin="round"/>'


# --- vector icons (emoji-free: no glyph font is guaranteed in every renderer) - #
def icon_x(cx: float, cy: float, s: float = 5, stroke: str = TEXT_DIM, sw: float = 1.4) -> str:
    return (
        line(cx - s, cy - s, cx + s, cy + s, stroke, sw)
        + line(cx + s, cy - s, cx - s, cy + s, stroke, sw)
    )


def icon_dots(cx: float, cy: float, colour: str = TEXT_DIM, r: float = 1.6, gap: float = 5) -> str:
    return "".join(circle(cx + (i - 1) * gap, cy, r, colour) for i in range(3))


def icon_arrow(x: float, y: float, colour: str = TEXT_DIM, size: float = 9, direction: str = "right") -> str:
    s = size
    if direction == "right":
        return line(x - s, y, x + s, y, colour, 1.5) + path(
            f"M {x + s - 4:.1f} {y - 4:.1f} L {x + s:.1f} {y:.1f} L {x + s - 4:.1f} {y + 4:.1f}",
            stroke=colour, sw=1.5,
        )
    return line(x - s, y, x + s, y, colour, 1.5) + path(
        f"M {x - s + 4:.1f} {y - 4:.1f} L {x - s:.1f} {y:.1f} L {x - s + 4:.1f} {y + 4:.1f}",
        stroke=colour, sw=1.5,
    )


def icon_refresh(cx: float, cy: float, colour: str = TEXT_DIM, r: float = 7) -> str:
    return (
        path(f"M {cx + r:.1f} {cy:.1f} A {r} {r} 0 1 1 {cx - r * 0.4:.1f} {cy - r * 0.92:.1f}", stroke=colour, sw=1.6)
        + path(f"M {cx - r * 0.4:.1f} {cy - r * 0.92:.1f} l 5 -1 l -1 5 z", fill=colour)
    )


def icon_check(x: float, y: float, colour: str = TEXT_BRIGHT, s: float = 5) -> str:
    return polyline([(x - s, y), (x - s * 0.2, y + s * 0.75), (x + s, y - s * 0.8)], colour, 1.8)


def icon_grid(x: float, y: float, colour: str = TEXT_DIM, s: float = 6) -> str:
    parts = []
    for dx in (-s, 1):
        for dy in (-s, 1):
            parts.append(rect(x + dx, y + dy, s - 2, s - 2, colour, rx=1.2))
    return "".join(parts)


def icon_gear(cx: float, cy: float, colour: str = TEXT_DIM, r: float = 7) -> str:
    parts = [circle(cx, cy, r * 0.45, colour), circle(cx, cy, r, "none", stroke=colour, sw=1.5)]
    for k in range(4):
        ang = math.radians(k * 45)
        parts.append(line(cx + math.cos(ang) * r, cy + math.sin(ang) * r,
                          cx + math.cos(ang) * (r + 3), cy + math.sin(ang) * (r + 3), colour, 1.6))
    return "".join(parts)


def icon_expand(x: float, y: float, colour: str = TEXT_DIM, s: float = 7) -> str:
    return (
        polyline([(x - s, y - s + 4), (x - s, y - s), (x - s + 4, y - s)], colour, 1.5)
        + polyline([(x + s - 4, y - s), (x + s, y - s), (x + s, y - s + 4)], colour, 1.5)
        + polyline([(x + s, y + s - 4), (x + s, y + s), (x + s - 4, y + s)], colour, 1.5)
        + polyline([(x - s + 4, y + s), (x - s, y + s), (x - s, y + s - 4)], colour, 1.5)
    )


def icon_chevron_down(x: float, y: float, colour: str = TEXT_DIM, s: float = 5) -> str:
    return polyline([(x - s, y - s / 2), (x, y + s / 2), (x + s, y - s / 2)], colour, 1.6)


def icon_play(cx: float, cy: float, colour: str = TEXT_DIM, s: float = 7) -> str:
    return path(f"M {cx - s * 0.6:.1f} {cy - s:.1f} L {cx + s * 0.7:.1f} {cy:.1f} L {cx - s * 0.6:.1f} {cy + s:.1f} Z", fill=colour)


def icon_bug(cx: float, cy: float, colour: str = TEXT_DIM, s: float = 7) -> str:
    return (
        circle(cx, cy, s * 0.55, "none", stroke=colour, sw=1.5)
        + line(cx - s, cy - s * 0.6, cx - s * 0.5, cy - s * 0.3, colour, 1.4)
        + line(cx + s, cy - s * 0.6, cx + s * 0.5, cy - s * 0.3, colour, 1.4)
        + line(cx - s, cy + s * 0.6, cx - s * 0.5, cy + s * 0.3, colour, 1.4)
        + line(cx + s, cy + s * 0.6, cx + s * 0.5, cy + s * 0.3, colour, 1.4)
    )


def icon_branch(cx: float, cy: float, colour: str = TEXT_DIM, s: float = 7) -> str:
    return (
        circle(cx - s * 0.5, cy - s * 0.6, 2.2, colour)
        + circle(cx - s * 0.5, cy + s * 0.6, 2.2, colour)
        + circle(cx + s * 0.6, cy, 2.2, colour)
        + line(cx - s * 0.5, cy - s * 0.4, cx - s * 0.5, cy + s * 0.4, colour, 1.4)
        + path(f"M {cx - s * 0.5:.1f} {cy - s * 0.1:.1f} C {cx + s * 0.4:.1f} {cy - s * 0.1:.1f} {cx + s * 0.6:.1f} {cy - s * 0.3:.1f} {cx + s * 0.6:.1f} {cy:.1f}", stroke=colour, sw=1.4)
    )


def icon_search(cx: float, cy: float, colour: str = TEXT_DIM, s: float = 7) -> str:
    return (
        circle(cx - 1.5, cy - 1.5, s * 0.65, "none", stroke=colour, sw=1.6)
        + line(cx + 2.5, cy + 2.5, cx + s, cy + s, colour, 1.8)
    )


def icon_shield(cx: float, cy: float, colour: str = "#2C9FB5", s: float = 10) -> str:
    return path(
        f"M {cx:.1f} {cy - s:.1f} L {cx + s * 0.8:.1f} {cy - s * 0.55:.1f} "
        f"L {cx + s * 0.8:.1f} {cy + s * 0.2:.1f} L {cx:.1f} {cy + s:.1f} "
        f"L {cx - s * 0.8:.1f} {cy + s * 0.2:.1f} L {cx - s * 0.8:.1f} {cy - s * 0.55:.1f} Z",
        fill=colour, opacity=0.9,
    )


# --------------------------------------------------------------------------- #
# VS Code chrome
# --------------------------------------------------------------------------- #
def window_frame(x: float = 20, y: float = 20, w: float = W - 40, h: float = H - 40, radius: float = 12) -> str:
    parts = [
        f'<filter id="shadow" x="-10%" y="-10%" width="130%" height="130%">'
        f'<feDropShadow dx="0" dy="14" stdDeviation="22" flood-color="#000000" flood-opacity="0.55"/></filter>',
    ]
    body = rect(x, y, w, h, BG, rx=radius)
    frame = rect(x, y, w, h, "#00000000", rx=radius, stroke="#3a3a3a", sw=1.2)
    return "<defs>" + "".join(parts) + "</defs>" + f'<g filter="url(#shadow)">{body}{frame}</g>'


def title_bar(x: float, y: float, w: float, title: str) -> str:
    h = 38
    parts = [rect(x, y, w, h, BG_TITLE, rx=12)]
    parts.append(rect(x, y + h - 12, w, 12, BG_TITLE))
    # macOS traffic lights
    for i, colour in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
        parts.append(circle(x + 22 + i * 20, y + 19, 6, colour))
    parts.append(text(x + w / 2, y + 24, title, 12.5, "#d4d4d4", UI, anchor="middle"))
    return "".join(parts)


def tab_bar(x: float, y: float, w: float, tabs: Sequence[str], active: int, accent_colour: str = BLUE) -> str:
    h = 36
    parts = [rect(x, y, w, h, BG_ALT)]
    tx = x + 6
    for i, tab in enumerate(tabs):
        tw = 11 + len(tab) * 7.0 + 26
        if i == active:
            parts.append(rect(tx, y, tw, h, BG))
            parts.append(rect(tx, y, tw, 2, accent_colour))
            colour = TEXT_BRIGHT
        else:
            parts.append(rect(tx, y, tw, h, BG_ALT))
            colour = TEXT_DIM
        parts.append(text(tx + 14, y + 23, tab, 12.5, colour, UI))
        parts.append(icon_x(tx + tw - 14, y + 18, 4.2, TEXT_DIM))
        tx += tw + 1
    parts.append(line(x, y + h, x + w, y + h, BORDER))
    return "".join(parts)


def activity_bar(x: float, y: float, h: float) -> str:
    w = 52
    parts = [rect(x, y, w, h, BG_ACTIVITY)]
    drawers = [icon_grid, icon_search, icon_branch, icon_bug, icon_play, icon_gear]
    for i, drawer in enumerate(drawers):
        colour = TEXT_BRIGHT if i == 1 else TEXT_DIM
        cx, cy = x + 26, y + 44 + i * 52
        if i == 1:
            parts.append(rect(x + 4, y + 18 + i * 52, 44, 40, "#00000000", rx=6))
        parts.append(drawer(cx, cy, colour))
    parts.append(rect(x, y, 2, h, BLUE, opacity=0.0))
    return "".join(parts)


def sidebar(x: float, y: float, w: float, h: float, heading: str, entries: Sequence[tuple[str, int, bool]]) -> str:
    """``entries`` = (label, indent, is_file)."""
    parts = [rect(x, y, w, h, BG_SIDEBAR), line(x + w, y, x + w, y + h, BORDER)]
    parts.append(text(x + 16, y + 26, heading.upper(), 11, TEXT_DIM, UI, weight="600", letter_spacing=0.8))
    parts.append(icon_dots(x + w - 20, y + 22))
    ry = y + 44
    for label, indent, is_file in entries:
        colour = TEXT if not is_file else "#9cdcfe"
        if is_file:
            parts.append(text(x + 16 + indent, ry, "·", 13, "#c586c0", MONO))
        else:
            parts.append(icon_chevron_down(x + 18 + indent, ry - 4, TEXT_DIM, 4))
        parts.append(text(x + 30 + indent, ry, label, 12.5, colour, UI))
        ry += 24
    return "".join(parts)


def breadcrumbs(x: float, y: float, w: float, crumbs: Sequence[str]) -> str:
    h = 28
    parts = [rect(x, y, w, h, BG), line(x, y + h, x + w, y + h, BORDER)]
    cx = x + 16
    for i, crumb in enumerate(crumbs):
        colour = TEXT_DIM if i < len(crumbs) - 1 else TEXT
        parts.append(text(cx, y + 19, crumb, 12, colour, UI))
        cx += len(crumb) * 6.6 + 14
        if i < len(crumbs) - 1:
            parts.append(text(cx - 10, y + 19, "›", 12, TEXT_DIM, UI))
    return "".join(parts)


def status_bar(x: float, y: float, w: float, left: str, right: str) -> str:
    h = 26
    parts = [rect(x, y, w, h, STATUS, rx=0)]
    parts.append(icon_check(x + 16, y + 13, TEXT_BRIGHT, 4.5))
    parts.append(text(x + 28, y + 18, left, 11.5, TEXT_BRIGHT, UI))
    parts.append(text(x + w - 16, y + 18, right, 11.5, TEXT_BRIGHT, UI, anchor="end"))
    return "".join(parts)


def panel_header(x: float, y: float, w: float, label: str, accent: str = BLUE) -> str:
    parts = [rect(x, y, w, 30, "#2d2d2d"), line(x, y + 30, x + w, y + 30, BORDER)]
    parts.append(rect(x, y, 3, 30, accent))
    parts.append(text(x + 14, y + 20, label, 12, TEXT_BRIGHT, UI, weight="600"))
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Chart helpers
# --------------------------------------------------------------------------- #
def confidence_curve_points(
    curve: Sequence[tuple[float, float]],
    x0: float,
    y0: float,
    w: float,
    h: float,
    x_max: float,
) -> list[tuple[float, float]]:
    return [(x0 + (px / x_max) * w, y0 + h - (py / 100.0) * h) for px, py in curve]


def area_path(points: Sequence[tuple[float, float]], base_y: float) -> str:
    if not points:
        return ""
    d = f"M {points[0][0]:.1f} {base_y:.1f} "
    d += " ".join(f"L {x:.1f} {y:.1f}" for x, y in points)
    d += f" L {points[-1][0]:.1f} {base_y:.1f} Z"
    return d


# --------------------------------------------------------------------------- #
# Real data (with a safe fallback)
# --------------------------------------------------------------------------- #
FALLBACK: dict[str, Any] = {
    "towers": ["DE", "DA", "DS", "PMO"],
    "names": {"DE": "Data Engineering", "DA": "Data Analytics", "DS": "Data Science", "PMO": "PMO"},
    "support_tax": {"DE": 25.7, "DA": 25.8, "DS": 27.7, "PMO": 22.0},
    "mttr": {"DE": 7.8, "DA": 3.9, "DS": 10.9, "PMO": 7.9},
    "incidents": {"DE": 240, "DA": 388, "DS": 144, "PMO": 66},
    "hours": {"DE": 1855, "DA": 1530, "DS": 1574, "PMO": 518},
    "curve": [(0, 100.0), (120, 100.0), (180, 97.0), (220, 88.0), (240, 74.0), (260, 55.0), (280, 35.0), (320, 8.0), (352, 0.5)],
    "summary_rows": [
        ("DE", "10.4", "82.4", "25.7%", "143.5", "57.7%"),
        ("DA", "16.8", "64.4", "25.8%", "102.2", "57.3%"),
        ("DS", "5.5", "56.8", "27.7%", "109.9", "58.6%"),
        ("PMO", "2.8", "21.1", "22.0%", "37.3", "68.9%"),
    ],
    "recommend": {"DE": 70.6, "DA": 59.1, "DS": 38.0, "PMO": 23.5},
    "gross": {"DE": 320, "DA": 250, "DS": 205, "PMO": 96},
}


def load_real_data() -> dict[str, Any]:
    """Run the real pipeline; fall back to reference values on any failure."""
    try:
        from sprintshield.analytics.metrics import tower_metrics_table
        from sprintshield.analytics.monte_carlo import simulate_all_towers
        from sprintshield.client.synthetic_generator import generate_telemetry
        from sprintshield.config import load_config

        config = load_config(project_root=_ROOT)
        snapshots, tickets = generate_telemetry(sprints=24, config=config)
        metrics = tower_metrics_table(snapshots, tickets)
        simulations, summary = simulate_all_towers(config, snapshots, tickets, runs=10_000)

        data: dict[str, Any] = {
            "towers": [str(t) for t in metrics["tower_id"]],
            "names": {t.tower_id: t.name for t in config.towers},
            "support_tax": {str(r["tower_id"]): float(r["support_tax_pct"]) for _, r in metrics.iterrows()},
            "mttr": {str(r["tower_id"]): float(r["mttr_hours"] or 0) for _, r in metrics.iterrows()},
            "incidents": {str(r["tower_id"]): int(r["total_incidents"]) for _, r in metrics.iterrows()},
            "hours": {str(r["tower_id"]): float(r["total_unplanned_hours"]) for _, r in metrics.iterrows()},
            "gross": {t.tower_id: t.gross_hours_per_sprint for t in config.towers},
            "summary_rows": [],
            "recommend": {},
            "curve": [],
        }
        for _, row in summary.iterrows():
            tower = str(row["tower_id"])
            data["summary_rows"].append(
                (
                    tower,
                    f"{float(row['mean_incidents']):.1f}",
                    f"{float(row['mean_support_hours']):.1f}",
                    f"{float(row['mean_support_tax']) * 100:.1f}%",
                    f"{float(row['p85_support_hours']):.1f}",
                    f"{float(row['delivery_confidence']) * 100:.1f}%",
                )
            )
            data["recommend"][tower] = float(row["max_committed_hours_at_target"]) / config.simulation.hours_per_point

        de_result = simulations["DE"]
        curve_frame = de_result.confidence_curve()
        # Down-sample for a clean polyline.
        step = max(len(curve_frame) // 40, 1)
        data["curve"] = [
            (float(r["planned_hours"]), float(r["delivery_confidence"]) * 100)
            for _, r in curve_frame.iloc[::step].iterrows()
        ]
        return data
    except Exception as exc:  # pragma: no cover - fallback path
        print(f"[warn] falling back to reference data ({exc})")
        return dict(FALLBACK)


# --------------------------------------------------------------------------- #
# Screenshot 1: Interactive window - Monte Carlo
# --------------------------------------------------------------------------- #
def screenshot_interactive_monte_carlo(data: dict[str, Any]) -> str:
    p: list[str] = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
    p.append(rect(0, 0, W, H, "#0b1418"))
    p.append(window_frame())

    x0, y0 = 20, 20
    p.append(title_bar(x0, y0, W - 40, "interactive_analysis.py — sprintshield-analytics — Visual Studio Code"))
    tabs = ["interactive_analysis.py", "app.py", "monte_carlo.py", "sprint_capacity_audit.ipynb"]
    p.append(tab_bar(x0, y0 + 38, W - 40, tabs, active=0, accent_colour=YELLOW))

    top = y0 + 74
    body_h = H - 40 - top - 26 + y0
    p.append(activity_bar(x0, top, body_h))
    p.append(
        sidebar(
            x0 + 52,
            top,
            236,
            body_h,
            "Explorer",
            [
                ("sprintshield-analytics", 0, False),
                (".vscode", 10, False),
                ("src", 10, False),
                ("sprintshield", 20, False),
                ("analytics", 30, False),
                ("monte_carlo.py", 40, True),
                ("arrival_model.py", 40, True),
                ("client", 30, False),
                ("synthetic_generator.py", 40, True),
                ("scripts", 10, False),
                ("interactive_analysis.py", 20, True),
                ("tests", 10, False),
                ("README.md", 10, True),
            ],
        )
    )

    ex = x0 + 52 + 236
    ew = W - 40 - 52 - 236
    p.append(rect(ex, top, ew, body_h, BG))
    p.append(breadcrumbs(ex, top, ew, ["scripts", "interactive_analysis.py", "Python Interactive", "# 6. Monte Carlo"]))

    # ---- Left: code cell --------------------------------------------------- #
    code_top = top + 28
    code_w = ew * 0.44
    p.append(rect(ex, code_top, code_w, body_h - 28, BG))
    p.append(line(ex + code_w, code_top, ex + code_w, code_top + body_h - 28, BORDER))
    p.append(rect(ex, code_top, code_w, 30, "#2d2d2d"))
    p.append(text(ex + 14, code_top + 20, "Python Interactive", 12, YELLOW, UI, weight="600"))
    p.append(icon_chevron_down(ex + code_w - 62, code_top + 15, TEXT_DIM, 4.5))
    p.append(icon_expand(ex + code_w - 40, code_top + 15, TEXT_DIM, 6))
    p.append(icon_dots(ex + code_w - 18, code_top + 15))

    code_lines = [
        ("# %% [markdown]", COMMENT),
        ("# ## 6. Monte Carlo sprint capacity simulation", COMMENT),
        ("", TEXT),
        ("# %%", COMMENT),
        ("simulations, summary = simulate_all_towers(", TEXT),
        ("    CONFIG, snapshots, tickets, runs=10_000,", TEXT),
        (")", TEXT),
        ("summary[[", TEXT),
        ('    "tower_id", "mean_support_tax",', TEXT),
        ('    "p85_support_hours", "delivery_confidence",', TEXT),
        ("]]", TEXT),
        ("", TEXT),
        ("de = simulations['DE']", TEXT),
        ("de.delivery_confidence()", TEXT),
    ]
    ly = code_top + 56
    for i, (src, colour) in enumerate(code_lines):
        p.append(text(ex + 20, ly, f"{i + 1}", 12, LINE_NO, MONO, anchor="end"))
        if src.startswith("#"):
            p.append(text(ex + 34, ly, src, 12.5, COMMENT, MONO))
        else:
            p.append(text(ex + 34, ly, src, 12.5, "#9cdcfe" if "(" in src else TEXT, MONO))
        ly += 21
    # execution highlight
    p.append(rect(ex + 6, code_top + 56 + 13 * 21 - 15, code_w - 12, 20, CURRENT_LINE))

    # ---- Right: Interactive output ---------------------------------------- #
    ox = ex + code_w + 1
    ow = ew - code_w - 1
    p.append(rect(ox, code_top, ow, body_h - 28, BG))
    p.append(panel_header(ox, code_top, ow, "Interactive — Monte Carlo (10,000 runs)", YELLOW))

    # Summary table
    ty = code_top + 46
    cols = ["Tower", "μ Incid.", "μ Supp.h", "Tax", "P85 h", "Conf"]
    col_x = [ox + 20, ox + 108, ox + 196, ox + 288, ox + 360, ox + 448]
    p.append(rect(ox + 12, ty - 18, ow - 24, 26, "#2d2d2d", rx=4))
    for cx, col in zip(col_x, cols):
        p.append(text(cx, ty, col, 11.5, "#dcdcaa", UI, weight="600"))
    ty += 26
    for idx, row in enumerate(data["summary_rows"]):
        if idx % 2 == 0:
            p.append(rect(ox + 12, ty - 16, ow - 24, 24, "#262626", rx=3))
        tax_val = float(row[3].rstrip("%"))
        tax_colour = RED if tax_val > 30 else ("#d7ba7d" if tax_val > 20 else GREEN)
        vals = [row[0], row[1], row[2], row[3], row[4], row[5]]
        for j, (cx, val) in enumerate(zip(col_x, vals)):
            colour = tax_colour if j == 3 else TEXT
            p.append(text(cx, ty + 1, val, 11.5, colour, MONO))
        ty += 24

    # Confidence curve
    chart_x, chart_y = ox + 30, ty + 34
    chart_w, chart_h = ow - 70, 250
    p.append(rect(chart_x - 14, chart_y - 26, chart_w + 40, chart_h + 74, "#1a1a1a", rx=6))
    p.append(text(chart_x, chart_y - 8, "Delivery confidence curve — DE tower", 12, TEXT_BRIGHT, UI, weight="600"))

    # axes + gridlines
    for frac in [0, 0.25, 0.5, 0.75, 1.0]:
        gy = chart_y + chart_h - frac * chart_h
        p.append(line(chart_x, gy, chart_x + chart_w, gy, "#333333", 1, dash="3 4"))
        p.append(text(chart_x - 10, gy + 4, f"{int(frac * 100)}%", 9.5, LINE_NO, MONO, anchor="end"))
    p.append(line(chart_x, chart_y, chart_x, chart_y + chart_h, "#555555"))
    p.append(line(chart_x, chart_y + chart_h, chart_x + chart_w, chart_y + chart_h, "#555555"))

    x_max = max(px for px, _ in data["curve"]) * 1.08
    pts = confidence_curve_points(data["curve"], chart_x, chart_y, chart_w, chart_h, x_max)
    p.append(path(area_path(pts, chart_y + chart_h), fill="#2c9fb5", opacity=0.18))
    p.append(polyline(pts, "#2c9fb5", 2.6))

    # 85% target line
    target_y = chart_y + chart_h - 0.85 * chart_h
    p.append(line(chart_x, target_y, chart_x + chart_w, target_y, ORANGE, 1.6, dash="6 5"))
    p.append(text(chart_x + chart_w - 4, target_y - 6, "target 85%", 10, ORANGE, UI, anchor="end"))

    for frac in [0, 0.25, 0.5, 0.75, 1.0]:
        gx = chart_x + frac * chart_w
        p.append(text(gx, chart_y + chart_h + 18, f"{int(frac * x_max)}", 9.5, LINE_NO, MONO, anchor="middle"))
    p.append(text(chart_x + chart_w / 2, chart_y + chart_h + 40, "Planned commitment (hours)", 10.5, TEXT_DIM, UI, anchor="middle"))

    # recommendation callout
    rec = data["recommend"].get("DE", 70.6)
    p.append(rect(ox + 30, chart_y + chart_h + 46, ow - 70, 34, "#12312a", rx=6, stroke=GREEN, sw=1))
    p.append(icon_arrow(ox + 50, chart_y + chart_h + 63, GREEN, 8))
    p.append(text(ox + 66, chart_y + chart_h + 68, f"Commit <= {rec:.0f} story points to hold 85% delivery confidence", 11.5, GREEN, UI))

    p.append(status_bar(x0, H - 46, W - 40, "Python 3.12.13  |  Interactive  |  Jupyter: sprintshield-analytics", "Ln 15, Col 26   UTF-8   LF   Monte Carlo"))
    p.append("</svg>")
    return "".join(p)


# --------------------------------------------------------------------------- #
# Screenshot 2: Split editor - support tax breakdown
# --------------------------------------------------------------------------- #
def screenshot_support_tax_breakdown(data: dict[str, Any]) -> str:
    p: list[str] = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
    p.append(rect(0, 0, W, H, "#0b1418"))
    p.append(window_frame())

    x0, y0 = 20, 20
    p.append(title_bar(x0, y0, W - 40, "interactive_analysis.py — sprintshield-analytics — Visual Studio Code"))
    tabs = ["interactive_analysis.py", "sprint_capacity_audit.ipynb", "metrics.py", "README.md"]
    p.append(tab_bar(x0, y0 + 38, W - 40, tabs, active=0, accent_colour=YELLOW))

    top = y0 + 74
    body_h = H - 40 - top - 26 + y0
    p.append(activity_bar(x0, top, body_h))
    p.append(
        sidebar(
            x0 + 52,
            top,
            210,
            body_h,
            "Explorer",
            [
                ("src", 0, False),
                ("sprintshield", 10, False),
                ("analytics", 20, False),
                ("metrics.py", 30, True),
                ("effort_distribution.py", 30, True),
                ("reporting", 20, False),
                ("excel_exporter.py", 30, True),
                ("scripts", 10, False),
                ("interactive_analysis.py", 20, True),
            ],
        )
    )

    ex = x0 + 52 + 210
    ew = W - 40 - 52 - 210
    p.append(rect(ex, top, ew, body_h, BG))
    p.append(breadcrumbs(ex, top, ew, ["scripts", "interactive_analysis.py", "# 3. Support tax across towers"]))

    code_top = top + 28
    half = (ew - 2) / 2
    # ---- left editor ------------------------------------------------------- #
    p.append(rect(ex, code_top, half, body_h - 28, BG))
    p.append(line(ex + half, code_top, ex + half, code_top + body_h - 28, "#3c3c3c", 1))
    p.append(panel_header(ex, code_top, half, "interactive_analysis.py", BLUE))

    lines = [
        ("# %% [markdown]", COMMENT),
        ("# ## 3. Support tax across towers", COMMENT),
        ("", TEXT),
        ("# %%", COMMENT),
        ("tower_metrics = tower_metrics_table(", TEXT),
        ("    snapshots, tickets", TEXT),
        (")", TEXT),
        ("build_support_tax_bar(tower_metrics, names)", YELLOW),
        ("", TEXT),
        ("for t, row in tower_metrics.iterrows():", PURPLE),
        ("    print(row['tower_id'],", TEXT),
        ("          f\"{row['support_tax_pct']:.1f}%\")", ORANGE),
        ("", TEXT),
        ("# amber > 20%  |  red > 30%", COMMENT),
    ]
    ly = code_top + 56
    for i, (src, colour) in enumerate(lines):
        p.append(text(ex + 22, ly, f"{i + 1}", 12, LINE_NO, MONO, anchor="end"))
        p.append(text(ex + 38, ly, src, 12.5, colour, MONO))
        ly += 22

    # minimap
    mm_x = ex + half - 74
    p.append(rect(mm_x, code_top + 32, 60, 180, "#1a1a1a", rx=3))
    for i in range(26):
        wid = 8 + (i * 7) % 44
        p.append(rect(mm_x + 4, code_top + 38 + i * 6.5, wid, 2.4, "#3a3a3a"))

    # ---- right: interactive output ---------------------------------------- #
    ox = ex + half + 2
    ow = half - 2
    p.append(rect(ox, code_top, ow, body_h - 28, BG))
    p.append(panel_header(ox, code_top, ow, "Interactive — Support Tax by Tower", YELLOW))

    towers = data["towers"]
    taxes = data["support_tax"]
    names = data["names"]
    hours = data["hours"]

    # bar chart
    cx, cy = ox + 70, code_top + 76
    cw, ch = ow - 150, 210
    p.append(text(cx, cy - 20, "Support tax vs. 20% / 30% guardrails", 12.5, TEXT_BRIGHT, UI, weight="600"))
    for frac, label in [(0.0, "0%"), (0.5, "15%"), (1.0, "30%")]:
        gy = cy + ch - frac * ch
        p.append(line(cx, gy, cx + cw, gy, "#333333", 1, dash="3 4"))
        p.append(text(cx - 12, gy + 4, label, 9.5, LINE_NO, MONO, anchor="end"))
    p.append(line(cx, cy + ch, cx + cw, cy + ch, "#555555"))
    p.append(line(cx, cy, cx, cy + ch, "#555555"))

    max_tax = 30.0
    bar_w = cw / max(len(towers), 1) * 0.5
    gap = cw / max(len(towers), 1)
    for i, tower in enumerate(towers):
        value = taxes.get(tower, 0.0)
        bh = min(value / max_tax, 1.05) * ch
        bx = cx + gap * i + gap / 2 - bar_w / 2
        colour = RED if value > 30 else ("#d7ba7d" if value > 20 else GREEN)
        p.append(rect(bx, cy + ch - bh, bar_w, bh, colour, rx=3))
        p.append(text(bx + bar_w / 2, cy + ch - bh - 8, f"{value:.1f}%", 11, colour, MONO, anchor="middle"))
        p.append(text(bx + bar_w / 2, cy + ch + 20, tower, 12, TEXT, UI, weight="600", anchor="middle"))
        p.append(text(bx + bar_w / 2, cy + ch + 36, names.get(tower, ""), 9, TEXT_DIM, UI, anchor="middle"))

    # guardrail lines
    warn_y = cy + ch - (20 / max_tax) * ch
    crit_y = cy + ch - (30 / max_tax) * ch
    p.append(line(cx, warn_y, cx + cw, warn_y, "#d7ba7d", 1.4, dash="5 4"))
    p.append(text(cx + cw + 6, warn_y + 4, "20%", 9.5, "#d7ba7d", MONO))
    p.append(line(cx, crit_y, cx + cw, crit_y, RED, 1.4, dash="5 4"))
    p.append(text(cx + cw + 6, crit_y + 4, "30%", 9.5, RED, MONO))

    # table of hours
    ty = cy + ch + 68
    p.append(rect(ox + 20, ty - 18, ow - 40, 26, "#2d2d2d", rx=4))
    heads = ["Tower", "Support Tax", "Unplanned h", "MTTR", "Status"]
    hx = [ox + 34, ox + 150, ox + 268, ox + 386, ox + 452]
    for hxp, head in zip(hx, heads):
        p.append(text(hxp, ty, head, 11.5, "#dcdcaa", UI, weight="600"))
    ty += 26
    for i, tower in enumerate(towers):
        value = taxes.get(tower, 0.0)
        status = "CRITICAL" if value > 30 else ("WATCH" if value > 20 else "HEALTHY")
        colour = RED if value > 30 else ("#d7ba7d" if value > 20 else GREEN)
        if i % 2 == 0:
            p.append(rect(ox + 20, ty - 16, ow - 40, 24, "#262626", rx=3))
        p.append(text(hx[0], ty + 1, tower, 11.5, TEXT, MONO))
        p.append(text(hx[1], ty + 1, f"{value:.1f}%", 11.5, colour, MONO))
        p.append(text(hx[2], ty + 1, f"{hours.get(tower, 0):,.0f}", 11.5, TEXT, MONO))
        p.append(text(hx[3], ty + 1, f"{data['mttr'].get(tower, 0):.1f} h", 11.5, TEXT, MONO))
        p.append(text(hx[4], ty + 1, status, 11, colour, UI, weight="600"))
        ty += 24

    p.append(status_bar(x0, H - 46, W - 40, "Python 3.12.13  |  Interactive  |  Split editor", "Ln 9, Col 42   UTF-8   LF   Support Tax"))
    p.append("</svg>")
    return "".join(p)


# --------------------------------------------------------------------------- #
# Screenshot 3: Simple Browser running the Streamlit dashboard
# --------------------------------------------------------------------------- #
def screenshot_simple_browser(data: dict[str, Any]) -> str:
    p: list[str] = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
    p.append(rect(0, 0, W, H, "#0b1418"))
    p.append(window_frame())

    x0, y0 = 20, 20
    p.append(title_bar(x0, y0, W - 40, "Simple Browser: http://localhost:8501 — sprintshield-analytics — Visual Studio Code"))
    tabs = ["Simple Browser: http://localhost:8501", "app.py", "interactive_analysis.py"]
    p.append(tab_bar(x0, y0 + 38, W - 40, tabs, active=0, accent_colour=BLUE))

    top = y0 + 74
    body_h = H - 40 - top - 26 + y0
    p.append(activity_bar(x0, top, body_h))

    bx = x0 + 52
    bw = W - 40 - 52
    # browser toolbar
    p.append(rect(bx, top, bw, 38, "#2d2d2d"))
    p.append(line(bx, top + 38, bx + bw, top + 38, BORDER))
    p.append(icon_arrow(bx + 26, top + 19, TEXT_DIM, 8, "left"))
    p.append(icon_arrow(bx + 54, top + 19, TEXT_DIM, 8, "right"))
    p.append(icon_refresh(bx + 84, top + 19, TEXT_DIM, 6.5))
    p.append(rect(bx + 104, top + 7, bw - 240, 25, "#1e1e1e", rx=12))
    p.append(text(bx + 124, top + 24, "http://localhost:8501", 12, "#9cdcfe", MONO))
    p.append(icon_shield(bx + bw - 128, top + 19, "#2C9FB5", 7))
    p.append(text(bx + bw - 112, top + 24, "SprintShield", 11.5, TEXT_DIM, UI))

    # dashboard canvas
    dash_top = top + 38
    dash_h = body_h - 38
    p.append(rect(bx, dash_top, bw, dash_h, "#0f2027"))

    # sidebar
    sb_w = 250
    p.append(rect(bx, dash_top, sb_w, dash_h, "#12262e"))
    p.append(line(bx + sb_w, dash_top, bx + sb_w, dash_top + dash_h, "#1c3b47"))
    p.append(icon_shield(bx + 32, dash_top + 34, "#2C9FB5", 10))
    p.append(text(bx + 50, dash_top + 40, "SprintShield", 16, TEXT_BRIGHT, UI, weight="700"))
    p.append(text(bx + 22, dash_top + 60, "Unplanned Support Demand &", 9.5, "#9fb6bd", UI))
    p.append(text(bx + 22, dash_top + 74, "Capacity Forecaster", 9.5, "#9fb6bd", UI))

    p.append(text(bx + 22, dash_top + 108, "Monte Carlo runs", 11, "#9fb6bd", UI))
    p.append(rect(bx + 22, dash_top + 116, sb_w - 44, 8, "#1c3b47", rx=4))
    p.append(rect(bx + 22, dash_top + 116, (sb_w - 44) * 0.8, 8, "#2C9FB5", rx=4))
    p.append(text(bx + 22, dash_top + 142, "Target delivery confidence", 11, "#9fb6bd", UI))
    p.append(rect(bx + 22, dash_top + 150, sb_w - 44, 8, "#1c3b47", rx=4))
    p.append(rect(bx + 22, dash_top + 150, (sb_w - 44) * 0.85, 8, "#4CAF50", rx=4))
    p.append(text(bx + 22, dash_top + 176, "Storage backend", 11, "#9fb6bd", UI))
    p.append(rect(bx + 22, dash_top + 184, 120, 24, "#0a161b", rx=5))
    p.append(text(bx + 32, dash_top + 200, "deltalake", 11, "#9cdcfe", MONO))
    p.append(text(bx + 22, dash_top + 232, "24 sprints · 838 tickets", 9.5, "#7f979e", UI))

    # main content
    mx = bx + sb_w + 28
    mw = bw - sb_w - 56
    p.append(text(mx, dash_top + 40, "SprintShield Analytics", 24, TEXT_BRIGHT, UI, weight="700"))
    p.append(text(mx, dash_top + 60, "Quantifying engineering capacity lost to unplanned production support", 11, "#9fb6bd", UI))

    # KPI strip
    tax = sum(data["support_tax"].values()) / max(len(data["support_tax"]), 1)
    kpis = [
        ("Overall Support Tax", f"{tax:.1f}%", RED if tax > 30 else "#F4A261"),
        ("Unplanned Hours", f"{sum(data['hours'].values()):,.0f}", "#2C9FB5"),
        ("Flow Efficiency", f"{100 - tax:.1f}%", "#4CAF50"),
        ("Incidents", f"{sum(data['incidents'].values()):,.0f}", "#F4A261"),
        ("MTTR", f"{sum(data['mttr'].values()) / max(len(data['mttr']), 1):.1f} h", "#2C9FB5"),
    ]
    kw = (mw - 4 * 14) / 5
    for i, (label, value, colour) in enumerate(kpis):
        kx = mx + i * (kw + 14)
        p.append(rect(kx, dash_top + 78, kw, 74, "#16323d", rx=12, stroke=colour, sw=1))
        p.append(text(kx + 14, dash_top + 112, value, 22, colour, UI, weight="700"))
        p.append(text(kx + 14, dash_top + 134, label.upper(), 8.5, "#9fb6bd", UI, weight="600", letter_spacing=0.6))

    # tabs
    tab_y = dash_top + 172
    labels = ["What-If Planning Simulator", "Tower Capacity Radar", "Active Support WIP", "Reports"]
    tx = mx
    for i, label in enumerate(labels):
        tw = len(label) * 7.6 + 22
        colour = TEXT_BRIGHT if i == 0 else "#7f979e"
        p.append(text(tx + 11, tab_y, label, 11.5, colour, UI, weight="600" if i == 0 else "normal"))
        if i == 0:
            p.append(rect(tx, tab_y + 8, tw, 2.5, "#2C9FB5"))
        tx += tw
    p.append(line(mx, tab_y + 11, mx + mw, tab_y + 11, "#1c3b47"))

    # what-if panel
    py = tab_y + 30
    pw = mw * 0.40
    p.append(rect(mx, py, pw, dash_h - (py - dash_top) - 24, "#12262e", rx=12, stroke="#1c3b47", sw=1))
    p.append(text(mx + 20, py + 30, "What-If Planning Simulator", 14, TEXT_BRIGHT, UI, weight="700"))
    p.append(text(mx + 20, py + 54, "Tower", 10.5, "#9fb6bd", UI))
    p.append(rect(mx + 20, py + 62, pw - 40, 30, "#0a161b", rx=6, stroke="#1c3b47", sw=1))
    p.append(text(mx + 32, py + 82, "DE — Data Engineering", 12, TEXT, UI))
    p.append(text(mx + 20, py + 116, "Planned sprint commitment (story points)", 10.5, "#9fb6bd", UI))
    p.append(text(mx + pw - 30, py + 116, "80", 12, "#2C9FB5", MONO, weight="700", anchor="end"))
    p.append(rect(mx + 20, py + 126, pw - 40, 8, "#1c3b47", rx=4))
    p.append(rect(mx + 20, py + 126, (pw - 40) * 0.52, 8, "#2C9FB5", rx=4))
    p.append(circle(mx + 20 + (pw - 40) * 0.52, py + 130, 8, "#2C9FB5"))

    p.append(text(mx + 20, py + 164, "Gross capacity (hours)", 10.5, "#9fb6bd", UI))
    p.append(text(mx + pw - 30, py + 164, "320", 12, "#4CAF50", MONO, weight="700", anchor="end"))
    p.append(rect(mx + 20, py + 174, pw - 40, 8, "#1c3b47", rx=4))
    p.append(rect(mx + 20, py + 174, (pw - 40) * 0.66, 8, "#4CAF50", rx=4))
    p.append(circle(mx + 20 + (pw - 40) * 0.66, py + 178, 8, "#4CAF50"))

    p.append(rect(mx + 20, py + 200, (pw - 40) / 2 - 8, 54, "#0a161b", rx=8))
    p.append(text(mx + 34, py + 224, "256 h", 17, "#2C9FB5", UI, weight="700"))
    p.append(text(mx + 34, py + 242, "PLANNED HOURS", 8.5, "#9fb6bd", UI, weight="600"))
    p.append(rect(mx + 20 + (pw - 40) / 2 + 8, py + 200, (pw - 40) / 2 - 8, 54, "#0a161b", rx=8))
    p.append(text(mx + 34 + (pw - 40) / 2 + 8, py + 224, "320 h", 17, "#4CAF50", UI, weight="700"))
    p.append(text(mx + 34 + (pw - 40) / 2 + 8, py + 242, "GROSS CAPACITY", 8.5, "#9fb6bd", UI, weight="600"))

    # right: gauge + curve
    gx = mx + pw + 20
    gw = mw - pw - 20
    p.append(rect(gx, py, gw, dash_h - (py - dash_top) - 24, "#12262e", rx=12, stroke="#1c3b47", sw=1))
    p.append(text(gx + 20, py + 30, "Delivery confidence — live", 13, TEXT_BRIGHT, UI, weight="700"))

    # gauge
    gc_x, gc_y = gx + 110, py + 150
    r = 62
    conf = 57.7
    p.append(path(_arc(gc_x, gc_y, r, 180, 360), stroke="#1c3b47", sw=14))
    p.append(path(_arc(gc_x, gc_y, r, 180, 180 + 180 * conf / 100), stroke="#4CAF50", sw=14))
    p.append(text(gc_x, gc_y - 6, f"{conf:.1f}%", 30, "#4CAF50", UI, weight="700", anchor="middle"))
    p.append(text(gc_x, gc_y + 18, "delivery confidence", 10, "#9fb6bd", UI, anchor="middle"))
    p.append(text(gc_x, gc_y + 40, "−27.3 pts vs 85% target", 10, "#F4A261", UI, anchor="middle"))

    # mini curve
    ccx, ccy = gx + 230, py + 76
    ccw, cch = gw - 260, 150
    p.append(rect(ccx - 10, ccy - 22, ccw + 40, cch + 60, "#0a161b", rx=8))
    p.append(text(ccx, ccy - 6, "Confidence curve", 11, TEXT_BRIGHT, UI, weight="600"))
    for frac in [0, 0.5, 1.0]:
        gy = ccy + cch - frac * cch
        p.append(line(ccx, gy, ccx + ccw, gy, "#243b45", 1, dash="3 4"))
    x_max = max(px for px, _ in data["curve"]) * 1.08
    pts = confidence_curve_points(data["curve"], ccx, ccy, ccw, cch, x_max)
    p.append(path(area_path(pts, ccy + cch), fill="#2C9FB5", opacity=0.2))
    p.append(polyline(pts, "#2C9FB5", 2.4))
    ty2 = ccy + cch - 0.85 * cch
    p.append(line(ccx, ty2, ccx + ccw, ty2, "#F4A261", 1.4, dash="5 4"))

    # recommendation banner
    ry = py + dash_h - (py - dash_top) - 84
    p.append(rect(gx, ry, gw, 52, "#12312a", rx=10, stroke="#4CAF50", sw=1))
    p.append(icon_arrow(gx + 40, ry + 20, "#4CAF50", 9))
    p.append(text(gx + 56, ry + 24, f"Commit <= {data['recommend'].get('DE', 70.6):.0f} story points", 13, "#4CAF50", UI, weight="700"))
    p.append(text(gx + 18, ry + 42, "and ring-fence a 143 h support contingency for 85% confidence.", 10.5, "#9fb6bd", UI))

    p.append(status_bar(x0, H - 46, W - 40, "Simple Browser: http://localhost:8501", "Streamlit  ·  Python 3.12.13  ·  Ln 1, Col 1"))
    p.append("</svg>")
    return "".join(p)


def _arc(cx: float, cy: float, r: float, start_deg: float, end_deg: float) -> str:
    start = math.radians(start_deg)
    end = math.radians(end_deg)
    x1, y1 = cx + r * math.cos(start), cy + r * math.sin(start)
    x2, y2 = cx + r * math.cos(end), cy + r * math.sin(end)
    large = 1 if (end_deg - start_deg) > 180 else 0
    return f"M {x1:.2f} {y1:.2f} A {r} {r} 0 {large} 1 {x2:.2f} {y2:.2f}"


# --------------------------------------------------------------------------- #
# Screenshot 4: Excel Viewer tab - capacity ledger
# --------------------------------------------------------------------------- #
def screenshot_excel_viewer(data: dict[str, Any]) -> str:
    p: list[str] = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">']
    p.append(rect(0, 0, W, H, "#0b1418"))
    p.append(window_frame())

    x0, y0 = 20, 20
    p.append(title_bar(x0, y0, W - 40, "sprint_support_ledger.xlsx — sprintshield-analytics — Visual Studio Code"))
    tabs = ["sprint_support_ledger.xlsx", "stakeholder_steerco_deck.pptx", "app.py"]
    p.append(tab_bar(x0, y0 + 38, W - 40, tabs, active=0, accent_colour="#217346"))

    top = y0 + 74
    body_h = H - 40 - top - 26 + y0
    p.append(activity_bar(x0, top, body_h))
    p.append(
        sidebar(
            x0 + 52,
            top,
            220,
            body_h,
            "Explorer",
            [
                ("reports", 0, False),
                ("sprint_support_ledger.xlsx", 10, True),
                ("stakeholder_steerco_deck.pptx", 10, True),
                ("examples", 0, False),
                ("sample_sprint_ledger.xlsx", 10, True),
                ("sample_steerco_deck.pptx", 10, True),
                ("data", 0, False),
                ("delta", 10, False),
            ],
        )
    )

    ex = x0 + 52 + 220
    ew = W - 40 - 52 - 220
    p.append(rect(ex, top, ew, body_h, BG))
    p.append(breadcrumbs(ex, top, ew, ["reports", "sprint_support_ledger.xlsx", "Support Tax Heatmap"]))

    # Excel-viewer toolbar
    tb_y = top + 28
    p.append(rect(ex, tb_y, ew, 34, "#2d2d2d"))
    p.append(line(ex, tb_y + 34, ex + ew, tb_y + 34, BORDER))
    p.append(icon_grid(ex + 24, tb_y + 16, "#4ec9b0", 6))
    p.append(text(ex + 40, tb_y + 22, "Excel Viewer", 12, "#4ec9b0", UI, weight="600"))
    p.append(icon_expand(ex + ew - 66, tb_y + 16, TEXT_DIM, 6))
    p.append(icon_gear(ex + ew - 36, tb_y + 16, TEXT_DIM, 6))
    p.append(icon_dots(ex + ew - 14, tb_y + 16))

    # sheet tabs
    st_y = tb_y + 34
    sheets = ["Executive KPI Summary", "Tower Breakdown Matrix", "Support Tax Heatmap", "Sprint Detail"]
    sx = ex + 12
    for i, sheet in enumerate(sheets):
        sw_ = len(sheet) * 7.4 + 26
        if i == 2:
            p.append(rect(sx, st_y, sw_, 30, "#217346", rx=4))
            colour = TEXT_BRIGHT
        else:
            colour = TEXT_DIM
        p.append(text(sx + 13, st_y + 20, sheet, 11.5, colour, UI))
        sx += sw_ + 4
    p.append(line(ex, st_y + 30, ex + ew, st_y + 30, BORDER))

    # formula bar
    fb_y = st_y + 30
    p.append(rect(ex, fb_y, ew, 28, "#252526"))
    p.append(text(ex + 16, fb_y + 19, "C4", 11.5, TEXT, UI, weight="600"))
    p.append(line(ex + 52, fb_y, ex + 52, fb_y + 28, BORDER))
    p.append(text(ex + 64, fb_y + 19, "fx   = 28.4%", 11.5, "#9cdcfe", MONO))

    # grid
    grid_y = fb_y + 28
    col_w, row_h = 100, 30
    label_w = 176
    towers = data["towers"]
    taxes = data["support_tax"]
    n_sprints = 12
    headers = ["Tower"] + [f"S{i:02d}" for i in range(1, n_sprints + 1)] + ["Avg"]

    # column headers
    p.append(rect(ex, grid_y, ew, row_h, "#2d2d2d"))
    p.append(text(ex + 20, grid_y + 20, "#", 11, TEXT_DIM, MONO))
    for j, head in enumerate(headers):
        hx = ex + label_w + j * col_w
        p.append(text(hx + 10, grid_y + 20, head, 11.5, TEXT_BRIGHT, UI, weight="600"))
    gy = grid_y + row_h

    rng = random.Random(20260724)
    for i, tower in enumerate(towers):
        avg = taxes.get(tower, 0.0)
        row_vals = []
        for _ in range(n_sprints):
            val = max(2.0, min(46.0, rng.gauss(avg, avg * 0.42)))
            row_vals.append(val)
        if i % 2 == 0:
            p.append(rect(ex, gy, ew, row_h, "#262626"))
        p.append(text(ex + 20, gy + 20, str(i + 1), 11, TEXT_DIM, MONO))
        p.append(text(ex + 44, gy + 20, tower, 11.5, TEXT, UI, weight="700"))
        p.append(text(ex + 76, gy + 20, data["names"].get(tower, "")[:13], 11, TEXT_DIM, UI))
        for j, val in enumerate(row_vals):
            hx = ex + label_w + (j + 1) * col_w
            cell_colour = _heat_colour(val)
            p.append(rect(hx + 3, gy + 3, col_w - 8, row_h - 6, cell_colour, rx=3))
            p.append(text(hx + col_w / 2, gy + 20, f"{val:.1f}%", 11.5, "#1a1a1a", MONO, anchor="middle"))
        hx = ex + label_w + (n_sprints + 1) * col_w
        avg_colour = _heat_colour(avg)
        p.append(rect(hx + 3, gy + 3, col_w - 8, row_h - 6, avg_colour, rx=3))
        p.append(text(hx + col_w / 2, gy + 20, f"{avg:.1f}%", 11.5, "#1a1a1a", MONO, weight="700", anchor="middle"))
        gy += row_h

    # legend
    ly = gy + 26
    p.append(text(ex + 20, ly, "Conditional formatting:", 11, TEXT_DIM, UI))
    for k, (label, colour) in enumerate(
        [("<= 20% healthy", "#8fd694"), ("> 20% watch", "#f0c674"), ("> 30% critical", "#f28b82")]
    ):
        lx = ex + 170 + k * 170
        p.append(rect(lx, ly - 12, 18, 14, colour, rx=3))
        p.append(text(lx + 26, ly, label, 11, TEXT, UI))

    # status
    p.append(status_bar(x0, H - 46, W - 40, "Excel Viewer  |  sprint_support_ledger.xlsx  |  Support Tax Heatmap", "4 sheets  ·  118 rows  ·  README preview"))
    p.append("</svg>")
    return "".join(p)


def _heat_colour(value: float) -> str:
    """Green -> amber -> red ramp matching the workbook conditional formatting."""
    if value <= 20:
        t = max(0.0, min(1.0, value / 20.0))
        return _mix("#c3f0ca", "#f0d68a", t)
    if value <= 30:
        t = (value - 20) / 10.0
        return _mix("#f0d68a", "#f2a08a", t)
    t = min(1.0, (value - 30) / 15.0)
    return _mix("#f2a08a", "#e06c62", t)


def _mix(c1: str, c2: str, t: float) -> str:
    a = tuple(int(c1[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i : i + 2], 16) for i in (1, 3, 5))
    out = tuple(int(round(a[k] + (b[k] - a[k]) * t)) for k in range(3))
    return "#%02x%02x%02x" % out


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
SCREENSHOTS = {
    "vscode_interactive_monte_carlo.svg": screenshot_interactive_monte_carlo,
    "vscode_support_tax_breakdown.svg": screenshot_support_tax_breakdown,
    "vscode_simple_browser_dashboard.svg": screenshot_simple_browser,
    "vscode_excel_viewer_capacity_ledger.svg": screenshot_excel_viewer,
}


def generate_all(out_dir: str | Path | None = None) -> list[Path]:
    """Render every screenshot into ``out_dir`` and return the paths."""
    target = Path(out_dir) if out_dir is not None else _ROOT / "docs" / "screenshots"
    target.mkdir(parents=True, exist_ok=True)
    data = load_real_data()
    written: list[Path] = []
    for filename, builder in SCREENSHOTS.items():
        svg = builder(data)
        path = target / filename
        path.write_text(svg, encoding="utf-8")
        written.append(path)
        print(f"  rendered {filename}  ({len(svg) / 1024:.1f} KB)")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=None, help="Output directory (default docs/screenshots).")
    args = parser.parse_args(argv)

    print("SprintShield - rendering VS Code SVG screenshots")
    paths = generate_all(args.out_dir)
    print(f"Done: {len(paths)} assets written to {paths[0].parent}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

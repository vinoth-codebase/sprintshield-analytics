"""Tests for the Click CLI surface."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from sprintshield import __version__
from sprintshield.cli import cli


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def invoke(runner: CliRunner, config, tmp_path, monkeypatch):
    """Invoke the CLI with an isolated Delta Lake root."""
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "delta"))
    monkeypatch.setenv("REPORT_OUTPUT_DIR", str(tmp_path / "reports"))
    # Rich sizes tables from the terminal width; give it room so column headers
    # are not truncated in captured output.
    monkeypatch.setenv("COLUMNS", "200")

    def _invoke(*args: str, **kwargs):
        base = ["--project-root", str(config.project_root)]
        return runner.invoke(cli, base + list(args), **kwargs)

    return _invoke


# --------------------------------------------------------------------------- #
# Root group
# --------------------------------------------------------------------------- #
def test_version_flag(runner):
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_lists_subcommands(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for command in ("ingest", "train", "simulate", "export", "report", "dashboard", "screenshots"):
        assert command in result.output


def test_subcommand_help(invoke):
    assert invoke("simulate", "--help").exit_code == 0


# --------------------------------------------------------------------------- #
# ingest
# --------------------------------------------------------------------------- #
def test_ingest_persists_telemetry(invoke, tmp_path):
    result = invoke("ingest", "--sprints", "3", "--seed", "5")
    assert result.exit_code == 0, result.output
    assert "Ingestion complete" in result.output
    assert (tmp_path / "delta" / "sprint_snapshots").exists()


def test_ingest_with_explicit_backend(invoke, tmp_path):
    result = invoke("ingest", "--sprints", "2", "--backend", "deltalake")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "delta" / "support_tickets").exists()


def test_ingest_jira_source_warns_and_falls_back(invoke):
    result = invoke("ingest", "--sprints", "2", "--source", "jira")
    assert result.exit_code == 0, result.output
    assert "Jira extraction requested" in result.output


def test_ingest_append_mode(invoke):
    assert invoke("ingest", "--sprints", "2").exit_code == 0
    assert invoke("ingest", "--sprints", "2", "--mode", "append").exit_code == 0


# --------------------------------------------------------------------------- #
# train
# --------------------------------------------------------------------------- #
def test_train_prints_model_tables(invoke):
    result = invoke("train")
    assert result.exit_code == 0, result.output
    assert "Arrival Demand Models" in result.output
    assert "Resolution Effort Models" in result.output
    assert "negative_binomial" in result.output or "poisson" in result.output


def test_train_single_tower(invoke):
    result = invoke("train", "--tower", "DE")
    assert result.exit_code == 0, result.output
    assert "DE" in result.output


# --------------------------------------------------------------------------- #
# simulate
# --------------------------------------------------------------------------- #
def test_simulate_prints_summary_table(invoke):
    result = invoke("simulate", "--gross-hours", "320", "--planned-points", "80", "--runs", "500")
    assert result.exit_code == 0, result.output
    assert "Monte Carlo Sprint Simulation" in result.output
    assert "Support Tax %" in result.output


def test_simulate_json_output(invoke):
    result = invoke("simulate", "--runs", "300", "--json-out")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert len(payload) == 4
    assert "delivery_confidence" in payload[0]


def test_simulate_single_tower(invoke):
    result = invoke("simulate", "--tower", "DE", "--runs", "300")
    assert result.exit_code == 0, result.output
    assert "Monte Carlo Summary - DE" in result.output
    assert "Delivery Confidence Curve" in result.output


def test_simulate_single_tower_json(invoke):
    result = invoke("simulate", "--tower", "DA", "--runs", "300", "--json-out")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["tower_id"] == "DA"


def test_simulate_unknown_tower_errors(invoke):
    result = invoke("simulate", "--tower", "ZZ")
    assert result.exit_code != 0
    assert "Unknown tower" in result.output


def test_simulate_custom_target_confidence(invoke):
    result = invoke("simulate", "--runs", "300", "--target-confidence", "0.9")
    assert result.exit_code == 0, result.output
    assert "90%" in result.output


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #
def test_export_excel_and_pptx(invoke, tmp_path):
    result = invoke("export", "--excel", "--pptx")
    assert result.exit_code == 0, result.output
    assert "Reports generated" in result.output
    assert (tmp_path / "reports" / "sprint_support_ledger.xlsx").exists()
    assert (tmp_path / "reports" / "stakeholder_steerco_deck.pptx").exists()


def test_export_all_flag(invoke, tmp_path):
    assert invoke("export", "--all").exit_code == 0
    assert (tmp_path / "reports" / "sprint_support_ledger.xlsx").exists()


def test_export_defaults_to_both_reports(invoke, tmp_path):
    assert invoke("export").exit_code == 0
    assert (tmp_path / "reports" / "stakeholder_steerco_deck.pptx").exists()


def test_export_custom_out_dir(invoke, tmp_path):
    target = tmp_path / "custom_reports"
    result = invoke("export", "--excel", "--out-dir", str(target))
    assert result.exit_code == 0, result.output
    assert (target / "sprint_support_ledger.xlsx").exists()


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def test_report_prints_executive_summary(invoke):
    result = invoke("report")
    assert result.exit_code == 0, result.output
    assert "Executive KPI Summary" in result.output
    assert "overall_support_tax_pct" in result.output
    assert "Tower Metrics" in result.output

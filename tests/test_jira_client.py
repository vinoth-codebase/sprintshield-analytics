"""Tests for the Jira REST client, auth handlers and synthetic generator."""

from __future__ import annotations

import httpx
import pandas as pd
import pytest

from sprintshield.client.auth import JiraAuth, JiraAuthError, JiraCredentials
from sprintshield.client.jira_client import JiraClient, JiraClientError
from sprintshield.client.synthetic_generator import (
    SyntheticTelemetryGenerator,
    generate_telemetry,
)
from sprintshield.config import SprintShieldConfig

BASE = "https://example.atlassian.net"


# --------------------------------------------------------------------------- #
# Credentials & auth
# --------------------------------------------------------------------------- #
def test_credentials_from_env_cloud():
    creds = JiraCredentials.from_env(
        {"JIRA_BASE_URL": BASE + "/", "JIRA_EMAIL": "a@b.com", "JIRA_API_TOKEN": "tok", "JIRA_BOARD_ID": "7"}
    )
    assert creds.base_url == BASE  # trailing slash stripped
    assert creds.board_id == 7
    assert creds.is_configured() is True


def test_credentials_from_env_server_pat():
    creds = JiraCredentials.from_env({"JIRA_BASE_URL": BASE, "JIRA_PAT": "pat-token"})
    assert creds.is_configured() is True
    assert creds.verify_ssl is True


def test_credentials_not_configured_without_secret():
    creds = JiraCredentials.from_env({"JIRA_BASE_URL": BASE})
    assert creds.is_configured() is False


def test_auth_basic_header_is_base64():
    auth = JiraAuth(credentials=JiraCredentials(base_url=BASE, email="a@b.com", api_token="secret"))
    headers = auth.headers()
    assert headers["Authorization"].startswith("Basic ")
    assert auth.auth_scheme() == "basic"
    # Cached on the second call.
    assert auth.headers() == headers


def test_auth_bearer_header_for_pat():
    auth = JiraAuth(credentials=JiraCredentials(base_url=BASE, personal_access_token="pat"))
    assert auth.headers()["Authorization"] == "Bearer pat"
    assert auth.auth_scheme() == "bearer"


def test_auth_raises_without_credentials():
    auth = JiraAuth(credentials=JiraCredentials(base_url=BASE))
    with pytest.raises(JiraAuthError):
        auth.headers()
    assert auth.auth_scheme() == "none"


def test_auth_from_env():
    auth = JiraAuth.from_env({"JIRA_BASE_URL": BASE, "JIRA_EMAIL": "x@y.z", "JIRA_API_TOKEN": "t"})
    assert auth.credentials.email == "x@y.z"


# --------------------------------------------------------------------------- #
# Transport helpers
# --------------------------------------------------------------------------- #
def _issue(key: str, summary: str = "broken pipeline", labels=None, resolved=True):
    fields = {
        "summary": summary,
        "created": "2026-02-03T08:00:00.000+0000",
        "resolutiondate": "2026-02-03T14:30:00.000+0000" if resolved else None,
        "priority": {"name": "High"},
        "issuetype": {"name": "Incident"},
        "status": {"name": "Done" if resolved else "Open"},
        "labels": labels or ["asset:airflow_orchestration"],
        "customfield_10016": 3.0,
    }
    return {"key": key, "id": key, "fields": fields}


def _client(handler, **kwargs) -> JiraClient:
    auth = JiraAuth(credentials=JiraCredentials(base_url=BASE, email="a@b.com", api_token="t"))
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url=BASE)
    return JiraClient(auth=auth, base_url=BASE, client=http, backoff_base=0.0, **kwargs)


def test_board_sprint_pagination():
    calls = {"n": 0}

    all_sprints = [{"id": i, "name": f"S{i}"} for i in range(1, 6)]

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        start = int(request.url.params.get("startAt", 0))
        size = int(request.url.params.get("maxResults", 2))
        page = all_sprints[start : start + size]
        return httpx.Response(200, json={"values": page, "total": len(all_sprints)})

    client = _client(handler, page_size=2)
    sprints = client.get_board_sprints(board_id=9)
    assert len(sprints) == 5
    assert calls["n"] >= 3
    client.close()


def test_search_issues_respects_max_results():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issues": [_issue("K-1"), _issue("K-2")], "total": 2})

    client = _client(handler)
    issues = client.search_issues("project = X", max_results=1)
    assert len(issues) == 1
    client.close()


def test_search_support_issues_builds_jql():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["jql"] = request.url.params.get("jql", "")
        return httpx.Response(200, json={"issues": [], "total": 0})

    client = _client(handler)
    client.search_support_issues(["OPS"])
    assert "issuetype in" in captured["jql"]
    assert "OPS" in captured["jql"]
    client.close()


def test_retry_on_rate_limit_then_success():
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json={"issues": [_issue("K-1")], "total": 1})

    client = _client(handler)
    issues = client.search_issues("x")
    assert len(issues) == 1
    assert state["n"] == 2
    client.close()


def test_client_error_on_400():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad jql")

    client = _client(handler, max_retries=1)
    with pytest.raises(JiraClientError):
        client.search_issues("nonsense")
    client.close()


def test_client_error_after_exhausting_retries():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    client = _client(handler, max_retries=2)
    with pytest.raises(JiraClientError):
        client.search_issues("x")
    client.close()


def test_network_error_is_retried_then_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _client(handler, max_retries=2)
    with pytest.raises(JiraClientError):
        client.search_issues("x")
    client.close()


# --------------------------------------------------------------------------- #
# Issue detail endpoints
# --------------------------------------------------------------------------- #
def test_changelog_and_scope_changes():
    def handler(request: httpx.Request) -> httpx.Response:
        if "changelog" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "values": [
                        {"items": [{"field": "Sprint"}, {"field": "summary"}]},
                        {"items": [{"field": "Story Points"}]},
                    ]
                },
            )
        return httpx.Response(200, json={})

    client = _client(handler)
    assert client.count_scope_changes("K-1") == 2
    assert len(client.get_changelog("K-1")) == 2
    client.close()


def test_worklog_hours():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"worklogs": [{"timeSpentSeconds": 3600}, {"timeSpentSeconds": 1800}]},
        )

    client = _client(handler)
    assert client.total_logged_hours("K-1") == pytest.approx(1.5)
    assert len(client.get_worklogs("K-1")) == 2
    client.close()


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def test_normalise_ticket_maps_fields():
    client = _client(lambda r: httpx.Response(200, json={}))
    row = client.normalise_ticket(
        _issue("OPS-1", labels=["asset:feature_store", "release-regression"]),
        sprint_id="S03",
        tower_id="DS",
    )
    assert row["ticket_id"] == "OPS-1"
    assert row["tower_id"] == "DS"
    assert row["sprint_id"] == "S03"
    assert row["asset"] == "feature_store"
    assert row["priority"] == "P2"  # High -> P2
    assert row["resolution_hours"] == pytest.approx(6.5)
    assert row["is_release_regression"] is True
    assert row["is_planned"] is False
    client.close()


def test_normalise_ticket_without_resolution():
    client = _client(lambda r: httpx.Response(200, json={}))
    row = client.normalise_ticket(_issue("OPS-2", resolved=False), "S01", "DE")
    assert row["resolved_at"] is None
    assert row["resolution_hours"] is None
    assert row["status"] == "Open"
    client.close()


def test_fetch_support_tickets_returns_canonical_frame():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"issues": [_issue("OPS-1"), _issue("OPS-2")], "total": 2})

    client = _client(handler)
    frame = client.fetch_support_tickets("DE", "S01", ["OPS"])
    assert isinstance(frame, pd.DataFrame)
    assert len(frame) == 2
    assert "resolution_hours" in frame.columns
    client.close()


def test_fetch_sprint_snapshot_shape():
    client = _client(lambda r: httpx.Response(200, json={}))
    snapshot = client.fetch_sprint_snapshot(
        "DE",
        {"id": 11, "name": "Sprint 11", "startDate": "2026-03-02", "endDate": "2026-03-13"},
        [_issue("K-1"), _issue("K-2")],
    )
    assert snapshot["sprint_id"] == "11"
    assert snapshot["committed_points"] == 6.0
    assert snapshot["tower_id"] == "DE"
    client.close()


def test_columns_helper():
    assert "tower_id" in JiraClient.columns("sprint_snapshots")
    assert "resolution_hours" in JiraClient.columns("support_tickets")


def test_client_context_manager_without_injected_transport():
    auth = JiraAuth(credentials=JiraCredentials(base_url=BASE, personal_access_token="pat"))
    client = JiraClient(auth=auth, base_url=BASE)
    with client as c:
        assert c.base_url == BASE


# --------------------------------------------------------------------------- #
# Synthetic generator
# --------------------------------------------------------------------------- #
def test_generator_produces_expected_shapes(config: SprintShieldConfig):
    generator = SyntheticTelemetryGenerator(config=config, seed=7)
    snaps, ticks = generator.generate(sprints=4)
    assert snaps["sprint_id"].nunique() == 4
    assert set(snaps["tower_id"]) == set(config.tower_ids())
    assert len(snaps) == 4 * len(config.towers)
    assert not ticks.empty
    assert set(ticks["tower_id"]).issubset(set(config.tower_ids()))


def test_generator_is_deterministic_for_a_seed(config: SprintShieldConfig):
    a = SyntheticTelemetryGenerator(config=config, seed=99).generate(sprints=3)[0]
    b = SyntheticTelemetryGenerator(config=config, seed=99).generate(sprints=3)[0]
    # `recorded_at` is a wall-clock audit stamp and is expected to differ.
    columns = [c for c in a.columns if c != "recorded_at"]
    pd.testing.assert_frame_equal(a[columns], b[columns])


def test_generator_support_tax_is_realistic(config: SprintShieldConfig):
    snaps, _ = generate_telemetry(sprints=12, config=config, seed=3)
    tax = snaps["unplanned_support_hours"].sum() / snaps["gross_hours"].sum()
    assert 0.05 < tax < 0.60
    assert (snaps["support_tax"] >= 0).all()
    assert (snaps["flow_efficiency"] >= 0).all()
    assert (snaps["flow_efficiency"] <= 1.0).all()


def test_generator_injects_holiday_capacity_noise(config: SprintShieldConfig):
    # The 24-sprint window spans a full calendar year and therefore crosses the
    # winter shutdown, so at least one sprint must show reduced gross capacity.
    snaps, _ = generate_telemetry(sprints=24, config=config, seed=5)
    gross = snaps.groupby("sprint_id")["gross_hours"].first()
    assert gross.min() < gross.max()
    assert gross.min() < config.noise.holiday_capacity_drop * gross.max() + 1e-6


def test_generator_ticket_ids_are_unique(config: SprintShieldConfig):
    _, ticks = generate_telemetry(sprints=6, config=config, seed=11)
    assert ticks["ticket_id"].is_unique


def test_generator_resolution_hours_are_positive(config: SprintShieldConfig):
    _, ticks = generate_telemetry(sprints=6, config=config, seed=13)
    resolved = ticks["resolution_hours"].dropna()
    assert (resolved > 0).all()
    assert resolved.max() <= config.effort_model.max_hours + 1e-6


def test_generator_marks_some_tickets_open(config: SprintShieldConfig):
    _, ticks = generate_telemetry(sprints=12, config=config, seed=17)
    assert (ticks["status"] == "Open").any()
    assert (ticks["status"] == "Done").any()


def test_generate_telemetry_accepts_start_date(config: SprintShieldConfig):
    from datetime import date

    snaps, _ = generate_telemetry(
        sprints=2, config=config, seed=1, start_date=date(2026, 3, 2)
    )
    assert snaps["start_date"].min() == "2026-03-02"

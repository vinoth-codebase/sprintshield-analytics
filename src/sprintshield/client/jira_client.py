"""Jira REST API client for Agile boards, issue search, changelogs and worklogs.

The client wraps ``httpx`` with token authentication, automatic pagination,
transient-failure retries with exponential backoff, and normalisation of the
raw Jira payloads into the canonical SprintShield frames. It is fully
dependency-injectable (pass a custom ``httpx.Client`` or ``transport``) so the
test-suite can drive it entirely offline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Sequence

import httpx
import pandas as pd

from sprintshield.client.auth import JiraAuth
from sprintshield.storage.base import SPRINT_SNAPSHOT_COLUMNS, SUPPORT_TICKET_COLUMNS


class JiraClientError(RuntimeError):
    """Raised for non-recoverable Jira API failures."""


# Jira issue types that represent *unplanned* production support rather than
# committed sprint work.
SUPPORT_ISSUE_TYPES = {"Bug", "Incident", "Support", "Service Request", "Hotfix"}
SEVERITY_TO_PRIORITY = {
    "Highest": "P1",
    "High": "P2",
    "Medium": "P3",
    "Low": "P4",
    "Lowest": "P5",
}


@dataclass(frozen=True)
class PaginationState:
    """Bookkeeping for a paginated Jira collection."""

    start_at: int
    max_results: int
    total: int

    @property
    def has_more(self) -> bool:
        return self.start_at + self.max_results < self.total


class JiraClient:
    """Thin, resilient REST client for Jira Cloud / Server."""

    AGILE_BOARD_SPRINTS = "/rest/agile/1.0/board/{board_id}/sprint"
    AGILE_SPRINT_ISSUES = "/rest/agile/1.0/sprint/{sprint_id}/issue"
    ISSUE_SEARCH = "/rest/api/3/search"
    ISSUE = "/rest/api/3/issue/{issue_id}"
    ISSUE_CHANGELOG = "/rest/api/3/issue/{issue_id}/changelog"
    ISSUE_WORKLOG = "/rest/api/3/issue/{issue_id}/worklog"

    def __init__(
        self,
        auth: JiraAuth | None = None,
        base_url: str | None = None,
        client: httpx.Client | None = None,
        max_retries: int = 4,
        page_size: int = 50,
        backoff_base: float = 0.5,
        timeout: float = 30.0,
    ) -> None:
        self.auth = auth or JiraAuth.from_env()
        self.base_url = (base_url or self.auth.credentials.base_url).rstrip("/")
        self.page_size = page_size
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._owns_client = client is None
        if client is not None:
            self._client = client
        else:
            self._client = httpx.Client(
                base_url=self.base_url or "http://localhost",
                headers=self.auth.headers() if self.auth.credentials.is_configured() else {},
                verify=self.auth.credentials.verify_ssl,
                timeout=timeout,
            )

    # -- lifecycle ----------------------------------------------------------- #
    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "JiraClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # -- low-level transport ------------------------------------------------- #
    def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET ``path`` with retries on 429/5xx and transient network errors."""
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._client.get(path, params=params)
                if response.status_code in (429, 500, 502, 503, 504):
                    retry_after = float(response.headers.get("Retry-After", 0) or 0)
                    raise _TransientJiraError(
                        f"HTTP {response.status_code} from {path}", retry_after=retry_after
                    )
                if response.status_code >= 400:
                    raise JiraClientError(
                        f"Jira API error {response.status_code} for {path}: {response.text[:200]}"
                    )
                return response.json()
            except _TransientJiraError as exc:
                last_error = exc
                if attempt < self.max_retries - 1:
                    time.sleep(exc.retry_after or self.backoff_base * (2**attempt))
            except httpx.TransportError as exc:  # network hiccup
                last_error = exc
                if attempt < self.max_retries - 1:
                    time.sleep(self.backoff_base * (2**attempt))
        raise JiraClientError(f"Jira request to {path} failed after {self.max_retries} attempts: {last_error}")

    # -- pagination ---------------------------------------------------------- #
    def _paginate(
        self,
        path: str,
        collection_key: str,
        params: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield every item across a paginated Jira collection."""
        params = dict(params or {})
        start_at = 0
        while True:
            params.update({"startAt": start_at, "maxResults": self.page_size})
            payload = self._request(path, params)
            values = payload.get(collection_key) or payload.get("issues") or []
            for item in values:
                yield item
            total = int(payload.get("total", len(values)))
            state = PaginationState(start_at, self.page_size, total)
            if not state.has_more or not values:
                break
            start_at += self.page_size

    # -- Agile board endpoints ---------------------------------------------- #
    def get_board_sprints(self, board_id: int | None = None) -> list[dict[str, Any]]:
        """Return every sprint on the given Agile board."""
        bid = board_id if board_id is not None else self.auth.credentials.board_id
        return list(self._paginate(self.AGILE_BOARD_SPRINTS.format(board_id=bid), "values"))

    def get_sprint_issues(self, sprint_id: int) -> list[dict[str, Any]]:
        """Return every issue committed to a sprint (committed scope)."""
        return list(self._paginate(self.AGILE_SPRINT_ISSUES.format(sprint_id=sprint_id), "issues"))

    def search_issues(
        self,
        jql: str,
        fields: Sequence[str] | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        """Run a JQL search, paginating until exhausted (or ``max_results``)."""
        params: dict[str, Any] = {"jql": jql}
        if fields:
            params["fields"] = ",".join(fields)
        collected: list[dict[str, Any]] = []
        for issue in self._paginate(self.ISSUE_SEARCH, "issues", params):
            collected.append(issue)
            if max_results is not None and len(collected) >= max_results:
                break
        return collected

    def search_support_issues(self, project_keys: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """Search for unplanned production support work (bugs, incidents, requests)."""
        types = ", ".join(f'"{t}"' for t in sorted(SUPPORT_ISSUE_TYPES))
        jql = f"issuetype in ({types})"
        if project_keys:
            keys = ", ".join(f'"{k}"' for k in project_keys)
            jql += f" AND project in ({keys})"
        jql += " ORDER BY created ASC"
        return self.search_issues(jql)

    # -- issue detail endpoints --------------------------------------------- #
    def get_changelog(self, issue_id: str) -> list[dict[str, Any]]:
        """Return the raw changelog history for an issue."""
        payload = self._request(self.ISSUE_CHANGELOG.format(issue_id=issue_id))
        return list(payload.get("values", []))

    def get_worklogs(self, issue_id: str) -> list[dict[str, Any]]:
        """Return every worklog entry logged against an issue."""
        payload = self._request(self.ISSUE_WORKLOG.format(issue_id=issue_id))
        return list(payload.get("worklogs", []))

    def total_logged_hours(self, issue_id: str) -> float:
        """Sum the hours logged across every worklog entry for an issue."""
        total = 0.0
        for entry in self.get_worklogs(issue_id):
            total += float(entry.get("timeSpentSeconds", 0) or 0) / 3600.0
        return total

    def count_scope_changes(self, issue_id: str) -> int:
        """Count sprint-field transitions (mid-sprint scope churn) in a changelog."""
        changes = 0
        for entry in self.get_changelog(issue_id):
            for item in entry.get("items", []):
                if item.get("field") in {"Sprint", "Story Points", "Story point estimate"}:
                    changes += 1
        return changes

    # -- normalisation ------------------------------------------------------- #
    @staticmethod
    def _field(issue: dict[str, Any], name: str, default: Any = None) -> Any:
        return (issue.get("fields") or {}).get(name, default)

    def normalise_ticket(self, issue: dict[str, Any], sprint_id: str, tower_id: str) -> dict[str, Any]:
        """Map one raw Jira issue onto the canonical support-ticket row."""
        fields = issue.get("fields") or {}
        created = fields.get("created") or datetime.now(timezone.utc).isoformat()
        resolved = fields.get("resolutiondate")
        resolution_hours = None
        if resolved and created:
            try:
                delta = _parse_dt(resolved) - _parse_dt(created)
                resolution_hours = max(delta.total_seconds() / 3600.0, 0.0)
            except ValueError:
                resolution_hours = None
        priority = (fields.get("priority") or {}).get("name")
        labels = fields.get("labels") or []
        asset = next((lbl for lbl in labels if lbl.startswith("asset:")), "asset:unknown").split(":", 1)[-1]
        return {
            "ticket_id": str(issue.get("key", issue.get("id", "unknown"))),
            "tower_id": tower_id,
            "sprint_id": sprint_id,
            "asset": asset,
            "summary": fields.get("summary", ""),
            "issue_type": (fields.get("issuetype") or {}).get("name", "Unknown"),
            "priority": SEVERITY_TO_PRIORITY.get(priority, priority or "P3"),
            "status": (fields.get("status") or {}).get("name", "Open"),
            "created_at": str(created),
            "resolved_at": str(resolved) if resolved else None,
            "resolution_hours": resolution_hours,
            "story_points": fields.get("customfield_10016") or fields.get("story_points") or 0.0,
            "is_planned": False,
            "is_release_regression": "release-regression" in labels,
            "labels": ",".join(str(x) for x in labels),
        }

    def fetch_support_tickets(
        self,
        tower_id: str,
        sprint_id: str,
        project_keys: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        """Search and normalise unplanned support tickets into a canonical frame."""
        rows = [
            self.normalise_ticket(issue, sprint_id=sprint_id, tower_id=tower_id)
            for issue in self.search_support_issues(project_keys)
        ]
        frame = pd.DataFrame(rows, columns=list(SUPPORT_TICKET_COLUMNS))
        return frame

    def fetch_sprint_snapshot(
        self,
        tower_id: str,
        sprint: dict[str, Any],
        completed_issues: Iterable[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Build one canonical sprint-snapshot row from a board sprint payload."""
        sprint_id = str(sprint.get("id", ""))
        committed = sum(
            float(self._field(i, "customfield_10016") or 0.0) for i in (completed_issues or [])
        )
        start = sprint.get("startDate") or sprint.get("start_date")
        end = sprint.get("endDate") or sprint.get("end_date")
        return {
            "tower_id": tower_id,
            "sprint_id": sprint_id,
            "sprint_name": sprint.get("name", f"Sprint {sprint_id}"),
            "start_date": str(start) if start else None,
            "end_date": str(end) if end else None,
            "committed_points": committed,
            "completed_points": committed,
            "scope_change_points": 0.0,
            "gross_hours": 0.0,
            "planned_feature_hours": 0.0,
            "unplanned_support_hours": 0.0,
            "incident_count": 0.0,
            "support_tax": 0.0,
            "flow_efficiency": 1.0,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def columns(table: str) -> list[str]:
        """Expose the canonical columns for a logical table."""
        return list(SPRINT_SNAPSHOT_COLUMNS if table == "sprint_snapshots" else SUPPORT_TICKET_COLUMNS)


class _TransientJiraError(RuntimeError):
    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _parse_dt(value: str) -> datetime:
    """Parse an ISO-8601 Jira timestamp into an aware datetime."""
    cleaned = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed

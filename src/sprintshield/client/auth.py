"""Jira authentication helpers.

Builds the HTTP headers and base URL for Atlassian Cloud (email + API token,
HTTP Basic) and Jira Server / Data Center (bearer personal access token).
Credentials are read from the environment or injected explicitly; nothing is
ever logged.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Mapping


class JiraAuthError(RuntimeError):
    """Raised when credentials are missing or malformed."""


@dataclass(frozen=True)
class JiraCredentials:
    """Resolved Jira connection credentials."""

    base_url: str
    email: str | None = None
    api_token: str | None = None
    personal_access_token: str | None = None
    verify_ssl: bool = True
    board_id: int = 1

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "JiraCredentials":
        source = env if env is not None else os.environ
        return cls(
            base_url=source.get("JIRA_BASE_URL", "").rstrip("/"),
            email=source.get("JIRA_EMAIL") or None,
            api_token=source.get("JIRA_API_TOKEN") or None,
            personal_access_token=source.get("JIRA_PAT") or None,
            verify_ssl=str(source.get("JIRA_VERIFY_SSL", "true")).lower() not in {"false", "0", "no"},
            board_id=int(source.get("JIRA_BOARD_ID", "1") or 1),
        )

    def is_configured(self) -> bool:
        """True when a base URL plus some credential are present."""
        has_cloud = bool(self.email and self.api_token)
        has_server = bool(self.personal_access_token)
        return bool(self.base_url) and (has_cloud or has_server)


@dataclass
class JiraAuth:
    """Produces authentication headers for outbound Jira requests."""

    credentials: JiraCredentials
    _header_cache: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "JiraAuth":
        return cls(credentials=JiraCredentials.from_env(env))

    def headers(self) -> dict[str, str]:
        """Return the ``Authorization`` header plus JSON content negotiation."""
        if self._header_cache:
            return dict(self._header_cache)

        base = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.credentials.personal_access_token:
            base["Authorization"] = f"Bearer {self.credentials.personal_access_token}"
        elif self.credentials.email and self.credentials.api_token:
            raw = f"{self.credentials.email}:{self.credentials.api_token}".encode("utf-8")
            token = base64.b64encode(raw).decode("ascii")
            base["Authorization"] = f"Basic {token}"
        else:
            raise JiraAuthError(
                "No Jira credentials configured. Set JIRA_EMAIL + JIRA_API_TOKEN "
                "for Atlassian Cloud, or JIRA_PAT for Jira Server."
            )
        self._header_cache = base
        return dict(base)

    def auth_scheme(self) -> str:
        """Return a redacted label describing the active scheme."""
        if self.credentials.personal_access_token:
            return "bearer"
        if self.credentials.email and self.credentials.api_token:
            return "basic"
        return "none"

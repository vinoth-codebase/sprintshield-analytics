"""Client subpackage: Jira REST ingestion and synthetic multi-tower telemetry."""

from sprintshield.client.auth import JiraAuth, JiraCredentials
from sprintshield.client.jira_client import JiraClient, JiraClientError
from sprintshield.client.synthetic_generator import (
    SyntheticTelemetryGenerator,
    generate_telemetry,
)

__all__ = [
    "JiraAuth",
    "JiraCredentials",
    "JiraClient",
    "JiraClientError",
    "SyntheticTelemetryGenerator",
    "generate_telemetry",
]

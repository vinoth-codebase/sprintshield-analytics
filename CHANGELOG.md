# Changelog

All notable changes to **SprintShield Analytics** are recorded here.

## [1.0.0] - 2026-07-24

- `2026-06-22 10:15` chore: scaffold sprintshield-analytics repository, pyproject.toml, and vscode config
- `2026-06-22 16:40` feat(config): implement yaml settings loader for storage, towers, and simulation parameters
- `2026-06-23 14:20` feat(storage): define abstract base storage interface with generic telemetry methods
- `2026-06-24 09:30` feat(storage): implement delta lake adapter with partitioned parquet storage
- `2026-06-24 11:50` feat(storage): add delta lake time-travel query support for historical sprint audits
- `2026-06-24 14:40` feat(storage): build snowflake cloud adapter with connection pooling and query retries
- `2026-06-24 18:15` feat(storage): construct storage factory provider selecting backend via env variable
- `2026-06-25 10:45` test(storage): add unit tests for delta lake and snowflake repository adapters
- `2026-06-25 16:20` feat(client): implement httpx-based jira rest client for agile boards and issue searches
- `2026-06-26 09:15` feat(client): add pagination and token authentication handlers for jira rest endpoints
- `2026-06-26 13:30` feat(client): implement changelog and worklog parser to measure logged ticket hours
- `2026-06-26 17:45` feat(client): implement synthetic sprint telemetry generator simulating 24 bi-weekly sprints

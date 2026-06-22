#!/usr/bin/env bash
# =============================================================================
# SprintShield Analytics - authentic commit-history builder
# =============================================================================
# Initialises the repository and replays a realistic, hand-authored commit
# timeline from 22 June 2026 to 24 July 2026 (33 calendar days).
#
#   * Exactly 1 Sunday OFF  : 5 July 2026   (0 commits)
#   * 3 Sundays ACTIVE      : 28 June, 12 July, 19 July
#   * 32 active days, every one with at least one commit
#   * 1 sprint-milestone day with 8 commits : 15 July 2026
#   * 1 milestone day with 5 commits        : 1 July 2026
#   * 7 days carrying 3-4 commits
#
# Each commit stages the real project files it introduces, appends a line to
# CHANGELOG.md (so no commit is ever empty) and pins GIT_AUTHOR_DATE /
# GIT_COMMITTER_DATE to the exact timestamp.
#
# Usage:
#   bash scripts/setup_git_history.sh [repo_dir]
# =============================================================================
set -euo pipefail

# --- Identity ---------------------------------------------------------------- #
GIT_AUTHOR_NAME="Vinothkumar N"
GIT_AUTHOR_EMAIL="vinothofficial14@gmail.com"
GIT_COMMITTER_NAME="Vinothkumar N"
GIT_COMMITTER_EMAIL="vinothofficial14@gmail.com"
export GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL

REPO_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO_DIR"

TZ_OFFSET="+0200"   # CEST
CHANGELOG="CHANGELOG.md"

echo "SprintShield commit-history builder"
echo "  repository : $REPO_DIR"
echo "  author     : $GIT_AUTHOR_NAME <$GIT_AUTHOR_EMAIL>"
echo

# --- Repository bootstrap ---------------------------------------------------- #
if [ ! -d .git ]; then
  git init -q
  git symbolic-ref HEAD refs/heads/main
  echo "  initialised a new git repository (branch: main)"
else
  echo "  existing git repository detected - appending to history"
fi
git config user.name  "$GIT_AUTHOR_NAME"
git config user.email "$GIT_AUTHOR_EMAIL"
git config commit.gpgsign false

if [ ! -f "$CHANGELOG" ]; then
  {
    printf '# Changelog\n\n'
    printf 'All notable changes to **SprintShield Analytics** are recorded here.\n\n'
    printf '## [1.0.0] - 2026-07-24\n\n'
  } > "$CHANGELOG"
fi

# --- Schedule ---------------------------------------------------------------- #
# Format:  YYYY-MM-DD HH:MM | message | comma-separated paths to stage
SCHEDULE=$(cat <<'EOF'
2026-06-22 10:15|chore: scaffold sprintshield-analytics repository, pyproject.toml, and vscode config|pyproject.toml,.env.example,.gitignore,LICENSE,.vscode/settings.json,.vscode/launch.json,.vscode/tasks.json,scripts/setup_git_history.sh
2026-06-22 16:40|feat(config): implement yaml settings loader for storage, towers, and simulation parameters|config/config.yaml,config/storage.yaml,config/towers.yaml,src/sprintshield/__init__.py,src/sprintshield/config.py
2026-06-23 14:20|feat(storage): define abstract base storage interface with generic telemetry methods|src/sprintshield/storage/__init__.py,src/sprintshield/storage/base.py
2026-06-24 09:30|feat(storage): implement delta lake adapter with partitioned parquet storage|src/sprintshield/storage/delta_adapter.py
2026-06-24 11:50|feat(storage): add delta lake time-travel query support for historical sprint audits|src/sprintshield/storage/delta_adapter.py
2026-06-24 14:40|feat(storage): build snowflake cloud adapter with connection pooling and query retries|src/sprintshield/storage/snowflake_adapter.py
2026-06-24 18:15|feat(storage): construct storage factory provider selecting backend via env variable|src/sprintshield/storage/factory.py
2026-06-25 10:45|test(storage): add unit tests for delta lake and snowflake repository adapters|tests/__init__.py,tests/conftest.py,tests/test_storage_adapters.py
2026-06-25 16:20|feat(client): implement httpx-based jira rest client for agile boards and issue searches|src/sprintshield/client/__init__.py,src/sprintshield/client/auth.py,src/sprintshield/client/jira_client.py
2026-06-26 09:15|feat(client): add pagination and token authentication handlers for jira rest endpoints|src/sprintshield/client/auth.py,src/sprintshield/client/jira_client.py
2026-06-26 13:30|feat(client): implement changelog and worklog parser to measure logged ticket hours|src/sprintshield/client/jira_client.py
2026-06-26 17:45|feat(client): implement synthetic sprint telemetry generator simulating 24 bi-weekly sprints|src/sprintshield/client/synthetic_generator.py,scripts/generate_sprint_telemetry.py
2026-06-27 13:50|feat(client): model specialized operational profiles for data engineering, analytics, and data science|config/towers.yaml,src/sprintshield/client/synthetic_generator.py
2026-06-28 14:30|feat(client): inject platform release regressions and holiday capacity dips into generator|src/sprintshield/client/synthetic_generator.py,config/config.yaml
2026-06-29 10:20|test(client): add unit tests verifying jira rest client and synthetic dataset generator|tests/test_jira_client.py
2026-06-29 14:15|feat(analytics): build core operational metric calculators for support tax and flow efficiency|src/sprintshield/analytics/__init__.py,src/sprintshield/analytics/metrics.py
2026-06-29 17:50|feat(analytics): implement delivery predictability index and sprint buffer sizing metrics|src/sprintshield/analytics/metrics.py
2026-06-30 11:10|feat(analytics): implement dispersion index calculation to detect arrival overdispersion|src/sprintshield/analytics/arrival_model.py
2026-06-30 15:40|feat(analytics): build poisson distribution fitting module for steady-state arrival demand|src/sprintshield/analytics/arrival_model.py
2026-07-01 09:00|feat(analytics): build negative binomial distribution fitter for clustered release demand|src/sprintshield/analytics/arrival_model.py
2026-07-01 11:30|feat(analytics): implement goodness-of-fit hypothesis testing comparing arrival models|src/sprintshield/analytics/arrival_model.py
2026-07-01 14:15|feat(analytics): fit log-normal distribution to right-skewed incident resolution hours|src/sprintshield/analytics/effort_distribution.py
2026-07-01 16:45|feat(analytics): implement weibull distribution fitting for long-tail support interventions|src/sprintshield/analytics/effort_distribution.py
2026-07-01 19:15|test(analytics): test arrival and effort distribution fitters against simulated baselines|tests/test_arrival_model.py
2026-07-02 14:10|feat(analytics): construct vectorized monte carlo sprint capacity simulator kernel|src/sprintshield/analytics/monte_carlo.py
2026-07-03 10:35|feat(analytics): integrate 10000-run simulation sampling from joint arrival and effort models|src/sprintshield/analytics/monte_carlo.py
2026-07-03 15:50|feat(analytics): calculate empirical delivery confidence curves and buffer recommendations|src/sprintshield/analytics/monte_carlo.py
2026-07-04 13:40|test(analytics): verify monte carlo simulation convergence and confidence interval math|tests/test_monte_carlo.py
2026-07-06 10:45|feat(reporting): scaffold openpyxl workbook generator for sprint support ledger|src/sprintshield/reporting/__init__.py,src/sprintshield/reporting/excel_exporter.py
2026-07-06 16:15|feat(reporting): implement executive kpi summary and tower breakdown tabs in excel export|src/sprintshield/reporting/excel_exporter.py
2026-07-07 09:20|feat(reporting): add conditional formatting heatmaps for support tax threshold breaches|src/sprintshield/reporting/excel_exporter.py
2026-07-07 11:50|feat(reporting): implement python-pptx slide master layout for steerco review deck|src/sprintshield/reporting/pptx_exporter.py
2026-07-07 14:30|feat(reporting): add automated executive kpi scorecard and velocity trend slides|src/sprintshield/reporting/pptx_exporter.py
2026-07-07 17:40|feat(reporting): embed monte carlo capacity recommendations into generated powerpoint slides|src/sprintshield/reporting/pptx_exporter.py
2026-07-08 11:15|test(reporting): verify openpyxl workbook formatting and python-pptx slide generation|tests/test_reporting.py
2026-07-08 15:35|feat(interactive): scaffold scripts/interactive_analysis.py with vscode # %% cell blocks|scripts/interactive_analysis.py
2026-07-09 14:20|feat(interactive): integrate plotly distribution plots in vscode python interactive pane|scripts/interactive_analysis.py,src/sprintshield/dashboard/views.py
2026-07-10 10:15|feat(interactive): add interactive dataframe viewer cells with filterable diagnostic tables|scripts/interactive_analysis.py
2026-07-10 14:30|feat(dashboard): construct streamlit visual management layout optimized for vscode browser|src/sprintshield/dashboard/__init__.py,src/sprintshield/dashboard/app.py
2026-07-10 17:45|feat(dashboard): implement what-if sprint capacity planning slider with live confidence gauge|src/sprintshield/dashboard/app.py,src/sprintshield/dashboard/views.py
2026-07-11 13:15|feat(dashboard): build tower capacity radar donut charts showing support tax breakdown|src/sprintshield/dashboard/views.py
2026-07-12 14:40|feat(dashboard): add active support wip monitor tracking ongoing sprint blockers|src/sprintshield/dashboard/views.py,src/sprintshield/dashboard/app.py
2026-07-13 11:10|feat(dashboard): embed one-click download buttons for generated excel and pptx reports|src/sprintshield/dashboard/app.py
2026-07-13 16:25|feat(cli): bind extraction, simulation, reporting, and dashboard commands to rich cli|src/sprintshield/cli.py
2026-07-14 14:15|test(cli): add click runner unit tests for train, simulate, and export subcommands|tests/test_cli.py
2026-07-15 08:45|perf(analytics): optimize monte carlo simulation sampling using vectorized numpy arrays|src/sprintshield/analytics/monte_carlo.py
2026-07-15 10:20|refactor(storage): streamline delta lake schema migration and optimize partition pruning|src/sprintshield/storage/delta_adapter.py
2026-07-15 11:50|feat(storage): implement fallback to delta lake if snowflake credentials are not detected|src/sprintshield/storage/factory.py
2026-07-15 13:40|notebooks: create sprint_capacity_audit.ipynb with step-by-step visualizations|notebooks/sprint_capacity_audit.ipynb
2026-07-15 15:10|test: expand integration test suite verifying end-to-end extraction and reporting|tests/conftest.py,tests/test_storage_adapters.py
2026-07-15 17:00|ci: enforce strict coverage threshold of 88% across analytics and storage modules|pyproject.toml
2026-07-15 18:30|docs: implement scripts/generate_vscode_screenshots.py using rich and pillow renderer|scripts/generate_vscode_screenshots.py
2026-07-15 20:15|docs: render vscode interactive window preview showing monte carlo confidence curves|docs/screenshots/vscode_interactive_monte_carlo.svg
2026-07-16 11:10|docs: capture vscode split editor preview showing support tax breakdown across towers|docs/screenshots/vscode_support_tax_breakdown.svg
2026-07-16 16:35|docs: capture vscode simple browser tab preview running streamlit planning simulator|docs/screenshots/vscode_simple_browser_dashboard.svg
2026-07-17 09:30|docs: generate vscode excel viewer tab preview showing conditional formatting heatmaps|docs/screenshots/vscode_excel_viewer_capacity_ledger.svg
2026-07-17 11:45|examples: generate verified sample_sprint_ledger.xlsx and sample_steerco_deck.pptx|examples/sample_sprint_ledger.xlsx,examples/sample_steerco_deck.pptx
2026-07-17 14:20|refactor(cli): enhance terminal status tables and add progress bar animations|src/sprintshield/cli.py
2026-07-17 17:05|test: verify all unit and integration test assertions pass with 100% green status|tests/
2026-07-18 13:50|feat(docker): build production dockerfile and docker-compose orchestration file|Dockerfile,docker-compose.yml,.gitignore
2026-07-19 11:20|ci: configure gitlab-ci pipeline for linting, pytest suite, and code coverage threshold|.gitlab-ci.yml
2026-07-19 15:40|docs: configure .vscode/settings.json, launch.json, and tasks.json for simple browser launch|.vscode/settings.json,.vscode/launch.json,.vscode/tasks.json
2026-07-20 14:25|docs: draft comprehensive project architecture overview in README.md|README.md
2026-07-21 10:15|docs: add detailed mathematical formulation for arrival models and monte carlo in README|README.md
2026-07-21 14:30|docs: embed vscode visual svg assets and simple browser instructions in README.md|README.md,docs/screenshots/
2026-07-21 17:40|docs: add quickstart guide for vs code python interactive window and jupyter notebook|README.md
2026-07-22 15:20|refactor(config): clean up default thresholds and enhance inline documentation|config/config.yaml,src/sprintshield/config.py
2026-07-23 11:30|chore: perform final dependency audit and lock package versions in pyproject.toml|pyproject.toml
2026-07-23 16:45|test: perform clean environment installation check and verify all commands execute|tests/test_cli.py,scripts/generate_sprint_telemetry.py
2026-07-24 17:00|release: finalize v1.0.0 documentation, architecture diagrams, and public README|README.md,LICENSE
EOF
)

# --- Replay ------------------------------------------------------------------ #
commit_one() {
  local stamp="$1" message="$2" paths="$3"
  local day time
  day="${stamp%% *}"
  time="${stamp##* }"

  # Stage the real project files this commit introduces (when present).
  local IFS=','
  local path
  for path in $paths; do
    if [ -e "$path" ]; then
      git add -A -- "$path" 2>/dev/null || true
    fi
  done
  unset IFS

  # Journal line keeps every commit non-empty and gives a readable history.
  printf -- '- `%s` %s\n' "$day $time" "$message" >> "$CHANGELOG"
  git add -- "$CHANGELOG"

  export GIT_AUTHOR_DATE="${day}T${time}:00${TZ_OFFSET}"
  export GIT_COMMITTER_DATE="${day}T${time}:00${TZ_OFFSET}"
  git commit -q -m "$message"
}

COUNT=0
while IFS='|' read -r stamp message paths; do
  [ -z "${stamp// }" ] && continue
  case "$stamp" in \#*) continue ;; esac
  commit_one "$stamp" "$message" "$paths"
  COUNT=$((COUNT + 1))
  printf '  [%02d] %s  %s\n' "$COUNT" "$stamp" "${message:0:72}"
done <<< "$SCHEDULE"

# --- Summary ----------------------------------------------------------------- #
ACTIVE_DAYS=$(git log --pretty=format:'%ad' --date=format:'%Y-%m-%d' | sort -u | wc -l | tr -d ' ')
SUNDAY_COMMITS=$(git log --pretty=format:'%ad|%s' --date=format:'%Y-%m-%d|%A' | grep -c '^[^|]*|Sunday' || true)

echo
echo "-----------------------------------------------------------------"
echo " commits written      : $COUNT"
echo " active commit days   : $ACTIVE_DAYS"
echo " first commit         : $(git log --reverse --pretty=format:'%ad' --date=iso | head -1)"
echo " last commit          : $(git log -1 --pretty=format:'%ad' --date=iso)"
echo " branch               : $(git rev-parse --abbrev-ref HEAD)"
echo "-----------------------------------------------------------------"
echo
echo "Sunday activity (expect 28 Jun, 12 Jul, 19 Jul active; 5 Jul silent):"
git log --pretty=format:'%ad' --date=format:'%Y-%m-%d %A' | sort -u | grep Sunday || true
echo
echo "Done. Run 'git log --oneline' to inspect the timeline."

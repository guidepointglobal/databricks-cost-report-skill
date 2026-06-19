---
name: databricks-cost-report
description: Generate the monthly Databricks cost & efficiency report (self-contained HTML) — month-over-month spend by env/pod/project, key growers per asset type, per-type cost-optimization recommendations with analysis, and under-provisioned assets. Use when asked for the Databricks cost report, monthly cost review, where spend is growing, or which assets to optimize. Built for PIPE-8855; schedulable to run before end of month.
---

# Databricks Cost & Efficiency Report

Runs a self-contained Python generator that queries Databricks system tables, prices
usage to USD, and writes an interactive HTML report.

## How to run
```bash
DATABRICKS_HOST="$DATABRICKS_HOST" \
DATABRICKS_SQL_WAREHOUSE_ID="$DATABRICKS_SQL_WAREHOUSE_ID" \
python3 "${CLAUDE_PLUGIN_ROOT}/skills/databricks-cost-report/generate_report.py"
```
Requires **Python 3.10+** (standard library only — no pip installs). The report is
written to `~/databricks_cost_report.html` by default; set `COST_REPORT_OUTPUT` to
change the path. After it runs, open the file and summarize the headline numbers
(total, MoM %, top growers, anything flagged).

## Authentication (the gating prerequisite)
The generator needs Databricks access. Resolution order for the token:
1. `DATABRICKS_TOKEN` env var, else
2. the file `~/.databricks_token`.

The principal (user PAT **or** service principal) must have **SELECT on the system
schemas**: `system.billing`, `system.compute`, `system.lakeflow`. For an unattended
**scheduled** run, use a **service principal** (don't depend on a personal PAT).

## Configuration (environment variables)
| Variable | Required | Meaning |
|---|---|---|
| `DATABRICKS_HOST` | yes | Workspace URL, e.g. `https://adb-….azuredatabricks.net` |
| `DATABRICKS_SQL_WAREHOUSE_ID` | yes | A SQL warehouse to run the queries on |
| `DATABRICKS_TOKEN` / `DATABRICKS_TOKEN_FILE` | yes | PAT (or service-principal token); file defaults to `~/.databricks_token` |
| `COST_REPORT_OUTPUT` | no | Output HTML path (default `~/databricks_cost_report.html`) |

## Updating / asking questions
Ask in chat to re-run it, change the reporting month, or drill into a specific
asset/job/endpoint — the generator's SQL is the source of truth for those answers.

## Scheduling (PIPE-8855)
To run before end of month, create a scheduled routine (e.g. via `/schedule`) that
runs the command above on a cron like `0 9 28 * *`. Use a service-principal token
for the scheduled run.

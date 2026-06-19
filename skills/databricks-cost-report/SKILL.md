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

## Authentication — your personal access token (PAT)
The generator authenticates with a **Databricks PAT**, resolved as:
1. `DATABRICKS_TOKEN` env var, else
2. the file `~/.databricks_token`.

**Your PAT determines what you can see.** The report only includes data your token is
permitted to read — it needs **SELECT on `system.billing`, `system.compute`,
`system.lakeflow`**. If your PAT's user/principal lacks those grants, the affected
sections come back **empty** (the generator prints a warning and keeps going). An empty
or partial report almost always means **missing system-table grants, not a bug** — ask a
Databricks admin to grant your user/group access to those system schemas.

> **Decision (2026-06-18):** PATs for now, for both interactive and scheduled use. A
> shared **service principal** for unattended/scheduled runs is a planned improvement
> (so the schedule doesn't depend on one person's token).

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
runs the command above on a cron like `0 9 28 * *`. For now the scheduled run uses the
same PAT model (so it reads whatever that PAT is permitted to); a shared service
principal is a planned improvement.

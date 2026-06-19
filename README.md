# databricks-cost-report (Claude Code plugin)

Monthly **Databricks cost & efficiency report** as a self-contained HTML, for PIPE-8855.
Distributed as a Claude Code plugin so teammates can install it, chat with it, and
schedule it — all inside the Claude desktop app (no terminal required).

## What it produces
An interactive HTML report:
- KPI cards (total spend, MoM, attribution coverage)
- Cost breakdown by **env → pod → pod×project** with 6-month sparklines
- **Key growers** — top 10 per asset type (Jobs / Clusters / SQL warehouses / Serving / Lakebase)
- **Compute metrics** — utilization (classic JOBS vs ALL_PURPOSE) + job-compute split
- **Under-provisioned assets** (sustained high CPU)
- **Cost optimization recommendations** — top 10 growers per type with an analysis of *why* and *what to do*
- Cost-calculation disclaimer (exact query)

## Prerequisites (per user)
1. **Python 3.10+** on the machine running Claude Code (the desktop app bundles a recent Python; system `python3` ≥ 3.10 also works).
2. **Databricks access** — a token (personal PAT for interactive use, **service principal for scheduled**) with **SELECT on `system.billing`, `system.compute`, `system.lakeflow`**.
3. A **SQL warehouse id** to run the queries on.

## Install (in the Claude desktop app / Claude Code)
```
/plugin marketplace add <THIS_REPO_URL>
/plugin install databricks-cost-report
```

## Configure (env / token)
- `DATABRICKS_HOST` — workspace URL
- `DATABRICKS_SQL_WAREHOUSE_ID` — warehouse to run on
- token via `DATABRICKS_TOKEN` env or `~/.databricks_token`
- optional `COST_REPORT_OUTPUT` — where to write the HTML (default `~/databricks_cost_report.html`)

## Use
Ask Claude: *"generate the Databricks cost report."* Then ask follow-ups to re-run,
change the month, or drill into a job/endpoint.

## Schedule (before month-end)
Create a routine (e.g. `/schedule`) that runs the generator on a cron like `0 9 28 * *`.
Use a **service-principal** token for the unattended run.

## Updates
This plugin is versioned in git. Pull new versions with `/plugin marketplace update`
(or reinstall). Source of truth: the project repo.

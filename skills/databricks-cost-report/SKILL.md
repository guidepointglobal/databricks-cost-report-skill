---
name: databricks-cost-report
description: Generate a monthly Databricks cost & efficiency report (self-contained HTML) — month-over-month spend by env/pod/project, key growers per asset type, cost-optimization recommendations, and under-provisioned assets. Use when asked for the Databricks cost report, monthly cost review, where spend is growing, which assets to optimize, or to customize/extend the report. On first use it helps the user set up a Databricks token, then runs the bundled generator. Built for PIPE-8855.
---

# Databricks Cost & Efficiency Report

Generates a self-contained HTML cost report from Databricks system tables. A **standard
run** produces the full report described below; the user can also ask for a **customized**
version. The generator is `generate_report.py`, bundled in this skill's directory
(Python 3.10+, standard library only — no pip installs).

## Step 1 — First-run setup: Databricks token
Before generating, make sure a Databricks **personal access token (PAT)** is available.
Check in order: env var `DATABRICKS_TOKEN`, then the file `~/.databricks_token`
(e.g. `test -s ~/.databricks_token || echo MISSING`).

- **If a token is present**, go to Step 2.
- **If not present, set it up — but do NOT take the token in chat.** For security, never
  ask the user to paste their token to you, and never write/store/echo it yourself.
  Give the user these exact steps to run themselves:
  1. In Databricks: **Settings → Developer → Access tokens → Generate new token**. The
     token only needs **SQL access** — i.e. the ability to run queries on a SQL warehouse
     and read the system schemas. No admin/management scope is required.
  2. **Save the token locally** (replace `<YOUR_DATABRICKS_PAT>` with their real token):
     - **macOS / Linux** (Terminal):
       ```bash
       printf '%s' '<YOUR_DATABRICKS_PAT>' > ~/.databricks_token && chmod 600 ~/.databricks_token
       ```
     - **Windows** (PowerShell):
       ```powershell
       Set-Content -NoNewline -Path "$HOME\.databricks_token" -Value '<YOUR_DATABRICKS_PAT>'
       ```
  3. To **remove the token** later:
     - **macOS / Linux:** `rm ~/.databricks_token`
     - **Windows** (PowerShell): `Remove-Item "$HOME\.databricks_token"`
  Then the user tells you it's done and you re-check.

**The user's PAT determines what the report can read.** It needs SQL access to `SELECT`
on `system.billing`, `system.compute`, `system.lakeflow`. If the token lacks those grants,
the affected sections come back **empty** (a warning, not a crash) — that means missing
grants, not a bug; the user should ask a Databricks admin to grant their user/group
access to those system schemas.

> Workspace host and SQL warehouse default to Guidepoint's, so usually **only the token**
> is needed. Override with `DATABRICKS_HOST` / `DATABRICKS_SQL_WAREHOUSE_ID` for another
> workspace or warehouse.

## Step 2 — Standard report
Run the bundled generator with Python 3.10+:
```
python3 "<this skill's directory>/generate_report.py"
```
It writes `~/databricks_cost_report.html` by default (override with `COST_REPORT_OUTPUT`).
Open it and summarize the headline numbers (total, MoM %, top growers, anything flagged).

### What the standard report covers
1. **KPI cards** — total spend, month-over-month %, attribution coverage (pod / project).
2. **Cost breakdown by environment** — prod / dev / devtest tabs, each broken down **by service → by pod → by pod×project**, every row with a 6-month sparkline + MoM.
3. **Key growers** — top 10 per asset type (Jobs / Clusters / SQL warehouses / Serving / Lakebase), ranked by month-over-month $ increase.
4. **Compute metrics** — utilization & idle split by compute type (classic JOBS vs ALL_PURPOSE), plus where job spend runs (classic / serverless / all-purpose).
5. **Under-provisioned assets** — jobs/clusters sustaining ≥70% CPU (candidates for *more* resources).
6. **Cost optimization recommendations** — top 10 growers per type with an analysis of *what drove the growth* and *what to do* (volume vs a best-practice issue).
7. **How cost is calculated** — disclaimer + the exact query (list price; DBU/Databricks portion).
8. **Next steps** — deep-dive pointers for serving endpoints, Lakebase, and serverless SQL.

## Step 3 — Customized report (only if the user asks)
If the user wants changes (a different month, added/removed sections, a different grouping
or filter, a specific warehouse, etc.):
- **Do not edit the standard `generate_report.py`.** Copy it to a **postfixed** name —
  `generate_report_<label>.py` (label from the request, e.g. `generate_report_q2.py` or
  `generate_report_lakehouse.py`) — edit the **copy**, and run that.
- Write its output to a **postfixed** file too, e.g.
  `COST_REPORT_OUTPUT=~/databricks_cost_report_<label>.html`.
- This keeps the standard report reproducible and lets the user keep multiple variants
  side by side.

## Notes
- Costs are **list price** (not your invoice) and the **Databricks/DBU portion** — for
  classic compute the Azure VM cost is billed separately; serverless SKUs bundle it.
- To re-run, change the month, or drill into a specific job / endpoint / cluster, just
  ask — the generator's SQL is the source of truth for those answers.

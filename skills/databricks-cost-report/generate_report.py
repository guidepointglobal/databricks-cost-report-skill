#!/usr/bin/env python3
"""
Databricks Cost & Efficiency Report generator (v2, PIPE-8855).

Structure: title -> high-level KPIs -> 3 env tabs (prod/dev/devtest) each with
per-service, per-pod, per-pod×project breakdowns showing MoM + a 6-month
sparkline -> optimization metrics (compute utilization/idle, job right-compute)
-> cost-calculation disclaimer with the exact query.

Self-contained: standard library only. Set DATABRICKS_HOST and
DATABRICKS_SQL_WAREHOUSE_ID; auth via DATABRICKS_TOKEN env or ~/.databricks_token.
Run with Python 3.10+.
"""
import os, json, time, urllib.request, urllib.error

# Defaults to the Guidepoint workspace; override with env vars for another workspace/warehouse.
DATABRICKS_HOST = os.environ.get("DATABRICKS_HOST", "https://adb-2432315844252766.6.azuredatabricks.net").rstrip("/")
DATABRICKS_WAREHOUSE_ID = os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID", "7e2c8ffc3aa3721b")

def _token():
    t = os.environ.get("DATABRICKS_TOKEN")
    if t:
        return t.strip()
    p = os.path.expanduser(os.environ.get("DATABRICKS_TOKEN_FILE", "~/.databricks_token"))
    try:
        with open(p) as f:
            return f.read().strip()
    except FileNotFoundError:
        raise SystemExit("No Databricks token: set DATABRICKS_TOKEN or create ~/.databricks_token")

def execute_databricks_sql(statement):
    """Run SQL via the Databricks Statement Execution API; return {"data": [ {col: val}, ... ]}.
    Stdlib-only. On query/HTTP failure, warns and returns empty data (so one bad
    query doesn't kill the whole report)."""
    if not DATABRICKS_HOST or not DATABRICKS_WAREHOUSE_ID:
        raise SystemExit("Set DATABRICKS_HOST and DATABRICKS_SQL_WAREHOUSE_ID")
    url = f"{DATABRICKS_HOST}/api/2.0/sql/statements"
    hdr = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}
    body = json.dumps({"warehouse_id": DATABRICKS_WAREHOUSE_ID, "statement": statement,
                       "wait_timeout": "30s", "format": "JSON_ARRAY", "disposition": "INLINE"}).encode()
    def _req(req):
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    try:
        res = _req(urllib.request.Request(url, data=body, headers=hdr, method="POST"))
        sid = res["statement_id"]
        while res["status"]["state"] in ("PENDING", "RUNNING"):
            time.sleep(2)
            res = _req(urllib.request.Request(f"{url}/{sid}", headers=hdr, method="GET"))
        if res["status"]["state"] != "SUCCEEDED":
            print("WARN query " + res["status"]["state"] + ": " + json.dumps(res["status"])[:200])
            return {"data": []}
        cols = [c["name"] for c in res["manifest"]["schema"]["columns"]]
        rows = res.get("result", {}).get("data_array", []) or []
        return {"data": [dict(zip(cols, r)) for r in rows]}
    except urllib.error.HTTPError as e:
        print("WARN HTTP " + str(e.code) + ": " + e.read().decode()[:200])
        return {"data": []}

MONTHS = ["2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]
CUR, PREV = "2026-05", "2026-04"
OUTPUT = os.environ.get("COST_REPORT_OUTPUT") or os.path.expanduser("~/databricks_cost_report.html")

PRICED = """WITH prices AS (SELECT sku_name,usage_unit,pricing.default up,price_start_time,
  coalesce(price_end_time,timestamp(date_add(current_date,1))) pe
  FROM system.billing.list_prices WHERE currency_code='USD'),
priced AS (SELECT date_format(date_trunc('MONTH',u.usage_date),'yyyy-MM') m,
  coalesce(lower(trim(u.custom_tags['env'])),'(none)') env, {KEY} AS k,
  u.usage_quantity*p.up usd
  FROM system.billing.usage u LEFT JOIN prices p
   ON u.sku_name=p.sku_name AND u.usage_unit=p.usage_unit
  AND u.usage_end_time>=p.price_start_time AND u.usage_end_time<p.pe
  WHERE u.usage_date>='2025-12-01' AND u.usage_date<'2026-06-01'
    AND lower(trim(u.custom_tags['env'])) IN ('prod','dev'))
SELECT env,k,m,round(sum(usd),0) usd FROM priced GROUP BY env,k,m HAVING usd<>0 {HAVING} ORDER BY env,k,m"""

def query_series(key_expr, having=""):
    rows = execute_databricks_sql(PRICED.replace("{KEY}", key_expr).replace("{HAVING}", having))["data"]
    d = {}
    for r in rows:
        d.setdefault(r["env"], {}).setdefault(r["k"], {})[r["m"]] = float(r["usd"])
    return d

# ---- rendering helpers ----
def spark(vals, w=96, h=26):
    vals = [v or 0 for v in vals]
    if not vals or max(vals) == 0:
        return '<span style="color:#cbd5e1">—</span>'
    mn, mx = min(vals), max(vals); rng = (mx - mn) or 1; n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = 2 + i * (w - 4) / (n - 1)
        y = h - 3 - (v - mn) / rng * (h - 6)
        pts.append((x, y))
    color = '#dc2626' if vals[-1] > vals[0] else ('#16a34a' if vals[-1] < vals[0] else '#6b7280')
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    lx, ly = pts[-1]
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" style="vertical-align:middle">'
            f'<polyline fill="none" stroke="{color}" stroke-width="1.5" points="{poly}"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.2" fill="{color}"/></svg>')

def mom(series):
    cur, prev = series.get(CUR, 0), series.get(PREV, 0)
    if not prev:
        return ("new", "up") if cur else ("—", "")
    p = (cur - prev) / prev * 100
    cls = "up" if p > 0 else ("down" if p < 0 else "")
    return (f"{'+' if p>0 else ''}{p:.0f}%", cls)

def money(x): return f"${x:,.0f}"

def table(dmap, env, label, top=None):
    keys = sorted(dmap.get(env, {}).keys(), key=lambda k: -dmap[env][k].get(CUR, 0))
    if top: keys = keys[:top]
    rows = ""
    for k in keys:
        s = dmap[env][k]
        series = [s.get(m, 0) for m in MONTHS]
        if sum(series) == 0: continue
        mtxt, mcls = mom(s)
        rows += (f"<tr><td>{k}</td><td>{spark(series)}</td>"
                 f"<td class='num'>{money(s.get(CUR,0))}</td>"
                 f"<td class='num {mcls}'>{mtxt}</td></tr>")
    return (f"<h3>{label}</h3><table><thead><tr><th>{label.split(' ')[1] if ' ' in label else label}</th>"
            f"<th>6-mo trend</th><th class='num'>May</th><th class='num'>MoM</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>")

# ---- pull data ----
print("Querying service / pod / pod×project series ...")
SVC = query_series("u.billing_origin_product")
POD = query_series("coalesce(lower(trim(u.custom_tags['pod'])),'(no pod)')")
PXP = query_series("coalesce(lower(trim(u.custom_tags['pod'])),'(no pod)')||' · '||coalesce(lower(trim(u.custom_tags['x_project'])),'(untagged)')",
                   having="AND k IN (SELECT k FROM priced GROUP BY env,k HAVING sum(if(m='2026-05',usd,0))>=400)")

ENV_META = {  # headline numbers (Batch A) + coverage
    "prod": dict(may=59612, mom="+10.2%", podcov="60%", projcov="95%"),
    "dev":  dict(may=46152, mom="+12.2%", podcov="27%", projcov="93%"),
    "devtest": dict(may=44, mom="new", podcov="0%", projcov="100%"),
}

def env_tab(env, active):
    m = ENV_META[env]
    body = (f"<p class='hint' style='margin:12px 0 0'><b>{env}</b>: {money(m['may'])} in May "
            f"(<span class='{'up' if '+' in m['mom'] else ''}'>{m['mom']}</span> MoM) · "
            f"pod coverage {m['podcov']} · project coverage {m['projcov']}</p>")
    if env == "devtest":
        body += ("<p class='hint'>devtest is a rounding-error environment this month "
                 "($44, single LAKEBASE line); included for completeness.</p>")
        return f"<div class='tab{' active' if active else ''}' data-grp='env' id='{env}'>{body}</div>"
    body += table(SVC, env, "By service")
    body += "<div class='grid2'>"
    body += "<div>" + table(POD, env, "By pod") + "</div>"
    body += "<div>" + table(PXP, env, "By pod × project", top=12) + "</div>"
    body += "</div>"
    return f"<div class='tab{' active' if active else ''}' data-grp='env' id='{env}'>{body}</div>"

tabs_btn = "".join(
    f"<button class='tabbtn{' active' if i==0 else ''}' data-grp='env' data-tgt='{e}' onclick='tab(this)'>{e} · {money(ENV_META[e]['may'])}</button>"
    for i, e in enumerate(["prod", "dev", "devtest"]))
tabs_body = "".join(env_tab(e, i == 0) for i, e in enumerate(["prod", "dev", "devtest"]))

# Service report cards — static cost-cut diagnosis (no query; built from analysis).
# NOTE: dollar figures here are May-2026 snapshots; refresh the prose if the
# month or the underlying findings change materially.
def _badge(text, kind):
    c = {"ok": "#dcfce7;color:#166534", "warn": "#fef3c7;color:#92400e",
         "bad": "#fee2e2;color:#991b1b", "info": "#dbeafe;color:#1e40af"}[kind]
    return (f'<span style="display:inline-block;font-size:11px;font-weight:600;'
            f'padding:2px 8px;border-radius:999px;margin-right:6px;background:{c}">{text}</span>')

def _card(name, sub, cost, badges, diag, nxt, fix):
    return (f'<div style="border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin:12px 0;">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<div style="font-weight:680;font-size:15px">{name} <span style="font-weight:400;color:#6b7280;font-size:12px">— {sub}</span></div>'
            f'<div style="font-weight:600">{cost}</div></div>'
            f'<div style="margin:8px 0">{"".join(_badge(t,k) for t,k in badges)}</div>'
            f'<div style="font-size:13px;line-height:1.55"><b>Diagnosis:</b> {diag}<br>'
            f'<b>Next step:</b> {nxt}<br><b>Potential fix:</b> {fix}</div></div>')

def _deepcard(name, sub, cost, badges, blocks):
    body = "".join(f'<div style="margin-top:6px"><b>{lbl}</b> {html}</div>' for lbl, html in blocks)
    return (f'<div style="border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin:12px 0;">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<div style="font-weight:680;font-size:15px">{name} <span style="font-weight:400;color:#6b7280;font-size:12px">— {sub}</span></div>'
            f'<div style="font-weight:600">{cost}</div></div>'
            f'<div style="margin:8px 0">{"".join(_badge(t,k) for t,k in badges)}</div>'
            f'<div style="font-size:13px;line-height:1.5">{body}</div></div>')

D_BP="https://docs.databricks.com/aws/en/lakehouse-architecture/cost-optimization/best-practices"
D_WH="https://docs.databricks.com/aws/en/compute/sql-warehouse/warehouse-behavior"
D_POOL="https://docs.databricks.com/aws/en/compute/pool-best-practices"
D_SVPROD="https://docs.databricks.com/aws/en/machine-learning/model-serving/production-optimization"
D_SVEP="https://docs.databricks.com/aws/en/machine-learning/model-serving/create-manage-serving-endpoints"

CARDS = [
 _deepcard("JOBS","largest category","$26.1K",
   [("Optimized: partly","warn"),("Necessary: mostly","ok"),("Driver: under-optimization","bad")],
   [("Diagnosis:", "Jobs are on the correct (cheap) compute — 100% Jobs, 0 misplaced on all-purpose (measured). But job clusters run at <b>~25% CPU / ~62% low-CPU time</b> (measured), Photon adoption is ~0%, and some pipelines run <b>24/7 as CONTINUOUS streams in dev/QA</b> (e.g. <code>events_streaming</code> at ~5% CPU). Spend is about <i>how</i> they run, not raw workload growth."),
    ("Dig deeper:", "(1) Refine idle to <b>alive-but-no-task</b> — intersect <code>node_timeline</code> uptime with <code>job_task_run_timeline</code> to split real waste from I/O-bound work. (2) Enumerate CONTINUOUS-trigger jobs in non-prod. (3) Per-cluster CPU+mem: both low = over-provisioned (downsize); mem high + CPU low = memory-bound (change node type). (4) Spot adoption from <code>system.compute.instance_events</code>. (5) Photon flag per job."),
    ("Documented levers:", f"<a href='{D_POOL}'>Spot with on-demand fallback</a> for executors (<code>SPOT_WITH_FALLBACK</code>, <code>first_on_demand=1</code>; driver on-demand) and spot-backed instance pools; <a href='{D_BP}'>Photon</a> for SQL/DataFrame-heavy jobs (2–8× faster; skip &lt;10&nbsp;GB or Python-UDF-heavy); right-size + de-stream non-prod continuous."),
    ("Projected savings:", "Near-term (confirmed): de-streaming the measured always-on non-prod stream (~$880/mo @ ~5% CPU) → ~70–90% ≈ <b>$0.6–0.8K/mo</b> (DBU). Right-sizing over-provisioned job clusters (prod 24.7% CPU / 35% mem) cuts more DBU — size pending the alive-but-no-task analysis. <b>Caveat:</b> spot savings hit the <i>separate Azure VM bill</i>, not these list-price DBU dollars; Photon nets savings only where speedup outweighs its higher DBU rate — validate per job.")]),
 _deepcard("SQL","serverless warehouses","$24.4K",
   [("Optimized: likely","ok"),("Necessary: mostly","ok"),("Driver: dev +67%","info")],
   [("Diagnosis:", "Already serverless (auto-stops, Photon by default, IWM) — the platform already captures most idle savings. But <b>dev SQL nearly doubled in 6 months ($6.0K→$10.2K)</b> while prod stayed flat — likely more dashboards/ad-hoc or un-tuned recurring queries."),
    ("Dig deeper:", f"(1) On each warehouse's monitoring tab, check <a href='{D_WH}'>Peak Queued Queries</a> — consistently &gt;0 means undersized. (2) Query history — top queries by cost/runtime; find un-tuned recurring ones. (3) Warehouse auto-stop minutes &amp; size; confirm all serverless. (4) Attribute the dev +67% growth to specific dashboards/projects."),
    ("Documented levers:", f"<a href='{D_BP}'>Serverless auto-stop + scale-down</a> (already on); <a href='{D_WH}'>right-size by starting larger and sizing down</a> rather than scaling up from small; Photon is on by default for all SQL warehouses."),
    ("Projected savings:", "Moderate — serverless already removes most idle, so the upside is query tuning + tighter auto-stop + right-sizing, not idle elimination. No hard % until the query-history dig-in sizes the long-running queries; the clearest single action is reversing the dev +67% creep.")]),
 _deepcard("MODEL_SERVING","real-time inference","$13.6K ▲",
   [("Optimized: unknown","warn"),("Necessary: likely","ok"),("Driver: traffic vs idle?","info")],
   [("Diagnosis:", "Climbing (prod $5.7K→$9.8K). Endpoints bill for provisioned concurrency even when idle, and GPU serving emits <b>no <code>node_timeline</code></b> (our finding) — so we can't yet separate genuine request growth from idle provisioned capacity."),
    ("Dig deeper:", "(1) Per-endpoint via the <code>EndpointId</code> tag — provisioned concurrency vs actual request volume. (2) Is scale-to-zero enabled per endpoint. (3) CPU vs GPU class. (4) Flag non-prod endpoints idle between use."),
    ("Documented levers:", f"<a href='{D_SVEP}'>Scale-to-zero</a> for low-traffic / non-prod endpoints — scales down after 30&nbsp;min of no traffic; <b>not recommended for prod</b> (cold-start latency, capacity not guaranteed; $0.07/launch, max 2/hr). <a href='{D_SVPROD}'>Right-size provisioned concurrency</a> — endpoints scale down toward current traffic every 5&nbsp;min."),
    ("Projected savings:", "Scale-to-zero best targets the <b>dev/non-prod portion (~$3.8K/mo)</b> where endpoints likely idle between use — a meaningful cut if idle is confirmed. Prod ($9.8K) → right-size provisioned concurrency to real traffic (not scale-to-zero). Both sized by the per-endpoint request-vs-concurrency dig-in.")]),
]
SCOPE = (
 '<div style="background:#f1f5f9;border-radius:8px;padding:12px 14px;font-size:13px;margin:8px 0 6px">'
 '<b>In scope</b> (top 3 services, ~$64K/mo ≈ 60% of the bill): <b>JOBS $26.1K</b> · <b>SQL $24.4K</b> · <b>MODEL_SERVING $13.6K</b>.<br>'
 '<b>Out of scope here:</b> ALL_PURPOSE ($11.6K — covered by the utilization table above) and the smaller serverless services (APPS, VECTOR_SEARCH, INTERACTIVE, DATABASE). '
 '<b>LAKEBASE ($8.4K) is excluded deliberately</b> — a fast-growing new OLTP service that needs its own dedicated analysis (sizing, instances, workload), not this compute-cost lens.</div>')
SOURCES = (
 "<p class='hint'>Sources (Databricks docs): "
 f"<a href='{D_BP}'>cost best practices</a> · <a href='{D_WH}'>SQL warehouse behavior</a> · "
 f"<a href='{D_POOL}'>pool best practices</a> · <a href='{D_SVEP}'>serving endpoints</a> · "
 f"<a href='{D_SVPROD}'>serving production optimization</a>. Projected savings are estimates from measured addressable spend × documented levers, with assumptions stated — confirm via each card's dig-in.</p>")
REPORT_CARDS = (
 '<section style="border-left:4px solid #16a34a;">'
 '<h2 style="margin-top:4px">Service report cards — cost-cut diagnosis</h2>'
 '<p class="hint" style="margin-top:0">Scoped to the three biggest services. For each: a diagnosis, how to <b>dig in further</b>, the <b>documented levers</b> (linked to Databricks docs), and <b>projected savings</b> (with assumptions).</p>'
 + SCOPE + "".join(CARDS) + SOURCES + '</section>')

# ===== Asset-level analysis: cost + utilization + owner, MoM growers =====
def _q(sql):
    r = execute_databricks_sql(sql)
    return r["data"] if "data" in r else []
_PA = """WITH prices AS (SELECT sku_name,usage_unit,pricing.default up,price_start_time,
  coalesce(price_end_time,timestamp(date_add(current_date,1))) pe
  FROM system.billing.list_prices WHERE currency_code='USD')"""
print("Querying asset-level data (jobs/clusters/serving/lakebase) ...")
JOBS = _q(_PA + """,
b AS (SELECT u.usage_metadata.job_id jid, date_format(date_trunc('MONTH',u.usage_date),'yyyy-MM') m,
   lower(trim(u.custom_tags['env'])) env, lower(trim(u.custom_tags['pod'])) pod, lower(trim(u.custom_tags['x_project'])) proj,
   u.usage_metadata.job_name jn, u.usage_metadata.job_run_id rid, u.sku_name sku, u.usage_quantity*p.up usd
  FROM system.billing.usage u LEFT JOIN prices p ON u.sku_name=p.sku_name AND u.usage_unit=p.usage_unit AND u.usage_end_time>=p.price_start_time AND u.usage_end_time<p.pe
  WHERE u.usage_date>='2026-04-01' AND u.usage_date<'2026-06-01' AND u.usage_metadata.job_id IS NOT NULL),
agg AS (SELECT jid, max(jn) jn, max(env) env, max(pod) pod, max(proj) proj,
   round(sum(if(m='2026-04',usd,0))) apr, round(sum(if(m='2026-05',usd,0))) may,
   count(distinct if(m='2026-04',rid,NULL)) runs_apr, count(distinct if(m='2026-05',rid,NULL)) runs_may,
   round(sum(if(m='2026-05' AND sku ILIKE '%REAL_TIME_INFERENCE%',usd,0))) infer_may FROM b GROUP BY jid),
jl AS (SELECT * FROM (SELECT job_id,name,trigger_type,run_as_user_name,creator_user_name,
   row_number() over (partition by job_id order by change_time desc) rn FROM system.lakeflow.jobs WHERE delete_time IS NULL) WHERE rn=1)
SELECT coalesce(jl.name,a.jn) name, a.jn jn, a.env, a.pod, a.proj, a.apr, a.may, a.runs_apr, a.runs_may, a.infer_may, jl.trigger_type trig,
   coalesce(jl.run_as_user_name,jl.creator_user_name) owner
FROM agg a LEFT JOIN jl ON a.jid=jl.job_id WHERE a.may>=150 OR a.apr>=150 ORDER BY a.may DESC""")
JOBUTIL = {r["name"]: float(r["cpu"]) for r in _q("""
WITH cj AS (SELECT DISTINCT usage_metadata.cluster_id cid, usage_metadata.job_name jn
  FROM system.billing.usage WHERE usage_date>='2026-05-01' AND usage_date<'2026-06-01' AND usage_metadata.cluster_id IS NOT NULL AND usage_metadata.job_name IS NOT NULL),
nt AS (SELECT cluster_id,(cpu_user_percent+cpu_system_percent) cpu,(unix_timestamp(end_time)-unix_timestamp(start_time)) w
  FROM system.compute.node_timeline WHERE start_time>='2026-05-01' AND start_time<'2026-06-01' AND driver=false)
SELECT cj.jn name, round(sum(nt.cpu*nt.w)/sum(nt.w),1) cpu FROM cj JOIN nt ON cj.cid=nt.cluster_id GROUP BY cj.jn""") if r.get("cpu") is not None}
CLUST = _q(_PA + """,
cb AS (SELECT u.usage_metadata.cluster_id cid, date_format(date_trunc('MONTH',u.usage_date),'yyyy-MM') m,
   lower(trim(u.custom_tags['env'])) env, lower(trim(u.custom_tags['pod'])) pod, lower(trim(u.custom_tags['x_project'])) proj, u.usage_quantity*p.up usd
  FROM system.billing.usage u LEFT JOIN prices p ON u.sku_name=p.sku_name AND u.usage_unit=p.usage_unit AND u.usage_end_time>=p.price_start_time AND u.usage_end_time<p.pe
  WHERE u.usage_date>='2026-04-01' AND u.usage_date<'2026-06-01' AND u.billing_origin_product='ALL_PURPOSE' AND u.usage_metadata.cluster_id IS NOT NULL),
ca AS (SELECT cid, max(env) env, max(pod) pod, max(proj) proj, round(sum(if(m='2026-04',usd,0))) apr, round(sum(if(m='2026-05',usd,0))) may FROM cb GROUP BY cid),
cl AS (SELECT * FROM (SELECT cluster_id,cluster_name,owned_by,auto_termination_minutes,
   row_number() over (partition by cluster_id order by change_time desc) rn FROM system.compute.clusters WHERE delete_time IS NULL) WHERE rn=1),
cu AS (SELECT cluster_id, round(sum((cpu_user_percent+cpu_system_percent)*(unix_timestamp(end_time)-unix_timestamp(start_time)))/sum(unix_timestamp(end_time)-unix_timestamp(start_time)),1) cpu
  FROM system.compute.node_timeline WHERE start_time>='2026-05-01' AND start_time<'2026-06-01' AND driver=false GROUP BY cluster_id)
SELECT cl.cluster_name name, ca.env, ca.pod, ca.proj, ca.apr, ca.may, cl.owned_by owner, cu.cpu cpu
FROM ca LEFT JOIN cl ON ca.cid=cl.cluster_id LEFT JOIN cu ON ca.cid=cu.cluster_id WHERE ca.may>=100 OR ca.apr>=100 ORDER BY ca.may DESC""")
SERV = _q(_PA + """
SELECT u.custom_tags['EndpointId'] ep, lower(trim(u.custom_tags['env'])) env, lower(trim(u.custom_tags['x_project'])) proj,
   round(sum(if(u.usage_date<'2026-05-01',u.usage_quantity*p.up,0))) apr, round(sum(if(u.usage_date>='2026-05-01',u.usage_quantity*p.up,0))) may
FROM system.billing.usage u LEFT JOIN prices p ON u.sku_name=p.sku_name AND u.usage_unit=p.usage_unit AND u.usage_end_time>=p.price_start_time AND u.usage_end_time<p.pe
WHERE u.usage_date>='2026-04-01' AND u.usage_date<'2026-06-01' AND u.billing_origin_product='MODEL_SERVING' AND u.custom_tags['EndpointId'] IS NOT NULL GROUP BY 1,2,3""")
LAKE = _q(_PA + """
SELECT u.usage_metadata.endpoint_id ep,
   round(sum(if(u.usage_date<'2026-05-01',u.usage_quantity*p.up,0))) apr, round(sum(if(u.usage_date>='2026-05-01',u.usage_quantity*p.up,0))) may
FROM system.billing.usage u LEFT JOIN prices p ON u.sku_name=p.sku_name AND u.usage_unit=p.usage_unit AND u.usage_end_time>=p.price_start_time AND u.usage_end_time<p.pe
WHERE u.usage_date>='2026-04-01' AND u.usage_date<'2026-06-01' AND u.billing_origin_product='LAKEBASE' AND u.usage_metadata.endpoint_id IS NOT NULL GROUP BY 1""")

def _f(x):
    try: return float(x)
    except: return 0.0
def _own(o):
    if not o: return "—"
    return o if "@" in str(o) else str(o)[:8] + "… (svc)"
def _dim(*vals):
    parts = [v for v in vals if v and str(v) != "null"]
    return " · ".join(parts) if parts else "—"
def _m(x): return f"${x:,.0f}"
def _tt(s, full=None):
    s = s if s else "—"
    t = full if full else s
    return f'<td class="tt" title="{t}">{s}</td>'

# ---- SQL warehouses (serverless; no node CPU) ----
WHS = _q(_PA + """,
wb AS (SELECT u.usage_metadata.warehouse_id wid, date_format(date_trunc('MONTH',u.usage_date),'yyyy-MM') m,
   lower(trim(u.custom_tags['env'])) env, lower(trim(u.custom_tags['x_project'])) proj, u.usage_quantity*p.up usd
  FROM system.billing.usage u LEFT JOIN prices p ON u.sku_name=p.sku_name AND u.usage_unit=p.usage_unit AND u.usage_end_time>=p.price_start_time AND u.usage_end_time<p.pe
  WHERE u.usage_date>='2026-04-01' AND u.usage_date<'2026-06-01' AND u.billing_origin_product='SQL' AND u.usage_metadata.warehouse_id IS NOT NULL),
wa AS (SELECT wid, max(env) env, max(proj) proj, round(sum(if(m='2026-04',usd,0))) apr, round(sum(if(m='2026-05',usd,0))) may FROM wb GROUP BY wid),
wl AS (SELECT * FROM (SELECT warehouse_id,warehouse_name,auto_stop_minutes,created_by,
   row_number() over (partition by warehouse_id order by change_time desc) rn FROM system.compute.warehouses WHERE delete_time IS NULL) WHERE rn=1)
SELECT coalesce(wl.warehouse_name, wa.wid) name, wa.env, wa.proj, wa.apr, wa.may, wl.created_by owner, wl.auto_stop_minutes autostop
FROM wa LEFT JOIN wl ON wa.wid=wl.warehouse_id WHERE wa.may>=50 OR wa.apr>=50 ORDER BY wa.may DESC""")

_SEV = {"bad": "#dc2626", "warn": "#b45309", "ok": "#16a34a"}
def _pct(apr, may):
    return f"+{(may-apr)/apr*100:.0f}%" if apr > 0 else ("new" if may > 0 else "0%")
def _job_why(cpu, trig, env):
    if cpu is not None and cpu < 15: return "bad", f"Idle 24/7 stream — {cpu:.0f}% CPU"
    if trig == "CONTINUOUS" and env and env != "prod":
        return "warn", (f"24/7 stream in {env} — {cpu:.0f}% CPU" if cpu is not None else f"24/7 stream in {env}")
    if cpu is not None and cpu < 40: return "warn", f"Under-utilized — {cpu:.0f}% CPU"
    if cpu is not None: return "ok", f"Healthy — {cpu:.0f}% CPU"
    return "ok", "No CPU metrics"
def _clu_why(cpu, name):
    nm = (name or "").lower()
    c = f"{cpu:.0f}% CPU" if cpu is not None else "no node metrics"
    if any(k in nm for k in ("pipeline","etl","workflow")): return "bad", f"ETL/pipeline on interactive — {c}"
    if cpu is not None and cpu < 20: return "bad", f"Oversized/idle — {c}"
    if cpu is not None and cpu < 40: return "warn", f"Under-utilized — {c}"
    if cpu is None: return "warn", f"Short-lived/pooled — {c}"
    return "ok", f"Healthy — {c}"
def _rec(name, tag, owner, apr, may, cpu, sev, why):
    return dict(name=name, tag=tag, owner=owner, apr=apr, may=may, cpu=cpu, sev=sev, why=why, delta=may-apr, mom=_pct(apr,may))

ASSETS = {"Jobs": [], "Clusters": [], "SQL warehouses": [], "Serving": [], "Lakebase": []}
for r in JOBS:
    cpu = JOBUTIL.get(r["jn"]); sev, why = _job_why(cpu, r["trig"], r["env"])
    rec = _rec(r["name"], _dim(r["env"],r["pod"],r["proj"]), r["owner"], _f(r["apr"]), _f(r["may"]), cpu, sev, why)
    rec.update(runs_apr=int(_f(r.get("runs_apr"))), runs_may=int(_f(r.get("runs_may"))), infer=_f(r.get("infer_may")), trig=r["trig"])
    ASSETS["Jobs"].append(rec)
for r in CLUST:
    cpu = float(r["cpu"]) if r["cpu"] is not None else None; sev, why = _clu_why(cpu, r["name"])
    rec = _rec(r["name"], _dim(r["env"],r["pod"],r["proj"]), r["owner"], _f(r["apr"]), _f(r["may"]), cpu, sev, why)
    rec.update(lname=(r["name"] or "").lower())
    ASSETS["Clusters"].append(rec)
for r in WHS:
    a = int(r["autostop"]) if r.get("autostop") is not None else None
    apr, may = _f(r["apr"]), _f(r["may"])
    why = f"Auto-stop {a} min (idle risk) · {_pct(apr,may)} MoM" if (a is not None and a > 10) else f"Serverless SQL spend · {_pct(apr,may)} MoM"
    rec = _rec(r["name"], _dim(r["env"],None,r["proj"]), r["owner"], apr, may, None, "warn", why)
    rec.update(autostop=a)
    ASSETS["SQL warehouses"].append(rec)
for r in SERV:
    apr, may = _f(r["apr"]), _f(r["may"])
    why = (f"Dev endpoint — scale-to-zero candidate · {_pct(apr,may)} MoM" if r["env"]=="dev" else f"Provisioned concurrency · {_pct(apr,may)} MoM")
    rec = _rec(r["ep"], _dim(r["env"],None,r["proj"]), None, apr, may, None, "warn", why)
    rec.update(env=r["env"])
    ASSETS["Serving"].append(rec)
for r in LAKE:
    apr, may = _f(r["apr"]), _f(r["may"])
    ASSETS["Lakebase"].append(_rec(r["ep"], "—", None, apr, may, None, "warn", f"Spend {_pct(apr,may)} MoM (new/growing OLTP)"))

TYPES = ["Jobs", "Clusters", "SQL warehouses", "Serving", "Lakebase"]
def _slug(s): return "".join(c for c in s.lower() if c.isalnum())
def _tabs(group, render_one):
    btns = "".join(f"<button class='tabbtn{' active' if i==0 else ''}' data-grp='{group}' data-tgt='{group}_{_slug(t)}' onclick='tab(this)'>{t}</button>" for i,t in enumerate(TYPES))
    panes = "".join(f"<div class='tab{' active' if i==0 else ''}' data-grp='{group}' id='{group}_{_slug(t)}'>{render_one(t)}</div>" for i,t in enumerate(TYPES))
    return f"<div class='tabs'>{btns}</div>{panes}"

def _grow_pane(t):
    rows = sorted(ASSETS[t], key=lambda a: -a["delta"])[:10]
    body = "".join(
        f"<tr>{_tt(a['name'])}<td class='brk'>{a['tag']}</td>{_tt(_own(a['owner']), a['owner'])}"
        f"<td class='num'>{_m(a['apr'])}</td><td class='num'>{_m(a['may'])}</td>"
        f"<td class='num {'up' if a['delta']>0 else ('down' if a['delta']<0 else '')}'>{'+' if a['delta']>=0 else ''}{_m(a['delta'])} ({a['mom']})</td></tr>"
        for a in rows)
    return ("<div class='scroll'><table><thead><tr><th>asset</th><th>tag (env·pod·project)</th><th>owner</th>"
            "<th class='num'>Apr</th><th class='num'>May</th><th class='num'>Δ MoM</th></tr></thead>"
            f"<tbody>{body or '<tr><td>—</td></tr>'}</tbody></table></div>")
KEY_GROWERS = ('<section style="border-left:4px solid #dc2626;"><h2 style="margin-top:4px">Key growers — top 10 per asset type</h2>'
 '<p class="hint" style="margin-top:0">Biggest Apr→May $ increases within each asset type — switch tabs by type.</p>'
 + _tabs("grow", _grow_pane) + '</section>')

def _analysis(t, a):
    apr, may = a["apr"], a["may"]
    if t == "Jobs":
        ra, rm, infer, cpu, trig = a.get("runs_apr",0), a.get("runs_may",0), a.get("infer",0), a.get("cpu"), a.get("trig")
        if apr < 5: return "warn", f"New workload this month ({rm} runs) — confirm it's intended."
        if infer and infer >= 0.4*may: return "bad", f"Growth is real-time model-serving (inference): ${infer:,.0f} of ${may:,.0f}. If this is bulk, switch to batch inference (far cheaper per token)."
        if ra > 0 and rm >= 1.3*ra: return "ok", f"More runs ({ra}→{rm}) — volume growth; fine if the workload genuinely increased."
        if ra > 0 and rm <= 1.2*ra and may > 1.3*apr: return "warn", f"Runs flat ({ra}→{rm}) but cost up — heavier per run (longer / bigger cluster); check for oversizing."
        if cpu is not None and cpu < 15 and trig == "CONTINUOUS": return "bad", f"Always-on stream at {cpu:.0f}% CPU — de-stream / trigger."
        return "warn", f"Mixed growth (runs {ra}→{rm}); review run size &amp; frequency."
    if t == "Clusters":
        cpu, nm = a.get("cpu"), a.get("lname","")
        if apr < 5: return "warn", "New / newly-ramped interactive cluster — confirm intended."
        if any(k in nm for k in ("pipeline","etl","workflow")): return "bad", "ETL/pipeline running on interactive compute — move to (cheaper) Jobs compute."
        if cpu is not None and cpu < 20: return "bad", f"Growth on a low-utilization cluster ({cpu:.0f}% CPU) — right-size / tighten auto-stop."
        if cpu is not None and cpu < 40: return "warn", f"Moderate utilization ({cpu:.0f}% CPU) — watch sizing as it grows."
        return "ok", "Interactive-cluster growth at healthy utilization — likely real usage."
    if t == "SQL warehouses":
        au = a.get("autostop")
        if apr < 5: return "warn", "New warehouse this month — confirm intended."
        if au is not None and au > 10: return "warn", f"More query volume; auto-stop is {au} min — tighten to cut idle."
        return "ok", "More serverless SQL volume (already auto-stops); tune heavy/recurring queries if it keeps rising."
    if t == "Serving":
        if apr < 5: return "warn", "New endpoint this month — confirm intended."
        if a.get("env") == "dev": return "warn", "Dev endpoint growth — enable scale-to-zero (idle between use)."
        return "warn", "More inference traffic / provisioned concurrency — right-size to real volume; batch bulk calls."
    if t == "Lakebase":
        if apr < 5: return "warn", "New OLTP instance — confirm purpose/owner."
        return "warn", "OLTP usage growing — confirm sizing; cull if it's an idle dev/branch DB."
    return "ok", ""

def _rec_pane(t):
    rows = sorted(ASSETS[t], key=lambda a: -a["delta"])[:10]
    body = ""
    for a in rows:
        sev, txt = _analysis(t, a)
        body += (f"<tr>{_tt(a['name'])}<td class='brk'>{a['tag']}</td>"
                 f"<td class='num'>{_m(a['may'])}</td>"
                 f"<td class='num {'up' if a['delta']>0 else ('down' if a['delta']<0 else '')}'>{a['mom']}</td>"
                 f"<td class='brk' style='color:{_SEV[sev]}'>{txt}</td></tr>")
    return ("<div class='scroll'><table><thead><tr><th>asset</th><th>tag (env·pod·project)</th>"
            "<th class='num'>$/mo</th><th class='num'>MoM</th><th>analysis — what grew &amp; what to do</th></tr></thead>"
            f"<tbody>{body or '<tr><td>—</td></tr>'}</tbody></table></div>")
RECS = ('<section style="border-left:4px solid #7c3aed;"><h2 style="margin-top:4px">Cost optimization recommendations</h2>'
 '<p class="hint" style="margin-top:0">Top 10 <b>growers</b> within each asset type (biggest Apr→May increase), with a quick read on <b>what drove the growth</b> and whether it needs action — just more volume, or a best-practice issue. Heuristic — use it to decide what to deep-dive. Switch tabs by type.</p>'
 + _tabs("rec", _rec_pane) + '</section>')

# ---- Under-provisioned: jobs/clusters running hot (>=70% CPU) ----
_under = []
for r in JOBS:
    cpu = JOBUTIL.get(r["jn"])
    if cpu is not None and cpu >= 70 and _f(r["may"]) >= 150:
        _under.append((cpu, _f(r["may"]), r["name"], "job", _dim(r["env"],r["pod"],r["proj"]), r["owner"]))
for r in CLUST:
    cpu = float(r["cpu"]) if r["cpu"] is not None else None
    if cpu is not None and cpu >= 70 and _f(r["may"]) >= 100:
        _under.append((cpu, _f(r["may"]), r["name"], "cluster", _dim(r["env"],r["pod"],r["proj"]), r["owner"]))
_under.sort(key=lambda x: -x[0])
_ub = "".join(
    f"<tr>{_tt(name)}<td class='nw'>{typ}</td><td class='brk'>{dim}</td>{_tt(_own(owner), owner)}"
    f"<td class='num'>{_m(may)}</td><td class='num up'>{cpu:.0f}%</td>"
    f"<td class='brk'>CPU-bound — add workers / larger node type</td></tr>"
    for cpu,may,name,typ,dim,owner in _under)
UNDERPROV = ('<section style="border-left:4px solid #0891b2;"><h2 style="margin-top:4px">Under-provisioned assets — running hot</h2>'
 '<p class="hint" style="margin-top:0">Jobs &amp; clusters sustaining <b>≥70% CPU</b> — likely throttled / slower than they should be; candidates for <b>more</b> resources (larger node, more workers), not less. Serverless types auto-scale and are excluded.</p>'
 '<div class="scroll"><table><thead><tr><th>asset</th><th>type</th><th>tag</th><th>owner</th><th class="num">$/mo</th><th class="num">CPU</th><th>note</th></tr></thead>'
 f'<tbody>{_ub or "<tr><td>No jobs/clusters sustained ≥70% CPU — compute is over-provisioned, not under.</td></tr>"}</tbody></table></div></section>')

# ---- Job compute split: where job-attributed $ runs (classic / serverless / all-purpose) ----
JC = _q(_PA + """
SELECT coalesce(lower(trim(u.custom_tags['env'])),'(none)') env,
  CASE WHEN u.billing_origin_product='ALL_PURPOSE' THEN 'allpurpose'
       WHEN u.sku_name ILIKE '%SERVERLESS%' THEN 'serverless'
       WHEN u.billing_origin_product='JOBS' THEN 'classic'
       ELSE 'other' END cat,
  round(sum(u.usage_quantity*p.up)) usd
FROM system.billing.usage u LEFT JOIN prices p ON u.sku_name=p.sku_name AND u.usage_unit=p.usage_unit AND u.usage_end_time>=p.price_start_time AND u.usage_end_time<p.pe
WHERE u.usage_date>='2026-05-01' AND u.usage_date<'2026-06-01' AND u.usage_metadata.job_id IS NOT NULL
GROUP BY 1,2""")
_jc = {}
for r in JC:
    _jc.setdefault(r["env"], {})[r["cat"]] = _f(r["usd"])
def _pct_of(x, tot): return f"{100*x/tot:.0f}%" if tot else "—"
_jc_rows = ""
for e in ["prod", "dev"]:
    d = _jc.get(e, {}); cl, sv, ap = d.get("classic",0), d.get("serverless",0), d.get("allpurpose",0)
    tot = cl + sv + ap + d.get("other",0)
    _jc_rows += (f"<tr><td>{e}</td>"
        f"<td class='num'>{_m(cl)} <span style='color:#6b7280'>({_pct_of(cl,tot)})</span></td>"
        f"<td class='num'>{_m(sv)} <span style='color:#6b7280'>({_pct_of(sv,tot)})</span></td>"
        f"<td class='num'>{_m(ap)} <span style='color:#6b7280'>({_pct_of(ap,tot)})</span></td>"
        f"<td class='num'>{_m(tot)}</td></tr>")
JCOMPUTE_HTML = ("<h3>Job level — where job spend runs (May)</h3>"
  "<div class='scroll'><table><thead><tr><th>env</th><th class='num'>Jobs compute (classic)</th><th class='num'>Jobs serverless</th><th class='num'>All-purpose</th><th class='num'>total job $</th></tr></thead>"
  f"<tbody>{_jc_rows}</tbody></table></div>"
  "<div class='hint'>How job-attributed spend splits across compute types. All-purpose ≈ $0 is good — no scheduled work on (3–4× pricier) interactive compute. Jobs-serverless has no node CPU metrics, so judge it by run frequency / duration, not utilization.</div>")

# ---- Next steps for serverless types (no CPU signal → config/usage-based actions) ----
NEXT_STEPS = (
 '<section style="border-left:4px solid #0d9488;"><h2 style="margin-top:4px">Next steps</h2>'
 '<p style="font-size:14px;margin:0">Deep dive into <b>serving endpoints</b>, <b>Lakebase</b>, and <b>serverless SQL</b> for optimization strategy.</p>'
 '</section>')

HTML = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Databricks Cost &amp; Efficiency Report — May 2026</title>
<style>
 :root{{--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--blue:#2563eb;--up:#dc2626;--down:#16a34a;--bg:#f9fafb;}}
 *{{box-sizing:border-box}} body{{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);margin:0;background:var(--bg)}}
 .wrap{{max-width:1040px;margin:0 auto;padding:32px 24px 64px}}
 h1{{font-size:26px;margin:0 0 2px}} .sub{{color:var(--muted);font-size:13px;margin-bottom:24px}}
 h2{{font-size:18px;margin:28px 0 12px}} h3{{font-size:13.5px;margin:18px 0 6px;color:#374151}}
 .cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:16px 0 8px}}
 .card{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px}}
 .card .label{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}}
 .card .value{{font-size:28px;font-weight:680;margin-top:6px}} .card .value.up{{color:var(--up)}}
 .card .note{{font-size:12px;color:var(--muted);margin-top:4px}}
 section{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:20px;margin:16px 0}}
 table{{width:100%;border-collapse:collapse;font-size:13px}} th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}} th{{white-space:nowrap;color:#374151}}
 table.hitlist{{min-width:1180px}} tbody tr:hover{{background:#f9fafb}}
 th.num,td.num{{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}} td.up{{color:var(--up)}} td.down{{color:var(--down)}} td.brk{{overflow-wrap:anywhere;word-break:break-word}} td.nw{{white-space:nowrap}}
 .up{{color:var(--up)}} .down{{color:var(--down)}}
 .tabs{{display:flex;gap:8px;border-bottom:2px solid var(--line)}} .tabbtn{{background:none;border:none;padding:10px 16px;font-size:14px;font-weight:600;color:var(--muted);cursor:pointer;border-bottom:3px solid transparent;margin-bottom:-2px}}
 .tabbtn.active{{color:var(--blue);border-bottom-color:var(--blue)}} .tab{{display:none}} .tab.active{{display:block}}
 .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:24px}} .hint{{color:var(--muted);font-size:12px;margin-top:8px}}
 .pill{{display:inline-block;font-size:11px;padding:2px 8px;border-radius:999px;background:#eff6ff;color:#1e40af;margin-left:6px}}
 .warn{{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:10px 12px;font-size:13px;color:#92400e;margin-top:10px}}
 pre{{background:#0f172a;color:#e2e8f0;border-radius:10px;padding:16px;overflow:auto;font-size:12px;line-height:1.5}}
 .scroll{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
 td.tt{{max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;border-bottom:1px dashed #94a3b8;cursor:help}} td.tt:hover{{color:var(--blue);background:#eff6ff}}
 @media(max-width:760px){{.cards,.grid2{{grid-template-columns:1fr}} td.tt{{max-width:120px}} .wrap{{padding:20px 12px}}}}
</style></head><body><div class="wrap">
 <h1>Databricks Cost &amp; Efficiency Report</h1>
 <div class="sub">Reporting month: <b>May 2026</b> · 6-month trend (Dec 2025–May 2026) · list-price estimate · generated 2026-06-11</div>
 <div class="cards">
  <div class="card"><div class="label">Total cost (May)</div><div class="value">$105,808</div><div class="note">all environments</div></div>
  <div class="card"><div class="label">Month-over-month</div><div class="value up">+11.1%</div><div class="note">Apr $95,205 → May $105,808</div></div>
  <div class="card"><div class="label">Attribution coverage</div><div class="value">46% <span style="font-size:14px;color:var(--muted);font-weight:500">pod</span></div><div class="note">x_project 94% · pod is the gap</div></div>
 </div>

 <section><h2 style="margin-top:4px">Cost breakdown by environment</h2>
  <div class="tabs">{tabs_btn}</div>
  {tabs_body}
 </section>

 {KEY_GROWERS}

 <section style="border-left:4px solid var(--blue)">
  <h2 style="margin-top:4px">Compute metrics</h2>
  <h3>Compute level — utilization &amp; idle, split by compute type (classic, May)</h3>
  <table><thead><tr><th>env</th><th>compute type</th><th class="num">CPU util (wtd)</th><th class="num">Mem util</th><th class="num">Idle (CPU&lt;20%)</th><th class="num">worker node-hrs</th></tr></thead>
  <tbody>
   <tr><td>prod</td><td><b>JOBS</b></td><td class="num up">24.7%</td><td class="num">35.2%</td><td class="num up">61.4%</td><td class="num">6,394</td></tr>
   <tr><td>prod</td><td>ALL_PURPOSE</td><td class="num up">28.1%</td><td class="num">53.6%</td><td class="num">46.8%</td><td class="num">3,423</td></tr>
   <tr><td>dev</td><td><b>JOBS</b></td><td class="num up">25.0%</td><td class="num">55.1%</td><td class="num up">62.2%</td><td class="num">8,147</td></tr>
   <tr><td>dev</td><td>ALL_PURPOSE</td><td class="num up">25.1%</td><td class="num">51.9%</td><td class="num up">60.3%</td><td class="num">1,678</td></tr>
  </tbody></table>
  <div class="warn"><b>JOB clusters are the under-utilized ones</b> — ~25% CPU and <b>~62% idle</b> in both envs, and they're the bulk of classic node-hours (dev 83%, prod 65%). All-purpose is smaller and, in prod, actually <b>less</b> idle (46.8%). Lever: right-size job clusters (fewer/smaller workers) and de-stream always-on non-prod jobs; pair with auto-termination on all-purpose.</div>
  <div class="hint"><b>Coverage:</b> utilization is measurable only for classic <b>JOBS</b> and <b>ALL_PURPOSE</b> clusters. <b>MODEL_SERVING</b> has clusters (~$13.6K of spend) but emits no <code>node_timeline</code> data — measure it via endpoint metrics instead. All other services (SQL, LAKEBASE, VECTOR_SEARCH, APPS, INTERACTIVE, DATABASE) are serverless — no clusters to measure.</div>
  {JCOMPUTE_HTML}
 </section>

 {UNDERPROV}

 <section><h2 style="margin-top:4px">How cost is calculated <span class="pill">disclaimer</span></h2>
  <p style="font-size:13.5px;margin:0 0 6px">Every dollar figure is <b>usage × list price</b>, summed. Caveats: <b>list price, not invoice</b> (public rates; your discounted bill is lower — use for relative comparison &amp; trends); the <b>Databricks/DBU portion</b> only (classic-compute Azure VM cost is billed separately; serverless bundles it); tags <b>case-normalized</b> with <code>lower(trim())</code>.</p>
  <p style="font-size:13.5px;margin:10px 0 6px">Exact query (change <code>GROUP BY</code>/filter per view):</p>
  <pre><code>WITH prices AS (
  SELECT sku_name, usage_unit, pricing.default AS unit_price,
         price_start_time,
         coalesce(price_end_time, timestamp(date_add(current_date,1))) AS price_end_time
  FROM system.billing.list_prices WHERE currency_code = 'USD'
)
SELECT
  coalesce(lower(trim(u.custom_tags['env'])),'(none)')        AS env,
  coalesce(lower(trim(u.custom_tags['pod'])),'(no pod)')      AS pod,
  coalesce(lower(trim(u.custom_tags['x_project'])),'(untagged)') AS x_project,
  u.billing_origin_product                                     AS service,
  round(sum(u.usage_quantity * p.unit_price), 2)               AS usd
FROM system.billing.usage u
LEFT JOIN prices p
  ON u.sku_name = p.sku_name AND u.usage_unit = p.usage_unit
 AND u.usage_end_time &gt;= p.price_start_time
 AND u.usage_end_time &lt;  p.price_end_time
WHERE u.usage_date &gt;= '2026-05-01' AND u.usage_date &lt; '2026-06-01'
GROUP BY 1,2,3,4 ORDER BY usd DESC;</code></pre>
  <p class="hint">Utilization joins <code>system.compute.node_timeline</code> on <code>cluster_id</code>; job metrics join <code>system.lakeflow.job_run_timeline</code> on <code>job_id</code>. Ref: <a href="https://docs.databricks.com/aws/en/lakehouse-architecture/cost-optimization/best-practices">Databricks cost-optimization best practices</a>.</p>
 </section>

 {RECS}

 {NEXT_STEPS}
</div>
<script>
 function tab(b){{
  var g=b.dataset.grp;
  document.querySelectorAll('.tabbtn[data-grp="'+g+'"]').forEach(function(x){{x.classList.remove('active')}});
  document.querySelectorAll('.tab[data-grp="'+g+'"]').forEach(function(x){{x.classList.remove('active')}});
  b.classList.add('active');
  document.getElementById(b.dataset.tgt).classList.add('active');
 }}
</script></body></html>"""

with open(OUTPUT, "w") as f:
    f.write(HTML)
print("Wrote", OUTPUT, "(", len(HTML), "bytes )")

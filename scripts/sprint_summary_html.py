"""Generate ONE self-contained sprint-summary HTML file (same data + structure as
the PDF: Team -> Squad -> Developer, all tables). Easy to open in a browser and
hand-edit afterwards. Read-only.

Usage:
    python scripts/sprint_summary_html.py [OUT_DIR] [SPRINT_NAME]
        OUT_DIR     where to write the .html (default: current dir)
        SPRINT_NAME sprint to summarise (default: the board's current sprint)
"""
import os
import sys
import html

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv

load_dotenv()

from youtrack_mcp.api.client import YouTrackClient
from youtrack_mcp.api.agiles import AgileBoardsClient
from youtrack_mcp.api.issues import IssuesClient
from youtrack_mcp.reporting.sprint_summary import (
    build_sprint_summary,
    ordered_stages,
)

BASE = (os.getenv("YOUTRACK_URL") or "").rstrip("/")
BOARD = os.getenv("YOUTRACK_DEFAULT_BOARD", "Dev Board")
OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else "."
SPRINT = sys.argv[2] if len(sys.argv) > 2 else None


def e(s):
    return html.escape("" if s is None else str(s))


def first_name(v):
    return v.split()[0] if v else v


CSS = """
:root{
  --dark:#1f2937; --slate:#374151; --zebra:#f3f4f6; --grid:#e5e7eb;
  --link:#1a56db; --amber-h:#92400e; --amber-bg:#fef3c7; --muted:#6b7280;
  --good:#047857; --bad:#b91c1c;
}
*{box-sizing:border-box}
body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  color:#111827; margin:0; padding:32px; background:#f9fafb; line-height:1.45}
.wrap{max-width:1080px; margin:0 auto}
h1{font-size:26px; margin:0 0 4px}
h2{font-size:19px; margin:34px 0 4px; color:var(--dark);
  border-bottom:2px solid var(--dark); padding-bottom:4px}
h3{font-size:14px; margin:18px 0 6px; color:var(--slate)}
.sub{color:var(--muted); font-size:13px; margin:0 0 6px}
.dev{font-size:14px; font-weight:700; color:var(--dark); margin:16px 0 4px}
.dev .meta{font-weight:400; color:var(--muted); font-size:12.5px}
table{border-collapse:collapse; width:100%; margin:6px 0 14px; font-size:13px}
th,td{border:1px solid var(--grid); padding:6px 9px; text-align:left;
  vertical-align:top}
thead th{background:var(--dark); color:#fff; font-weight:600}
tbody tr:nth-child(even){background:var(--zebra)}
td.num,th.num{text-align:right; white-space:nowrap}
tr.total td{background:#e5e7eb; font-weight:700}
a{color:var(--link); text-decoration:none}
a:hover{text-decoration:underline}
.cols{display:flex; gap:24px; flex-wrap:wrap; align-items:flex-start}
.cols>div{flex:1; min-width:300px}
.kpi td:first-child{color:var(--slate)}
.kpi td:last-child{text-align:right; font-weight:600; white-space:nowrap}
.unplanned thead th{background:var(--amber-h)}
.unplanned tbody tr:nth-child(even){background:var(--amber-bg)}
.delta-up{color:var(--good)} .delta-down{color:var(--bad)}
.squad-card{background:#fff; border:1px solid var(--grid); border-radius:10px;
  padding:18px 22px; margin:18px 0; box-shadow:0 1px 2px rgba(0,0,0,.04)}
.pill{display:inline-block; background:#eef2ff; color:#3730a3; border-radius:999px;
  padding:1px 9px; font-size:12px; margin-left:6px}
footer{color:var(--muted); font-size:12px; margin-top:30px; text-align:center}
"""


def kpi_table(block):
    rows = [
        ("Total tasks", block["total_tasks"]),
        ("Planned", block["planned_tasks"]),
        ("Unplanned (mid-sprint)", block["unplanned_tasks"]),
        ("Completed (Published)", block["completed_tasks"]),
        ("Completion rate", f"{block['completion_rate']}%"),
        ("Total story points", block["total_points"]),
        ("Completed points", block["completed_points"]),
        ("Unplanned points", block["unplanned_points"]),
    ]
    body = "".join(f"<tr><td>{e(k)}</td><td>{e(v)}</td></tr>" for k, v in rows)
    return f'<table class="kpi"><tbody>{body}</tbody></table>'


def snapshot_table(block):
    start, end = block["start_snapshot"], block["end_snapshot"]
    stages = ordered_stages(start, end)
    rows = ""
    for s in stages:
        a, b = start.get(s, 0), end.get(s, 0)
        d = b - a
        if d > 0:
            dtxt = f'<span class="delta-up">+{d}</span>'
        elif d < 0:
            dtxt = f'<span class="delta-down">{d}</span>'
        else:
            dtxt = "—"
        rows += (f"<tr><td>{e(s)}</td><td class='num'>{a}</td>"
                 f"<td class='num'>{b}</td><td class='num'>{dtxt}</td></tr>")
    return (
        "<table><thead><tr><th>Status</th><th class='num'>At start</th>"
        "<th class='num'>Now</th><th class='num'>Change</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def rollup_table(rollup):
    rows = ""
    for r in rollup:
        rows += (
            f"<tr><td><b>{e(r['squad'])}</b></td>"
            f"<td class='num'>{r['tasks']}</td>"
            f"<td class='num'>{r['points']}</td>"
            f"<td class='num'>{r['completed']}</td>"
            f"<td class='num'>{r['unplanned']}</td>"
            f"<td class='num'>{r['completion_rate']}%</td></tr>"
        )
    return (
        "<table><thead><tr><th>Squad</th><th class='num'>Tasks</th>"
        "<th class='num'>Points</th><th class='num'>Done</th>"
        "<th class='num'>Unplanned</th><th class='num'>Completion</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def unplanned_table(block, with_squad=False):
    items = block["unplanned_list"]
    if not items:
        return '<p class="sub">No unplanned tasks added mid-sprint.</p>'
    head = "<th>Task</th>" + ("<th>Squad</th>" if with_squad else "") + \
        "<th>Status</th><th class='num'>Points</th>"
    rows = ""
    for it in items:
        iid = it["id"]
        link = f'<a href="{BASE}/issue/{e(iid)}">{e(iid)}</a>'
        summ = e((it.get("summary") or "").strip()[:80])
        sq = f"<td>{e(it.get('squad',''))}</td>" if with_squad else ""
        rows += (f"<tr><td>{link} {summ}</td>{sq}"
                 f"<td>{e(it.get('stage_now',''))}</td>"
                 f"<td class='num'>{e(it.get('points',0))}</td></tr>")
    return (f'<table class="unplanned"><thead><tr>{head}</tr></thead>'
            f"<tbody>{rows}</tbody></table>")


def developer_block(name, d):
    a, r = d["assigned"], d["reviewing"]
    stages = ordered_stages(a["by_stage"], r["by_stage"])
    rows = ""
    for s in stages:
        av = a["by_stage"].get(s, {})
        rv = r["by_stage"].get(s, {})
        rows += (
            f"<tr><td>{e(s)}</td>"
            f"<td class='num'>{av.get('count','') or '—'}</td>"
            f"<td class='num'>{av.get('points',0)}</td>"
            f"<td class='num'>{rv.get('count','') or '—'}</td>"
            f"<td class='num'>{rv.get('points',0)}</td></tr>"
        )
    rows += (
        f"<tr class='total'><td>Total</td>"
        f"<td class='num'>{a['count']}</td><td class='num'>{a['points']}</td>"
        f"<td class='num'>{r['count']}</td><td class='num'>{r['points']}</td></tr>"
    )
    heading = (
        f'<div class="dev">{e(first_name(name) or name)} '
        f'<span class="meta">— assigned {a["count"]} ({a["points"]} pts), '
        f'reviewing {r["count"]} ({r["points"]} pts)</span></div>'
    )
    table = (
        "<table><thead><tr><th>Status</th><th class='num'>Assigned</th>"
        "<th class='num'>Asg pts</th><th class='num'>Reviewing</th>"
        "<th class='num'>Rev pts</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )
    return heading + table


def team_section(data):
    block = data["board_summary"]
    return f"""
<h2>Team summary — all squads</h2>
<p class="sub">{data['issue_count']} tasks (Epic excluded)</p>
<div class="cols">
  <div><h3>KPIs</h3>{kpi_table(block)}</div>
  <div><h3>Status at start vs now</h3>{snapshot_table(block)}</div>
</div>
<h3>Per-squad rollup</h3>
{rollup_table(block['squad_rollup'])}
<h3>Unplanned mid-sprint additions</h3>
{unplanned_table(block, with_squad=True)}
"""


def squad_section(name, block):
    devs = block["developers"]
    dev_html = "".join(
        developer_block(n, d)
        for n, d in sorted(
            devs.items(),
            key=lambda kv: -(kv[1]["assigned"]["count"] + kv[1]["reviewing"]["count"]),
        )
    ) or '<p class="sub">No assignees or reviewers on this squad.</p>'

    unplanned = ""
    if block["unplanned_list"]:
        unplanned = f"<h3>Unplanned additions</h3>{unplanned_table(block)}"

    return f"""
<div class="squad-card">
  <h2 style="margin-top:0">{e(name)}
    <span class="pill">{block['total_tasks']} tasks</span>
    <span class="pill">{block['total_points']} pts</span>
    <span class="pill">{block['completed_tasks']} done</span>
    <span class="pill">{block['unplanned_tasks']} unplanned</span>
  </h2>
  <div class="cols">
    <div><h3>KPIs</h3>{kpi_table(block)}</div>
    <div><h3>Status at start vs now</h3>{snapshot_table(block)}</div>
  </div>
  {unplanned}
  <h3>Developers</h3>
  {dev_html}
</div>
"""


def main():
    client = YouTrackClient()
    agile = AgileBoardsClient(client)
    issues = IssuesClient(client)

    print(f"Building sprint summary for board '{BOARD}' "
          f"({SPRINT or 'current sprint'})...")
    data = build_sprint_summary(agile, issues, BOARD, SPRINT)

    sprint_name = data["sprint"]
    safe_name = (sprint_name or "sprint").replace(" ", "-").replace("#", "")
    path = os.path.join(OUT_DIR, f"sprint-summary-{safe_name}.html")
    os.makedirs(OUT_DIR, exist_ok=True)

    squads_html = "".join(
        squad_section(sq, data["squads"][sq]) for sq in sorted(data["squads"].keys())
    )

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sprint Summary — {e(sprint_name)}</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<h1>Sprint Summary — {e(sprint_name)}</h1>
<p class="sub">{e(data['board'])} &middot; {e(data['start'])} → {e(data['finish'])}</p>
{team_section(data)}
<h2>Squads</h2>
{squads_html}
<footer>Generated by Jason-MCP · read-only sprint summary</footer>
</div></body></html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)

    b = data["board_summary"]
    print(f"  sprint: {sprint_name} ({data['start']} -> {data['finish']})")
    print(f"  tasks: {b['total_tasks']} (planned {b['planned_tasks']}, "
          f"unplanned {b['unplanned_tasks']}); points {b['total_points']} "
          f"(done {b['completed_points']})")
    print(f"  squads: {', '.join(sorted(data['squads'].keys()))}")
    print(f"  -> {path}")


if __name__ == "__main__":
    main()

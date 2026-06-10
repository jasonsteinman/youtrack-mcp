"""Generate ONE combined sprint-summary PDF, all tables (stuck-tasks style):

  1. TEAM    — KPIs, status at start vs now, a per-squad rollup, unplanned additions.
  2. SQUAD   — one page each: KPIs, status at start vs now, then a per-DEVELOPER
               breakdown: tasks assigned to them and where they are, plus tasks /
               story points they're the reviewer of and where they are.

Epic is excluded. Read-only.

Usage:
    python scripts/sprint_summary_pdf.py [OUT_DIR] [SPRINT_NAME]
        OUT_DIR     where to write the PDF (default: current dir)
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

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    PageBreak,
    KeepTogether,
)

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

DARK = colors.HexColor("#1f2937")
SLATE = colors.HexColor("#374151")
AMBER_HEAD = colors.HexColor("#92400e")
AMBER_BG = colors.HexColor("#fef3c7")
ZEBRA = colors.HexColor("#f3f4f6")
GRID = colors.HexColor("#d1d5db")
LINKBLUE = colors.HexColor("#1a56db")


def safe(s):
    s = html.escape(str(s) if s is not None else "")
    return s.encode("latin-1", "ignore").decode("latin-1")


def first_name(v):
    return v.split()[0] if v else v


def make_styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontSize=20),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=15,
                             textColor=DARK, spaceBefore=6, spaceAfter=2),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontSize=11,
                             textColor=SLATE, spaceBefore=6, spaceAfter=2),
        "dev": ParagraphStyle("dev", parent=base["Normal"], fontSize=10,
                             textColor=DARK, fontName="Helvetica-Bold",
                             spaceBefore=6, spaceAfter=1),
        "sub": ParagraphStyle("sub", parent=base["Normal"], fontSize=9,
                             textColor=colors.grey),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontSize=8.5, leading=11),
        "cellb": ParagraphStyle("cellb", parent=base["Normal"], fontSize=8.5,
                               leading=11, fontName="Helvetica-Bold"),
        "cellr": ParagraphStyle("cellr", parent=base["Normal"], fontSize=8.5,
                               leading=11, alignment=2),
        "head": ParagraphStyle("head", parent=base["Normal"], fontSize=8.5,
                              textColor=colors.white, fontName="Helvetica-Bold"),
        "headr": ParagraphStyle("headr", parent=base["Normal"], fontSize=8.5,
                               textColor=colors.white, fontName="Helvetica-Bold",
                               alignment=2),
        "link": ParagraphStyle("link", parent=base["Normal"], fontSize=8.5, leading=11,
                              textColor=LINKBLUE),
    }


def _grid(t, header_bg=DARK, zebra=ZEBRA, zebra_from=AMBER_BG, valign="MIDDLE"):
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, zebra]),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("VALIGN", (0, 0), (-1, -1), valign),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _hrow(headers, st, right_from=1):
    return [
        Paragraph(f"{safe(h)}", st["head"] if idx < right_from else st["headr"])
        for idx, h in enumerate(headers)
    ]


# --------------------------------------------------------------------------- #
# Tables                                                                       #
# --------------------------------------------------------------------------- #
def kpi_table(block, st):
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
    data = [[Paragraph(safe(k), st["cell"]), Paragraph(safe(v), st["cellr"])]
            for k, v in rows]
    t = Table(data, colWidths=[55 * mm, 25 * mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, ZEBRA]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def snapshot_table(block, st):
    start, end = block["start_snapshot"], block["end_snapshot"]
    stages = ordered_stages(start, end)
    data = [_hrow(["Status", "At start", "Now", "Change"], st)]
    for s in stages:
        a, b = start.get(s, 0), end.get(s, 0)
        d = b - a
        dtxt = f"+{d}" if d > 0 else (str(d) if d < 0 else "—")
        data.append([
            Paragraph(safe(s), st["cell"]),
            Paragraph(str(a), st["cellr"]),
            Paragraph(str(b), st["cellr"]),
            Paragraph(dtxt, st["cellr"]),
        ])
    return _grid(Table(data, colWidths=[60 * mm, 25 * mm, 25 * mm, 25 * mm],
                       repeatRows=1))


def rollup_table(rollup, st):
    data = [_hrow(["Squad", "Tasks", "Points", "Done", "Unplanned", "Completion"], st)]
    for r in rollup:
        data.append([
            Paragraph(safe(r["squad"]), st["cellb"]),
            Paragraph(str(r["tasks"]), st["cellr"]),
            Paragraph(str(r["points"]), st["cellr"]),
            Paragraph(str(r["completed"]), st["cellr"]),
            Paragraph(str(r["unplanned"]), st["cellr"]),
            Paragraph(f"{r['completion_rate']}%", st["cellr"]),
        ])
    return _grid(Table(
        data, colWidths=[45 * mm, 22 * mm, 22 * mm, 20 * mm, 26 * mm, 27 * mm],
        repeatRows=1))


def unplanned_table(block, st, with_squad=False):
    items = block["unplanned_list"]
    if not items:
        return Paragraph("No unplanned tasks added mid-sprint.", st["sub"])
    headers = ["Task"] + (["Squad"] if with_squad else []) + ["Status", "Points"]
    data = [_hrow(headers, st, right_from=len(headers) - 2)]
    for it in items:
        iid = it["id"]
        url = f"{BASE}/issue/{iid}"
        row = [Paragraph(
            f'<a href="{url}"><b>{safe(iid)}</b></a> '
            f'{safe((it.get("summary") or "").strip()[:64])}', st["link"])]
        if with_squad:
            row.append(Paragraph(safe(it.get("squad", "")), st["cell"]))
        row.append(Paragraph(safe(it.get("stage_now", "")), st["cell"]))
        row.append(Paragraph(safe(it.get("points", 0)), st["cellr"]))
        data.append(row)
    widths = ([95 * mm, 28 * mm, 25 * mm, 17 * mm] if with_squad
              else [120 * mm, 25 * mm, 17 * mm])
    t = Table(data, colWidths=widths, repeatRows=1)
    return _grid(t, header_bg=AMBER_HEAD, zebra=AMBER_BG, valign="TOP")


def developer_table(name, block_dev, st):
    """One developer: stage rows with assigned/review counts + points, total row."""
    assigned = block_dev["assigned"]
    reviewing = block_dev["reviewing"]
    stages = ordered_stages(assigned["by_stage"], reviewing["by_stage"])

    heading = Paragraph(
        f"{safe(first_name(name) or name)} &nbsp;—&nbsp; "
        f"assigned <b>{assigned['count']}</b> ({assigned['points']} pts), "
        f"reviewing <b>{reviewing['count']}</b> ({reviewing['points']} pts)",
        st["dev"],
    )

    data = [_hrow(
        ["Status", "Assigned", "Asg pts", "Reviewing", "Rev pts"], st)]
    for s in stages:
        a = assigned["by_stage"].get(s, {})
        r = reviewing["by_stage"].get(s, {})
        data.append([
            Paragraph(safe(s), st["cell"]),
            Paragraph(str(a.get("count", 0)) or "—", st["cellr"]),
            Paragraph(str(a.get("points", 0)), st["cellr"]),
            Paragraph(str(r.get("count", 0)) or "—", st["cellr"]),
            Paragraph(str(r.get("points", 0)), st["cellr"]),
        ])
    data.append([
        Paragraph("<b>Total</b>", st["cellb"]),
        Paragraph(f"<b>{assigned['count']}</b>", st["cellr"]),
        Paragraph(f"<b>{assigned['points']}</b>", st["cellr"]),
        Paragraph(f"<b>{reviewing['count']}</b>", st["cellr"]),
        Paragraph(f"<b>{reviewing['points']}</b>", st["cellr"]),
    ])
    t = Table(data, colWidths=[55 * mm, 25 * mm, 22 * mm, 25 * mm, 22 * mm],
              repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), SLATE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, ZEBRA]),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e5e7eb")),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return KeepTogether([heading, t, Spacer(1, 2)])


# --------------------------------------------------------------------------- #
# Sections                                                                     #
# --------------------------------------------------------------------------- #
def team_section(data, st):
    block = data["board_summary"]
    elems = [Paragraph("Team summary — all squads", st["h2"]),
             Paragraph(f"{data['issue_count']} tasks (Epic excluded)", st["sub"]),
             Spacer(1, 6)]
    elems.append(kpi_table(block, st))
    elems.append(Spacer(1, 8))
    elems.append(Paragraph("Status at start vs now", st["h3"]))
    elems.append(snapshot_table(block, st))
    elems.append(Spacer(1, 8))
    elems.append(Paragraph("Per-squad rollup", st["h3"]))
    elems.append(rollup_table(block["squad_rollup"], st))
    elems.append(Spacer(1, 8))
    elems.append(Paragraph("Unplanned mid-sprint additions", st["h3"]))
    elems.append(unplanned_table(block, st, with_squad=True))
    return elems


def squad_section(name, block, st):
    elems = [Paragraph(f"{safe(name)}", st["h2"]),
             Paragraph(
                 f"{block['total_tasks']} tasks &middot; {block['total_points']} pts "
                 f"&middot; {block['completed_tasks']} done "
                 f"&middot; {block['unplanned_tasks']} unplanned", st["sub"]),
             Spacer(1, 6)]
    # squad KPIs beside the status snapshot
    side = Table([[kpi_table(block, st), snapshot_table(block, st)]],
                 colWidths=[85 * mm, 90 * mm])
    side.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elems.append(side)
    elems.append(Spacer(1, 6))

    if block["unplanned_list"]:
        elems.append(Paragraph("Unplanned additions", st["h3"]))
        elems.append(unplanned_table(block, st))
        elems.append(Spacer(1, 6))

    elems.append(Paragraph("Developers", st["h3"]))
    devs = block["developers"]
    # sort by total involvement (assigned + reviewing count), desc
    for name_, d in sorted(
        devs.items(),
        key=lambda kv: -(kv[1]["assigned"]["count"] + kv[1]["reviewing"]["count"]),
    ):
        elems.append(developer_table(name_, d, st))
    if not devs:
        elems.append(Paragraph("No assignees or reviewers on this squad.", st["sub"]))
    return elems


def main():
    client = YouTrackClient()
    agile = AgileBoardsClient(client)
    issues = IssuesClient(client)

    print(f"Building sprint summary for board '{BOARD}' "
          f"({SPRINT or 'current sprint'})...")
    data = build_sprint_summary(agile, issues, BOARD, SPRINT)

    sprint_name = data["sprint"]
    safe_name = (sprint_name or "sprint").replace(" ", "-").replace("#", "")
    path = os.path.join(OUT_DIR, f"sprint-summary-{safe_name}.pdf")
    os.makedirs(OUT_DIR, exist_ok=True)

    st = make_styles()
    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
    )
    elems = [
        Paragraph(f"Sprint Summary — {safe(sprint_name)}", st["title"]),
        Paragraph(
            f"{safe(data['board'])} &middot; {safe(data['start'])} → "
            f"{safe(data['finish'])}", st["sub"]),
        Spacer(1, 10),
    ]
    elems += team_section(data, st)

    for sq in sorted(data["squads"].keys()):
        elems.append(PageBreak())
        elems += squad_section(sq, data["squads"][sq], st)

    doc.build(elems)

    b = data["board_summary"]
    print(f"  sprint: {sprint_name} ({data['start']} -> {data['finish']})")
    print(f"  tasks: {b['total_tasks']} (planned {b['planned_tasks']}, "
          f"unplanned {b['unplanned_tasks']}); points {b['total_points']} "
          f"(done {b['completed_points']})")
    print(f"  squads: {', '.join(sorted(data['squads'].keys()))}")
    print(f"  -> {path}")


if __name__ == "__main__":
    main()

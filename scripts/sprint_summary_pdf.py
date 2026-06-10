"""Generate ONE combined sprint-summary PDF: a board overview (KPIs + a grouped
status-at-start vs status-at-end bar chart + planned-vs-unplanned), followed by a
section per squad. Charts via matplotlib, layout via reportlab. Read-only.

Usage:
    python scripts/sprint_summary_pdf.py [OUT_DIR] [SPRINT_NAME]
        OUT_DIR     where to write the PDF (default: current dir)
        SPRINT_NAME sprint to summarise (default: the board's current sprint)
"""
import os
import sys
import html
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv

load_dotenv()

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

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
    Image,
    PageBreak,
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

START_COLOR = "#9ca3af"  # grey  — status at start
END_COLOR = "#2563eb"  # blue  — status now
PLANNED_COLOR = "#10b981"  # green
UNPLANNED_COLOR = "#f59e0b"  # amber


def safe(s):
    s = html.escape(str(s) if s is not None else "")
    return s.encode("latin-1", "ignore").decode("latin-1")


# --------------------------------------------------------------------------- #
# Charts -> reportlab Image                                                    #
# --------------------------------------------------------------------------- #
def _fig_to_image(fig, width_mm):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    w = width_mm * mm
    iw, ih = fig.get_size_inches()
    return Image(buf, width=w, height=w * (ih / iw))


def start_end_chart(summary_block, width_mm=170):
    start = summary_block["start_snapshot"]
    end = summary_block["end_snapshot"]
    stages = ordered_stages(start, end)
    if not stages:
        return None
    xs = range(len(stages))
    sv = [start.get(s, 0) for s in stages]
    ev = [end.get(s, 0) for s in stages]

    fig, ax = plt.subplots(figsize=(9, 3.6))
    w = 0.4
    b1 = ax.bar([x - w / 2 for x in xs], sv, w, label="At start", color=START_COLOR)
    b2 = ax.bar([x + w / 2 for x in xs], ev, w, label="Now", color=END_COLOR)
    ax.set_xticks(list(xs))
    ax.set_xticklabels(stages, rotation=25, ha="right", fontsize=8)
    ax.set_ylabel("Tasks")
    ax.set_title("Status at sprint start vs now", fontsize=11, fontweight="bold")
    ax.legend(frameon=False, fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for bars in (b1, b2):
        for r in bars:
            h = r.get_height()
            if h:
                ax.annotate(
                    f"{int(h)}",
                    (r.get_x() + r.get_width() / 2, h),
                    ha="center",
                    va="bottom",
                    fontsize=7,
                )
    return _fig_to_image(fig, width_mm)


def planned_unplanned_chart(summary_block, width_mm=80):
    planned = summary_block["planned_tasks"]
    unplanned = summary_block["unplanned_tasks"]
    if planned + unplanned == 0:
        return None
    fig, ax = plt.subplots(figsize=(3.6, 3.2))
    ax.pie(
        [planned, unplanned],
        labels=[f"Planned\n{planned}", f"Unplanned\n{unplanned}"],
        colors=[PLANNED_COLOR, UNPLANNED_COLOR],
        autopct=lambda p: f"{p:.0f}%" if p > 0 else "",
        startangle=90,
        textprops={"fontsize": 8},
    )
    ax.set_title("Planned vs unplanned", fontsize=11, fontweight="bold")
    return _fig_to_image(fig, width_mm)


# --------------------------------------------------------------------------- #
# Tables                                                                       #
# --------------------------------------------------------------------------- #
def kpi_table(block, styles):
    rows = [
        ["Total tasks", block["total_tasks"]],
        ["Planned", block["planned_tasks"]],
        ["Unplanned (mid-sprint)", block["unplanned_tasks"]],
        ["Completed (Published)", block["completed_tasks"]],
        ["Completion rate", f"{block['completion_rate']}%"],
        ["Total story points", block["total_points"]],
        ["Completed points", block["completed_points"]],
        ["Unplanned points", block["unplanned_points"]],
    ]
    data = [[Paragraph(f"<b>{safe(k)}</b>", styles["cell"]),
             Paragraph(safe(v), styles["cellr"])] for k, v in rows]
    t = Table(data, colWidths=[55 * mm, 25 * mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def snapshot_table(block, styles):
    start = block["start_snapshot"]
    end = block["end_snapshot"]
    stages = ordered_stages(start, end)
    head = [Paragraph(f"<b>{safe(h)}</b>", styles["head"])
            for h in ["Status", "At start", "Now", "Δ"]]
    data = [head]
    for s in stages:
        a, b = start.get(s, 0), end.get(s, 0)
        d = b - a
        dtxt = f"+{d}" if d > 0 else (str(d) if d < 0 else "0")
        data.append([
            Paragraph(safe(s), styles["cell"]),
            Paragraph(str(a), styles["cellr"]),
            Paragraph(str(b), styles["cellr"]),
            Paragraph(dtxt, styles["cellr"]),
        ])
    t = Table(data, colWidths=[55 * mm, 25 * mm, 25 * mm, 20 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def unplanned_table(block, styles):
    items = block["unplanned_list"]
    if not items:
        return Paragraph("No unplanned tasks added mid-sprint.", styles["sub"])
    head = [Paragraph(f"<b>{safe(h)}</b>", styles["head"])
            for h in ["Task", "Points"]]
    data = [head]
    for it in items:
        iid = it["id"]
        url = f"{BASE}/issue/{iid}"
        task = Paragraph(
            f'<a href="{url}"><b>{safe(iid)}</b></a> '
            f'{safe((it.get("summary") or "").strip()[:70])}',
            styles["link"],
        )
        data.append([task, Paragraph(safe(it.get("points", 0)), styles["cellr"])])
    t = Table(data, colWidths=[150 * mm, 20 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#92400e")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fef3c7")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


# --------------------------------------------------------------------------- #
# Build                                                                        #
# --------------------------------------------------------------------------- #
def make_styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontSize=20),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=14,
                             textColor=colors.HexColor("#1f2937"), spaceBefore=8),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontSize=11,
                             textColor=colors.HexColor("#374151")),
        "sub": ParagraphStyle("sub", parent=base["Normal"], fontSize=9,
                             textColor=colors.grey),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontSize=8.5, leading=11),
        "cellr": ParagraphStyle("cellr", parent=base["Normal"], fontSize=8.5,
                               leading=11, alignment=2),
        "head": ParagraphStyle("head", parent=base["Normal"], fontSize=8.5,
                              textColor=colors.white, fontName="Helvetica-Bold"),
        "link": ParagraphStyle("link", parent=base["Normal"], fontSize=8.5, leading=11,
                              textColor=colors.HexColor("#1a56db")),
    }


def section(block, styles, is_board=False):
    elems = []
    title = "Board overview — all squads" if is_board else block["label"]
    elems.append(Paragraph(safe(title), styles["h2"]))
    elems.append(Spacer(1, 4))

    # KPI table beside a planned/unplanned pie
    kt = kpi_table(block, styles)
    pie = planned_unplanned_chart(block, width_mm=78)
    if pie is not None:
        side = Table([[kt, pie]], colWidths=[85 * mm, 85 * mm])
        side.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        elems.append(side)
    else:
        elems.append(kt)
    elems.append(Spacer(1, 8))

    # status-at-start vs now: chart + table
    chart = start_end_chart(block, width_mm=170)
    if chart is not None:
        elems.append(chart)
        elems.append(Spacer(1, 4))
    elems.append(Paragraph("Status at start vs now", styles["h3"]))
    elems.append(snapshot_table(block, styles))
    elems.append(Spacer(1, 8))

    elems.append(Paragraph("Unplanned mid-sprint additions", styles["h3"]))
    elems.append(unplanned_table(block, styles))
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

    styles = make_styles()
    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
    )
    elems = [
        Paragraph(f"Sprint Summary — {safe(sprint_name)}", styles["title"]),
        Paragraph(
            f"{safe(data['board'])} &middot; {safe(data['start'])} → {safe(data['finish'])} "
            f"&middot; {data['issue_count']} tasks",
            styles["sub"],
        ),
        Spacer(1, 10),
    ]
    elems += section(data["board_summary"], styles, is_board=True)

    for sq in sorted(data["squads"].keys()):
        elems.append(PageBreak())
        elems += section(data["squads"][sq], styles)

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

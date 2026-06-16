"""Generate one landscape PDF per squad listing stuck tasks (>48h in current Stage),
with the task ID hyperlinked to the YouTrack issue. Read-only."""
import os, sys, time, html
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from youtrack_mcp.api.client import YouTrackClient
from youtrack_mcp.api.issues import IssuesClient
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

BASE = (os.getenv("YOUTRACK_URL") or "").rstrip("/")
BOARD = os.getenv("YOUTRACK_DEFAULT_BOARD", "Dev Board")
SQUADS = ["Squad B", "Squad C", "Squad D"]
EXCLUDED_STAGES = {"Published", "Backlog"}
THRESHOLD_HOURS = 48
OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else "."

c = YouTrackClient()
ic = IssuesClient(c)


def cf(issue, name):
    for x in issue.get("customFields", []):
        if x["name"] == name:
            v = x.get("value")
            if isinstance(v, dict):
                return v.get("name") or v.get("fullName") or v.get("login")
            if isinstance(v, list):
                return (v[0].get("fullName") or v[0].get("login") or v[0].get("name")) if v else None
            return v
    return None


def safe(s):
    """XML-escape + drop characters the base PDF fonts can't render."""
    s = html.escape(s or "")
    return s.encode("latin-1", "ignore").decode("latin-1")


def first_name(v):
    return v.split()[0] if v else "-"


def comments(iid):
    cm = c.get(f"issues/{iid}/comments?fields=text,author(fullName,login)")
    out = []
    for x in (cm or [])[-3:]:
        who = (x.get("author") or {}).get("fullName") or (x.get("author") or {}).get("login") or "?"
        txt = " ".join((x.get("text") or "").split())[:140]
        if txt:
            out.append(f"<b>{safe(who.split()[0])}:</b> {safe(txt)}")
    return "<br/>".join(out) or "-"


# ---- gather ----
query = "Board " + BOARD + ": {current sprint} Squad: {Squad B}, {Squad C}, {Squad D}"
fields = "idReadable,summary,created,customFields(name,value(name,fullName,login))"
rows = c.get("issues", params={"query": query, "fields": fields, "$top": 200})
eligible = [i for i in rows if cf(i, "Stage") not in EXCLUDED_STAGES]

now = time.time()
by_squad = {s: [] for s in SQUADS}
for i in eligible:
    acts = ic.get_issue_activities(i["idReadable"])
    sc = [a for a in acts if (a.get("field") or {}).get("name") == "Stage" and a.get("timestamp")]
    ts = (sc[-1]["timestamp"] if sc else i.get("created")) / 1000.0
    hrs = (now - ts) / 3600
    if hrs > THRESHOLD_HOURS:
        sq = cf(i, "Squad")
        if sq in by_squad:
            by_squad[sq].append((i, hrs))

styles = getSampleStyleSheet()
cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7.5, leading=9)
link = ParagraphStyle("link", parent=cell, textColor=colors.HexColor("#1a56db"))
head = ParagraphStyle("head", parent=styles["Normal"], fontSize=8, leading=10,
                      textColor=colors.white, fontName="Helvetica-Bold")
title = ParagraphStyle("title", parent=styles["Title"], fontSize=16)
sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=9, textColor=colors.grey)

COLS = [125, 62, 62, 70, 42, 38, 370]  # points; sums ~ 769 (A4 landscape usable)
HEADERS = ["Task", "Dev", "Reviewer", "Status", "In status", "Pri", "Last comments"]


def build_pdf(squad, items):
    items.sort(key=lambda t: -t[1])
    safe_name = squad.replace(" ", "-")
    path = os.path.join(OUT_DIR, f"stuck-tasks-{safe_name}.pdf")
    doc = SimpleDocTemplate(path, pagesize=landscape(A4),
                            leftMargin=20, rightMargin=20, topMargin=22, bottomMargin=22)
    elems = [Paragraph(f"Stuck Tasks — {safe(squad)}", title),
             Paragraph(f"{BOARD} · current sprint · &gt;48h in current status · {len(items)} tasks", sub),
             Spacer(1, 8)]

    data = [[Paragraph(f"<b>{h}</b>", head) for h in HEADERS]]
    for i, hrs in items:
        iid = i["idReadable"]
        url = f"{BASE}/issue/{iid}"
        task = Paragraph(
            f'<a href="{url}"><b>{safe(iid)}</b></a><br/>{safe((i.get("summary") or "").strip()[:60])}', link)
        data.append([
            task,
            Paragraph(safe(first_name(cf(i, "Assignee"))), cell),
            Paragraph(safe(first_name(cf(i, "Reviewer"))), cell),
            Paragraph(safe(cf(i, "Stage") or "-"), cell),
            Paragraph(f"{hrs/24:.1f}d", cell),
            Paragraph(safe((cf(i, "Priority") or "-")[:4]), cell),
            Paragraph(comments(iid), cell),
        ])

    t = Table(data, colWidths=COLS, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    elems.append(t)
    doc.build(elems)
    return path, len(items)


print(f"scanned {len(eligible)} eligible (of {len(rows)} in B/C/D)")
for s in SQUADS:
    p, n = build_pdf(s, by_squad[s])
    print(f"  {s}: {n} stuck -> {p}")

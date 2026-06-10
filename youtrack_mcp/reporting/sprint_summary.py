"""Core compute for sprint summaries.

Reconstructs, for one sprint, the status of every task **at the sprint start** vs
**right now (sprint end)**, flags **unplanned** mid-sprint additions (count + story
points), and aggregates the result per squad and for the whole board.

The reconstruction lives in PURE functions (`stage_at`, `sprint_entry_ts`,
`is_unplanned`, `aggregate`) that take plain activity lists, so they can be unit
tested with hand-built fixtures. Only `build_sprint_summary()` touches the network.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

UNKNOWN_STAGE = "(unknown)"
UNKNOWN_SQUAD = "(no squad)"

# Canonical board column order (Dev Board). Both the correct and the board's own
# typo spellings ("Rady to ...") are listed so whichever exists sorts correctly.
STAGE_ORDER = [
    "Backlog",
    "In Progress",
    "OnHold",
    "Review",
    "Code Review",
    "Ready to Staging",
    "Rady to Staging",
    "Test",
    "Ready to Publish",
    "Rady to Publish",
    "Published",
]

# Stages that count a task as "done" for KPI purposes.
DONE_STAGES = {"published"}


# --------------------------------------------------------------------------- #
# Pure helpers (unit-testable; no network)                                     #
# --------------------------------------------------------------------------- #
def _names(items: Any) -> List[str]:
    """Pull display names out of an activity `added`/`removed` list."""
    out: List[str] = []
    for x in items or []:
        if isinstance(x, dict):
            n = (
                x.get("name")
                or x.get("fullName")
                or x.get("login")
                or x.get("text")
            )
            if n:
                out.append(n)
        elif x:
            out.append(str(x))
    return out


def _is_stage_activity(act: Dict[str, Any]) -> bool:
    field = act.get("field") or {}
    if (field.get("name") or "").lower() == "stage":
        return True
    cf = field.get("customField") or {}
    return (cf.get("name") or "").lower() == "stage"


def stage_changes(activities: List[Dict[str, Any]]) -> List[tuple]:
    """Return Stage-change events as ``[(ts, old, new), ...]`` sorted ascending."""
    changes = []
    for a in activities or []:
        if not _is_stage_activity(a):
            continue
        ts = a.get("timestamp")
        if ts is None:
            continue
        old = next(iter(_names(a.get("removed"))), None)
        new = next(iter(_names(a.get("added"))), None)
        changes.append((ts, old, new))
    changes.sort(key=lambda c: c[0])
    return changes


def stage_at(
    activities: List[Dict[str, Any]],
    at_ts: Optional[int],
    current_stage: Optional[str] = None,
) -> Optional[str]:
    """Reconstruct the Stage value as of ``at_ts`` (epoch millis).

    - Take the `new` value of the last Stage change with ts <= at_ts.
    - If ``at_ts`` predates every change, the value then was the `old` value of the
      first change.
    - If the issue never changed Stage, fall back to ``current_stage``.
    """
    if at_ts is None:
        return current_stage
    changes = stage_changes(activities)
    if not changes:
        return current_stage
    before = [c for c in changes if c[0] <= at_ts]
    if before:
        return before[-1][2]
    return changes[0][1]


def sprint_entry_ts(
    activities: List[Dict[str, Any]],
    sprint_name: str,
    fallback: Optional[int] = None,
) -> Optional[int]:
    """Most recent timestamp the issue was *added* to ``sprint_name``.

    Scans SprintCategory events whose `added` includes the sprint name and returns
    the latest. If there are none (the issue was created straight into the sprint, or
    its activity log doesn't cover the add), returns ``fallback`` (issue.created).
    """
    target = (sprint_name or "").strip().lower()
    best: Optional[int] = None
    for a in activities or []:
        atype = a.get("$type") or ""
        if atype and atype != "SprintCategory":
            continue
        added = [n.strip().lower() for n in _names(a.get("added"))]
        if target and target in added:
            ts = a.get("timestamp")
            if ts is not None and (best is None or ts > best):
                best = ts
    return best if best is not None else fallback


def is_unplanned(
    entry_ts: Optional[int],
    sprint_start: Optional[int],
    grace_ms: int = 0,
) -> bool:
    """True if the issue entered the sprint AFTER it started (mid-sprint addition)."""
    if entry_ts is None or sprint_start is None:
        return False
    return entry_ts > sprint_start + grace_ms


def ordered_stages(*snapshots: Dict[str, int]) -> List[str]:
    """Union of stage keys across snapshots, in canonical board order, extras last.

    Matching is case-insensitive; the actual (as-seen) stage spelling is returned.
    """
    seen: Dict[str, str] = {}  # lower-cased -> actual spelling
    for snap in snapshots:
        for k in (snap or {}).keys():
            seen.setdefault((k or "").strip().lower(), k)
    order_lower = [s.strip().lower() for s in STAGE_ORDER]
    ordered = [seen[o] for o in order_lower if o in seen]
    extras = sorted(
        actual for low, actual in seen.items() if low not in set(order_lower)
    )
    return ordered + extras


def aggregate(items: List[Dict[str, Any]], label: str) -> Dict[str, Any]:
    """Roll a list of per-issue records up into one squad/board summary block."""
    planned = [i for i in items if not i.get("unplanned")]
    unplanned = [i for i in items if i.get("unplanned")]

    start_counts: Dict[str, int] = {}
    for i in planned:
        s = i.get("stage_at_start") or UNKNOWN_STAGE
        start_counts[s] = start_counts.get(s, 0) + 1

    end_counts: Dict[str, int] = {}
    for i in items:
        s = i.get("stage_now") or UNKNOWN_STAGE
        end_counts[s] = end_counts.get(s, 0) + 1

    done = [i for i in items if (i.get("stage_now") or "").lower() in DONE_STAGES]
    total_points = sum(i.get("story_points", 0) for i in items)
    done_points = sum(i.get("story_points", 0) for i in done)
    unplanned_points = sum(i.get("story_points", 0) for i in unplanned)

    return {
        "label": label,
        "total_tasks": len(items),
        "planned_tasks": len(planned),
        "unplanned_tasks": len(unplanned),
        "completed_tasks": len(done),
        "total_points": round(total_points, 1),
        "completed_points": round(done_points, 1),
        "unplanned_points": round(unplanned_points, 1),
        "completion_rate": (
            round(100 * len(done) / len(items), 1) if items else 0.0
        ),
        "start_snapshot": start_counts,
        "end_snapshot": end_counts,
        "unplanned_list": [
            {
                "id": i.get("id"),
                "summary": i.get("summary"),
                "points": i.get("story_points", 0),
                "stage_now": i.get("stage_now"),
                "squad": i.get("squad"),
            }
            for i in unplanned
        ],
    }


def _role_slot() -> Dict[str, Any]:
    return {"count": 0, "points": 0.0, "by_stage": {}}


def dev_summary(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per-developer breakdown for a set of issues.

    Returns ``{name: {"assigned": {...}, "reviewing": {...}}}`` where each role has
    ``count``, ``points`` and ``by_stage`` (a {stage: {count, points}} map showing
    *where* that developer's tasks currently are).
    """
    devs: Dict[str, Dict[str, Any]] = {}

    def _add(name, role, stage, pts):
        d = devs.setdefault(
            name, {"assigned": _role_slot(), "reviewing": _role_slot()}
        )[role]
        d["count"] += 1
        d["points"] += pts
        bs = d["by_stage"].setdefault(stage, {"count": 0, "points": 0.0})
        bs["count"] += 1
        bs["points"] += pts

    for i in items:
        stage = i.get("stage_now") or UNKNOWN_STAGE
        pts = i.get("story_points", 0) or 0
        if i.get("assignee"):
            _add(i["assignee"], "assigned", stage, pts)
        if i.get("reviewer"):
            _add(i["reviewer"], "reviewing", stage, pts)

    for d in devs.values():
        for role in ("assigned", "reviewing"):
            d[role]["points"] = round(d[role]["points"], 1)
            for bs in d[role]["by_stage"].values():
                bs["points"] = round(bs["points"], 1)
    return devs


def squad_rollup(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One compact row per squad: tasks, points, completed, completion rate."""
    squads = sorted({i.get("squad") for i in items if i.get("squad")})
    rows = []
    for sq in squads:
        block = aggregate([i for i in items if i.get("squad") == sq], sq)
        rows.append(
            {
                "squad": sq,
                "tasks": block["total_tasks"],
                "points": block["total_points"],
                "completed": block["completed_tasks"],
                "unplanned": block["unplanned_tasks"],
                "completion_rate": block["completion_rate"],
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# Field extraction                                                            #
# --------------------------------------------------------------------------- #
def _cf(issue: Dict[str, Any], name: str) -> Any:
    for cf in issue.get("customFields", []) or []:
        if cf.get("name") == name:
            v = cf.get("value")
            if isinstance(v, dict):
                return v.get("name") or v.get("fullName") or v.get("login")
            if isinstance(v, list):
                if not v:
                    return None
                v0 = v[0]
                return v0.get("fullName") or v0.get("login") or v0.get("name")
            return v
    return None


def _story_points(issue: Dict[str, Any]) -> float:
    """Story points, trying a couple of common field spellings."""
    for name in ("Story Point", "Story Points", "Story point", "Estimation"):
        raw = _cf(issue, name)
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
    return 0.0


def _ms_to_date(ms: Optional[int]) -> Optional[str]:
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Network-touching entry point                                                #
# --------------------------------------------------------------------------- #
def build_sprint_summary(
    agile,
    issues_client,
    board: str,
    sprint: Optional[str] = None,
    exclude_squads: Optional[List[str]] = ("Epic",),
) -> Dict[str, Any]:
    """Compute the full sprint summary (team + per-squad + per-developer).

    Args:
        agile: an AgileBoardsClient.
        issues_client: an IssuesClient (for get_issue_activities).
        board: agile board name.
        sprint: sprint name, or None for the board's current sprint.
        exclude_squads: squad names to drop entirely (default: ("Epic",)).

    Returns:
        A JSON-serialisable dict: board/sprint meta, board_summary (with a
        squad_rollup), per-squad summaries (each with a per-developer breakdown),
        and the per-issue records that back them.
    """
    b = agile.find_board(board)
    if not b:
        raise ValueError(f"Board '{board}' not found.")

    if sprint:
        sp = agile.find_sprint(b, sprint)
    else:
        cur = b.get("currentSprint") or {}
        sp = agile.find_sprint(b, cur.get("name")) if cur.get("name") else None
    if not sp:
        raise ValueError(
            f"Sprint '{sprint or '(current)'}' not found on '{b.get('name')}'."
        )

    sprint_name = sp.get("name")
    sprint_start = sp.get("start")
    sprint_finish = sp.get("finish")

    full = agile.get_sprint(
        b["id"],
        sp["id"],
        fields="name,issues(id,idReadable,summary,created,"
        "customFields(name,value(name,login,fullName)))",
    )
    raw_issues = full.get("issues", []) or []

    issues: List[Dict[str, Any]] = []
    for i in raw_issues:
        iid = i.get("idReadable")
        try:
            acts = issues_client.get_issue_activities(iid)
        except Exception as e:  # one bad issue shouldn't sink the whole report
            logger.warning("Activities fetch failed for %s: %s", iid, e)
            acts = []

        stage_now = _cf(i, "Stage")
        squad = _cf(i, "Squad") or UNKNOWN_SQUAD
        points = _story_points(i)
        created = i.get("created")

        entered = sprint_entry_ts(acts, sprint_name, fallback=created)
        unplanned = is_unplanned(entered, sprint_start)
        s_start = stage_at(acts, sprint_start, current_stage=stage_now)

        issues.append(
            {
                "id": iid,
                "summary": i.get("summary"),
                "squad": squad,
                "assignee": _cf(i, "Assignee"),
                "reviewer": _cf(i, "Reviewer"),
                "stage_now": stage_now or UNKNOWN_STAGE,
                # planned tasks have a start status; unplanned ones weren't here yet
                "stage_at_start": (s_start or UNKNOWN_STAGE) if not unplanned else None,
                "story_points": points,
                "entered_ts": entered,
                "entered_date": _ms_to_date(entered),
                "unplanned": unplanned,
            }
        )

    if exclude_squads:
        excl = {str(s).strip().lower() for s in exclude_squads}
        issues = [i for i in issues if (i["squad"] or "").strip().lower() not in excl]

    board_summary = aggregate(issues, "Board (all squads)")
    board_summary["squad_rollup"] = squad_rollup(issues)

    squads = sorted({i["squad"] for i in issues})
    squad_blocks = {}
    for sq in squads:
        sq_items = [i for i in issues if i["squad"] == sq]
        block = aggregate(sq_items, sq)
        block["developers"] = dev_summary(sq_items)
        squad_blocks[sq] = block

    return {
        "board": b.get("name"),
        "sprint": sprint_name,
        "start": _ms_to_date(sprint_start),
        "finish": _ms_to_date(sprint_finish),
        "start_ms": sprint_start,
        "finish_ms": sprint_finish,
        "issue_count": len(issues),
        "board_summary": board_summary,
        "squads": squad_blocks,
        "issues": issues,
    }

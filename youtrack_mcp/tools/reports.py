"""
YouTrack reporting tools.

Sprint-oriented story-point reports that the stock MCP doesn't provide:
  - sprint_developer_report: story points per developer, broken down by Stage.
  - sprint_squad_report:     story points per squad, broken down by Stage.

Counting rules (chosen by the project owner):
  - A task with multiple assignees credits its FULL story points to EACH assignee.
  - Tasks with no story-point estimate are reported as a separate "unestimated" count.
"""

import logging
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional

from youtrack_mcp.api.client import YouTrackClient
from youtrack_mcp.mcp_wrappers import sync_wrapper
from youtrack_mcp.utils import format_json_response

logger = logging.getLogger(__name__)

# Default agile board to report on. Set YOUTRACK_DEFAULT_BOARD in your environment,
# or pass the `board` argument explicitly on each call.
DEFAULT_BOARD = os.getenv("YOUTRACK_DEFAULT_BOARD", "")

# Field names as configured in the YouTrack project.
STAGE_FIELD = "Stage"
SQUAD_FIELD = "Squad"
STORY_POINT_FIELD = "Story Point"
ASSIGNEE_FIELD = "Assignee"

# Fields we ask YouTrack to return for each issue in a report.
_ISSUE_FIELDS = (
    "idReadable,summary,"
    "customFields(name,value(name,login,fullName,minutes,presentation))"
)


def _cf_map(issue: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten an issue's customFields list into a {name: value} map.

    Single-value fields (state/enum/user) resolve to their display string,
    integer fields to their number, and multi-value fields to a list.
    """
    result: Dict[str, Any] = {}
    for cf in issue.get("customFields", []) or []:
        name = cf.get("name")
        value = cf.get("value")
        if isinstance(value, dict):
            result[name] = (
                value.get("name")
                or value.get("fullName")
                or value.get("login")
                or value.get("presentation")
            )
        elif isinstance(value, list):
            result[name] = [
                (
                    v.get("fullName") or v.get("name") or v.get("login")
                    if isinstance(v, dict)
                    else v
                )
                for v in value
            ]
        else:
            # Scalars (integers like Story Point) and None pass through as-is.
            result[name] = value
    return result


def _empty_bucket() -> Dict[str, int]:
    return {"points": 0, "tasks": 0, "unestimated": 0}


def _render_table(title: str, rows_by_group: Dict[str, Dict[str, Dict[str, int]]]) -> str:
    """Render a Markdown table: one row per (group, stage) with point totals."""
    lines = [f"### {title}", "", "| Group | Stage | Story Points | Tasks | Unestimated |", "|---|---|---:|---:|---:|"]
    for group in sorted(rows_by_group):
        stages = rows_by_group[group]
        g_points = g_tasks = g_unest = 0
        for stage in sorted(stages):
            b = stages[stage]
            lines.append(
                f"| {group} | {stage} | {b['points']} | {b['tasks']} | {b['unestimated']} |"
            )
            g_points += b["points"]
            g_tasks += b["tasks"]
            g_unest += b["unestimated"]
        lines.append(
            f"| **{group} — total** | | **{g_points}** | **{g_tasks}** | **{g_unest}** |"
        )
    return "\n".join(lines)


class ReportTools:
    """Sprint reporting tools for YouTrack."""

    def __init__(self):
        """Initialize the report tools."""
        self.client = YouTrackClient()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _find_board(self, board_name: str) -> Optional[Dict[str, Any]]:
        """Return the agile board dict whose name matches board_name (case-insensitive)."""
        agiles = self.client.get(
            "agiles",
            params={"fields": "id,name,currentSprint(id,name),sprints(id,name,archived)"},
        )
        for b in agiles or []:
            if (b.get("name") or "").lower() == board_name.lower():
                return b
        return None

    def _resolve_sprint(self, board_name: str, sprint: Optional[str]) -> Dict[str, str]:
        """Resolve the target sprint. If sprint is None, use the board's current sprint.

        Returns {"board": <name>, "sprint": <name>} or raises ValueError.
        """
        if not board_name:
            raise ValueError(
                "No board specified. Pass board=... or set the YOUTRACK_DEFAULT_BOARD environment variable."
            )
        board = self._find_board(board_name)
        if not board:
            raise ValueError(
                f"Board '{board_name}' not found. Available boards can be listed via the agiles API."
            )
        if sprint:
            names = [s.get("name") for s in board.get("sprints", []) or []]
            match = next((n for n in names if n and n.lower() == sprint.lower()), None)
            if not match:
                raise ValueError(
                    f"Sprint '{sprint}' not found on board '{board['name']}'. "
                    f"Known sprints include: {', '.join(n for n in names[-10:] if n)}"
                )
            return {"board": board["name"], "sprint": match}
        current = (board.get("currentSprint") or {}).get("name")
        if not current:
            raise ValueError(f"Board '{board['name']}' has no current sprint; pass sprint explicitly.")
        return {"board": board["name"], "sprint": current}

    def _fetch_sprint_issues(
        self, board: str, sprint: str, extra_query: str = ""
    ) -> List[Dict[str, Any]]:
        """Fetch all issues on a board's sprint via YouTrack search query."""
        # Verified syntax: board name unbraced, sprint name braced.
        query = f"Board {board}: {{{sprint}}}"
        if extra_query:
            query = f"{query} {extra_query}"
        issues = self.client.get(
            "issues",
            params={"query": query, "$top": 1000, "fields": _ISSUE_FIELDS},
        )
        return issues if isinstance(issues, list) else []

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #
    @sync_wrapper
    def sprint_developer_report(
        self,
        sprint: Optional[str] = None,
        board: str = DEFAULT_BOARD,
        assignee: Optional[str] = None,
    ) -> str:
        """
        Report story points per developer for a sprint, broken down by Stage (status).

        FORMAT: sprint_developer_report(sprint="Sprint #91", board="Dev Board")
                sprint_developer_report()  # defaults to the board's current sprint

        A task with several assignees credits full points to each. Tasks without a
        story-point estimate are counted under "unestimated".

        Args:
            sprint: Sprint name (e.g. "Sprint #91"). Defaults to the board's current sprint.
            board: Agile board name. Defaults to the YOUTRACK_DEFAULT_BOARD env var.
            assignee: Optional login/name to limit the report to a single developer.

        Returns:
            JSON string with a rendered table plus structured data.
        """
        try:
            resolved = self._resolve_sprint(board, sprint)
            extra = f"for: {assignee}" if assignee else ""
            issues = self._fetch_sprint_issues(resolved["board"], resolved["sprint"], extra)

            agg: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(
                lambda: defaultdict(_empty_bucket)
            )
            for issue in issues:
                fields = _cf_map(issue)
                stage = fields.get(STAGE_FIELD) or "(no stage)"
                sp = fields.get(STORY_POINT_FIELD)
                assignees = fields.get(ASSIGNEE_FIELD) or []
                if isinstance(assignees, str):
                    assignees = [assignees]
                if not assignees:
                    assignees = ["(unassigned)"]
                for dev in assignees:
                    bucket = agg[dev][stage]
                    bucket["tasks"] += 1
                    if isinstance(sp, (int, float)):
                        bucket["points"] += int(sp)
                    else:
                        bucket["unestimated"] += 1

            data = {dev: dict(stages) for dev, stages in agg.items()}
            table = _render_table(
                f"Developer story points — {resolved['board']} / {resolved['sprint']}", data
            )
            return format_json_response(
                {
                    "board": resolved["board"],
                    "sprint": resolved["sprint"],
                    "issue_count": len(issues),
                    "table": table,
                    "data": data,
                }
            )
        except Exception as e:
            logger.exception("Error building sprint developer report")
            return format_json_response({"error": str(e)})

    @sync_wrapper
    def sprint_squad_report(
        self,
        sprint: Optional[str] = None,
        board: str = DEFAULT_BOARD,
        squad: Optional[str] = None,
    ) -> str:
        """
        Report story points per squad for a sprint, broken down by Stage (status).

        FORMAT: sprint_squad_report(sprint="Sprint #91")           # all squads
                sprint_squad_report(squad="Squad A")               # one squad, current sprint

        Each task is counted once, under its Squad. Tasks without a story-point
        estimate are counted under "unestimated".

        Args:
            sprint: Sprint name (e.g. "Sprint #91"). Defaults to the board's current sprint.
            board: Agile board name. Defaults to the YOUTRACK_DEFAULT_BOARD env var.
            squad: Optional squad name (e.g. "Squad A") to limit the report.

        Returns:
            JSON string with a rendered table plus structured data.
        """
        try:
            resolved = self._resolve_sprint(board, sprint)
            extra = f"{SQUAD_FIELD}: {{{squad}}}" if squad else ""
            issues = self._fetch_sprint_issues(resolved["board"], resolved["sprint"], extra)

            agg: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(
                lambda: defaultdict(_empty_bucket)
            )
            for issue in issues:
                fields = _cf_map(issue)
                stage = fields.get(STAGE_FIELD) or "(no stage)"
                group = fields.get(SQUAD_FIELD) or "(no squad)"
                sp = fields.get(STORY_POINT_FIELD)
                bucket = agg[group][stage]
                bucket["tasks"] += 1
                if isinstance(sp, (int, float)):
                    bucket["points"] += int(sp)
                else:
                    bucket["unestimated"] += 1

            data = {grp: dict(stages) for grp, stages in agg.items()}
            table = _render_table(
                f"Squad story points — {resolved['board']} / {resolved['sprint']}", data
            )
            return format_json_response(
                {
                    "board": resolved["board"],
                    "sprint": resolved["sprint"],
                    "issue_count": len(issues),
                    "table": table,
                    "data": data,
                }
            )
        except Exception as e:
            logger.exception("Error building sprint squad report")
            return format_json_response({"error": str(e)})

    def close(self) -> None:
        """Close the report tools."""
        if hasattr(self.client, "close"):
            self.client.close()

    def get_tool_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Get tool definitions with descriptions."""
        return {
            "sprint_developer_report": {
                "description": (
                    "Story points per developer for a sprint, broken down by Stage/status "
                    '(Backlog, In Progress, Review, Test, Published, ...). '
                    'Example: sprint_developer_report(sprint="Sprint #91", board="Dev Board"). '
                    "Omit sprint to use the board's current sprint. A task with multiple "
                    "assignees credits full points to each; unestimated tasks are counted separately."
                ),
                "function": self.sprint_developer_report,
                "parameter_descriptions": {
                    "sprint": "Sprint name e.g. 'Sprint #91' (default: board's current sprint)",
                    "board": "Agile board name (default: YOUTRACK_DEFAULT_BOARD env var)",
                    "assignee": "Optional login/name to limit to one developer",
                },
            },
            "sprint_squad_report": {
                "description": (
                    "Story points per squad for a sprint, broken down by Stage/status. "
                    'Example: sprint_squad_report(squad="Squad A"). Omit sprint to use the '
                    "board's current sprint; omit squad to report all squads. Each task is "
                    "counted once; unestimated tasks are counted separately."
                ),
                "function": self.sprint_squad_report,
                "parameter_descriptions": {
                    "sprint": "Sprint name e.g. 'Sprint #91' (default: board's current sprint)",
                    "board": "Agile board name (default: YOUTRACK_DEFAULT_BOARD env var)",
                    "squad": "Optional squad name e.g. 'Squad A' (default: all squads)",
                },
            },
        }

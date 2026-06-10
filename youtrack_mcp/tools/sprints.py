"""
YouTrack sprint tools.

New capabilities the stock MCP lacks:
  - list_sprints:        list a board's sprints and its current sprint.
  - get_sprint_issues:   list the issues on a sprint with key fields.
  - move_issue_to_sprint: move an issue from one sprint to another.
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from youtrack_mcp.api.client import YouTrackClient, ResourceNotFoundError
from youtrack_mcp.api.agiles import AgileBoardsClient
from youtrack_mcp.api.issues import IssuesClient
from youtrack_mcp.mcp_wrappers import sync_wrapper
from youtrack_mcp.reporting.sprint_summary import build_sprint_summary
from youtrack_mcp.utils import format_json_response

logger = logging.getLogger(__name__)

# Set YOUTRACK_DEFAULT_BOARD in your environment, or pass `board` on each call.
DEFAULT_BOARD = os.getenv("YOUTRACK_DEFAULT_BOARD", "")


def _date_to_millis(date_str: str) -> int:
    """Parse a 'YYYY-MM-DD' (or ISO) date string to epoch millis (UTC midnight)."""
    s = (date_str or "").strip()
    # Accept a full ISO timestamp too, but we only need the date part.
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s[:10], fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError(
        f"Invalid date '{date_str}'. Use YYYY-MM-DD (e.g. 2026-06-15)."
    )


def _millis_to_date(millis: Optional[int]) -> Optional[str]:
    """Render epoch millis as a 'YYYY-MM-DD' date string (UTC)."""
    if not isinstance(millis, (int, float)):
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d"
    )


class SprintTools:
    """Agile sprint tools for YouTrack."""

    def __init__(self):
        self.client = YouTrackClient()
        self.agile = AgileBoardsClient(self.client)
        self.issues = IssuesClient(self.client)

    def _issue_db_id(self, issue_id: str) -> str:
        """Resolve a readable issue id (PROJ-123) to its internal DB id (2-456)."""
        data = self.client.get(f"issues/{issue_id}", params={"fields": "id,idReadable"})
        db_id = data.get("id") if isinstance(data, dict) else None
        if not db_id:
            raise ValueError(f"Could not resolve issue '{issue_id}'.")
        return db_id

    @sync_wrapper
    def list_sprints(self, board: str = DEFAULT_BOARD) -> str:
        """
        List the sprints on an agile board, plus which one is current.

        FORMAT: list_sprints(board="Dev Board")

        Args:
            board: Agile board name (default: YOUTRACK_DEFAULT_BOARD env var).

        Returns:
            JSON string with the board's sprints (most recent last) and current sprint.
        """
        try:
            b = self.agile.find_board(board)
            if not b:
                names = [x.get("name") for x in self.agile.list_boards()]
                return format_json_response(
                    {"error": f"Board '{board}' not found.", "available_boards": names}
                )
            sprints = [
                {
                    "name": s.get("name"),
                    "archived": s.get("archived", False),
                    "start": _millis_to_date(s.get("start")),
                    "finish": _millis_to_date(s.get("finish")),
                    "goal": s.get("goal"),
                }
                for s in b.get("sprints", []) or []
            ]
            return format_json_response(
                {
                    "board": b.get("name"),
                    "current_sprint": (b.get("currentSprint") or {}).get("name"),
                    "sprint_count": len(sprints),
                    "sprints": sprints,
                }
            )
        except Exception as e:
            logger.exception("Error listing sprints")
            return format_json_response({"error": str(e)})

    @sync_wrapper
    def get_sprint_issues(
        self, sprint: Optional[str] = None, board: str = DEFAULT_BOARD
    ) -> str:
        """
        List the issues on a sprint with key fields (Stage, Assignee, Story Point).

        FORMAT: get_sprint_issues(sprint="Sprint #91", board="Dev Board")
                get_sprint_issues()  # current sprint of the default board

        Args:
            sprint: Sprint name (default: the board's current sprint).
            board: Agile board name (default: YOUTRACK_DEFAULT_BOARD env var).

        Returns:
            JSON string with the list of issues on the sprint.
        """
        try:
            b = self.agile.find_board(board)
            if not b:
                return format_json_response({"error": f"Board '{board}' not found."})
            target = (
                self.agile.find_sprint(b, sprint)
                if sprint
                else (b.get("currentSprint") or None)
            )
            if not target:
                return format_json_response(
                    {"error": f"Sprint '{sprint or '(current)'}' not found on '{b.get('name')}'."}
                )
            full = self.agile.get_sprint(
                b["id"],
                target["id"],
                fields="name,issues(idReadable,summary,"
                "customFields(name,value(name,login,fullName)))",
            )
            issues = []
            for i in full.get("issues", []) or []:
                fields = {
                    cf.get("name"): (
                        (cf.get("value") or {}).get("name")
                        or (cf.get("value") or {}).get("fullName")
                        if isinstance(cf.get("value"), dict)
                        else cf.get("value")
                    )
                    for cf in i.get("customFields", []) or []
                }
                issues.append(
                    {
                        "id": i.get("idReadable"),
                        "summary": i.get("summary"),
                        "stage": fields.get("Stage"),
                        "assignee": fields.get("Assignee"),
                        "story_point": fields.get("Story Point"),
                    }
                )
            return format_json_response(
                {"board": b.get("name"), "sprint": full.get("name"),
                 "issue_count": len(issues), "issues": issues}
            )
        except Exception as e:
            logger.exception("Error getting sprint issues")
            return format_json_response({"error": str(e)})

    @sync_wrapper
    def move_issue_to_sprint(
        self,
        issue_id: str,
        target_sprint: str,
        from_sprint: Optional[str] = None,
        board: str = DEFAULT_BOARD,
    ) -> str:
        """
        Move an issue to a different sprint on an agile board.

        FORMAT: move_issue_to_sprint(issue_id="PROJ-123", target_sprint="Sprint #92",
                                     from_sprint="Sprint #91", board="Dev Board")

        Adds the issue to target_sprint. If from_sprint is given, the issue is also
        removed from that sprint (a true "move"). If from_sprint is omitted, the issue
        is simply added to the target sprint.

        Args:
            issue_id: Readable issue id, e.g. "PROJ-123".
            target_sprint: Sprint to move the issue into, e.g. "Sprint #92".
            from_sprint: Sprint to remove the issue from (optional), e.g. "Sprint #91".
            board: Agile board name (default: YOUTRACK_DEFAULT_BOARD env var).

        Returns:
            JSON string describing what was changed.
        """
        try:
            b = self.agile.find_board(board)
            if not b:
                return format_json_response({"error": f"Board '{board}' not found."})

            target = self.agile.find_sprint(b, target_sprint)
            if not target:
                names = [s.get("name") for s in b.get("sprints", []) or []]
                return format_json_response(
                    {"error": f"Target sprint '{target_sprint}' not found on '{b.get('name')}'.",
                     "known_sprints": names[-10:]}
                )

            source = None
            if from_sprint:
                source = self.agile.find_sprint(b, from_sprint)
                if not source:
                    return format_json_response(
                        {"error": f"Source sprint '{from_sprint}' not found on '{b.get('name')}'."}
                    )

            db_id = self._issue_db_id(issue_id)

            # Add to the target sprint first, so the issue is never left orphaned.
            self.agile.add_issue_to_sprint(b["id"], target["id"], db_id)
            removed_from = None
            if source:
                # On single-sprint boards, adding to the target already removed the
                # issue from its old sprint, so the explicit remove may 404. Treat
                # "already gone" as success.
                try:
                    self.agile.remove_issue_from_sprint(b["id"], source["id"], db_id)
                except ResourceNotFoundError:
                    pass
                removed_from = source.get("name")

            return format_json_response(
                {
                    "status": "success",
                    "issue": issue_id,
                    "board": b.get("name"),
                    "added_to": target.get("name"),
                    "removed_from": removed_from,
                    "message": (
                        f"Moved {issue_id} to '{target.get('name')}'"
                        + (f" (removed from '{removed_from}')" if removed_from else "")
                    ),
                }
            )
        except Exception as e:
            logger.exception(f"Error moving issue {issue_id} to sprint {target_sprint}")
            return format_json_response({"error": str(e), "issue": issue_id})

    @sync_wrapper
    def create_sprint(
        self,
        name: str,
        start_date: Optional[str] = None,
        finish_date: Optional[str] = None,
        duration_weeks: Optional[float] = None,
        goal: Optional[str] = None,
        board: str = DEFAULT_BOARD,
    ) -> str:
        """
        Create a new sprint on an agile board.

        FORMAT: create_sprint(name="Sprint #93", start_date="2026-06-15",
                              finish_date="2026-07-06", board="Dev Board")
                create_sprint(name="Sprint #93", start_date="2026-06-15",
                              duration_weeks=3)

        Args:
            name: Sprint name (required), e.g. "Sprint #93".
            start_date: Start date "YYYY-MM-DD" (optional).
            finish_date: End date "YYYY-MM-DD" (optional).
            duration_weeks: If finish_date is omitted, end = start + this many
                weeks (optional). Ignored when finish_date is given.
            goal: Sprint goal text (optional).
            board: Agile board name (default: YOUTRACK_DEFAULT_BOARD env var).

        Returns:
            JSON string with the created sprint.
        """
        try:
            b = self.agile.find_board(board)
            if not b:
                return format_json_response({"error": f"Board '{board}' not found."})

            if self.agile.find_sprint(b, name):
                return format_json_response(
                    {"error": f"Sprint '{name}' already exists on '{b.get('name')}'."}
                )

            start_ms = _date_to_millis(start_date) if start_date else None
            finish_ms = _date_to_millis(finish_date) if finish_date else None
            if finish_ms is None and start_ms is not None and duration_weeks:
                finish_ms = int(
                    start_ms + timedelta(weeks=duration_weeks).total_seconds() * 1000
                )
            if start_ms is not None and finish_ms is not None and finish_ms < start_ms:
                return format_json_response(
                    {"error": "finish_date is before start_date."}
                )

            created = self.agile.create_sprint(
                b["id"], name, start=start_ms, finish=finish_ms, goal=goal
            )
            return format_json_response(
                {
                    "status": "success",
                    "board": b.get("name"),
                    "sprint": created.get("name", name),
                    "start": _millis_to_date(created.get("start")),
                    "finish": _millis_to_date(created.get("finish")),
                    "goal": created.get("goal"),
                }
            )
        except ValueError as e:
            return format_json_response({"error": str(e)})
        except Exception as e:
            logger.exception(f"Error creating sprint {name}")
            return format_json_response({"error": str(e)})

    @sync_wrapper
    def update_sprint(
        self,
        sprint: str,
        new_name: Optional[str] = None,
        start_date: Optional[str] = None,
        finish_date: Optional[str] = None,
        goal: Optional[str] = None,
        board: str = DEFAULT_BOARD,
    ) -> str:
        """
        Update an existing sprint's name, start/finish dates, or goal.

        FORMAT: update_sprint(sprint="Sprint #92", finish_date="2026-06-22")

        Args:
            sprint: Name of the sprint to update, e.g. "Sprint #92".
            new_name: New sprint name (optional).
            start_date: New start date "YYYY-MM-DD" (optional).
            finish_date: New end date "YYYY-MM-DD" (optional).
            goal: New sprint goal text (optional).
            board: Agile board name (default: YOUTRACK_DEFAULT_BOARD env var).

        Returns:
            JSON string with the updated sprint.
        """
        try:
            b = self.agile.find_board(board)
            if not b:
                return format_json_response({"error": f"Board '{board}' not found."})
            target = self.agile.find_sprint(b, sprint)
            if not target:
                names = [s.get("name") for s in b.get("sprints", []) or []]
                return format_json_response(
                    {"error": f"Sprint '{sprint}' not found on '{b.get('name')}'.",
                     "known_sprints": names[-10:]}
                )

            body: Dict[str, Any] = {}
            if new_name is not None:
                body["name"] = new_name
            if start_date is not None:
                body["start"] = _date_to_millis(start_date)
            if finish_date is not None:
                body["finish"] = _date_to_millis(finish_date)
            if goal is not None:
                body["goal"] = goal
            if not body:
                return format_json_response(
                    {"error": "Nothing to update. Provide new_name, start_date, "
                              "finish_date, or goal."}
                )

            updated = self.agile.update_sprint(b["id"], target["id"], body)
            return format_json_response(
                {
                    "status": "success",
                    "board": b.get("name"),
                    "sprint": updated.get("name", target.get("name")),
                    "start": _millis_to_date(updated.get("start")),
                    "finish": _millis_to_date(updated.get("finish")),
                    "goal": updated.get("goal"),
                }
            )
        except ValueError as e:
            return format_json_response({"error": str(e)})
        except Exception as e:
            logger.exception(f"Error updating sprint {sprint}")
            return format_json_response({"error": str(e)})

    @sync_wrapper
    def rollover_sprint(
        self,
        from_sprint: str,
        to_sprint: str,
        keep_stages: Optional[List[str]] = None,
        dry_run: bool = True,
        board: str = DEFAULT_BOARD,
    ) -> str:
        """
        Carry over all unfinished issues from one sprint to another in one call:
        every issue whose Stage is NOT in keep_stages is moved to to_sprint;
        issues in keep_stages stay behind. Covers all assignees/squads.

        FORMAT: rollover_sprint(from_sprint="Sprint #91", to_sprint="Sprint #92")
                rollover_sprint(from_sprint="Sprint #91", to_sprint="Sprint #92",
                                dry_run=False)

        Args:
            from_sprint: Sprint to carry issues out of, e.g. "Sprint #91".
            to_sprint: Sprint to move them into, e.g. "Sprint #92".
            keep_stages: Stage values to leave behind (default: ["Published"]).
            dry_run: If True (default), only report what WOULD move without
                changing anything. Set False to actually move them.
            board: Agile board name (default: YOUTRACK_DEFAULT_BOARD env var).

        Returns:
            JSON string listing moved/kept issues (or the plan, when dry_run).
        """
        try:
            keep = keep_stages if keep_stages is not None else ["Published"]
            keep_lower = {str(s).strip().lower() for s in keep}

            b = self.agile.find_board(board)
            if not b:
                return format_json_response({"error": f"Board '{board}' not found."})
            source = self.agile.find_sprint(b, from_sprint)
            if not source:
                return format_json_response(
                    {"error": f"Source sprint '{from_sprint}' not found on '{b.get('name')}'."}
                )
            target = self.agile.find_sprint(b, to_sprint)
            if not target:
                return format_json_response(
                    {"error": f"Target sprint '{to_sprint}' not found on '{b.get('name')}'."}
                )

            full = self.agile.get_sprint(
                b["id"],
                source["id"],
                fields="name,issues(id,idReadable,summary,"
                "customFields(name,value(name)))",
            )

            to_move, kept = [], []
            for i in full.get("issues", []) or []:
                stage = None
                for cf in i.get("customFields", []) or []:
                    if cf.get("name") == "Stage":
                        val = cf.get("value")
                        stage = val.get("name") if isinstance(val, dict) else None
                        break
                entry = {"id": i.get("idReadable"), "stage": stage}
                if stage is not None and stage.lower() in keep_lower:
                    kept.append(entry)
                else:
                    entry["_db_id"] = i.get("id")
                    to_move.append(entry)

            if dry_run:
                return format_json_response(
                    {
                        "status": "dry_run",
                        "board": b.get("name"),
                        "from": source.get("name"),
                        "to": target.get("name"),
                        "keep_stages": keep,
                        "would_move_count": len(to_move),
                        "would_move": [
                            {"id": e["id"], "stage": e["stage"]} for e in to_move
                        ],
                        "would_keep_count": len(kept),
                        "would_keep": kept,
                        "note": "Nothing changed. Re-run with dry_run=False to apply.",
                    }
                )

            moved, errors = [], []
            for e in to_move:
                try:
                    self.agile.add_issue_to_sprint(b["id"], target["id"], e["_db_id"])
                    try:
                        self.agile.remove_issue_from_sprint(
                            b["id"], source["id"], e["_db_id"]
                        )
                    except ResourceNotFoundError:
                        pass
                    moved.append({"id": e["id"], "stage": e["stage"]})
                except Exception as move_err:
                    errors.append({"id": e["id"], "error": str(move_err)})

            return format_json_response(
                {
                    "status": "success" if not errors else "partial",
                    "board": b.get("name"),
                    "from": source.get("name"),
                    "to": target.get("name"),
                    "keep_stages": keep,
                    "moved_count": len(moved),
                    "moved": moved,
                    "kept_count": len(kept),
                    "kept": kept,
                    "errors": errors,
                }
            )
        except Exception as e:
            logger.exception(f"Error rolling over sprint {from_sprint} -> {to_sprint}")
            return format_json_response({"error": str(e)})

    @sync_wrapper
    def start_sprint(
        self,
        board: str = DEFAULT_BOARD,
        days_ahead: int = 3,
        keep_stages: Optional[List[str]] = None,
        dry_run: bool = True,
    ) -> str:
        """
        Start the next sprint: auto-detect the ending sprint and the upcoming one,
        then carry over every unfinished issue (Stage not in keep_stages) into the
        upcoming sprint. Use this for "start the sprint".

        The ending sprint is the board's current sprint (or, if none is set, the most
        recently started one). The upcoming sprint is the next sprint by start date.
        If that next sprint starts more than `days_ahead` days out, it's still used
        but flagged, so a dry run can't silently target the wrong sprint.

        FORMAT: start_sprint()                      # dry run, default board
                start_sprint(dry_run=False)         # actually move

        Args:
            board: Agile board name (default: YOUTRACK_DEFAULT_BOARD env var).
            days_ahead: Window, in days, for "about to start" (default: 3). Informational.
            keep_stages: Stage values to leave behind (default: ["Published"]).
            dry_run: If True (default), only report the plan; False to apply.

        Returns:
            JSON string with the detected sprints plus the rollover plan/result.
        """
        try:
            b = self.agile.find_board(board)
            if not b:
                return format_json_response({"error": f"Board '{board}' not found."})

            sprints = [
                s for s in (b.get("sprints") or [])
                if not s.get("archived") and s.get("start") is not None
            ]
            if not sprints:
                return format_json_response(
                    {"error": f"Board '{b.get('name')}' has no dated, active sprints."}
                )
            sprints.sort(key=lambda s: s["start"])
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            # Ending sprint: the current one if set, else the latest already-started.
            current_name = (b.get("currentSprint") or {}).get("name")
            ending = next((s for s in sprints if s.get("name") == current_name), None)
            if ending is None:
                started = [s for s in sprints if s["start"] <= now_ms]
                ending = started[-1] if started else sprints[0]

            # Upcoming sprint: the next one to start after the ending sprint.
            later = [s for s in sprints if s["start"] > ending["start"]]
            if not later:
                return format_json_response({
                    "error": (
                        f"No sprint starts after '{ending.get('name')}'. "
                        "Create the next sprint first (create_sprint)."
                    ),
                    "ending_sprint": ending.get("name"),
                })
            upcoming = later[0]

            starts_in_days = round((upcoming["start"] - now_ms) / 86_400_000, 1)
            within_window = starts_in_days <= days_ahead

            # Delegate the actual move to the tested rollover logic.
            rolled = json.loads(
                self.rollover_sprint(
                    from_sprint=ending["name"],
                    to_sprint=upcoming["name"],
                    keep_stages=keep_stages,
                    dry_run=dry_run,
                    board=b.get("name"),
                )
            )
            rolled["detected"] = {
                "ending_sprint": ending.get("name"),
                "ending_finish": _millis_to_date(ending.get("finish")),
                "upcoming_sprint": upcoming.get("name"),
                "upcoming_start": _millis_to_date(upcoming.get("start")),
                "upcoming_starts_in_days": starts_in_days,
                "within_window": within_window,
            }
            if not within_window:
                rolled["warning"] = (
                    f"Upcoming sprint '{upcoming.get('name')}' starts in {starts_in_days} "
                    f"days (> {days_ahead}-day window). Verify this is the right sprint "
                    "before applying."
                )
            return format_json_response(rolled)
        except Exception as e:
            logger.exception("Error starting sprint")
            return format_json_response({"error": str(e)})

    @sync_wrapper
    def sprint_summary(
        self,
        sprint: Optional[str] = None,
        board: str = DEFAULT_BOARD,
    ) -> str:
        """
        Summarise a sprint: each task's status AT START vs NOW, the progress made,
        and the unplanned tasks added mid-sprint (count + story points), broken down
        per squad and for the whole board. Use this for "sprint summary".

        Status-at-start is reconstructed from each issue's Stage history (replayed to
        the sprint start time); unplanned additions are detected from when each issue
        was added to the sprint (after the start = unplanned).

        FORMAT: sprint_summary()                       # current sprint, default board
                sprint_summary(sprint="Sprint #91")

        Args:
            sprint: Sprint name (default: the board's current sprint).
            board: Agile board name (default: YOUTRACK_DEFAULT_BOARD env var).

        Returns:
            JSON string with board + per-squad summaries (start/end snapshots, KPIs,
            and the list of unplanned mid-sprint additions).
        """
        try:
            data = build_sprint_summary(self.agile, self.issues, board, sprint)
            return format_json_response(data)
        except ValueError as e:
            return format_json_response({"error": str(e)})
        except Exception as e:
            logger.exception("Error building sprint summary")
            return format_json_response({"error": str(e)})

    def close(self) -> None:
        if hasattr(self.client, "close"):
            self.client.close()

    def get_tool_definitions(self) -> Dict[str, Dict[str, Any]]:
        return {
            "list_sprints": {
                "description": (
                    'List an agile board\'s sprints and its current sprint. '
                    'Example: list_sprints(board="Dev Board").'
                ),
                "function": self.list_sprints,
                "parameter_descriptions": {
                    "board": "Agile board name (default: YOUTRACK_DEFAULT_BOARD env var)",
                },
            },
            "get_sprint_issues": {
                "description": (
                    "List the issues on a sprint with Stage, Assignee, and Story Point. "
                    'Example: get_sprint_issues(sprint="Sprint #91"). Omit sprint for the '
                    "board's current sprint."
                ),
                "function": self.get_sprint_issues,
                "parameter_descriptions": {
                    "sprint": "Sprint name e.g. 'Sprint #91' (default: current sprint)",
                    "board": "Agile board name (default: YOUTRACK_DEFAULT_BOARD env var)",
                },
            },
            "move_issue_to_sprint": {
                "description": (
                    "Move an issue to another sprint on an agile board. "
                    'Example: move_issue_to_sprint(issue_id="PROJ-123", target_sprint="Sprint #92", '
                    'from_sprint="Sprint #91"). Adds to target_sprint and, if from_sprint is given, '
                    "removes it from that sprint."
                ),
                "function": self.move_issue_to_sprint,
                "parameter_descriptions": {
                    "issue_id": "Readable issue id e.g. 'PROJ-123'",
                    "target_sprint": "Sprint to move the issue into e.g. 'Sprint #92'",
                    "from_sprint": "Sprint to remove the issue from (optional) e.g. 'Sprint #91'",
                    "board": "Agile board name (default: YOUTRACK_DEFAULT_BOARD env var)",
                },
            },
            "create_sprint": {
                "description": (
                    "Create a new sprint on an agile board, optionally with start/finish "
                    'dates or a duration. Example: create_sprint(name="Sprint #93", '
                    'start_date="2026-06-15", duration_weeks=3).'
                ),
                "function": self.create_sprint,
                "parameter_descriptions": {
                    "name": "Sprint name e.g. 'Sprint #93' (required)",
                    "start_date": "Start date 'YYYY-MM-DD' (optional)",
                    "finish_date": "End date 'YYYY-MM-DD' (optional)",
                    "duration_weeks": "If finish_date omitted, end = start + N weeks (optional)",
                    "goal": "Sprint goal text (optional)",
                    "board": "Agile board name (default: YOUTRACK_DEFAULT_BOARD env var)",
                },
            },
            "update_sprint": {
                "description": (
                    "Update an existing sprint's name, start/finish dates, or goal. "
                    'Example: update_sprint(sprint="Sprint #92", finish_date="2026-06-22").'
                ),
                "function": self.update_sprint,
                "parameter_descriptions": {
                    "sprint": "Name of the sprint to update e.g. 'Sprint #92'",
                    "new_name": "New sprint name (optional)",
                    "start_date": "New start date 'YYYY-MM-DD' (optional)",
                    "finish_date": "New end date 'YYYY-MM-DD' (optional)",
                    "goal": "New sprint goal text (optional)",
                    "board": "Agile board name (default: YOUTRACK_DEFAULT_BOARD env var)",
                },
            },
            "rollover_sprint": {
                "description": (
                    "Carry over all unfinished issues from one sprint to another in one call "
                    "(all assignees/squads): every issue whose Stage is not in keep_stages is "
                    "moved; the rest stay. Defaults to a safe dry_run that only reports the plan. "
                    'Example: rollover_sprint(from_sprint="Sprint #91", to_sprint="Sprint #92", '
                    "dry_run=False)."
                ),
                "function": self.rollover_sprint,
                "parameter_descriptions": {
                    "from_sprint": "Sprint to carry issues out of e.g. 'Sprint #91'",
                    "to_sprint": "Sprint to move them into e.g. 'Sprint #92'",
                    "keep_stages": "Stage values to leave behind (default: ['Published'])",
                    "dry_run": "If true (default) only report what would move; false to apply",
                    "board": "Agile board name (default: YOUTRACK_DEFAULT_BOARD env var)",
                },
            },
            "start_sprint": {
                "description": (
                    "Start the next sprint: auto-detect the ending sprint and the upcoming "
                    "one, then carry over every unfinished issue (Stage not in keep_stages) "
                    "into the upcoming sprint. Safe dry_run by default. Use for "
                    '"start the sprint". Example: start_sprint(dry_run=False).'
                ),
                "function": self.start_sprint,
                "parameter_descriptions": {
                    "board": "Agile board name (default: YOUTRACK_DEFAULT_BOARD env var)",
                    "days_ahead": "Window in days for 'about to start' (default: 3)",
                    "keep_stages": "Stage values to leave behind (default: ['Published'])",
                    "dry_run": "If true (default) only report the plan; false to apply",
                },
            },
            "sprint_summary": {
                "description": (
                    "Summarise a sprint: each task's status at start vs now, the progress "
                    "made, and the unplanned tasks added mid-sprint (count + story points), "
                    "per squad and for the whole board. Use for \"sprint summary\". "
                    'Example: sprint_summary(sprint="Sprint #91"). Omit sprint for current.'
                ),
                "function": self.sprint_summary,
                "parameter_descriptions": {
                    "sprint": "Sprint name e.g. 'Sprint #91' (default: current sprint)",
                    "board": "Agile board name (default: YOUTRACK_DEFAULT_BOARD env var)",
                },
            },
        }

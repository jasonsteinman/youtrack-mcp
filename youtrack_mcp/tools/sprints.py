"""
YouTrack sprint tools.

New capabilities the stock MCP lacks:
  - list_sprints:        list a board's sprints and its current sprint.
  - get_sprint_issues:   list the issues on a sprint with key fields.
  - move_issue_to_sprint: move an issue from one sprint to another.
"""

import logging
import os
from typing import Any, Dict, Optional

from youtrack_mcp.api.client import YouTrackClient, ResourceNotFoundError
from youtrack_mcp.api.agiles import AgileBoardsClient
from youtrack_mcp.mcp_wrappers import sync_wrapper
from youtrack_mcp.utils import format_json_response

logger = logging.getLogger(__name__)

# Set YOUTRACK_DEFAULT_BOARD in your environment, or pass `board` on each call.
DEFAULT_BOARD = os.getenv("YOUTRACK_DEFAULT_BOARD", "")


class SprintTools:
    """Agile sprint tools for YouTrack."""

    def __init__(self):
        self.client = YouTrackClient()
        self.agile = AgileBoardsClient(self.client)

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
                {"name": s.get("name"), "archived": s.get("archived", False)}
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
        }

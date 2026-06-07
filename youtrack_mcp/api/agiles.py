"""
YouTrack Agile (boards & sprints) API client.

The stock MCP has no access to agile boards or sprints. This client wraps the
`/api/agiles` endpoints needed to list boards/sprints and move issues between
sprints.
"""

import logging
from typing import Any, Dict, List, Optional

from youtrack_mcp.api.client import YouTrackClient

logger = logging.getLogger(__name__)


class AgileBoardsClient:
    """Client for YouTrack agile boards and sprints."""

    def __init__(self, client: YouTrackClient):
        self.client = client

    def list_boards(self) -> List[Dict[str, Any]]:
        """Return all agile boards with their sprints and current sprint."""
        boards = self.client.get(
            "agiles",
            params={
                "fields": "id,name,projects(shortName,name),"
                "currentSprint(id,name),sprints(id,name,archived)"
            },
        )
        return boards if isinstance(boards, list) else []

    def find_board(self, board_name: str) -> Optional[Dict[str, Any]]:
        """Find a board by name (case-insensitive)."""
        for board in self.list_boards():
            if (board.get("name") or "").lower() == board_name.lower():
                return board
        return None

    def find_sprint(
        self, board: Dict[str, Any], sprint_name: str
    ) -> Optional[Dict[str, Any]]:
        """Find a sprint on a board by name (case-insensitive)."""
        for sprint in board.get("sprints", []) or []:
            if (sprint.get("name") or "").lower() == sprint_name.lower():
                return sprint
        return None

    def get_sprint(
        self, board_id: str, sprint_id: str, fields: str
    ) -> Dict[str, Any]:
        """Get a single sprint (optionally with its issues) by id."""
        return self.client.get(
            f"agiles/{board_id}/sprints/{sprint_id}", params={"fields": fields}
        )

    def add_issue_to_sprint(
        self, board_id: str, sprint_id: str, issue_db_id: str
    ) -> Dict[str, Any]:
        """Add an issue (by internal DB id, e.g. '2-123') to a sprint."""
        return self.client.post(
            f"agiles/{board_id}/sprints/{sprint_id}/issues",
            data={"id": issue_db_id},
        )

    def remove_issue_from_sprint(
        self, board_id: str, sprint_id: str, issue_db_id: str
    ) -> Dict[str, Any]:
        """Remove an issue (by internal DB id) from a sprint."""
        return self.client.delete(
            f"agiles/{board_id}/sprints/{sprint_id}/issues/{issue_db_id}"
        )

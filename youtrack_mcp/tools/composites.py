"""
Composite YouTrack tools that chain several operations into one call.

`create_task` creates an issue and, in one shot, optionally sets its assignee,
reviewer, squad, priority, story points, and target sprint — using the fixed
multi-user payload for Assignee/Reviewer and the agile API for the sprint.
Each sub-step is recorded so partial failures are visible rather than silent.
"""

import json
import logging
import os
from typing import Any, Dict, Optional

from youtrack_mcp.api.client import YouTrackClient
from youtrack_mcp.api.issues import IssuesClient
from youtrack_mcp.api.projects import ProjectsClient
from youtrack_mcp.tools.team_actions import TeamTools
from youtrack_mcp.tools.sprints import SprintTools
from youtrack_mcp.mcp_wrappers import sync_wrapper
from youtrack_mcp.utils import format_json_response

logger = logging.getLogger(__name__)

DEFAULT_BOARD = os.getenv("YOUTRACK_DEFAULT_BOARD", "")


class CompositeTools:
    """Multi-step convenience tools for YouTrack."""

    def __init__(self):
        self.client = YouTrackClient()
        self.issues_api = IssuesClient(self.client)
        self.projects_api = ProjectsClient(self.client)
        self.team = TeamTools()
        self.sprints = SprintTools()

    @sync_wrapper
    def create_task(
        self,
        project: str,
        summary: str,
        description: Optional[str] = None,
        assignee: Optional[str] = None,
        sprint: Optional[str] = None,
        reviewer: Optional[str] = None,
        squad: Optional[str] = None,
        priority: Optional[str] = None,
        story_point: Optional[int] = None,
        board: str = DEFAULT_BOARD,
    ) -> str:
        """
        Create a task and optionally assign it, set reviewer/squad/priority/points,
        and move it into a sprint — all in one call.

        FORMAT: create_task(project="PROJ", summary="Investigate bug", assignee="Sam",
                            sprint="Sprint #92", reviewer="Alex", priority="Critical")

        Args:
            project: Project short name or id (e.g. "PROJ").
            summary: Task title (required).
            description: Task description (optional).
            assignee: Name or login to assign (optional).
            sprint: Sprint to place the task in, e.g. "Sprint #92" (optional).
            reviewer: Name or login for the Reviewer field (optional).
            squad: Squad value, e.g. "Squad A" (optional).
            priority: Priority value, e.g. "Critical" (optional).
            story_point: Integer story points (optional).
            board: Agile board for the sprint (default: YOUTRACK_DEFAULT_BOARD env var).

        Returns:
            JSON string with the new issue id and a per-step result map.
        """
        steps: Dict[str, Any] = {}
        # --- create -------------------------------------------------------
        try:
            project_id = project
            if project and not str(project).startswith("0-"):
                proj = self.projects_api.get_project_by_name(project)
                if not proj:
                    return format_json_response({"error": f"Project '{project}' not found."})
                project_id = proj.id
            issue = self.issues_api.create_issue(project_id, summary, description)
            db_id = getattr(issue, "id", None) or (
                issue.get("id") if isinstance(issue, dict) else None
            )
            if not db_id:
                return format_json_response(
                    {"error": "Issue creation returned no id", "detail": str(issue)}
                )
            info = self.client.get(f"issues/{db_id}", params={"fields": "idReadable"})
            issue_id = info.get("idReadable", db_id) if isinstance(info, dict) else db_id
            steps["created"] = issue_id
        except Exception as e:
            logger.exception("create_task: creation failed")
            return format_json_response({"error": f"Could not create task: {e}"})

        # --- simple (enum/integer) fields via the standard updater --------
        simple: Dict[str, Any] = {}
        if priority:
            simple["Priority"] = priority
        if squad:
            simple["Squad"] = squad
        if story_point is not None:
            simple["Story Point"] = story_point
        if simple:
            try:
                self.issues_api.update_issue_custom_fields(issue_id, simple)
                steps["fields_set"] = simple
            except Exception as e:
                steps["fields_error"] = str(e)

        # --- user fields via the fixed multi-user setter ------------------
        if assignee:
            try:
                login = self.team._resolve_login(assignee)
                self.team._set_user_field(issue_id, "Assignee", login)
                steps["assignee"] = login
            except Exception as e:
                steps["assignee_error"] = str(e)
        if reviewer:
            try:
                login = self.team._resolve_login(reviewer)
                self.team._set_user_field(issue_id, "Reviewer", login)
                steps["reviewer"] = login
            except Exception as e:
                steps["reviewer_error"] = str(e)

        # --- sprint -------------------------------------------------------
        if sprint:
            try:
                res = json.loads(
                    self.sprints.move_issue_to_sprint(
                        issue_id, target_sprint=sprint, board=board
                    )
                )
                steps["sprint"] = res.get("added_to") or res.get("error")
            except Exception as e:
                steps["sprint_error"] = str(e)

        return format_json_response(
            {"status": "success", "issue": steps.get("created"), "result": steps}
        )

    def close(self) -> None:
        if hasattr(self.client, "close"):
            self.client.close()

    def get_tool_definitions(self) -> Dict[str, Dict[str, Any]]:
        return {
            "create_task": {
                "description": (
                    "Create a task and optionally assign it, set reviewer/squad/priority/"
                    "story points, and move it into a sprint — in one call. "
                    'Example: create_task(project="PROJ", summary="Investigate bug", '
                    'assignee="Sam", sprint="Sprint #92", reviewer="Alex", priority="Critical"). '
                    "Names are resolved to logins automatically. Returns the new issue id and "
                    "a per-step result map."
                ),
                "function": self.create_task,
                "parameter_descriptions": {
                    "project": "Project short name or id (e.g. 'PROJ')",
                    "summary": "Task title (required)",
                    "description": "Task description (optional)",
                    "assignee": "Name or login to assign (optional)",
                    "sprint": "Sprint to place the task in, e.g. 'Sprint #92' (optional)",
                    "reviewer": "Name or login for the Reviewer field (optional)",
                    "squad": "Squad value, e.g. 'Squad A' (optional)",
                    "priority": "Priority value, e.g. 'Critical' (optional)",
                    "story_point": "Integer story points (optional)",
                    "board": "Agile board for the sprint (default: YOUTRACK_DEFAULT_BOARD env var)",
                },
            },
        }

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


def _value_name(v):
    """Display name from a custom-field value (dict, list-of-users, or scalar)."""
    if isinstance(v, dict):
        return v.get("name") or v.get("fullName") or v.get("login")
    if isinstance(v, list):
        if not v:
            return None
        x = v[0]
        return x.get("fullName") or x.get("login") or x.get("name") if isinstance(x, dict) else x
    return v

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

    def _sprint_lookup(self, board: str, want_ids) -> Dict[str, str]:
        """Map issue idReadable -> sprint name, scanning the board's most recent
        non-archived sprints (current first) until every wanted id is found.

        On this board sprint membership lives on the agile board, not on the issue,
        so we resolve it there. Bounded to a handful of sprints to keep it cheap.
        """
        found: Dict[str, str] = {}
        want = set(want_ids)
        try:
            b = self.sprints.agile.find_board(board)
            if not b:
                return found
            sprints = [s for s in (b.get("sprints") or []) if not s.get("archived")]
            sprints.sort(key=lambda s: s.get("start") or 0, reverse=True)
            for sp in sprints[:6]:
                if not (want - set(found)):
                    break
                full = self.sprints.agile.get_sprint(
                    b["id"], sp["id"], fields="name,issues(idReadable)"
                )
                for iss in full.get("issues", []) or []:
                    rid = iss.get("idReadable")
                    if rid in want and rid not in found:
                        found[rid] = sp.get("name")
        except Exception as e:
            logger.debug(f"related_issues: sprint lookup failed: {e}")
        return found

    @sync_wrapper
    def related_issues(
        self,
        subject: str,
        limit: int = 5,
        comments: int = 5,
        project: Optional[str] = None,
        board: str = DEFAULT_BOARD,
    ) -> str:
        """
        Find the issues most related to a subject and return them enriched: assignee,
        status, sprint, and the last few comments — newest issues first. Use this when
        someone asks "what's been done about X" / "any tickets about X".

        FORMAT: related_issues(subject="login page crash")
                related_issues(subject="payment retry", limit=5, project="DEMO")

        Args:
            subject: Free-text subject/keywords to search for (required).
            limit: How many issues to return (default: 5).
            comments: How many recent comments to include per issue (default: 5).
            project: Restrict to a project short name, e.g. "DEMO" (optional).
            board: Agile board to resolve sprint membership on (default env board).

        Returns:
            JSON string with a list of related issues, each with id, summary, assignee,
            status, sprint, and last comments.
        """
        try:
            q = (subject or "").strip()
            if not q:
                return format_json_response({"error": "subject is required"})
            if project:
                q = f"project: {project} {q}"
            q = f"{q} sort by: updated desc"

            fields = (
                "idReadable,summary,updated,"
                "customFields(name,value(name,login,fullName))"
            )
            rows = self.client.get(
                "issues", params={"query": q, "fields": fields, "$top": max(1, limit)}
            )
            rows = rows if isinstance(rows, list) else []

            # resolve each issue's sprint via the board (one bounded pass)
            sprint_map = self._sprint_lookup(
                board, [i.get("idReadable") for i in rows]
            ) if board else {}

            results = []
            for i in rows:
                iid = i.get("idReadable")
                cf = {c.get("name"): c.get("value") for c in i.get("customFields", []) or []}
                status = _value_name(cf.get("Stage")) or _value_name(cf.get("State"))
                assignee = _value_name(cf.get("Assignee"))
                sprint = sprint_map.get(iid)

                # last N comments
                last_comments = []
                try:
                    cm = self.client.get(
                        f"issues/{iid}/comments",
                        params={"fields": "text,created,author(fullName,login)"},
                    )
                    for c in (cm or [])[-max(0, comments):]:
                        who = (c.get("author") or {}).get("fullName") or (
                            c.get("author") or {}
                        ).get("login") or "?"
                        txt = " ".join((c.get("text") or "").split())
                        if txt:
                            last_comments.append({"author": who, "text": txt[:300]})
                except Exception as e:
                    logger.debug(f"related_issues: comments failed for {iid}: {e}")

                results.append(
                    {
                        "id": iid,
                        "summary": i.get("summary"),
                        "assignee": assignee,
                        "status": status,
                        "sprint": sprint,
                        "comments": last_comments,
                    }
                )

            return format_json_response(
                {"subject": subject, "count": len(results), "issues": results}
            )
        except Exception as e:
            logger.exception("related_issues failed")
            return format_json_response({"error": str(e)})

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
            "related_issues": {
                "description": (
                    "Find the issues most related to a subject and return them enriched "
                    "with assignee, status, sprint, and the last few comments (newest "
                    "issues first). Use when someone asks what's been done about a topic. "
                    'Example: related_issues(subject="login crash", limit=5, project="DEMO").'
                ),
                "function": self.related_issues,
                "parameter_descriptions": {
                    "subject": "Free-text subject/keywords to search for (required)",
                    "limit": "How many issues to return (default: 5)",
                    "comments": "How many recent comments per issue (default: 5)",
                    "project": "Restrict to a project short name e.g. 'DEMO' (optional)",
                },
            },
        }

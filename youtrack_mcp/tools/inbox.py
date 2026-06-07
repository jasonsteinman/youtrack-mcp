"""
YouTrack inbox & task-summary tools.

YouTrack has no clean "my notifications" REST endpoint, so `my_inbox` builds a
practical proxy by combining recent activity that would have notified you:
mentions of you, your assigned issues, and issues you've commented on.

`task_summary` returns a compact, human-readable abstraction of a single issue
(status/Stage, priority, assignee, reviewer, squad, story points, recent comments).
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from youtrack_mcp.api.client import YouTrackClient
from youtrack_mcp.mcp_wrappers import sync_wrapper
from youtrack_mcp.utils import format_json_response

logger = logging.getLogger(__name__)

_FIELDS = (
    "idReadable,summary,updated,project(shortName),"
    "customFields(name,value(name,login,fullName,presentation))"
)


def _ms_to_date(ms: Optional[int]) -> Optional[str]:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


def _flatten(issue: Dict[str, Any]) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    for cf in issue.get("customFields", []) or []:
        v = cf.get("value")
        if isinstance(v, dict):
            fields[cf["name"]] = v.get("name") or v.get("fullName") or v.get("login")
        elif isinstance(v, list):
            fields[cf["name"]] = [
                (x.get("fullName") or x.get("name") or x.get("login")) if isinstance(x, dict) else x
                for x in v
            ]
        else:
            fields[cf["name"]] = v
    return fields


class InboxTools:
    """Notification-inbox and task-summary tools for YouTrack."""

    def __init__(self):
        self.client = YouTrackClient()

    def _search(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            r = self.client.get(
                "issues", params={"query": query, "$top": limit, "fields": _FIELDS}
            )
            return r if isinstance(r, list) else []
        except Exception as e:
            logger.warning(f"inbox sub-query failed ({query}): {e}")
            return []

    @sync_wrapper
    def my_inbox(self, days: int = 7) -> str:
        """
        Show a notification-style inbox: recent activity that concerns you.

        FORMAT: my_inbox(days=7)

        Combines three signals from the last `days` days, de-duplicated:
          - issues that @-mention you
          - issues assigned to you with recent activity
          - issues you've commented on with recent activity

        Args:
            days: How many days back to look (default: 7).

        Returns:
            JSON string with a list of issues and why each is in your inbox.
        """
        try:
            window = f"updated: {{minus {int(days)}d}} .. Today"
            buckets = {
                "mentioned": self._search(f"mentions: me {window}"),
                "assigned": self._search(f"for: me {window}"),
                "commented": self._search(f"commenter: me {window}"),
            }
            merged: Dict[str, Dict[str, Any]] = {}
            for reason, issues in buckets.items():
                for i in issues:
                    rid = i.get("idReadable")
                    if not rid:
                        continue
                    if rid not in merged:
                        f = _flatten(i)
                        merged[rid] = {
                            "id": rid,
                            "summary": i.get("summary"),
                            "stage": f.get("Stage"),
                            "priority": f.get("Priority"),
                            "assignee": f.get("Assignee"),
                            "updated": _ms_to_date(i.get("updated")),
                            "updated_ms": i.get("updated") or 0,
                            "reasons": [],
                        }
                    merged[rid]["reasons"].append(reason)

            items = sorted(merged.values(), key=lambda x: x["updated_ms"], reverse=True)
            for it in items:
                it.pop("updated_ms", None)
            return format_json_response(
                {
                    "window_days": int(days),
                    "count": len(items),
                    "summary": {k: len(v) for k, v in buckets.items()},
                    "items": items,
                }
            )
        except Exception as e:
            logger.exception("Error building inbox")
            return format_json_response({"error": str(e)})

    @sync_wrapper
    def task_summary(self, issue_id: str, comments: int = 3) -> str:
        """
        Get a compact, human-readable summary of an issue: status, priority,
        people, squad, story points, and the most recent comments.

        FORMAT: task_summary(issue_id="PROJ-123", comments=3)

        Args:
            issue_id: Readable issue id, e.g. "PROJ-123".
            comments: How many recent comments to include (default: 3).

        Returns:
            JSON string with the issue abstraction.
        """
        try:
            issue = self.client.get(
                f"issues/{issue_id}",
                params={"fields": "idReadable,summary,description,created,updated,"
                        "project(shortName,name),reporter(fullName,login),"
                        "customFields(name,value(name,login,fullName,presentation))"},
            )
            f = _flatten(issue)
            recent: List[Dict[str, Any]] = []
            if comments and int(comments) > 0:
                try:
                    cdata = self.client.get(
                        f"issues/{issue_id}/comments",
                        params={"fields": "text,created,author(fullName,login)",
                                "$top": int(comments)},
                    )
                    for c in (cdata if isinstance(cdata, list) else [])[-int(comments):]:
                        recent.append(
                            {
                                "author": (c.get("author") or {}).get("fullName"),
                                "created": _ms_to_date(c.get("created")),
                                "text": (c.get("text") or "")[:500],
                            }
                        )
                except Exception as ce:
                    logger.warning(f"Could not load comments for {issue_id}: {ce}")

            desc = issue.get("description") or ""
            summary = {
                "id": issue.get("idReadable"),
                "summary": issue.get("summary"),
                "project": (issue.get("project") or {}).get("shortName"),
                "status_stage": f.get("Stage"),
                "state": f.get("State"),
                "priority": f.get("Priority"),
                "assignee": f.get("Assignee"),
                "reviewer": f.get("Reviewer"),
                "qa": f.get("QA"),
                "squad": f.get("Squad"),
                "story_point": f.get("Story Point"),
                "reporter": (issue.get("reporter") or {}).get("fullName"),
                "updated": _ms_to_date(issue.get("updated")),
                "description": desc[:600] + ("…" if len(desc) > 600 else ""),
                "recent_comments": recent,
            }
            return format_json_response(summary)
        except Exception as e:
            logger.exception(f"Error summarizing issue {issue_id}")
            return format_json_response({"error": str(e), "issue": issue_id})

    def close(self) -> None:
        if hasattr(self.client, "close"):
            self.client.close()

    def get_tool_definitions(self) -> Dict[str, Dict[str, Any]]:
        return {
            "my_inbox": {
                "description": (
                    "Notification-style inbox: recent issues that mention you, are assigned "
                    "to you, or that you've commented on. Example: my_inbox(days=7)."
                ),
                "function": self.my_inbox,
                "parameter_descriptions": {
                    "days": "How many days back to look (default: 7)",
                },
            },
            "task_summary": {
                "description": (
                    "Compact summary of an issue: status (Stage), priority, assignee, reviewer, "
                    "squad, story points, and recent comments. "
                    'Example: task_summary(issue_id="PROJ-123", comments=3).'
                ),
                "function": self.task_summary,
                "parameter_descriptions": {
                    "issue_id": "Readable issue id e.g. 'PROJ-123'",
                    "comments": "Number of recent comments to include (default: 3)",
                },
            },
        }

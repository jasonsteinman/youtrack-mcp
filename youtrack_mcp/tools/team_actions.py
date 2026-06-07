"""
YouTrack team-action tools.

Name-aware wrappers around common people operations. Users on this instance are
usually referred to by display name ("Sam", "Alex"), but the API needs logins,
so every tool here resolves a name (or login) to a login first.

  - whois:                 resolve a name to a YouTrack login (read-only).
  - assign_issue:          set the Assignee by name or login (feature #1).
  - set_reviewer:          set the Reviewer custom field by name or login (feature #3).
  - comment_with_mentions: add a comment and @-mention people by name (feature #5).
"""

import logging
from typing import Any, Dict, List, Optional, Union

from youtrack_mcp.api.client import YouTrackClient
from youtrack_mcp.api.issues import IssuesClient
from youtrack_mcp.mcp_wrappers import sync_wrapper
from youtrack_mcp.utils import format_json_response

logger = logging.getLogger(__name__)


class TeamTools:
    """People-oriented action tools for YouTrack."""

    def __init__(self):
        self.client = YouTrackClient()
        self.issues_api = IssuesClient(self.client)
        self._users_cache: Optional[List[Dict[str, Any]]] = None

    # ------------------------------------------------------------------ #
    # User resolution
    # ------------------------------------------------------------------ #
    def _all_users(self) -> List[Dict[str, Any]]:
        if self._users_cache is None:
            users = self.client.get(
                "users", params={"fields": "login,fullName,name,email", "$top": 1000}
            )
            self._users_cache = users if isinstance(users, list) else []
        return self._users_cache

    def _resolve_login(self, name_or_login: str) -> str:
        """Resolve a display name or login to a single login, or raise ValueError.

        Match order: exact login -> exact full name -> unique substring of name/login.
        """
        needle = (name_or_login or "").strip().lower()
        if not needle:
            raise ValueError("No user name or login provided.")
        users = self._all_users()

        # 1) exact login
        for u in users:
            if (u.get("login") or "").lower() == needle:
                return u["login"]
        # 2) exact full name
        for u in users:
            if (u.get("fullName") or "").lower() == needle:
                return u["login"]
        # 3) unique substring match across full name / login / email
        matches = [
            u
            for u in users
            if needle in (u.get("fullName") or "").lower()
            or needle in (u.get("login") or "").lower()
            or needle in (u.get("email") or "").lower()
        ]
        if len(matches) == 1:
            return matches[0]["login"]
        if len(matches) > 1:
            opts = ", ".join(f"{m.get('fullName')} <{m.get('login')}>" for m in matches[:8])
            raise ValueError(
                f"'{name_or_login}' is ambiguous. Candidates: {opts}. Use a login to disambiguate."
            )
        raise ValueError(f"No user matches '{name_or_login}'.")

    def _user_id(self, login: str) -> str:
        """Resolve a login to its YouTrack user DB id (e.g. '1-23')."""
        u = self.client.get(f"users/{login}", params={"fields": "id,login"})
        uid = u.get("id") if isinstance(u, dict) else None
        if not uid:
            raise ValueError(f"Could not resolve user id for login '{login}'.")
        return uid

    def _set_user_field(self, issue_id: str, field_name: str, login: str) -> None:
        """Set a user-typed custom field (single or multi) to the given login.

        The stock updater mishandles user fields, so we post the correct payload
        directly: the field's own $type plus the user referenced by id.
        """
        uid = self._user_id(login)
        # Detect the field's concrete $type (Single/MultiUserIssueCustomField).
        issue = self.client.get(
            f"issues/{issue_id}", params={"fields": "customFields(name,$type)"}
        )
        ftype = None
        for cf in issue.get("customFields", []) or []:
            if cf.get("name") == field_name:
                ftype = cf.get("$type")
                break
        if not ftype:
            raise ValueError(f"Field '{field_name}' not found on {issue_id}.")
        value: Any = [{"id": uid}] if "Multi" in ftype else {"id": uid}
        payload = {"customFields": [{"name": field_name, "$type": ftype, "value": value}]}
        self.client.post(f"issues/{issue_id}", data=payload)

    @sync_wrapper
    def whois(self, name: str) -> str:
        """
        Resolve a person's name to their YouTrack login (read-only, no changes).

        FORMAT: whois(name="Alex")

        Args:
            name: Display name or login fragment, e.g. "Alex", "Alex Kim".

        Returns:
            JSON string with the resolved login and full name, or candidates if ambiguous.
        """
        try:
            login = self._resolve_login(name)
            match = next((u for u in self._all_users() if u["login"] == login), {})
            return format_json_response(
                {"query": name, "login": login, "full_name": match.get("fullName")}
            )
        except Exception as e:
            return format_json_response({"query": name, "error": str(e)})

    # ------------------------------------------------------------------ #
    # Write actions
    # ------------------------------------------------------------------ #
    @sync_wrapper
    def assign_issue(self, issue_id: str, person: str) -> str:
        """
        Set an issue's Assignee by name or login (resolves names automatically).

        FORMAT: assign_issue(issue_id="PROJ-123", person="Sam")

        Args:
            issue_id: Readable issue id, e.g. "PROJ-123".
            person: Display name or login of the new assignee, e.g. "Sam Lee".

        Returns:
            JSON string describing the change.
        """
        try:
            login = self._resolve_login(person)
            self._set_user_field(issue_id, "Assignee", login)
            return format_json_response(
                {"status": "success", "issue": issue_id, "field": "Assignee",
                 "set_to": login, "message": f"Assigned {issue_id} to {login}."}
            )
        except Exception as e:
            logger.exception(f"Error assigning issue {issue_id}")
            return format_json_response({"error": str(e), "issue": issue_id})

    @sync_wrapper
    def set_reviewer(self, issue_id: str, person: str) -> str:
        """
        Set an issue's Reviewer custom field by name or login.

        FORMAT: set_reviewer(issue_id="PROJ-123", person="Alex")

        Args:
            issue_id: Readable issue id, e.g. "PROJ-123".
            person: Display name or login of the reviewer, e.g. "Alex".

        Returns:
            JSON string describing the change.
        """
        try:
            login = self._resolve_login(person)
            self._set_user_field(issue_id, "Reviewer", login)
            return format_json_response(
                {"status": "success", "issue": issue_id, "field": "Reviewer",
                 "set_to": login, "message": f"Set reviewer of {issue_id} to {login}."}
            )
        except Exception as e:
            logger.exception(f"Error setting reviewer on issue {issue_id}")
            return format_json_response({"error": str(e), "issue": issue_id})

    @sync_wrapper
    def comment_with_mentions(
        self, issue_id: str, text: str, mention: Optional[Union[str, List[str]]] = None
    ) -> str:
        """
        Add a comment to an issue and @-mention people by name or login.

        FORMAT: comment_with_mentions(issue_id="PROJ-123", text="Please review",
                                      mention="Alex, Sam")

        Names in `mention` are resolved to logins and appended as @mentions so the
        mentioned people get notified.

        Args:
            issue_id: Readable issue id, e.g. "PROJ-123".
            text: The comment body.
            mention: A name/login or comma-separated list (or a list) of people to @-mention.

        Returns:
            JSON string with the posted comment result.
        """
        try:
            names: List[str] = []
            if isinstance(mention, str):
                names = [n.strip() for n in mention.split(",") if n.strip()]
            elif isinstance(mention, list):
                names = [str(n).strip() for n in mention if str(n).strip()]

            resolved, unresolved = [], []
            for n in names:
                try:
                    resolved.append(self._resolve_login(n))
                except ValueError:
                    unresolved.append(n)

            body = text or ""
            if resolved:
                body = f"{body}\n\n" + " ".join(f"@{login}" for login in resolved)

            result = self.issues_api.add_comment(issue_id, body)
            return format_json_response(
                {
                    "status": "success",
                    "issue": issue_id,
                    "mentioned": resolved,
                    "unresolved_mentions": unresolved,
                    "comment": result,
                }
            )
        except Exception as e:
            logger.exception(f"Error commenting on issue {issue_id}")
            return format_json_response({"error": str(e), "issue": issue_id})

    def close(self) -> None:
        if hasattr(self.client, "close"):
            self.client.close()

    def get_tool_definitions(self) -> Dict[str, Dict[str, Any]]:
        return {
            "whois": {
                "description": (
                    "Resolve a person's name to their YouTrack login (read-only). "
                    'Example: whois(name="Alex"). Returns candidates if ambiguous.'
                ),
                "function": self.whois,
                "parameter_descriptions": {"name": "Display name or login fragment"},
            },
            "assign_issue": {
                "description": (
                    "Set an issue's Assignee by name or login (names resolved automatically). "
                    'Example: assign_issue(issue_id="PROJ-123", person="Sam").'
                ),
                "function": self.assign_issue,
                "parameter_descriptions": {
                    "issue_id": "Readable issue id e.g. 'PROJ-123'",
                    "person": "Assignee name or login e.g. 'Sam Lee'",
                },
            },
            "set_reviewer": {
                "description": (
                    "Set an issue's Reviewer custom field by name or login. "
                    'Example: set_reviewer(issue_id="PROJ-123", person="Alex").'
                ),
                "function": self.set_reviewer,
                "parameter_descriptions": {
                    "issue_id": "Readable issue id e.g. 'PROJ-123'",
                    "person": "Reviewer name or login e.g. 'Alex'",
                },
            },
            "comment_with_mentions": {
                "description": (
                    "Add a comment to an issue and @-mention people by name or login so they "
                    'get notified. Example: comment_with_mentions(issue_id="PROJ-123", '
                    'text="Please review", mention="Alex, Sam").'
                ),
                "function": self.comment_with_mentions,
                "parameter_descriptions": {
                    "issue_id": "Readable issue id e.g. 'PROJ-123'",
                    "text": "The comment body",
                    "mention": "Name/login or comma-separated list of people to @-mention",
                },
            },
        }

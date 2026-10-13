"""
Helpers for the `InitiatedBy` user field.

When writes run as a shared bot account, so the person who
asked for a task is recorded in the user-typed `InitiatedBy` custom field instead.
These helpers let the create tools set it in the same POST that creates the issue:

  - user_field_payload:   build the customFields entry for a user field on a project.
  - create_with_user_field: create the issue with that entry, and if YouTrack rejects
    the value, create it without the field and report the error instead of failing.

Projects without the field are skipped with a warning.
"""

import logging
from typing import Any, Dict, Optional, Tuple

from youtrack_mcp.api.client import ResourceNotFoundError, YouTrackAPIError, YouTrackClient
from youtrack_mcp.api.issues import IssuesClient

logger = logging.getLogger(__name__)

INITIATED_BY_FIELD = "InitiatedBy"


def user_not_allowed_hint(field_name: str, login: str, error: Exception) -> str:
    """Explain YouTrack's terse 'Value is not allowed' for user fields."""
    msg = str(error)
    if "not allowed" in msg.lower():
        msg += (
            f" -- user '{login}' is not in the allowed users of '{field_name}' in this "
            "project (the field's user bundle). A project admin must add the user or "
            "their group to the field."
        )
    return msg


def user_id_for_login(client: YouTrackClient, login: str) -> str:
    """Resolve a login to its user DB id (e.g. '1-23'), or raise ValueError."""
    try:
        u = client.get(f"users/{login}", params={"fields": "id,login"})
    except ResourceNotFoundError:
        raise ValueError(f"No YouTrack user with login '{login}'.")
    uid = u.get("id") if isinstance(u, dict) else None
    if not uid:
        raise ValueError(f"Could not resolve user id for login '{login}'.")
    return uid


def project_user_field_type(
    client: YouTrackClient, project_id: str, field_name: str
) -> Optional[str]:
    """Issue-level $type of a user field on a project, or None if the project lacks it.

    Returns 'SingleUserIssueCustomField' or 'MultiUserIssueCustomField'.
    """
    fields = client.get(
        f"admin/projects/{project_id}/customFields",
        params={"fields": "field(name,fieldType(id))", "$top": 200},
    )
    for pcf in fields if isinstance(fields, list) else []:
        f = pcf.get("field") or {}
        if f.get("name") != field_name:
            continue
        type_id = (f.get("fieldType") or {}).get("id") or ""
        if not type_id.startswith("user"):
            raise ValueError(f"Field '{field_name}' is not a user field ({type_id}).")
        return "MultiUserIssueCustomField" if "*" in type_id else "SingleUserIssueCustomField"
    return None


def user_field_payload(
    client: YouTrackClient, project_id: str, field_name: str, login: str
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Build the customFields entry setting `field_name` to `login` on creation.

    Returns (entry, None), or (None, warning) when the project has no such field.
    Raises ValueError for an unknown login, so callers can stop before creating.
    """
    uid = user_id_for_login(client, login)
    ftype = project_user_field_type(client, project_id, field_name)
    if not ftype:
        return None, f"Project has no '{field_name}' field; skipped setting it to '{login}'."
    value: Any = [{"id": uid}] if "Multi" in ftype else {"id": uid}
    return {"name": field_name, "$type": ftype, "value": value}, None


def create_with_user_field(
    issues_api: IssuesClient,
    project_id: str,
    summary: str,
    description: Optional[str],
    entry: Optional[Dict[str, Any]],
    login: Optional[str] = None,
) -> Tuple[Any, Optional[str]]:
    """Create an issue with `entry` set in the same POST.

    If YouTrack rejects the field value, the issue is created without it and the
    error is returned, so the task itself is never lost. Returns (issue, field_error).
    """
    if not entry:
        return issues_api.create_issue(project_id, summary, description), None
    try:
        issue = issues_api.create_issue(
            project_id, summary, description,
            additional_fields={"customFields": [entry]},
        )
        return issue, None
    except YouTrackAPIError as e:
        # A 400 creates nothing, so retrying without the field cannot duplicate.
        if getattr(e, "status_code", None) != 400:
            raise
        field_error = user_not_allowed_hint(entry["name"], login or "?", e)
        logger.warning(f"Creating without {entry['name']}: {field_error}")
        return issues_api.create_issue(project_id, summary, description), field_error

"""
YouTrack Issue Attachments Module.

This module contains functions for handling issue attachments and raw data access:
- Raw issue data retrieval bypassing Pydantic models
- Attachment content access with base64 encoding
- Comprehensive attachment metadata retrieval
- File size analysis and format conversion

These functions enable file handling and detailed data access within YouTrack workflows.
"""

import json
import base64
import glob
import logging
import mimetypes
import os
from pathlib import PurePath
from typing import Any, Dict, List, Optional, Tuple

from youtrack_mcp.api.issues import AttachmentNotFoundError
from youtrack_mcp.mcp_wrappers import sync_wrapper
from youtrack_mcp.utils import format_json_response

logger = logging.getLogger(__name__)

# Uploads are only allowed from these directories (comma-separated, globs allowed).
# Override with YOUTRACK_ATTACHMENT_ALLOWED_DIRS.
DEFAULT_ALLOWED_DIRS = "~/tg-topics/*/inbox,~/tg-topics/*/outbox"
DEFAULT_MAX_MB = 20


def allowed_upload_dirs() -> List[str]:
    """Resolve the allowed upload directories (env patterns expanded, symlinks resolved)."""
    raw = os.getenv("YOUTRACK_ATTACHMENT_ALLOWED_DIRS", DEFAULT_ALLOWED_DIRS)
    dirs = []
    for pattern in (p.strip() for p in raw.split(",")):
        if not pattern:
            continue
        for match in glob.glob(os.path.expanduser(pattern)):
            if os.path.isdir(match):
                dirs.append(os.path.realpath(match))
    return dirs


def max_upload_bytes() -> int:
    """Upload size limit in bytes (YOUTRACK_ATTACHMENT_MAX_MB, default 20)."""
    try:
        mb = float(os.getenv("YOUTRACK_ATTACHMENT_MAX_MB", DEFAULT_MAX_MB))
    except ValueError:
        mb = DEFAULT_MAX_MB
    return int(mb * 1024 * 1024)


def attachment_markdown(name: str, mime_type: Optional[str] = None) -> str:
    """Markdown that links to (or, for images, embeds) an issue attachment by file name."""
    label = name.replace("[", "\\[").replace("]", "\\]")
    target = f"<{name}>" if any(c in name for c in " ()<>") else name
    prefix = "!" if (mime_type or "").startswith("image/") else ""
    return f"{prefix}[{label}]({target})"


def validate_upload_path(file_path: str) -> Tuple[str, int]:
    """Check that a file may be uploaded; return (real path, size) or raise ValueError.

    Rejects '..' segments, anything that (after resolving symlinks) is not inside an
    allowed directory, non-regular files, and files over the size limit.
    """
    if not file_path or not str(file_path).strip():
        raise ValueError("file_path is required.")
    if ".." in PurePath(file_path).parts:
        raise ValueError(f"Path traversal ('..') is not allowed: {file_path}")

    real = os.path.realpath(os.path.expanduser(file_path))
    allowed = allowed_upload_dirs()
    if not allowed:
        raise ValueError(
            "No allowed upload directory exists on this server. Configure "
            "YOUTRACK_ATTACHMENT_ALLOWED_DIRS (comma-separated, globs allowed)."
        )
    inside = False
    for d in allowed:
        try:
            if os.path.commonpath([real, d]) == d and real != d:
                inside = True
                break
        except ValueError:  # different drives on Windows
            continue
    if not inside:
        raise ValueError(
            f"'{file_path}' is outside the allowed upload directories "
            f"(inbox/ and outbox/ folders). Symlinks are resolved before checking."
        )
    if not os.path.isfile(real):
        raise ValueError(f"File not found or not a regular file: {file_path}")

    size = os.path.getsize(real)
    limit = max_upload_bytes()
    if size > limit:
        raise ValueError(
            f"File is {size / 1048576:.1f} MB; the upload limit is {limit / 1048576:.0f} MB."
        )
    return real, size


class Attachments:
    """Issue attachment and raw data access functions."""

    def __init__(self, issues_api, projects_api):
        """Initialize with API clients."""
        self.issues_api = issues_api
        self.projects_api = projects_api
        self.client = issues_api.client  # Direct access for raw API calls

    @sync_wrapper
    def get_issue_raw(self, issue_id: str) -> str:
        """
        Get raw information about a specific issue, bypassing the Pydantic model.

        Args:
            issue_id: The issue identifier (e.g., "DEMO-123", "PROJECT-456")

        Returns:
            Raw JSON string with the issue data
        """
        try:
            # Request comprehensive fields for raw issue data
            fields = "id,idReadable,summary,description,created,updated,project(id,name,shortName),reporter(id,login,name),assignee(id,login,name),customFields(id,name,value(id,name)),attachments(id,name,size,url),comments(id,text,author(login,name),created)"
            raw_issue = self.client.get(f"issues/{issue_id}?fields={fields}")
            return format_json_response(raw_issue)
        except Exception as e:
            logger.exception(f"Error getting raw issue {issue_id}")
            return format_json_response({"error": str(e)})

    @sync_wrapper
    def get_attachment_content(self, issue_id: str, attachment_id: str) -> str:
        """
        Get the content of an attachment as a base64-encoded string.

        Args:
            issue_id: The issue identifier (e.g., "DEMO-123", "PROJECT-456")
            attachment_id: The attachment ID (e.g., "1-123")

        Returns:
            JSON string with the attachment content encoded in base64
        """
        try:
            content = self.issues_api.get_attachment_content(
                issue_id, attachment_id
            )
            encoded_content = base64.b64encode(content).decode("utf-8")

            # Get attachment metadata for additional info
            issue_response = self.client.get(
                f"issues/{issue_id}?fields=attachments(id,name,mimeType,size)"
            )
            attachment_metadata = None

            if "attachments" in issue_response:
                for attachment in issue_response["attachments"]:
                    if attachment.get("id") == attachment_id:
                        attachment_metadata = attachment
                        break

            return json.dumps(
                {
                    "content": encoded_content,
                    "size_bytes_original": len(content),
                    "size_bytes_base64": len(encoded_content),
                    "filename": (
                        attachment_metadata.get("name")
                        if attachment_metadata
                        else None
                    ),
                    "mime_type": (
                        attachment_metadata.get("mimeType")
                        if attachment_metadata
                        else None
                    ),
                    "size_increase_percent": round(
                        (len(encoded_content) / len(content) - 1) * 100, 1
                    ) if len(content) > 0 else 0.0,
                    "status": "success",
                }
            )
        except Exception as e:
            logger.exception(
                f"Error getting attachment content for issue {issue_id}, attachment {attachment_id}"
            )
            return format_json_response({"error": str(e), "status": "error"})

    @sync_wrapper
    def add_attachment(
        self, issue_id: str, file_path: str, comment: Optional[str] = None
    ) -> str:
        """
        Upload a local file to an issue as an attachment.

        FORMAT: add_attachment(issue_id="DEMO-123", file_path="/home/bot/tg-topics/x/inbox/report.csv",
                               comment="Duplicates export")

        Only files under the allowed inbox/ and outbox/ folders can be uploaded
        (YOUTRACK_ATTACHMENT_ALLOWED_DIRS), up to YOUTRACK_ATTACHMENT_MAX_MB (default 20).

        Args:
            issue_id: The issue identifier (e.g., "DEMO-123")
            file_path: Local path of the file to upload
            comment: Optional comment to post with the attachment

        Returns:
            JSON string with the attachment id, name, size and URL
        """
        try:
            real, size = validate_upload_path(file_path)
        except ValueError as e:
            return format_json_response({"status": "error", "error": str(e), "issue_id": issue_id})

        name = os.path.basename(real)
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        try:
            with open(real, "rb") as fh:
                uploaded = self.client.post_multipart(
                    f"issues/{issue_id}/attachments?fields=id,name,size,mimeType,url",
                    files={"upload": (name, fh, mime)},
                )
            att = uploaded[0] if isinstance(uploaded, list) and uploaded else uploaded
            if not isinstance(att, dict) or not att.get("id"):
                return format_json_response(
                    {"status": "error", "issue_id": issue_id,
                     "error": f"Upload returned no attachment: {uploaded}"}
                )
        except Exception as e:
            logger.exception(f"Error uploading {file_path} to issue {issue_id}")
            return format_json_response({"status": "error", "error": str(e), "issue_id": issue_id})

        site = self.client.base_url.rstrip("/")
        if site.endswith("/api"):
            site = site[: -len("/api")]
        url = att.get("url") or ""
        result: Dict[str, Any] = {
            "status": "success",
            "issue_id": issue_id,
            "attachment": {
                "id": att.get("id"),
                "name": att.get("name", name),
                "size": att.get("size", size),
                "mime_type": att.get("mimeType", mime),
                "url": f"{site}{url}" if url.startswith("/") else url,
            },
            "issue_url": f"{site}/issue/{issue_id}",
        }

        if comment:
            # YouTrack ignores an attachments list on a new comment, but it resolves a
            # Markdown link whose target is an attachment's file name to that file.
            ref = attachment_markdown(result["attachment"]["name"], result["attachment"]["mime_type"])
            try:
                posted = self.client.post(
                    f"issues/{issue_id}/comments?fields=id",
                    data={"text": f"{comment}\n\n{ref}"},
                )
                result["comment_id"] = posted.get("id") if isinstance(posted, dict) else None
            except Exception as e:
                # The file is already attached; report the comment failure without hiding that.
                logger.exception(f"Attachment uploaded but comment failed on {issue_id}")
                result["comment_error"] = str(e)
        return format_json_response(result)

    @sync_wrapper
    def delete_attachment(self, issue_id: str, attachment_id: str) -> str:
        """
        Delete an attachment from an issue.

        Args:
            issue_id: The issue identifier (e.g., "DEMO-123", "PROJECT-456")
            attachment_id: The attachment ID to delete (e.g., "1-123")

        Returns:
            JSON string with the deletion status
        """
        try:
            self.issues_api.delete_attachment(issue_id, attachment_id)
            return format_json_response({
                "status": "success",
                "message": f"Attachment {attachment_id} successfully deleted from issue {issue_id}"
            })
        except AttachmentNotFoundError as e:
            logger.warning(f"Attachment not found: {e}")
            return format_json_response({"error": str(e), "status": "not_found"})
        except Exception as e:
            logger.exception(
                f"Error deleting attachment {attachment_id} from issue {issue_id}"
            )
            return format_json_response({"error": str(e), "status": "error"})

    def get_tool_definitions(self) -> Dict[str, Dict[str, Any]]:
        """Get tool definitions for attachment functions."""
        return {
            "get_issue_raw": {
                "description": "Get comprehensive raw issue data bypassing Pydantic models, including all fields, custom fields, attachments, and comments. Useful for detailed data analysis or when structured models are insufficient. Example: get_issue_raw(issue_id='DEMO-123')",
                "parameter_descriptions": {
                    "issue_id": "Issue identifier like 'DEMO-123' or 'PROJECT-456'"
                }
            },
            "get_attachment_content": {
                "description": "Download and retrieve attachment content as base64-encoded data with comprehensive metadata including file size analysis and format information. Supports files up to 10MB. Example: get_attachment_content(issue_id='DEMO-123', attachment_id='1-456')",
                "parameter_descriptions": {
                    "issue_id": "Issue identifier containing the attachment like 'DEMO-123'",
                    "attachment_id": "Attachment identifier from issue attachments list like '1-456' or '2-789'"
                }
            },
            "add_attachment": {
                "description": "Upload a local file (from the allowed inbox/ or outbox/ folders, max 20 MB by default) to an issue as an attachment, optionally posting a comment that references it. Returns the attachment id, name, size and URL. Example: add_attachment(issue_id='DEMO-123', file_path='/home/bot/tg-topics/t1/inbox/data.csv', comment='Export attached')",
                "parameter_descriptions": {
                    "issue_id": "Issue identifier like 'DEMO-123'",
                    "file_path": "Local path of the file to upload (must be inside an allowed inbox/ or outbox/ folder)",
                    "comment": "Optional comment to post with the attachment"
                }
            },
            "delete_attachment": {
                "description": "Delete an attachment from an issue. Requires appropriate permissions (either being the attachment author or having 'Delete Attachment' permission in the project). The deletion is permanent. Example: delete_attachment(issue_id='DEMO-123', attachment_id='1-456')",
                "parameter_descriptions": {
                    "issue_id": "Issue identifier containing the attachment like 'DEMO-123'",
                    "attachment_id": "Attachment identifier to delete from issue attachments list like '1-456' or '2-789'"
                }
            }
        } 
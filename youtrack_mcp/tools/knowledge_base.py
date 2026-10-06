"""
Read-only Knowledge Base tools, always enabled.

The full article toolset (create/update/comments/uploads) stays behind
YOUTRACK_ENABLE_KB; reading an article is safe, so `get_article` is always on.
Article ids look like issue ids ("RC-A-53"), which is why get_issue on them 404s.
"""

import logging
from typing import Any, Dict

from youtrack_mcp.api.client import YouTrackClient
from youtrack_mcp.mcp_wrappers import sync_wrapper
from youtrack_mcp.utils import format_json_response

logger = logging.getLogger(__name__)

ARTICLE_FIELDS = (
    "id,idReadable,summary,content,created,updated,"
    "project(shortName,name),reporter(login,fullName),"
    "parentArticle(idReadable,summary),childArticles(idReadable,summary),"
    "attachments(id,name,size,mimeType,url)"
)


class KnowledgeBaseTools:
    """Read-only Knowledge Base article tools."""

    def __init__(self):
        self.client = YouTrackClient()

    @sync_wrapper
    def get_article(self, article_id: str) -> str:
        """
        Read a Knowledge Base article: title, content, parent and child articles, attachments.

        FORMAT: get_article(article_id="RC-A-53")

        Args:
            article_id: Readable article id like "RC-A-53" (or the internal id like "106-244")

        Returns:
            JSON string with the article's title, content, parent article,
            child articles and attachments list
        """
        if not article_id or not str(article_id).strip():
            return format_json_response({"status": "error", "error": "article_id is required."})
        try:
            raw = self.client.get(
                f"articles/{str(article_id).strip()}", params={"fields": ARTICLE_FIELDS}
            )
        except Exception as e:
            logger.exception(f"Error getting article {article_id}")
            return format_json_response(
                {"status": "error", "article_id": article_id, "error": str(e)}
            )

        site = self.client.base_url.rstrip("/")
        if site.endswith("/api"):
            site = site[: -len("/api")]
        readable = raw.get("idReadable") or article_id
        parent = raw.get("parentArticle")
        reporter = raw.get("reporter") or {}
        article: Dict[str, Any] = {
            "id": readable,
            "title": raw.get("summary"),
            "url": f"{site}/articles/{readable}",
            "project": (raw.get("project") or {}).get("shortName"),
            "author": reporter.get("fullName") or reporter.get("login"),
            "created": raw.get("created"),
            "updated": raw.get("updated"),
            "parent_article": (
                {"id": parent.get("idReadable"), "title": parent.get("summary")}
                if parent
                else None
            ),
            "child_articles": [
                {"id": c.get("idReadable"), "title": c.get("summary")}
                for c in raw.get("childArticles") or []
            ],
            "attachments": [
                {
                    "id": a.get("id"),
                    "name": a.get("name"),
                    "size": a.get("size"),
                    "mime_type": a.get("mimeType"),
                    "url": f"{site}{a['url']}" if (a.get("url") or "").startswith("/") else a.get("url"),
                }
                for a in raw.get("attachments") or []
            ],
            "content": raw.get("content") or "",
        }
        return format_json_response(article)

    def close(self) -> None:
        if hasattr(self.client, "close"):
            self.client.close()

    def get_tool_definitions(self) -> Dict[str, Dict[str, Any]]:
        return {
            "get_article": {
                "description": (
                    "Read a Knowledge Base article (ids like 'RC-A-53' are articles, not "
                    "issues, so get_issue 404s on them). Returns title, content, parent "
                    "article, child articles and attachments. Read-only. "
                    'Example: get_article(article_id="RC-A-53")'
                ),
                "function": self.get_article,
                "parameter_descriptions": {
                    "article_id": "Readable article id like 'RC-A-53'",
                },
            },
        }

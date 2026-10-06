"""
Tests for the always-on, read-only get_article tool (RANGO-6296).
"""

import json
from unittest.mock import Mock, patch

import pytest

from youtrack_mcp.tools.knowledge_base import KnowledgeBaseTools
from youtrack_mcp.tools.loader import TOOL_PRIORITY

pytestmark = pytest.mark.unit


@pytest.fixture
def kb():
    with patch("youtrack_mcp.tools.knowledge_base.YouTrackClient"):
        tools = KnowledgeBaseTools()
    tools.client = Mock()
    tools.client.base_url = "https://yt.example.com/api"
    return tools


ARTICLE = {
    "id": "106-244",
    "idReadable": "RC-A-53",
    "summary": "[App] Event Taxonomy",
    "content": "# GA4\n\nEvents...",
    "updated": 1791025877278,
    "project": {"shortName": "RC"},
    "reporter": {"login": "alex", "fullName": "Alex Kim"},
    "parentArticle": {"idReadable": "RC-A-27", "summary": "Google Analytics"},
    "childArticles": [{"idReadable": "RC-A-60", "summary": "Web events"}],
    "attachments": [
        {"id": "8-1", "name": "flow.png", "size": 1200, "mimeType": "image/png",
         "url": "/api/files/8-1?sign=s"}
    ],
}


def test_get_article_returns_title_content_parent_and_attachments(kb):
    kb.client.get.return_value = ARTICLE

    result = json.loads(kb.get_article(article_id="RC-A-53"))

    assert kb.client.get.call_args.args[0] == "articles/RC-A-53"
    assert result["id"] == "RC-A-53"
    assert result["title"] == "[App] Event Taxonomy"
    assert result["content"].startswith("# GA4")
    assert result["url"] == "https://yt.example.com/articles/RC-A-53"
    assert result["parent_article"] == {"id": "RC-A-27", "title": "Google Analytics"}
    assert result["child_articles"] == [{"id": "RC-A-60", "title": "Web events"}]
    assert result["attachments"][0]["url"] == "https://yt.example.com/api/files/8-1?sign=s"
    assert result["author"] == "Alex Kim"


def test_top_level_article_has_no_parent(kb):
    kb.client.get.return_value = {**ARTICLE, "parentArticle": None, "attachments": []}

    result = json.loads(kb.get_article(article_id="RC-A-27"))

    assert result["parent_article"] is None
    assert result["attachments"] == []


def test_not_found_is_reported(kb):
    kb.client.get.side_effect = Exception("Resource not found")

    result = json.loads(kb.get_article(article_id="RC-A-999"))

    assert result["status"] == "error"
    assert "not found" in result["error"]


def test_blank_id_rejected(kb):
    result = json.loads(kb.get_article(article_id=" "))
    assert result["status"] == "error"
    kb.client.get.assert_not_called()


def test_only_read_tools_exposed(kb):
    assert set(kb.get_tool_definitions()) == {"get_article"}


def test_wins_over_the_kb_flag_version():
    assert TOOL_PRIORITY["KnowledgeBaseTools"]["get_article"] > 10

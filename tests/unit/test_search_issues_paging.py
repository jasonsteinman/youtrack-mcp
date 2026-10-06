"""
Tests for search_issues pagination and field selection (RANGO-6296).
"""

import json
from unittest.mock import Mock, patch

import pytest

from youtrack_mcp.tools.resources import ResourcesTools

pytestmark = pytest.mark.unit


def _issue(n, stage=True):
    fields = [
        {"name": "State", "value": {"name": "Open"}},
        {"name": "Assignee", "value": [{"login": "sam", "fullName": "Sam Lee"}]},
        {"name": "Priority", "value": {"name": "Major"}},
        {"name": "Story Point", "value": 3},
        {"name": "Reviewer", "value": []},
        {"name": "Check Assignee", "value": None},
    ]
    if stage:
        fields.append({"name": "Stage", "value": {"name": "Develop"}})
    return {
        "idReadable": f"DEMO-{n}",
        "summary": f"Issue {n}",
        "description": "long text " * 50,
        "updated": 1700000000000,
        "project": {"shortName": "DEMO"},
        "customFields": fields,
    }


@pytest.fixture
def tools():
    with patch("youtrack_mcp.tools.resources.YouTrackClient"), patch(
        "youtrack_mcp.tools.resources.IssuesClient"
    ), patch("youtrack_mcp.tools.resources.ProjectsClient"):
        t = ResourcesTools()
    t.client = Mock()
    t.client.base_url = "https://yt.example.com/api"
    return t


class TestPagination:
    def test_requests_one_extra_to_detect_more_pages(self, tools):
        tools.client.get.return_value = [_issue(i) for i in range(4)]

        result = json.loads(tools.search_issues("project: DEMO", limit=3, offset=6))

        params = tools.client.get.call_args.kwargs["params"]
        assert params["$top"] == 4 and params["$skip"] == 6
        assert result["returned"] == 3
        assert result["has_more"] is True
        assert result["next_offset"] == 9
        assert [i["id"] for i in result["issues"]] == ["DEMO-0", "DEMO-1", "DEMO-2"]

    def test_last_page(self, tools):
        tools.client.get.return_value = [_issue(1)]

        result = json.loads(tools.search_issues("project: DEMO", limit=3))

        assert result["has_more"] is False
        assert result["next_offset"] is None

    def test_limit_is_clamped(self, tools):
        tools.client.get.return_value = []

        tools.search_issues("x", limit=5000, offset=-3)

        params = tools.client.get.call_args.kwargs["params"]
        assert params["$top"] == 101 and params["$skip"] == 0

    def test_default_limit_is_20(self, tools):
        tools.client.get.return_value = []
        tools.search_issues("x")
        assert tools.client.get.call_args.kwargs["params"]["$top"] == 21


class TestFieldSelection:
    def test_compact_is_flat_and_small(self, tools):
        tools.client.get.return_value = [_issue(1)]

        issue = json.loads(tools.search_issues("x"))["issues"][0]

        assert issue == {
            "id": "DEMO-1",
            "summary": "Issue 1",
            "State": "Open",
            "Stage": "Develop",
            "Assignee": ["Sam Lee"],
            "Priority": "Major",
            "updated": 1700000000000,
        }
        assert "description" not in tools.client.get.call_args.kwargs["params"]["fields"]

    def test_compact_omits_fields_the_project_lacks(self, tools):
        tools.client.get.return_value = [_issue(1, stage=False)]

        issue = json.loads(tools.search_issues("x"))["issues"][0]

        assert "Stage" not in issue

    def test_custom_list(self, tools):
        tools.client.get.return_value = [_issue(1)]

        issue = json.loads(
            tools.search_issues("x", fields="id, description, story point, project, url, Nope")
        )["issues"][0]

        assert issue["id"] == "DEMO-1"
        assert issue["description"].startswith("long text")
        assert issue["story point"] == 3
        assert issue["project"] == "DEMO"
        assert issue["url"] == "https://yt.example.com/issue/DEMO-1"
        assert issue["Nope"] is None

    def test_base_only_list_skips_custom_fields_in_request(self, tools):
        tools.client.get.return_value = [_issue(1)]

        tools.search_issues("x", fields="id,summary")

        assert tools.client.get.call_args.kwargs["params"]["fields"] == "idReadable,summary"

    def test_full_returns_raw_issues(self, tools):
        tools.client.get.return_value = [_issue(1)]

        issue = json.loads(tools.search_issues("x", fields="full"))["issues"][0]

        assert issue["idReadable"] == "DEMO-1"
        assert "customFields" in issue

    def test_compact_output_is_much_smaller_than_full(self, tools):
        tools.client.get.return_value = [_issue(i) for i in range(20)]
        compact = tools.search_issues("x")
        full = tools.search_issues("x", fields="full")
        assert len(compact) * 4 < len(full)

    def test_api_error(self, tools):
        tools.client.get.side_effect = Exception("boom")

        result = json.loads(tools.search_issues("x"))

        assert result["status"] == "error"
        assert "boom" in result["error"]

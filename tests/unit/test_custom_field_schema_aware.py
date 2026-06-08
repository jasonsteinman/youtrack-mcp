"""Tests for schema-aware custom-field updates and field clearing.

These cover the bugs that blocked real DEMO operations:
  * Stage (a State field not literally named "State") was built as an EnumBundleElement.
  * Clearing user / enum / state fields was impossible (wrong type / no null/[] support).
"""

import json
import pytest
from unittest.mock import Mock, patch

from youtrack_mcp.api.issues import IssuesClient
from youtrack_mcp.api.client import YouTrackClient

pytestmark = pytest.mark.unit


# Concrete custom-field types as the live issue reports them (matches DEMO-4264).
ISSUE_FIELD_META = {
    "customFields": [
        {"id": "111-8", "name": "Stage", "$type": "StateIssueCustomField"},
        {"id": "112-1", "name": "Assignee", "$type": "MultiUserIssueCustomField"},
        {"id": "112-12", "name": "Reviewer", "$type": "MultiUserIssueCustomField"},
        {"id": "111-48", "name": "Squad", "$type": "SingleEnumIssueCustomField"},
        {"id": "112-6", "name": "QA", "$type": "SingleUserIssueCustomField"},
    ]
}


class TestSchemaAwareUpdates:
    @pytest.fixture
    def mock_client(self):
        return Mock(spec=YouTrackClient)

    @pytest.fixture
    def issues_client(self, mock_client):
        return IssuesClient(mock_client)

    def _posted_fields(self, mock_client):
        """Return the customFields[] list from the single POST to issues/<id>."""
        post_calls = [c for c in mock_client.post.call_args_list
                      if c.args and str(c.args[0]).startswith("issues/")]
        assert post_calls, "expected a POST to issues/<id>"
        return post_calls[-1].kwargs["data"]["customFields"]

    def _run_update(self, issues_client, mock_client, fields):
        """Drive _update_other_custom_fields with field metadata mocked."""
        # get_issue -> object exposing .project for project-id extraction.
        issue_obj = Mock()
        issue_obj.project = {"id": "0-99"}
        mock_client.post.return_value = {"ok": True}
        with patch.object(issues_client, "get_issue", return_value=issue_obj), \
             patch.object(issues_client, "_get_issue_field_types") as gft:
            gft.return_value = {
                cf["name"].lower(): {"id": cf["id"], "name": cf["name"], "$type": cf["$type"]}
                for cf in ISSUE_FIELD_META["customFields"]
            }
            issues_client._update_other_custom_fields(
                "DEMO-4264", fields, validate=False, use_commands=False
            )
        return self._posted_fields(mock_client)

    def test_stage_uses_state_bundle_element(self, issues_client, mock_client):
        """Stage must be a StateIssueCustomField holding a StateBundleElement (bug 1)."""
        fields = self._run_update(issues_client, mock_client, {"Stage": "Backlog"})
        stage = next(f for f in fields if f["name"] == "Stage")
        assert stage["$type"] == "StateIssueCustomField"
        assert stage["value"]["$type"] == "StateBundleElement"
        assert stage["value"]["name"] == "Backlog"

    def test_clear_multi_user_field_emits_empty_list(self, issues_client, mock_client):
        """Clearing a MultiUser field (Assignee) must send value: [] (bugs 2-3)."""
        fields = self._run_update(issues_client, mock_client, {"Assignee": None})
        assignee = next(f for f in fields if f["name"] == "Assignee")
        assert assignee["$type"] == "MultiUserIssueCustomField"
        assert assignee["value"] == []

    def test_clear_single_enum_field_emits_null(self, issues_client, mock_client):
        """Clearing a SingleEnum field (Squad) must send value: null (bug 4)."""
        fields = self._run_update(issues_client, mock_client, {"Squad": None})
        squad = next(f for f in fields if f["name"] == "Squad")
        assert squad["$type"] == "SingleEnumIssueCustomField"
        assert squad["value"] is None

    def test_clear_single_user_field_emits_null(self, issues_client, mock_client):
        """Clearing a SingleUser field (QA) must send value: null (bug 5)."""
        fields = self._run_update(issues_client, mock_client, {"QA": ""})
        qa = next(f for f in fields if f["name"] == "QA")
        assert qa["$type"] == "SingleUserIssueCustomField"
        assert qa["value"] is None

    def test_clear_all_people_fields_at_once(self, issues_client, mock_client):
        """The full 'unassign everything' request in one call."""
        fields = self._run_update(
            issues_client, mock_client,
            {"Assignee": None, "Reviewer": None, "Squad": None, "QA": None},
        )
        by_name = {f["name"]: f for f in fields}
        assert by_name["Assignee"]["value"] == []
        assert by_name["Reviewer"]["value"] == []
        assert by_name["Squad"]["value"] is None
        assert by_name["QA"]["value"] is None

    def test_set_user_field_resolves_to_id(self, issues_client, mock_client):
        """Setting a user field resolves the login to a User id element."""
        user = Mock()
        user.id = "1-9"
        with patch("youtrack_mcp.api.users.UsersClient") as MockUsers:
            MockUsers.return_value.get_user.return_value = user
            fields = self._run_update(issues_client, mock_client, {"QA": "john.doe"})
        qa = next(f for f in fields if f["name"] == "QA")
        assert qa["$type"] == "SingleUserIssueCustomField"
        assert qa["value"] == {"$type": "User", "id": "1-9"}


class TestClearRequestDetection:
    @pytest.fixture
    def issues_client(self):
        return IssuesClient(Mock(spec=YouTrackClient))

    @pytest.mark.parametrize("value,expected", [
        (None, True),
        ("", True),
        ("   ", True),
        ([], True),
        ((), True),
        ("Backlog", False),
        (0, False),
        (["a"], False),
    ])
    def test_is_clear_request(self, issues_client, value, expected):
        assert issues_client._is_clear_request(value) is expected


class TestBuildCustomFieldItem:
    @pytest.fixture
    def issues_client(self):
        return IssuesClient(Mock(spec=YouTrackClient))

    def test_unknown_field_type_falls_back_to_legacy(self, issues_client):
        """No metadata -> return None so the caller uses the legacy heuristic."""
        assert issues_client._build_custom_field_item(None, "Whatever", "x") is None

    def test_period_field_parses_minutes(self, issues_client):
        meta = {"id": "1", "name": "Estimation", "$type": "PeriodIssueCustomField"}
        item = issues_client._build_custom_field_item(meta, "Estimation", "2h 30m")
        assert item["value"] == {"$type": "PeriodValue", "minutes": 150}

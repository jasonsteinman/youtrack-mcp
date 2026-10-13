"""Tests for setting InitiatedBy at creation and the _set_user_field fixes."""

import json
from unittest.mock import Mock, patch

import pytest

from youtrack_mcp.api.client import (
    ResourceNotFoundError,
    ValidationError,
    YouTrackAPIError,
    YouTrackClient,
)
from youtrack_mcp.tools.initiated_by import (
    create_with_user_field,
    project_user_field_type,
    user_field_payload,
)

pytestmark = pytest.mark.unit

PROJECT_FIELDS = [
    {"field": {"name": "Assignee", "fieldType": {"id": "user[*]"}}},
    {"field": {"name": "InitiatedBy", "fieldType": {"id": "user[1]"}}},
    {"field": {"name": "Squad", "fieldType": {"id": "enum[1]"}}},
]


def _client(project_fields=PROJECT_FIELDS, user=None, user_exc=None):
    """Mock client answering users/<login> and admin/projects/<id>/customFields."""
    client = Mock(spec=YouTrackClient)

    def get(endpoint, params=None, **kw):
        if endpoint.startswith("users/"):
            if user_exc:
                raise user_exc
            return user or {"id": "1-23", "login": endpoint.split("/", 1)[1]}
        if endpoint.endswith("/customFields"):
            return project_fields
        raise AssertionError(f"unexpected GET {endpoint}")

    client.get.side_effect = get
    return client


class TestUserFieldPayload:
    def test_single_user_field(self):
        entry, warning = user_field_payload(_client(), "0-1", "InitiatedBy", "jane.doe")
        assert warning is None
        assert entry == {
            "name": "InitiatedBy",
            "$type": "SingleUserIssueCustomField",
            "value": {"id": "1-23"},
        }

    def test_multi_user_field(self):
        entry, _ = user_field_payload(_client(), "0-1", "Assignee", "jane.doe")
        assert entry["$type"] == "MultiUserIssueCustomField"
        assert entry["value"] == [{"id": "1-23"}]

    def test_project_without_field_warns(self):
        entry, warning = user_field_payload(
            _client(project_fields=PROJECT_FIELDS[:1]), "0-9", "InitiatedBy", "jane.doe"
        )
        assert entry is None
        assert "no 'InitiatedBy' field" in warning

    def test_unknown_login_raises(self):
        client = _client(user_exc=ResourceNotFoundError("404", 404))
        with pytest.raises(ValueError, match="No YouTrack user with login 'ghost'"):
            user_field_payload(client, "0-1", "InitiatedBy", "ghost")

    def test_non_user_field_rejected(self):
        with pytest.raises(ValueError, match="not a user field"):
            project_user_field_type(_client(), "0-1", "Squad")


class TestCreateWithUserField:
    ENTRY = {"name": "InitiatedBy", "$type": "SingleUserIssueCustomField", "value": {"id": "1-23"}}

    def test_field_sent_in_creation_call(self):
        api = Mock()
        api.create_issue.return_value = "issue"
        issue, err = create_with_user_field(api, "0-1", "S", "D", self.ENTRY, "jane.doe")
        assert (issue, err) == ("issue", None)
        api.create_issue.assert_called_once_with(
            "0-1", "S", "D", additional_fields={"customFields": [self.ENTRY]}
        )

    def test_rejected_value_creates_without_field_and_reports(self):
        api = Mock()
        api.create_issue.side_effect = [
            YouTrackAPIError('Error creating issue: 400 - "Value is not allowed"', 400),
            "issue",
        ]
        issue, err = create_with_user_field(api, "0-1", "S", None, self.ENTRY, "jane.doe")
        assert issue == "issue"
        assert "Value is not allowed" in err and "allowed users of 'InitiatedBy'" in err
        assert api.create_issue.call_args_list[1].args == ("0-1", "S", None)

    def test_non_400_error_propagates(self):
        api = Mock()
        api.create_issue.side_effect = YouTrackAPIError("boom", 500)
        with pytest.raises(YouTrackAPIError):
            create_with_user_field(api, "0-1", "S", None, self.ENTRY, "jane.doe")
        assert api.create_issue.call_count == 1

    def test_no_entry_plain_create(self):
        api = Mock()
        create_with_user_field(api, "0-1", "S", None, None)
        api.create_issue.assert_called_once_with("0-1", "S", None)


class TestSetUserField:
    @pytest.fixture
    def team(self):
        with patch("youtrack_mcp.tools.team_actions.YouTrackClient"):
            from youtrack_mcp.tools.team_actions import TeamTools

            t = TeamTools()
        t.client = Mock()
        t.client.get.side_effect = lambda ep, params=None, **kw: (
            {"id": "1-23"} if ep.startswith("users/")
            else {"customFields": [{"name": "InitiatedBy", "$type": "SingleUserIssueCustomField"}]}
        )
        return t

    def test_returns_json_string(self, team):
        out = team._set_user_field("DEMO-1", "InitiatedBy", "jane.doe")
        assert isinstance(out, str)
        assert json.loads(out)["set_to"] == "jane.doe"
        payload = team.client.post.call_args.kwargs["data"]
        assert payload["customFields"][0]["value"] == {"id": "1-23"}

    def test_output_schema_is_str(self, team):
        import inspect

        assert inspect.signature(team._set_user_field).return_annotation is str

    def test_youtrack_error_message_surfaces(self, team):
        team.client.post.side_effect = ValidationError(
            "API request failed with status 400: Value is not allowed (field: value)", 400
        )
        with pytest.raises(ValueError, match="Value is not allowed.*user bundle"):
            team._set_user_field("DEMO-1", "InitiatedBy", "jane.doe")


class TestClientErrorDetail:
    def test_error_description_included(self):
        client = YouTrackClient.__new__(YouTrackClient)
        resp = Mock(status_code=400, content=b"x")
        resp.json.return_value = {
            "error": "",
            "error_description": "Value is not allowed",
            "error_field": "value",
        }
        with pytest.raises(ValidationError, match="Value is not allowed \\(field: value\\)"):
            client._handle_response(resp)

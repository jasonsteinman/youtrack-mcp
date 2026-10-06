"""
Tests for add_attachment (RANGO-6296): upload from allowed inbox/outbox folders only.
"""

import json
import mimetypes
import os
from unittest.mock import Mock

import pytest

from youtrack_mcp.tools.issues.attachments import (
    Attachments,
    attachment_markdown,
    validate_upload_path,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def topics(tmp_path, monkeypatch):
    """A tg-topics-like tree with one topic, and the env pointing at its inbox/outbox."""
    root = tmp_path / "tg-topics"
    for sub in ("t1/inbox", "t1/outbox", "t1/private"):
        (root / sub).mkdir(parents=True)
    monkeypatch.setenv(
        "YOUTRACK_ATTACHMENT_ALLOWED_DIRS", f"{root}/*/inbox, {root}/*/outbox"
    )
    monkeypatch.delenv("YOUTRACK_ATTACHMENT_MAX_MB", raising=False)
    return root


@pytest.fixture
def tools():
    issues_api = Mock()
    issues_api.client = Mock()
    issues_api.client.base_url = "https://yt.example.com/api"
    issues_api.client.post_multipart.return_value = [
        {
            "id": "7-42",
            "name": "dupes.csv",
            "size": 9,
            "mimeType": "text/csv",
            "url": "/api/files/7-42?sign=abc",
        }
    ]
    issues_api.client.post.return_value = {"id": "4-99"}
    return Attachments(issues_api, Mock())


def _write(path, data=b"a,b\n1,2\n"):
    path.write_bytes(data)
    return str(path)


class TestValidateUploadPath:
    def test_inbox_file_allowed(self, topics):
        f = _write(topics / "t1" / "inbox" / "dupes.csv")
        real, size = validate_upload_path(f)
        assert real == os.path.realpath(f)
        assert size == 8

    def test_outbox_file_allowed(self, topics):
        f = _write(topics / "t1" / "outbox" / "result.txt")
        assert validate_upload_path(f)[1] == 8

    def test_outside_allowed_dirs_rejected(self, topics):
        f = _write(topics / "t1" / "private" / "secret.txt")
        with pytest.raises(ValueError, match="outside the allowed"):
            validate_upload_path(f)

    def test_dotdot_rejected_even_if_it_resolves_inside(self, topics):
        _write(topics / "t1" / "inbox" / "a.csv")
        sneaky = str(topics / "t1" / "inbox" / ".." / "inbox" / "a.csv")
        with pytest.raises(ValueError, match="traversal"):
            validate_upload_path(sneaky)

    def test_symlink_leaving_inbox_rejected(self, topics):
        target = _write(topics / "t1" / "private" / "secret.txt")
        link = topics / "t1" / "inbox" / "innocent.txt"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not permitted on this platform/user")
        with pytest.raises(ValueError, match="outside the allowed"):
            validate_upload_path(str(link))

    def test_allowed_dir_itself_rejected(self, topics):
        with pytest.raises(ValueError):
            validate_upload_path(str(topics / "t1" / "inbox"))

    def test_missing_file_rejected(self, topics):
        with pytest.raises(ValueError, match="not found"):
            validate_upload_path(str(topics / "t1" / "inbox" / "nope.csv"))

    def test_oversized_file_rejected(self, topics, monkeypatch):
        monkeypatch.setenv("YOUTRACK_ATTACHMENT_MAX_MB", "1")
        f = _write(topics / "t1" / "inbox" / "big.bin", b"x" * (1024 * 1024 + 1))
        with pytest.raises(ValueError, match="upload limit is 1 MB"):
            validate_upload_path(f)

    def test_file_at_limit_allowed(self, topics, monkeypatch):
        monkeypatch.setenv("YOUTRACK_ATTACHMENT_MAX_MB", "1")
        f = _write(topics / "t1" / "inbox" / "ok.bin", b"x" * (1024 * 1024))
        assert validate_upload_path(f)[1] == 1024 * 1024

    def test_no_allowed_dirs_exist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("YOUTRACK_ATTACHMENT_ALLOWED_DIRS", str(tmp_path / "missing" / "*"))
        f = _write(tmp_path / "x.txt")
        with pytest.raises(ValueError, match="YOUTRACK_ATTACHMENT_ALLOWED_DIRS"):
            validate_upload_path(f)

    def test_empty_path_rejected(self, topics):
        with pytest.raises(ValueError, match="required"):
            validate_upload_path("  ")


class TestAddAttachment:
    def test_upload_returns_id_name_size_and_absolute_url(self, topics, tools):
        f = _write(topics / "t1" / "inbox" / "dupes.csv")

        result = json.loads(tools.add_attachment(issue_id="RANGO-1", file_path=f))

        assert result["status"] == "success"
        assert result["attachment"] == {
            "id": "7-42",
            "name": "dupes.csv",
            "size": 9,
            "mime_type": "text/csv",
            "url": "https://yt.example.com/api/files/7-42?sign=abc",
        }
        assert result["issue_url"] == "https://yt.example.com/issue/RANGO-1"
        endpoint = tools.client.post_multipart.call_args.args[0]
        assert endpoint.startswith("issues/RANGO-1/attachments")
        name, _fh, mime = tools.client.post_multipart.call_args.kwargs["files"]["upload"]
        assert name == "dupes.csv"
        assert mime == (mimetypes.guess_type("dupes.csv")[0] or "application/octet-stream")
        tools.client.post.assert_not_called()

    def test_comment_is_posted_referencing_the_attachment(self, topics, tools):
        f = _write(topics / "t1" / "inbox" / "dupes.csv")

        result = json.loads(
            tools.add_attachment(issue_id="RANGO-1", file_path=f, comment="Duplicates export")
        )

        assert result["comment_id"] == "4-99"
        endpoint = tools.client.post.call_args.args[0]
        assert endpoint.startswith("issues/RANGO-1/comments")
        assert tools.client.post.call_args.kwargs["data"] == {
            "text": "Duplicates export\n\n[dupes.csv](dupes.csv)",
        }

    def test_comment_failure_keeps_the_upload_result(self, topics, tools):
        f = _write(topics / "t1" / "inbox" / "dupes.csv")
        tools.client.post.side_effect = Exception("403 Forbidden")

        result = json.loads(tools.add_attachment(issue_id="RANGO-1", file_path=f, comment="hi"))

        assert result["status"] == "success"
        assert result["attachment"]["id"] == "7-42"
        assert "403" in result["comment_error"]

    def test_rejected_path_never_reaches_the_api(self, topics, tools):
        f = _write(topics / "t1" / "private" / "secret.txt")

        result = json.loads(tools.add_attachment(issue_id="RANGO-1", file_path=f))

        assert result["status"] == "error"
        assert "outside the allowed" in result["error"]
        tools.client.post_multipart.assert_not_called()

    def test_upload_api_error_is_reported(self, topics, tools):
        f = _write(topics / "t1" / "inbox" / "dupes.csv")
        tools.client.post_multipart.side_effect = Exception("Permission denied")

        result = json.loads(tools.add_attachment(issue_id="RANGO-1", file_path=f))

        assert result["status"] == "error"
        assert "Permission denied" in result["error"]

    def test_empty_upload_response_is_an_error(self, topics, tools):
        f = _write(topics / "t1" / "inbox" / "dupes.csv")
        tools.client.post_multipart.return_value = []

        result = json.loads(tools.add_attachment(issue_id="RANGO-1", file_path=f))

        assert result["status"] == "error"


class TestAttachmentMarkdown:
    def test_plain_file_links_by_name(self):
        assert attachment_markdown("dupes.csv", "text/csv") == "[dupes.csv](dupes.csv)"

    def test_image_is_embedded(self):
        assert attachment_markdown("shot.png", "image/png") == "![shot.png](shot.png)"

    def test_spaces_and_brackets_are_safe(self):
        assert (
            attachment_markdown("sim run [v2] (final).py", "text/x-python")
            == r"[sim run \[v2\] (final).py](<sim run [v2] (final).py>)"
        )

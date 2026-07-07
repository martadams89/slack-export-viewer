import io
import zipfile
from pathlib import Path

import pytest

import app as app_module


def make_zip(bytes_io, members):
    with zipfile.ZipFile(bytes_io, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    bytes_io.seek(0)
    return bytes_io


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    for key in [
        "AUTH_ENABLED",
        "UPLOADER_USERNAME",
        "UPLOADER_PASSWORD",
        "UPLOADER_TARGET",
        "VIEWER_BASE_URL",
        "RESTART_ON_UPLOAD",
        "KUBE_RESTART_NAMESPACE",
        "KUBE_RESTART_DEPLOYMENT",
    ]:
        monkeypatch.delenv(key, raising=False)


class TestAuthEnabled:
    def test_defaults_to_false(self):
        assert app_module._auth_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "True", "yes", "on"])
    def test_truthy_values_enable_auth(self, monkeypatch, value):
        monkeypatch.setenv("AUTH_ENABLED", value)
        assert app_module._auth_enabled() is True

    def test_falsy_value_disables_auth(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "false")
        assert app_module._auth_enabled() is False


class TestCheckAuth:
    def test_matches_configured_credentials(self, monkeypatch):
        monkeypatch.setenv("UPLOADER_USERNAME", "admin")
        monkeypatch.setenv("UPLOADER_PASSWORD", "secret")
        assert app_module.check_auth("admin", "secret") is True

    def test_rejects_wrong_password(self, monkeypatch):
        monkeypatch.setenv("UPLOADER_USERNAME", "admin")
        monkeypatch.setenv("UPLOADER_PASSWORD", "secret")
        assert app_module.check_auth("admin", "wrong") is False

    def test_rejects_when_only_username_matches(self, monkeypatch):
        monkeypatch.setenv("UPLOADER_USERNAME", "admin")
        monkeypatch.setenv("UPLOADER_PASSWORD", "secret")
        assert app_module.check_auth("admin", "") is False


class TestGetTargetPath:
    def test_default_path(self):
        assert app_module._get_target_path() == Path("/data/export.zip")

    def test_custom_path(self, monkeypatch):
        monkeypatch.setenv("UPLOADER_TARGET", "/tmp/archive.zip")
        assert app_module._get_target_path() == Path("/tmp/archive.zip")


class TestArchiveMetadata:
    def test_missing_file(self, tmp_path):
        exists, size, mtime = app_module.archive_metadata(tmp_path / "missing.zip")
        assert exists is False
        assert size == 0
        assert mtime == "-"

    def test_existing_file(self, tmp_path):
        target = tmp_path / "export.zip"
        target.write_bytes(b"hello")
        exists, size, mtime = app_module.archive_metadata(target)
        assert exists is True
        assert size == 5
        assert mtime != "-"


class TestValidateSlackArchive:
    def test_rejects_missing_file(self, tmp_path):
        valid, message = app_module.validate_slack_archive(tmp_path / "missing.zip")
        assert valid is False
        assert "empty" in message.lower()

    def test_rejects_empty_file(self, tmp_path):
        target = tmp_path / "empty.zip"
        target.write_bytes(b"")
        valid, message = app_module.validate_slack_archive(target)
        assert valid is False
        assert "empty" in message.lower()

    def test_rejects_non_zip_file(self, tmp_path):
        target = tmp_path / "notazip.zip"
        target.write_bytes(b"not a zip file")
        valid, message = app_module.validate_slack_archive(target)
        assert valid is False
        assert "not a valid zip" in message.lower()

    def test_rejects_zip_missing_required_files(self, tmp_path):
        target = tmp_path / "incomplete.zip"
        make_zip(io.BytesIO(), {"general.json": "[]"})
        buf = make_zip(io.BytesIO(), {"general.json": "[]"})
        target.write_bytes(buf.getvalue())
        valid, message = app_module.validate_slack_archive(target)
        assert valid is False
        assert "channels.json" in message
        assert "users.json" in message

    def test_accepts_valid_slack_export(self, tmp_path):
        target = tmp_path / "export.zip"
        buf = make_zip(
            io.BytesIO(),
            {
                "channels.json": "[]",
                "users.json": "[]",
                "general/2021-01-01.json": "[]",
            },
        )
        target.write_bytes(buf.getvalue())
        valid, message = app_module.validate_slack_archive(target)
        assert valid is True
        assert "passed" in message.lower()


class TestRewriteHtml:
    def test_rewrites_root_relative_href(self):
        body = b'<a href="/static/app.css">'
        result = app_module._rewrite_html(body)
        assert result == b'<a href="/viewer/static/app.css">'

    def test_leaves_viewer_prefixed_paths_alone(self):
        body = b'<a href="/viewer/static/app.css">'
        result = app_module._rewrite_html(body)
        assert result == body

    def test_leaves_absolute_urls_alone(self):
        body = b'<a href="https://example.com/x">'
        result = app_module._rewrite_html(body)
        assert result == body


class TestRewriteLocation:
    def test_rewrites_root_relative_location(self):
        assert app_module._rewrite_location("/channels") == "/viewer/channels"

    def test_leaves_viewer_prefixed_location(self):
        assert app_module._rewrite_location("/viewer/channels") == "/viewer/channels"

    def test_leaves_external_location(self):
        assert app_module._rewrite_location("https://example.com") == "https://example.com"


class TestRoutes:
    @pytest.fixture
    def client(self):
        app_module.app.config.update(TESTING=True)
        return app_module.app.test_client()

    def test_index_without_auth(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert b"Slack Archive Upload" in response.data

    def test_index_requires_auth_when_enabled(self, client, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("UPLOADER_USERNAME", "admin")
        monkeypatch.setenv("UPLOADER_PASSWORD", "secret")
        response = client.get("/")
        assert response.status_code == 401

    def test_upload_rejects_non_zip(self, client):
        data = {"archive": (io.BytesIO(b"hello"), "notes.txt")}
        response = client.post("/upload", data=data, content_type="multipart/form-data")
        assert response.status_code == 302
        assert "type=warn" in response.headers["Location"]

    def test_upload_rejects_missing_file(self, client):
        response = client.post("/upload", data={}, content_type="multipart/form-data")
        assert response.status_code == 302
        assert "No+file+selected" in response.headers["Location"] or "No%20file%20selected" in response.headers["Location"]

    def test_upload_accepts_valid_archive(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("UPLOADER_TARGET", str(tmp_path / "export.zip"))
        buf = make_zip(io.BytesIO(), {"channels.json": "[]", "users.json": "[]"})
        data = {"archive": (buf, "export.zip")}
        response = client.post("/upload", data=data, content_type="multipart/form-data")
        assert response.status_code == 302
        assert "type=ok" in response.headers["Location"]
        assert (tmp_path / "export.zip").exists()

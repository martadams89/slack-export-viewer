import os
import re
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urljoin

import requests
from flask import Flask, Response, redirect, render_template_string, request, url_for

app = Flask(__name__)


UPLOAD_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Slack Archive Upload</title>
  <style>
    :root {
      --bg: #f3f7f9;
      --card: #ffffff;
      --text: #1d2939;
      --muted: #475467;
      --accent: #127ea6;
      --accent-hover: #0d6484;
      --ok: #067647;
      --warn: #b54708;
      --border: #d0d5dd;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Segoe UI, Tahoma, sans-serif;
      color: var(--text);
      background: radial-gradient(circle at 5% 5%, #d1f0ff, transparent 30%),
                  radial-gradient(circle at 95% 95%, #ffe5cc, transparent 28%),
                  var(--bg);
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 16px;
    }
    .card {
      width: min(680px, 100%);
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      box-shadow: 0 12px 28px rgba(16, 24, 40, 0.1);
      padding: 22px;
    }
    h1 { margin: 0 0 10px; font-size: 1.4rem; }
    p { margin: 0 0 10px; color: var(--muted); }
    .row { margin-top: 14px; }
    .status {
      margin-top: 12px;
      padding: 10px 12px;
      border-radius: 8px;
      background: #f8fafc;
      border: 1px solid var(--border);
      color: var(--muted);
    }
    .status.ok { color: var(--ok); }
    .status.warn { color: var(--warn); }
    input[type=file] {
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px;
      background: #fff;
    }
    button {
      margin-top: 12px;
      background: var(--accent);
      color: #fff;
      border: 0;
      border-radius: 9px;
      padding: 10px 14px;
      font-weight: 600;
      cursor: pointer;
    }
    button:hover { background: var(--accent-hover); }
    .hint { font-size: 0.9rem; }
    code { background: #f2f4f7; padding: 1px 5px; border-radius: 4px; }
  </style>
</head>
<body>
  <main class="card">
    <h1>Slack Export Archive Upload</h1>
    <p>Upload your Slack export zip file. It will be stored in persistent Docker volume storage and used by the viewer service.</p>

    {% if message %}
    <div class="status {{ 'ok' if message_type == 'ok' else 'warn' }}">{{ message }}</div>
    {% endif %}

    <div class="status {{ 'ok' if archive_exists else 'warn' }}">
      {% if archive_exists %}
      Archive is present at <code>{{ archive_path }}</code><br/>
      Size: {{ archive_size }} bytes<br/>
      Updated: {{ archive_mtime }}
      {% else %}
      No archive file found at <code>{{ archive_path }}</code>
      {% endif %}
    </div>

    <div class="status {{ 'ok' if auth_enabled else 'warn' }}">
      Authentication:
      {% if auth_enabled %}
      Enabled
      {% else %}
      Disabled
      {% endif %}
    </div>

    <form class="row" method="post" enctype="multipart/form-data" action="{{ url_for('upload') }}">
      <input type="file" name="archive" accept=".zip" required />
      <button type="submit">Upload Archive</button>
    </form>

    <p class="hint row">
      Viewer URL {% if auth_enabled %}(authenticated){% else %}(no authentication){% endif %}:
      <a href="{{ url_for('viewer_proxy_root') }}" target="_blank" rel="noreferrer">{{ url_for('viewer_proxy_root') }}</a>
    </p>
  </main>
</body>
</html>
"""


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _get_target_path() -> Path:
    target = os.getenv("UPLOADER_TARGET", "/data/export.zip").strip() or "/data/export.zip"
    return Path(target)


def _viewer_base_url() -> str:
    return os.getenv("VIEWER_BASE_URL", "http://slack-export-viewer:5000").rstrip("/") + "/"


def check_auth(username: str, password: str) -> bool:
    expected_username = os.getenv("UPLOADER_USERNAME", "").strip()
    expected_password = os.getenv("UPLOADER_PASSWORD", "").strip()
    return username == expected_username and password == expected_password


def authenticate() -> Response:
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="Slack Upload"'},
    )


def requires_auth(func):
    @wraps(func)
    def decorated(*args, **kwargs):
        if not _auth_enabled():
            return func(*args, **kwargs)

        auth = request.authorization
        if not auth or not check_auth(auth.username or "", auth.password or ""):
            return authenticate()

        return func(*args, **kwargs)

    return decorated


def archive_metadata(path: Path):
    if not path.exists():
        return False, 0, "-"
    stat = path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    return True, stat.st_size, mtime


@app.get("/")
@requires_auth
def index():
    target = _get_target_path()
    exists, size, mtime = archive_metadata(target)
    message = request.args.get("message", "")
    message_type = request.args.get("type", "ok")
    return render_template_string(
        UPLOAD_HTML,
        message=message,
        message_type=message_type,
        archive_exists=exists,
        archive_size=size,
        archive_mtime=mtime,
        archive_path=str(target),
        auth_enabled=_auth_enabled(),
    )


@app.post("/upload")
@requires_auth
def upload():
    target = _get_target_path()
    file = request.files.get("archive")

    if file is None or file.filename == "":
        return redirect(url_for("index", message="No file selected", type="warn"))

    if not file.filename.lower().endswith(".zip"):
        return redirect(url_for("index", message="Only .zip files are allowed", type="warn"))

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_suffix(".tmp")
    file.save(temp_path)
    temp_path.replace(target)

    return redirect(url_for("index", message="Archive uploaded successfully", type="ok"))


_ROOT_URL_RE = re.compile(
    rb'((?:href|src|action|content)=["\'])(/(?!viewer/))',
    re.IGNORECASE,
)


def _rewrite_html(body: bytes) -> bytes:
    """Rewrite root-relative URLs in HTML/JS so they point through /viewer/."""
    return _ROOT_URL_RE.sub(rb"\1/viewer/", body)


def _rewrite_location(location: str) -> str:
    """Rewrite a Location redirect header so it stays inside /viewer/."""
    if location.startswith("/") and not location.startswith("/viewer/"):
        return "/viewer" + location
    return location


@app.route("/viewer", defaults={"subpath": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@app.route("/viewer/<path:subpath>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@requires_auth
def viewer_proxy(subpath: str):
    target_url = urljoin(_viewer_base_url(), subpath)
    if request.query_string:
        target_url += "?" + request.query_string.decode("utf-8", errors="replace")

    inbound_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length"}
    }
    inbound_headers["X-Forwarded-For"] = request.remote_addr or ""
    inbound_headers["X-Forwarded-Proto"] = request.scheme

    proxied = requests.request(
        method=request.method,
        url=target_url,
        data=request.get_data(),
        headers=inbound_headers,
        cookies=request.cookies,
        allow_redirects=False,
        timeout=120,
    )

    excluded = {"content-encoding", "content-length", "transfer-encoding", "connection"}
    resp_headers = [(k, v) for k, v in proxied.headers.items() if k.lower() not in excluded]

    # Rewrite Location headers for redirects
    resp_headers = [
        (k, _rewrite_location(v)) if k.lower() == "location" else (k, v)
        for k, v in resp_headers
    ]

    content_type = proxied.headers.get("content-type", "")
    body = proxied.content
    if "html" in content_type or "javascript" in content_type:
        body = _rewrite_html(body)

    return Response(body, status=proxied.status_code, headers=resp_headers)


@app.get("/viewer/")
@requires_auth
def viewer_proxy_root():
    return viewer_proxy("")


# Validate auth-related env vars at startup only if auth is enabled
if _auth_enabled():
    _required_env("UPLOADER_USERNAME")
    _required_env("UPLOADER_PASSWORD")


if __name__ == "__main__":
    app.run(
        host=os.getenv("UPLOADER_BIND", "0.0.0.0"),
        port=int(os.getenv("UPLOADER_PORT", "8080")),
    )

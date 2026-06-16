# ──────────────────────────────────────────────────────────────────────────────
# Slack Export Viewer – Container Image
# Source: https://github.com/hfaran/slack-export-viewer
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.14-slim

LABEL org.opencontainers.image.title="slack-export-viewer" \
      org.opencontainers.image.description="Slack Export archive web viewer" \
      org.opencontainers.image.source="https://github.com/hfaran/slack-export-viewer"

# Non-root user for security
RUN groupadd --gid 1001 appgroup && \
    useradd  --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Install the package from PyPI (pinned to the current stable release)
RUN pip install --no-cache-dir "slack-export-viewer==4.0.0"

# The Slack export archive (.zip or extracted directory) is mounted here at runtime
VOLUME ["/data"]

EXPOSE 5000

# ── Runtime defaults (override via -e / env: in compose / k8s envFrom) ────────
# Bind to all interfaces so the container port is reachable from outside
ENV SEV_IP=0.0.0.0
ENV SEV_PORT=5000
# Path inside the container where the archive will be mounted
ENV SEV_ARCHIVE=/data/export.zip
# Never try to open a browser inside a headless container
ENV SEV_NO_BROWSER=true

USER appuser

CMD ["slack-export-viewer"]

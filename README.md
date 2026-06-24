# Slack Export Viewer – Container Deployment Guide

Containerised wrapper around [hfaran/slack-export-viewer](https://github.com/hfaran/slack-export-viewer) (v4.0.0).

## File layout

```
.
├── Dockerfile
├── .dockerignore
├── docker-compose.yml
├── data/
│   └── export.zip          ← place your Slack export here
└── kubernetes/
    ├── namespace.yaml
    ├── configmap.yaml
    ├── pvc.yaml
    ├── deployment.yaml
    └── service.yaml        (also contains the optional Ingress)
```

---

## 1 – Docker (local / single VM)

### Build and run

```bash
# Put your Slack export zip in ./data/
mkdir -p data
cp /path/to/my-slack-export.zip data/export.zip

# Build the image
docker build -t slack-export-viewer:4.0.0 .

# Run
docker run -d \
  --name slack-viewer \
  -p 5000:5000 \
  -v "$(pwd)/data:/data:ro" \
  -e SEV_ARCHIVE=/data/export.zip \
  slack-export-viewer:4.0.0
```

Browse to **http://localhost:5000**.

### Docker Compose (easier)

```bash
cp /path/to/my-slack-export.zip data/export.zip
docker compose up --build -d
```

Browse to **http://localhost:5000**.

### Upload Web UI + persistence + auth

The compose stack now includes an upload service with HTTP basic auth:

- Unified authenticated portal: http://localhost:8080

Behavior:

- Archive uploads are stored in a persistent named volume (`slack_data`) at `/data/export.zip`
- Uploaded archives are validated before replace (zip integrity + required `channels.json` and `users.json`)
- Invalid uploads are rejected and the previous working archive remains in place
- The viewer waits until `/data/export.zip` exists, then starts
- Viewer traffic is proxied through the authenticated uploader service at `/viewer`
- If `RESTART_ON_UPLOAD=true` in Kubernetes, uploader triggers a deployment rollout restart so new data is picked up
- Restarting containers keeps uploaded data because it is in the named volume

Default uploader credentials are defined in `docker-compose.yml`:

- Username: `admin`
- Password: `change-me-now`

Change these before exposing this outside localhost.

> Credentials can be disabled with `AUTH_ENABLED=false` in the uploader service environment.

PowerShell quick start:

```powershell
$env:COMPOSE_BAKE='false'
docker compose up -d --build
```

Then:

1. Open http://localhost:8080 and sign in.
2. Upload your Slack export zip.
3. Open http://localhost:8080/viewer.

If Docker Desktop reports an error similar to:

```text
failed to execute bake: read |0: file already closed
```

run compose with bake disabled for the command:

```bash
COMPOSE_BAKE=false docker compose up --build -d
```

On PowerShell:

```powershell
$env:COMPOSE_BAKE='false'
docker compose up --build -d
```

### Customisation

All options are set via environment variables in `docker-compose.yml`:

| Env var | Default | Description |
|---|---|---|
| `SEV_ARCHIVE` | `/data/export.zip` | Path to zip or extracted dir |
| `SEV_PORT` | `5000` | Port to listen on |
| `SEV_IP` | `0.0.0.0` | Interface to bind (keep as-is in containers) |
| `SEV_NO_BROWSER` | `true` | Don't launch a browser (required in containers) |
| `SEV_SHOW_DMS` | `false` | Show direct messages |
| `SEV_CHANNELS` | _(all)_ | Comma-separated list of channels to include |
| `SEV_HIDE_CHANNELS` | _(none)_ | Comma-separated channels to hide |
| `SEV_SKIP_CHANNEL_MEMBER_CHANGE` | `false` | Hide join/leave events |
| `SEV_SINCE` | _(none)_ | Only show messages after `YYYY-MM-DD` |

Full list: `docker run --rm slack-export-viewer:4.0.0 slack-export-viewer --help`

Uploader-specific environment variables:

| Env var | Default | Description |
|---|---|---|
| `UPLOADER_TARGET` | `/data/export.zip` | Upload destination file path |
| `VIEWER_BASE_URL` | `http://slack-export-viewer:5000` | URL used by uploader reverse-proxy for `/viewer` |
| `RESTART_ON_UPLOAD` | `false` | When `true`, call Kubernetes API to rollout-restart the viewer deployment |
| `KUBE_RESTART_DEPLOYMENT` | _(unset)_ | Deployment name to restart when `RESTART_ON_UPLOAD=true` |
| `KUBE_RESTART_NAMESPACE` | _(pod namespace)_ | Namespace for restart call (auto-detected in cluster) |

---

## 2 – Kubernetes

### Prerequisites

- A container registry (ACR, ECR, GHCR, Docker Hub, …)
- A storage class that supports the `ReadWriteOnce` access mode

### Steps

```bash
# 1. Push the image to your registry
docker build -t myregistry.azurecr.io/slack-export-viewer:4.0.0 .
docker push  myregistry.azurecr.io/slack-export-viewer:4.0.0

# 2. Update the image reference in kubernetes/deployment.yaml
#    image: myregistry.azurecr.io/slack-export-viewer:4.0.0

# 3. Set the storageClassName in kubernetes/pvc.yaml
#    e.g. "azurefile-csi" on AKS, "standard" on GKE/minikube

# 4. Apply all manifests
kubectl apply -f kubernetes/namespace.yaml
kubectl apply -f kubernetes/configmap.yaml
kubectl apply -f kubernetes/pvc.yaml
kubectl apply -f kubernetes/deployment.yaml
kubectl apply -f kubernetes/service.yaml

# 5. Copy your export archive into the PVC via the running pod
POD=$(kubectl get pod -n slack-viewer -l app=slack-export-viewer -o jsonpath='{.items[0].metadata.name}')
kubectl cp ./data/export.zip slack-viewer/${POD}:/data/export.zip

# 6. Restart the pod so it picks up the new file
kubectl rollout restart deployment/slack-export-viewer -n slack-viewer
```

### Exposing externally

- **LoadBalancer** – change `type: ClusterIP` to `type: LoadBalancer` in `service.yaml`.
- **Ingress** – uncomment the Ingress section in `service.yaml` and set your hostname.

---

## 3 – Azure App Service (Web App for Containers)

Azure App Service can run a Docker image directly from a registry.

### Steps

```bash
# 1. Create a resource group and ACR (skip if you already have one)
az group create -n rg-slack-viewer -l australiaeast
az acr  create -g rg-slack-viewer -n mySlackViewerAcr --sku Basic --admin-enabled true

# 2. Build & push
az acr build --registry mySlackViewerAcr \
             --image slack-export-viewer:4.0.0 .

# 3. Create an App Service Plan (Linux, B1 is sufficient)
az appservice plan create \
  -g rg-slack-viewer -n slack-viewer-plan \
  --is-linux --sku B1

# 4. Create the Web App
az webapp create \
  -g rg-slack-viewer -n my-slack-viewer \
  --plan slack-viewer-plan \
  --deployment-container-image-name mySlackViewerAcr.azurecr.io/slack-export-viewer:4.0.0

# 5. Set environment variables
az webapp config appsettings set \
  -g rg-slack-viewer -n my-slack-viewer \
  --settings \
    SEV_IP="0.0.0.0" \
    SEV_PORT="8000" \
    SEV_ARCHIVE="/data/export.zip" \
    SEV_NO_BROWSER="true" \
    WEBSITES_PORT="8000"
    # Azure App Service uses port 8000 (or whatever WEBSITES_PORT is set to)

# 6. Mount the export archive via Azure Files
#    a) Create a storage account and file share
az storage account create -g rg-slack-viewer -n slackviewerdata --sku Standard_LRS
az storage share-rm create --storage-account slackviewerdata --name exportdata

#    b) Upload your export zip
az storage file upload \
  --account-name slackviewerdata \
  --share-name exportdata \
  --source ./data/export.zip \
  --path export.zip

#    c) Mount the share into the Web App at /data
az webapp config storage-account add \
  -g rg-slack-viewer -n my-slack-viewer \
  --custom-id slack-export-storage \
  --storage-type AzureFiles \
  --account-name slackviewerdata \
  --share-name exportdata \
  --mount-path /data \
  --access-key "$(az storage account keys list \
       --account-name slackviewerdata \
       --query '[0].value' -o tsv)"

# 7. Browse to https://my-slack-viewer.azurewebsites.net
```

### Notes for Azure App Service

- App Service always routes inbound traffic to the port declared in `WEBSITES_PORT`. Set it to `8000` (or any value) and set `SEV_PORT` to the same value.
- Enable **Always On** (B1 and above) so the app doesn't spin down.
- Restrict access with **App Service Authentication** (Easy Auth) or **IP restrictions** because the export may contain private messages.

---

## Security considerations

- The export may contain private messages — do not expose it publicly without authentication.
- The container runs as a non-root user (UID 1001).
- The data volume is mounted **read-only** in all examples above.
- Consider placing an authenticating reverse proxy (nginx + basic auth, OAuth2 Proxy, Azure Easy Auth) in front of the app.

---

## 4 - GitHub Actions (GHCR build, self-update, self-clean)

This repository now includes automation for:

- Building and publishing container images to GHCR
- Automatically updating the pinned `slack-export-viewer` version in `Dockerfile`
- Cleaning old container versions from GHCR on a schedule

### Included files

- `.github/workflows/container-publish.yml`
- `.github/workflows/self-update-sev-version.yml`
- `.github/workflows/ghcr-cleanup.yml`
- `.github/dependabot.yml`

### GHCR image path

By default, published images go to:

```text
ghcr.io/<github-owner>/slack-export-viewer
```

For your account, that will be:

```text
ghcr.io/martadams89/slack-export-viewer
```

### What happens automatically

1. On push to `main` or `master` (and on version tags), GitHub builds and pushes the image.
2. Weekly, a workflow checks PyPI for the latest `slack-export-viewer` and opens a PR if `Dockerfile` is out of date.
3. Weekly, a cleanup workflow removes older GHCR versions (keeps the newest 10 tagged versions).
4. Dependabot opens PRs for GitHub Actions and Docker base image updates.

### First-time setup checks

- Ensure Actions are enabled on the repository.
- Ensure package permissions allow publishing to GHCR.
- If your default branch is not `main` or `master`, update the workflow triggers.

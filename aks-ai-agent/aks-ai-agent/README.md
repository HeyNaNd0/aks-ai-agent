# 🤖 AKS AI Monitoring Agent

> An autonomous AI-powered agent that monitors your Azure Kubernetes Service cluster,
> diagnoses problems using Claude AI, auto-remediates what it can, escalates what it can't,
> and documents everything — all without you lifting a finger.

[![CI/CD](https://github.com/YOUR_USERNAME/aks-ai-agent/actions/workflows/deploy.yml/badge.svg)](https://github.com/YOUR_USERNAME/aks-ai-agent/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)

---

## ✨ What It Does

| Problem Detected | Agent Action |
|---|---|
| Pod in CrashLoopBackOff | Deletes pod → K8s auto-recreates |
| Pod OOMKilled | Patches deployment to increase memory limits |
| ImagePullBackOff | Triggers rollout restart to re-authenticate |
| NotReady node | Cordons + drains workloads to healthy nodes |
| Node under pressure | Cordons node, prevents new scheduling |
| Deployment unavailable | Triggers rollout restart |
| PVC unbound | Documents + emails admin |
| Azure platform issue | Opens Azure Support case + GitHub issue |
| Any issue needing human | Emails admin with exact steps + commands |

Everything is **documented** in a local SQLite database with full audit trail.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       AKS AI Agent                              │
│                                                                 │
│  ┌─────────────┐    ┌───────────────┐    ┌──────────────────┐  │
│  │  monitor.py │───▶│diagnostics.py │───▶│ remediation.py   │  │
│  │             │    │               │    │                  │  │
│  │ Collects:   │    │ Claude AI:    │    │ Auto-fixes:      │  │
│  │ • Nodes     │    │ • Root cause  │    │ • Delete pods    │  │
│  │ • Pods      │    │ • Config vs   │    │ • Patch deploys  │  │
│  │ • Deploys   │    │   Platform?   │    │ • Drain nodes    │  │
│  │ • PVCs      │    │ • Auto-fix?   │    │ • Rollout restart│  │
│  │ • Events    │    │ • Fix steps   │    └──────────────────┘  │
│  │ • Azure API │    └───────────────┘           │              │
│  └─────────────┘                                │              │
│         │                                       ▼              │
│         │                            ┌──────────────────────┐  │
│         │                            │    notifier.py       │  │
│         │                            │                      │  │
│         │                            │ • Email admin        │  │
│         │                            │ • Azure Support case │  │
│         │                            │ • GitHub issue       │  │
│         │                            └──────────────────────┘  │
│         │                                       │              │
│         └───────────────────────────────────────▼              │
│                                      ┌──────────────────────┐  │
│                                      │    documenter.py     │  │
│                                      │                      │  │
│                                      │ • SQLite database    │  │
│                                      │ • Markdown reports   │  │
│                                      │ • Audit trail        │  │
│                                      └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📋 Prerequisites

Before deploying, you need:

- **An AKS cluster** running in Azure
- **An Azure Service Principal** with Contributor role on your resource group
- **An Anthropic API key** (get one at [console.anthropic.com](https://console.anthropic.com))
- **A GitHub Personal Access Token** with `repo` and `issues` scope
- **An email account** for sending notifications (Gmail App Password recommended)
- **kubectl** installed and configured
- **Docker** (for building the image locally, optional)

---

## 🚀 Deployment Guide

### Step 1 — Fork and clone this repo

```bash
# Fork on GitHub first, then:
git clone https://github.com/YOUR_USERNAME/aks-ai-agent.git
cd aks-ai-agent
```

### Step 2 — Create an Azure Service Principal

The agent needs this identity to talk to Azure APIs.

```bash
# Replace with your values
SUBSCRIPTION_ID="your-subscription-id"
RESOURCE_GROUP="your-resource-group"

# Create the service principal
az ad sp create-for-rbac \
  --name "aks-ai-agent" \
  --role Contributor \
  --scopes /subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP

# Output looks like:
# {
#   "appId": "CLIENT_ID",
#   "password": "CLIENT_SECRET",
#   "tenant": "TENANT_ID"
# }
```

> **Keep the output** — you'll need `appId`, `password`, and `tenant`.

### Step 3 — Add GitHub Actions Secrets

Go to your forked repo → **Settings → Secrets and variables → Actions** → **New repository secret**

Add these secrets:

| Secret Name | Where to find it |
|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) → API Keys |
| `AZURE_SUBSCRIPTION_ID` | Azure Portal → Subscriptions |
| `AZURE_TENANT_ID` | From Step 2 output (`tenant`) |
| `AZURE_CLIENT_ID` | From Step 2 output (`appId`) |
| `AZURE_CLIENT_SECRET` | From Step 2 output (`password`) |
| `ADMIN_EMAIL_PASSWORD` | Gmail: myaccount.google.com/apppasswords |
| `AKS_CLUSTER_NAME` | Your AKS cluster name |
| `AKS_RESOURCE_GROUP` | Your resource group name |
| `GITHUB_TOKEN` | Auto-provided by GitHub Actions ✓ |

### Step 4 — Edit the config file

Edit `config/config.yaml` with your cluster details:

```yaml
cluster:
  name: "your-aks-cluster-name"
  resource_group: "your-resource-group"

notifications:
  admin_email: "you@yourcompany.com"
  sender_email: "aks-agent@yourcompany.com"

github:
  issues_repo: "Azure/AKS"                  # For platform bugs
  tracking_repo: "your-org/your-aks-issues" # Your internal tracking
```

Commit and push:
```bash
git add config/config.yaml
git commit -m "Configure for my cluster"
git push origin main
```

### Step 5 — Watch the pipeline run

1. Go to **Actions** tab in your GitHub repo
2. Watch the **Build, Test & Deploy AKS Agent** workflow
3. It will:
   - Run linting and unit tests
   - Build and push a Docker image to GitHub Container Registry
   - Deploy to your AKS cluster in the `aks-agent` namespace

### Step 6 — Verify the agent is running

```bash
# Check the pod is Running
kubectl get pods -n aks-agent

# Expected output:
# NAME                           READY   STATUS    RESTARTS   AGE
# aks-ai-agent-xxxxxxxxx-xxxxx   1/1     Running   0          2m

# Tail the logs to see it working
kubectl logs -f -n aks-agent -l app=aks-ai-agent

# Expected log output:
# 2024-01-01 00:00:00 | INFO     | aks-agent.main | ═══ AKS AI Agent starting up ═══
# 2024-01-01 00:00:00 | INFO     | aks-agent.main | ▶ Starting monitoring cycle...
# 2024-01-01 00:00:05 | INFO     | aks-agent.main | ✅ No problems detected. Cluster healthy.
```

---

## 🔧 Local Development

```bash
# 1. Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and fill in .env
cp .env.example .env
# Edit .env with your real values

# 4. Run the agent locally (uses your ~/.kube/config)
python -m agent.main

# 5. Run tests
pytest tests/ -v
```

---

## 📊 Viewing the Issue Database

The agent stores all issues in `data/issues.db` (SQLite). View it with:

```bash
# Copy the DB from the pod
kubectl cp aks-agent/aks-ai-agent-xxx:/app/data/issues.db ./local-issues.db

# Open with DB Browser for SQLite (GUI)
# Download: sqlitebrowser.org

# Or query directly
sqlite3 local-issues.db "SELECT detected_at, problem_type, status, root_cause FROM issues ORDER BY detected_at DESC LIMIT 20;"
```

---

## 🔍 What Problems Are Detected

| Problem | Type | Default Severity |
|---|---|---|
| Node NotReady | `node_not_ready` | Critical |
| Node memory/disk/PID pressure | `node_pressure` | High |
| Pod CrashLoopBackOff | `pod_crashloop` | High |
| Pod OOMKilled | `pod_oomkilled` | High |
| Pod ImagePullBackOff | `pod_imagepull` | Medium |
| Pod stuck Pending | `pod_pending` | Medium |
| Deployment unavailable replicas | `deployment_unavailable` | High |
| PVC not bound | `pvc_unbound` | High |
| HPA unable to scale | `hpa_unable` | Medium |
| Namespace quota near limit | `quota_near_limit` | Medium |
| Certificate expiring | `certificate_expiring` | High |
| Azure node pool failed | `azure_quota_exceeded` | Critical |

---

## 🛡️ Security Notes

- The agent **never reads Secret contents** — only metadata
- All credentials are stored in Kubernetes Secrets, never in config files
- The service account follows **least-privilege** RBAC
- The container runs as a **non-root user** (UID 1000)
- Destructive actions (drain, delete pod) are followed by **verification**

---

## 📝 License

MIT — use it, fork it, build on it. If it helps you, drop a ⭐ on the repo!

---

## 🤝 Contributing

Pull requests welcome. Please:
1. Add tests for new problem types
2. Document new auto-fix behaviors in `remediation.py`
3. Update the problem table in this README

---

*Built with [Claude AI](https://claude.ai) by Anthropic — the AI that diagnoses its own cluster problems.*

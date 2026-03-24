# AKS AI Monitoring Agent

An autonomous AI agent that monitors an Azure Kubernetes Service (AKS) cluster 24/7, diagnoses problems using Claude AI, and either fixes them automatically or escalates with a full action plan.

## Why I Built This

I work AKS support every day. Customers come to me when their clusters break — but I only ever see the crisis, never the journey that got them there. I built this agent to understand what my customers experience: the full deployment lifecycle, the anxiety of watching pods crash in real time, and the challenge of figuring out whether a problem is your configuration or the Azure platform.

This project gave me that perspective. It might give you something useful too.

---

## What It Does

When a problem is detected the agent:

1. **Determines the origin** — is this a configuration issue (your side) or an Azure platform issue (Microsoft's side)?
2. **Auto-remediates if possible** — deletes crashloop pods, adjusts OOM memory limits, restarts deployments, cordons and drains nodes
3. **Emails the admin** if human intervention is needed — with exact steps and kubectl commands
4. **Opens an Azure Support case** and a **GitHub issue on the official AKS repo** for platform-level issues
5. **Documents everything** — SQLite database with full audit trail and Markdown reports

---

## Architecture

```
monitor.py → diagnostics.py → remediation.py
                            → notifier.py
                            → documenter.py
```

| Module | Responsibility |
|---|---|
| `monitor.py` | Collects cluster state every 5 minutes — nodes, pods, deployments, PVCs, events, HPAs, node pools, certificates |
| `diagnostics.py` | Sends problems to Claude AI for diagnosis — returns origin, severity, auto-fixability, root cause, fix steps |
| `remediation.py` | Executes auto-fixes and verifies them after 30 seconds |
| `notifier.py` | HTML email via SMTP, Azure Support cases via REST API, GitHub issues |
| `documenter.py` | SQLite database + Markdown reports |

---

## Stack

- **Language:** Python 3.12
- **AI:** Claude claude-opus-4-6 (Anthropic API)
- **Cluster:** Azure Kubernetes Service
- **Container Registry:** GitHub Container Registry (GHCR)
- **CI/CD:** GitHub Actions (lint/test → build/push → deploy)
- **Database:** SQLite

---

## Prerequisites

- Azure subscription with an AKS cluster
- GitHub account
- Anthropic API key (get one at [console.anthropic.com](https://console.anthropic.com))
- Gmail account with 2FA enabled (for email notifications)

---

## Deployment (Helm — Recommended)

Helm is recommended over raw `kubectl apply` because it manages all resources as a single release, handles upgrades and rollbacks cleanly, and lets you supply secrets at install time without ever writing them to disk.

### One-command install

```bash
helm install aks-ai-agent ./helm \
  --namespace aks-agent \
  --create-namespace \
  --set cluster.name="my-aks-cluster" \
  --set cluster.resourceGroup="my-rg" \
  --set cluster.subscriptionId="YOUR-SUB-ID" \
  --set azure.tenantId="YOUR-TENANT-ID" \
  --set azure.clientId="YOUR-CLIENT-ID" \
  --set azure.clientSecret="YOUR-CLIENT-SECRET" \
  --set anthropic.apiKey="sk-ant-..." \
  --set notifications.adminEmail="you@example.com" \
  --set notifications.smtpPassword="YOUR-GMAIL-APP-PASSWORD" \
  --set github.token="ghp_..."
```

### Recommended: values file

For anything beyond a quick test, keep your values in a file so you aren't passing secrets on the command line.

**`my-values.yaml`** (never commit this file):

```yaml
cluster:
  name: "my-aks-cluster"
  resourceGroup: "my-rg"
  subscriptionId: "00000000-0000-0000-0000-000000000000"

azure:
  tenantId: "00000000-0000-0000-0000-000000000000"
  clientId: "00000000-0000-0000-0000-000000000000"
  clientSecret: "YOUR-CLIENT-SECRET"

anthropic:
  apiKey: "sk-ant-..."

notifications:
  adminEmail: "you@example.com"
  smtpPassword: "YOUR-GMAIL-APP-PASSWORD"
  slackWebhook: ""          # optional

github:
  token: "ghp_..."

monitoring:
  intervalSeconds: 300      # how often the agent polls the cluster

storage:
  size: 1Gi
  storageClass: managed-csi

image:
  repository: ghcr.io/heynand0/aks-ai-agent
  tag: latest
```

Then install with:

```bash
helm install aks-ai-agent ./helm \
  --namespace aks-agent \
  --create-namespace \
  --values my-values.yaml
```

To upgrade after changing values:

```bash
helm upgrade aks-ai-agent ./helm --values my-values.yaml
```

> The raw `kubectl` method still works — see [Deployment (kubectl)](#deployment) below if you prefer it.

---

## Deployment

### 1. Fork and clone the repo

```bash
git clone https://github.com/YOUR-USERNAME/aks-ai-agent.git
cd aks-ai-agent
```

### 2. Update config/config.yaml

Replace all placeholders with your real values:

```yaml
cluster:
  name: "YOUR-AKS-CLUSTER-NAME"
  resource_group: "YOUR-RESOURCE-GROUP"

notifications:
  admin_email: "your-email@gmail.com"
  sender_email: "your-email@gmail.com"

github:
  tracking_repo: "YOUR-GITHUB-USERNAME/aks-ai-agent"
```

### 3. Create an Azure service principal

```bash
MSYS_NO_PATHCONV=1 az ad sp create-for-rbac \
  --name aks-ai-agent-sp \
  --role Contributor \
  --scopes /subscriptions/YOUR-SUBSCRIPTION-ID
```

> **Warning:** `Contributor` is overprivileged for this agent. It grants write access across your entire subscription. For a least-privilege setup, assign **Reader** plus **Azure Kubernetes Service Cluster User Role** instead — that is sufficient for cluster monitoring and kubeconfig retrieval.

> **Windows users:** The `MSYS_NO_PATHCONV=1` prefix is required when using Git Bash to prevent path conversion issues.

Save the output — you'll need `appId`, `password`, and `tenant`.

### 4. Create a Gmail App Password

1. Enable 2-Step Verification at [myaccount.google.com/security](https://myaccount.google.com/security)
2. Generate an app password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Save the password **with spaces** exactly as shown — they are part of the format

### 5. Add GitHub Secrets

Go to your repo → Settings → Secrets and variables → Actions → New repository secret

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | `appId` from Step 3 |
| `AZURE_CLIENT_SECRET` | `password` from Step 3 |
| `AZURE_TENANT_ID` | `tenant` from Step 3 |
| `AZURE_SUBSCRIPTION_ID` | Your Azure subscription ID |
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `ADMIN_EMAIL_PASSWORD` | Gmail app password from Step 4 |
| `GITHUB_TOKEN` | GitHub personal access token |

### 6. Deploy

```bash
git add .
git commit -m "configure: update config for my cluster"
git push
```

The GitHub Actions pipeline handles the rest:
- ✅ Lint and unit tests
- ✅ Build and push Docker image to GHCR
- ✅ Deploy to AKS

---

## Verify It's Running

```bash
kubectl get pods -n aks-agent
kubectl logs -f -n aks-agent -l app=aks-ai-agent
```

You should see:
```
✅ No problems detected. Cluster healthy.
✅ Agent running. Press Ctrl+C to stop.
```

---

## Test It

Deploy a pod that crashes on purpose:

```bash
kubectl run crash-test \
  --image=busybox \
  --restart=Always \
  --namespace=default \
  -- /bin/sh -c "exit 1"
```

Within 5 minutes the agent will detect it, send it to Claude AI for diagnosis, and email you.

Clean up after testing:

```bash
kubectl delete pod crash-test -n default
```

---

## CI/CD Pipeline

The GitHub Actions pipeline has three jobs:

```
Lint & Tests → Build & Push → Deploy to AKS
```

Every push to `master` runs lint and build. The deploy job is **skipped by default** — this lets you use the pipeline for CI (tests + image build) without needing a cluster configured.

To enable deployment, go to your repo **Settings → Secrets and variables → Actions → Variables** and add:

| Variable | Value |
|---|---|
| `DEPLOY_ENABLED` | `true` |

Once set, every push to `master` will deploy to AKS.

---

## Security Notes

- Never commit real values to `config/config.yaml` — use placeholders
- All secrets are stored in GitHub Actions Secrets and injected at deploy time
- The agent runs with least-privilege RBAC inside the cluster
- Rotate credentials if they are ever exposed

---

## Related

- 📝 [Medium Article](https://medium.com/@0H_b0yy/im-an-aks-support-engineer-i-built-an-ai-monitoring-agent-to-see-what-my-customers-see-dfef7fa23971) — Full writeup including every error hit during deployment and what each one taught me
- 🐦 Built by [@HeyNaNd0](https://github.com/HeyNaNd0)

---

## Running the Tests

The test suite covers rule-based problem detection (`TestProblemIdentification` — nodes, pods, PVCs, dedup, memory bump) and AI response parsing (`TestDiagnosticsResponseParsing` — valid JSON, fallback behaviour), all without requiring a real cluster or API key.

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the tests:

```bash
python -m pytest tests/ -v
```

Expected output:

```
tests/test_monitor.py::TestProblemIdentification::test_crashloop_pod_detected PASSED
tests/test_monitor.py::TestProblemIdentification::test_dedup_same_problem_not_doubled PASSED
tests/test_monitor.py::TestProblemIdentification::test_healthy_cluster_returns_no_problems PASSED
tests/test_monitor.py::TestProblemIdentification::test_imagepull_pod_detected PASSED
tests/test_monitor.py::TestProblemIdentification::test_memory_bump_helper PASSED
tests/test_monitor.py::TestProblemIdentification::test_notready_node_detected PASSED
tests/test_monitor.py::TestProblemIdentification::test_oomkilled_pod_detected PASSED
tests/test_monitor.py::TestProblemIdentification::test_unbound_pvc_detected PASSED
tests/test_monitor.py::TestDiagnosticsResponseParsing::test_fallback_diagnosis_never_auto_fixes PASSED
tests/test_monitor.py::TestDiagnosticsResponseParsing::test_valid_json_parsed_correctly PASSED

10 passed
```

> The Pydantic warning about Python 3.14 compatibility (`PydanticDeprecatedSince20`) is harmless and can be ignored — it comes from a transitive dependency and does not affect any agent behaviour.

---

## License

MIT

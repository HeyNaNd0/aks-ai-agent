#!/usr/bin/env bash
# =============================================================================
# scripts/setup.sh — AKS AI Agent interactive setup
# =============================================================================
# Creates an Azure service principal with least-privilege roles (Reader on the
# resource group + Azure Kubernetes Service Cluster User Role on the cluster),
# then prints — or writes — a ready-to-use helm install command.
#
# Usage:
#   chmod +x scripts/setup.sh
#   ./scripts/setup.sh
# =============================================================================
set -euo pipefail

# ── Colour codes ──────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'   # reset

# ── Helper functions ──────────────────────────────────────────────────────────
info()    { echo -e "${CYAN}${BOLD}    →  $*${NC}"; }
success() { echo -e "${GREEN}${BOLD}    ✔  $*${NC}"; }
warn()    { echo -e "${YELLOW}${BOLD}    ⚠  $*${NC}"; }
error()   { echo -e "${RED}${BOLD}    ✖  $*${NC}" >&2; exit 1; }
step()    { echo -e "\n${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
            echo -e "${BOLD}${CYAN}  $*${NC}"
            echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}${BOLD}"
echo "  ╔══════════════════════════════════════════════════════════════╗"
echo "  ║              AKS AI Agent — Interactive Setup               ║"
echo "  ║   Creates service principal · Assigns roles · Helm output   ║"
echo "  ╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Step 1: Prerequisites ─────────────────────────────────────────────────────
step "Step 1 of 4 — Checking prerequisites"
echo ""

check_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" &>/dev/null; then
    error "'${cmd}' is not installed or not on PATH. Install it then re-run this script."
  fi
  success "${cmd} is installed"
}

check_cmd az
check_cmd helm
check_cmd kubectl
check_cmd python3   # used to parse SP JSON output without requiring jq

echo ""
info "Verifying Azure CLI login..."
if ! az account show &>/dev/null; then
  error "Not logged in to Azure. Run 'az login' first, then re-run this script."
fi

AZ_NAME=$(az account show --query name -o tsv)
AZ_ID=$(az account show   --query id   -o tsv)
success "Logged in — subscription: ${BOLD}${AZ_NAME}${NC}${GREEN}${BOLD} (${AZ_ID})"

# ── Step 2: Interactive prompts ───────────────────────────────────────────────
step "Step 2 of 4 — Gathering configuration"
echo ""
echo -e "  ${BOLD}Press Enter to confirm each value. Secrets are hidden as you type.${NC}"
echo ""

read -p  "  Azure Subscription ID : " SUBSCRIPTION_ID
read -p  "  Resource Group name   : " RESOURCE_GROUP
read -p  "  AKS Cluster name      : " CLUSTER_NAME
read -p  "  Admin email address   : " ADMIN_EMAIL
echo ""
read -sp "  Anthropic API key     : " ANTHROPIC_API_KEY;  echo
read -sp "  Gmail App Password    : " GMAIL_APP_PASSWORD; echo
read -sp "  GitHub Token          : " GITHUB_TOKEN;       echo
echo ""

# Validate the resource group and cluster exist before doing anything destructive
info "Validating resource group '${RESOURCE_GROUP}'..."
if ! az group show \
      --name "$RESOURCE_GROUP" \
      --subscription "$SUBSCRIPTION_ID" \
      --output none 2>/dev/null; then
  error "Resource group '${RESOURCE_GROUP}' not found in subscription '${SUBSCRIPTION_ID}'."
fi
success "Resource group confirmed"

info "Validating AKS cluster '${CLUSTER_NAME}'..."
if ! az aks show \
      --name "$CLUSTER_NAME" \
      --resource-group "$RESOURCE_GROUP" \
      --output none 2>/dev/null; then
  error "AKS cluster '${CLUSTER_NAME}' not found in resource group '${RESOURCE_GROUP}'."
fi
success "AKS cluster confirmed"

# ── Step 3: Service principal + role assignments ───────────────────────────────
step "Step 3 of 4 — Creating Azure service principal and assigning roles"
echo ""

RG_SCOPE="/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}"

# Check whether the SP already exists so we can warn the user
EXISTING_SP=$(az ad sp list --display-name "aks-ai-agent-sp" --query "[0].appId" -o tsv 2>/dev/null || true)
if [[ -n "${EXISTING_SP}" ]]; then
  warn "Service principal 'aks-ai-agent-sp' already exists (appId: ${EXISTING_SP})."
  warn "Running create-for-rbac will reset its credentials. Continuing in 5 seconds..."
  sleep 5
fi

info "Creating service principal 'aks-ai-agent-sp' (no default role assigned)..."
# MSYS_NO_PATHCONV=1 prevents Git Bash on Windows from mangling the scope path
SP_JSON=$(MSYS_NO_PATHCONV=1 az ad sp create-for-rbac \
  --name "aks-ai-agent-sp" \
  --skip-assignment \
  --output json 2>/dev/null)

SP_CLIENT_ID=$(echo     "$SP_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['appId'])")
SP_CLIENT_SECRET=$(echo "$SP_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['password'])")
SP_TENANT_ID=$(echo     "$SP_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['tenant'])")

success "Service principal created — appId: ${SP_CLIENT_ID}"

info "Assigning Reader role on resource group '${RESOURCE_GROUP}'..."
MSYS_NO_PATHCONV=1 az role assignment create \
  --assignee    "$SP_CLIENT_ID" \
  --role        "Reader" \
  --scope       "$RG_SCOPE" \
  --output      none
success "Reader role assigned on ${RESOURCE_GROUP}"

info "Retrieving AKS cluster resource ID..."
CLUSTER_ID=$(MSYS_NO_PATHCONV=1 az aks show \
  --name           "$CLUSTER_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query          id \
  --output         tsv)

info "Assigning 'Azure Kubernetes Service Cluster User Role' on cluster '${CLUSTER_NAME}'..."
MSYS_NO_PATHCONV=1 az role assignment create \
  --assignee "$SP_CLIENT_ID" \
  --role     "Azure Kubernetes Service Cluster User Role" \
  --scope    "$CLUSTER_ID" \
  --output   none
success "AKS Cluster User Role assigned on ${CLUSTER_NAME}"

# ── Step 4: Summary and output ────────────────────────────────────────────────
step "Step 4 of 4 — Summary"
echo ""

echo -e "${GREEN}${BOLD}  What was created:${NC}"
echo -e "    • Service principal  : ${BOLD}aks-ai-agent-sp${NC}  (appId: ${SP_CLIENT_ID})"
echo -e "    • Role 1             : ${BOLD}Reader${NC} on resource group ${BOLD}${RESOURCE_GROUP}${NC}"
echo -e "    • Role 2             : ${BOLD}Azure Kubernetes Service Cluster User Role${NC} on ${BOLD}${CLUSTER_NAME}${NC}"
echo ""

HELM_CMD="helm install aks-ai-agent ./helm \\
  --namespace aks-agent \\
  --create-namespace \\
  --set cluster.name=\"${CLUSTER_NAME}\" \\
  --set cluster.resourceGroup=\"${RESOURCE_GROUP}\" \\
  --set cluster.subscriptionId=\"${SUBSCRIPTION_ID}\" \\
  --set azure.tenantId=\"${SP_TENANT_ID}\" \\
  --set azure.clientId=\"${SP_CLIENT_ID}\" \\
  --set azure.clientSecret=\"${SP_CLIENT_SECRET}\" \\
  --set anthropic.apiKey=\"${ANTHROPIC_API_KEY}\" \\
  --set notifications.adminEmail=\"${ADMIN_EMAIL}\" \\
  --set notifications.smtpPassword=\"${GMAIL_APP_PASSWORD}\" \\
  --set github.token=\"${GITHUB_TOKEN}\""

echo -e "${BOLD}  Ready-to-paste helm install command:${NC}"
echo ""
echo -e "${CYAN}${BOLD}  ${HELM_CMD}${NC}"
echo ""

# ── Offer to write my-values.yaml ─────────────────────────────────────────────
echo -e "${BOLD}  Create my-values.yaml with all values pre-filled?${NC}"
read -p  "  [y/N] " CREATE_VALUES
echo ""

if [[ "${CREATE_VALUES,,}" == "y" ]]; then
  cat > my-values.yaml <<EOF
# my-values.yaml — generated by scripts/setup.sh
# !! NEVER COMMIT THIS FILE — it contains real secrets !!

# ── Cluster identity ─────────────────────────────────────────────────────────
cluster:
  name: "${CLUSTER_NAME}"
  resourceGroup: "${RESOURCE_GROUP}"
  subscriptionId: "${SUBSCRIPTION_ID}"

# ── Azure service principal ──────────────────────────────────────────────────
azure:
  tenantId: "${SP_TENANT_ID}"
  clientId: "${SP_CLIENT_ID}"
  clientSecret: "${SP_CLIENT_SECRET}"

# ── Anthropic API ────────────────────────────────────────────────────────────
anthropic:
  apiKey: "${ANTHROPIC_API_KEY}"

# ── Notifications ────────────────────────────────────────────────────────────
notifications:
  adminEmail: "${ADMIN_EMAIL}"
  smtpPassword: "${GMAIL_APP_PASSWORD}"
  slackWebhook: ""   # optional

# ── GitHub ───────────────────────────────────────────────────────────────────
github:
  token: "${GITHUB_TOKEN}"

# ── Monitoring behaviour ─────────────────────────────────────────────────────
monitoring:
  intervalSeconds: 300

# ── Persistent storage ───────────────────────────────────────────────────────
storage:
  size: 1Gi
  storageClass: managed-csi

# ── Container image ──────────────────────────────────────────────────────────
image:
  repository: ghcr.io/heynand0/aks-ai-agent
  tag: latest
EOF
  success "my-values.yaml written"

  # ── .gitignore guard ───────────────────────────────────────────────────────
  GITIGNORE=".gitignore"
  if [[ -f "$GITIGNORE" ]]; then
    if grep -qxF "my-values.yaml" "$GITIGNORE"; then
      success "my-values.yaml is already listed in .gitignore"
    else
      echo "my-values.yaml" >> "$GITIGNORE"
      warn "my-values.yaml was not in .gitignore — added it automatically."
    fi
  else
    echo "my-values.yaml" > "$GITIGNORE"
    warn ".gitignore did not exist — created it and added my-values.yaml."
  fi

  echo ""
  echo -e "${BOLD}  Install with:${NC}"
  echo -e "${CYAN}${BOLD}  helm install aks-ai-agent ./helm \\${NC}"
  echo -e "${CYAN}${BOLD}    --namespace aks-agent \\${NC}"
  echo -e "${CYAN}${BOLD}    --create-namespace \\${NC}"
  echo -e "${CYAN}${BOLD}    --values my-values.yaml${NC}"
  echo ""
  echo -e "${BOLD}  Upgrade later with:${NC}"
  echo -e "${CYAN}${BOLD}  helm upgrade aks-ai-agent ./helm --values my-values.yaml${NC}"
fi

echo ""
success "Setup complete. The agent is ready to deploy."
echo ""

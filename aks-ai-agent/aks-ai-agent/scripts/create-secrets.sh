#!/usr/bin/env bash
# ============================================================
# create-secrets.sh — Safely create Kubernetes secrets
# ============================================================
# This script reads your .env file and creates the K8s secret
# WITHOUT the secret values ever touching a YAML file.
#
# Usage:
#   1. Fill in .env (copy from .env.example)
#   2. chmod +x scripts/create-secrets.sh
#   3. ./scripts/create-secrets.sh
# ============================================================
set -euo pipefail

# Colors for output
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo -e "${YELLOW}═══ AKS AI Agent Secret Setup ═══${NC}"

# ── Load .env ─────────────────────────────────────────────────
ENV_FILE=".env"
if [[ ! -f "$ENV_FILE" ]]; then
    echo -e "${RED}ERROR: .env file not found.${NC}"
    echo "  Copy .env.example to .env and fill in your values."
    exit 1
fi
source "$ENV_FILE"
echo -e "${GREEN}✓ Loaded .env${NC}"

# ── Check required vars ───────────────────────────────────────
REQUIRED_VARS=(
    ANTHROPIC_API_KEY
    AZURE_SUBSCRIPTION_ID
    AZURE_TENANT_ID
    AZURE_CLIENT_ID
    AZURE_CLIENT_SECRET
    ADMIN_EMAIL_PASSWORD
    GITHUB_TOKEN
)

for var in "${REQUIRED_VARS[@]}"; do
    if [[ -z "${!var:-}" ]]; then
        echo -e "${RED}ERROR: $var is not set in .env${NC}"
        exit 1
    fi
done
echo -e "${GREEN}✓ All required variables present${NC}"

# ── Create namespace ──────────────────────────────────────────
kubectl create namespace aks-agent --dry-run=client -o yaml | kubectl apply -f -
echo -e "${GREEN}✓ Namespace aks-agent ready${NC}"

# ── Create secret ─────────────────────────────────────────────
kubectl create secret generic aks-agent-secrets \
    --namespace aks-agent \
    --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
    --from-literal=AZURE_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID" \
    --from-literal=AZURE_TENANT_ID="$AZURE_TENANT_ID" \
    --from-literal=AZURE_CLIENT_ID="$AZURE_CLIENT_ID" \
    --from-literal=AZURE_CLIENT_SECRET="$AZURE_CLIENT_SECRET" \
    --from-literal=ADMIN_EMAIL_PASSWORD="$ADMIN_EMAIL_PASSWORD" \
    --from-literal=GITHUB_TOKEN="$GITHUB_TOKEN" \
    --dry-run=client -o yaml | kubectl apply -f -

echo -e "${GREEN}✓ Secret 'aks-agent-secrets' created/updated${NC}"

# ── Verify ────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}Secret keys stored (values hidden):${NC}"
kubectl get secret aks-agent-secrets -n aks-agent -o jsonpath='{.data}' \
    | python3 -c "import sys, json; [print(f'  ✓ {k}') for k in json.load(sys.stdin).keys()]"

echo ""
echo -e "${GREEN}═══ Secret setup complete! ═══${NC}"
echo "Next: kubectl apply -f k8s/rbac.yaml && kubectl apply -f k8s/deployment.yaml"

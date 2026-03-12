#!/usr/bin/env bash
# =============================================================================
# azure-cleanup.sh
# Cleans up all Azure resources created for an AKS project.
#
# WHAT THIS DELETES:
#   - Everything inside the specified resource group
#     (AKS cluster, ACR, networking, disks, managed identities, etc.)
#   - Your local kubectl context for the cluster
#   - Optionally: your local Azure CLI login session
#
# WHAT IT DOES NOT DELETE:
#   - Your GitHub repository or any local files
#   - GitHub Actions secrets (manual step — instructions printed at the end)
#
# USAGE:
#   chmod +x azure-cleanup.sh
#
#   # Option A: Interactive — script will prompt you for values
#   ./azure-cleanup.sh
#
#   # Option B: Pass values as flags
#   ./azure-cleanup.sh \
#     --resource-group my-aks-rg \
#     --cluster-name   my-aks-cluster \
#     --subscription   xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
#
#   # Add --confirm to actually delete (default is dry run)
#   ./azure-cleanup.sh --resource-group my-rg --cluster-name my-cluster --confirm
#
# REQUIREMENTS:
#   - Azure CLI installed and logged in (https://aka.ms/installazurecli)
#   - kubectl installed (only needed for context cleanup)
# =============================================================================

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Helpers ───────────────────────────────────────────────────────────────────
info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
danger()  { echo -e "${RED}[DELETE]${RESET} $*"; }
header()  { echo -e "\n${BOLD}$*${RESET}"; printf '─%.0s' {1..60}; echo; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }

# ── Default values (overridden by flags or prompts) ───────────────────────────
RESOURCE_GROUP=""
CLUSTER_NAME=""
SUBSCRIPTION_ID=""
DRY_RUN=true

# ── Parse flags ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --resource-group|-g) RESOURCE_GROUP="$2"; shift 2 ;;
    --cluster-name|-c)   CLUSTER_NAME="$2";   shift 2 ;;
    --subscription|-s)   SUBSCRIPTION_ID="$2"; shift 2 ;;
    --confirm)           DRY_RUN=false;        shift   ;;
    --help|-h)
      sed -n '/^# USAGE:/,/^# REQUIREMENTS:/p' "$0" | sed 's/^# //'
      exit 0 ;;
    *) error "Unknown argument: $1. Use --help for usage." ;;
  esac
done

# ── Interactive prompts for any missing values ────────────────────────────────
header "AKS Azure Cleanup Script"

if [[ -z "$RESOURCE_GROUP" ]]; then
  read -r -p "Enter your resource group name (e.g. my-aks-rg): " RESOURCE_GROUP
fi
if [[ -z "$CLUSTER_NAME" ]]; then
  read -r -p "Enter your AKS cluster name (e.g. my-aks-cluster): " CLUSTER_NAME
fi
if [[ -z "$SUBSCRIPTION_ID" ]]; then
  read -r -p "Enter your Azure subscription ID (press Enter to use current default): " SUBSCRIPTION_ID
fi

# Validate required inputs
[[ -z "$RESOURCE_GROUP" ]] && error "Resource group name is required."
[[ -z "$CLUSTER_NAME" ]]   && error "Cluster name is required."

# ── Check prerequisites ───────────────────────────────────────────────────────
header "Checking prerequisites"

if ! command -v az &>/dev/null; then
  error "Azure CLI (az) not found. Install it: https://aka.ms/installazurecli"
fi
success "Azure CLI found: $(az version --query '"azure-cli"' -o tsv 2>/dev/null || echo 'installed')"

if ! az account show &>/dev/null; then
  warn "Not logged in to Azure CLI. Logging in now..."
  az login
fi

# Set subscription if provided
if [[ -n "$SUBSCRIPTION_ID" ]]; then
  az account set --subscription "$SUBSCRIPTION_ID" \
    || error "Could not set subscription '$SUBSCRIPTION_ID'. Check the ID and try again."
fi

ACCOUNT=$(az account show --query "user.name" -o tsv)
ACTIVE_SUB=$(az account show --query "id" -o tsv)
ACTIVE_SUB_NAME=$(az account show --query "name" -o tsv)

success "Logged in as:   $ACCOUNT"
success "Subscription:   $ACTIVE_SUB_NAME ($ACTIVE_SUB)"

# ── Dry run banner ────────────────────────────────────────────────────────────
if [[ "$DRY_RUN" == true ]]; then
  echo ""
  echo -e "${YELLOW}╔══════════════════════════════════════════════════╗${RESET}"
  echo -e "${YELLOW}║  DRY RUN — Nothing will be deleted               ║${RESET}"
  echo -e "${YELLOW}║  Re-run with --confirm to actually delete         ║${RESET}"
  echo -e "${YELLOW}╚══════════════════════════════════════════════════╝${RESET}"
fi

# ── Step 1: Discover resources in the resource group ─────────────────────────
header "Step 1: Resources in '$RESOURCE_GROUP'"

if ! az group show --name "$RESOURCE_GROUP" &>/dev/null; then
  warn "Resource group '$RESOURCE_GROUP' does not exist."
  warn "It may have already been deleted, or the name may be wrong."
else
  info "The following resources will be deleted:"
  az resource list \
    --resource-group "$RESOURCE_GROUP" \
    --query "[].{Name:name, Type:type, Location:location}" \
    --output table 2>/dev/null || warn "Could not list resources."
  echo ""
  danger "ALL resources above (and the resource group itself) will be permanently deleted."
fi

# ── Step 2: kubectl context ───────────────────────────────────────────────────
header "Step 2: Local kubectl context"

if command -v kubectl &>/dev/null && kubectl config get-contexts 2>/dev/null | grep -q "$CLUSTER_NAME"; then
  info "Found kubectl context for cluster: $CLUSTER_NAME"
  danger "This kubectl context will be removed from ~/.kube/config"
else
  info "No kubectl context found for '$CLUSTER_NAME' — nothing to remove."
fi

# ── Dry run exit ──────────────────────────────────────────────────────────────
if [[ "$DRY_RUN" == true ]]; then
  echo ""
  info "Dry run complete. To delete everything listed above, run:"
  echo ""
  echo -e "  ${BOLD}./azure-cleanup.sh --resource-group $RESOURCE_GROUP --cluster-name $CLUSTER_NAME --confirm${RESET}"
  echo ""
  exit 0
fi

# ── Final confirmation ────────────────────────────────────────────────────────
header "Final confirmation"
echo -e "${RED}${BOLD}WARNING: This permanently deletes all resources listed above.${RESET}"
echo -e "${RED}This action CANNOT be undone.${RESET}"
echo ""
read -r -p "Type the resource group name to confirm: " CONFIRM_NAME

if [[ "$CONFIRM_NAME" != "$RESOURCE_GROUP" ]]; then
  echo ""
  warn "Input did not match '$RESOURCE_GROUP'. Aborting — nothing was deleted."
  exit 1
fi

# ── Step 3: Delete resource group ────────────────────────────────────────────
header "Step 3: Deleting resource group '$RESOURCE_GROUP'"

if az group show --name "$RESOURCE_GROUP" &>/dev/null; then
  info "Submitting deletion request (Azure handles this async — takes 5–15 min)..."
  az group delete \
    --name "$RESOURCE_GROUP" \
    --yes \
    --no-wait
  success "Deletion request submitted for '$RESOURCE_GROUP'."
  info "Monitor progress: az group show --name $RESOURCE_GROUP"
  info "(A 'ResourceGroupNotFound' error means it's fully deleted — that's expected.)"
else
  info "Resource group '$RESOURCE_GROUP' not found — already deleted."
fi

# ── Step 4: Remove kubectl context ───────────────────────────────────────────
header "Step 4: Cleaning up local kubectl context"

if command -v kubectl &>/dev/null; then
  if kubectl config get-contexts 2>/dev/null | grep -q "$CLUSTER_NAME"; then
    kubectl config delete-context "$CLUSTER_NAME" 2>/dev/null \
      && success "Removed kubectl context: $CLUSTER_NAME" \
      || warn "Could not remove context (may already be gone)."
    kubectl config delete-cluster "$CLUSTER_NAME" 2>/dev/null || true
  else
    info "No kubectl context to remove."
  fi
else
  info "kubectl not found — skipping context cleanup."
fi

# ── Step 5: Optional logout ───────────────────────────────────────────────────
header "Step 5: Azure CLI logout"
read -r -p "Log out of Azure CLI? (y/N): " LOGOUT_CONFIRM
if [[ "${LOGOUT_CONFIRM,,}" == "y" ]]; then
  az logout
  success "Logged out of Azure CLI."
else
  info "Keeping Azure CLI session active."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
header "Cleanup complete"
success "Resource group deletion is running in the background."
echo ""
echo -e "${BOLD}Manual steps still needed:${RESET}"
echo ""
echo "  1. GitHub Actions secrets — remove from your repo:"
echo "     https://github.com/<your-username>/<your-repo>/settings/secrets/actions"
echo "     Common secrets to delete:"
echo "       AZURE_CREDENTIALS, AZURE_TENANT_ID, AZURE_CLIENT_ID,"
echo "       AZURE_CLIENT_SECRET, AZURE_SUBSCRIPTION_ID,"
echo "       ACR_NAME, ANTHROPIC_API_KEY"
echo ""
echo "  2. Service principal (if you created one):"
echo "     az ad sp list --display-name <your-sp-name> --query '[].appId'"
echo "     az ad sp delete --id <appId>"
echo ""
echo "  3. Verify deletion is complete (run after ~15 min):"
echo "     az group show --name $RESOURCE_GROUP"
echo "     (A 'ResourceGroupNotFound' error means it's fully deleted)"
echo ""

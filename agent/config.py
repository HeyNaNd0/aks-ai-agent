"""
agent/config.py — Configuration Loader
=======================================
Reads config/config.yaml and exposes all settings as typed attributes.
Also reads secrets from environment variables (never from the YAML file).
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional

import yaml


@dataclass
class AgentConfig:
    """
    Single source of truth for all agent settings.

    Secrets (API keys, passwords) come ONLY from environment variables.
    Non-secret settings come from config.yaml (which is safe to commit).
    """

    # ── Raw config dict ───────────────────────────────────────────────────
    _raw: dict = field(default_factory=dict, repr=False)

    def __init__(self, config_path: str = "config/config.yaml"):
        with open(config_path, "r") as f:
            self._raw = yaml.safe_load(f)
        self._validate()

    def _validate(self):
        required_env = [
            "ANTHROPIC_API_KEY",
            "AZURE_SUBSCRIPTION_ID",
            "AZURE_TENANT_ID",
            "AZURE_CLIENT_ID",
            "AZURE_CLIENT_SECRET",
            "ADMIN_EMAIL_PASSWORD",
        ]
        missing = [v for v in required_env if not os.environ.get(v)]
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {missing}\n"
                "See .env.example for the full list."
            )

    # ── Cluster settings ──────────────────────────────────────────────────

    @property
    def kubeconfig_path(self) -> Optional[str]:
        """Path to kubeconfig file. None = use in-cluster config."""
        return os.environ.get("KUBECONFIG") or self._raw.get("cluster", {}).get("kubeconfig_path")

    @property
    def cluster_name(self) -> str:
        return self._raw["cluster"]["name"]

    @property
    def resource_group(self) -> str:
        return self._raw["cluster"]["resource_group"]

    @property
    def monitoring_interval_seconds(self) -> int:
        return int(self._raw.get("monitoring", {}).get("interval_seconds", 300))

    @property
    def monitored_namespaces(self) -> List[str]:
        return self._raw.get("monitoring", {}).get("namespaces", ["default"])

    @property
    def cpu_threshold_percent(self) -> float:
        return float(self._raw.get("thresholds", {}).get("cpu_percent", 85.0))

    @property
    def memory_threshold_percent(self) -> float:
        return float(self._raw.get("thresholds", {}).get("memory_percent", 85.0))

    @property
    def pod_restart_threshold(self) -> int:
        return int(self._raw.get("thresholds", {}).get("pod_restart_count", 5))

    @property
    def node_not_ready_threshold_seconds(self) -> int:
        return int(self._raw.get("thresholds", {}).get("node_not_ready_seconds", 120))

    # ── Azure settings ────────────────────────────────────────────────────

    @property
    def azure_subscription_id(self) -> str:
        return os.environ["AZURE_SUBSCRIPTION_ID"]

    @property
    def azure_tenant_id(self) -> str:
        return os.environ["AZURE_TENANT_ID"]

    @property
    def azure_client_id(self) -> str:
        return os.environ["AZURE_CLIENT_ID"]

    @property
    def azure_client_secret(self) -> str:
        return os.environ["AZURE_CLIENT_SECRET"]

    @property
    def azure_support_plan(self) -> str:
        """Azure support plan name (e.g. 'Developer', 'Standard', 'Professional Direct')."""
        return self._raw.get("azure", {}).get("support_plan", "Developer")

    # ── Anthropic / Claude AI settings ───────────────────────────────────

    @property
    def anthropic_api_key(self) -> str:
        return os.environ["ANTHROPIC_API_KEY"]

    @property
    def claude_model(self) -> str:
        return self._raw.get("ai", {}).get("model", "claude-opus-4-6")

    @property
    def ai_max_tokens(self) -> int:
        return int(self._raw.get("ai", {}).get("max_tokens", 4096))

    # ── Email settings ────────────────────────────────────────────────────

    @property
    def admin_email(self) -> str:
        return self._raw["notifications"]["admin_email"]

    @property
    def sender_email(self) -> str:
        return self._raw["notifications"]["sender_email"]

    @property
    def smtp_host(self) -> str:
        return self._raw.get("notifications", {}).get("smtp_host", "smtp.gmail.com")

    @property
    def smtp_port(self) -> int:
        return int(self._raw.get("notifications", {}).get("smtp_port", 587))

    @property
    def email_password(self) -> str:
        return os.environ["ADMIN_EMAIL_PASSWORD"]

    # ── GitHub settings ───────────────────────────────────────────────────

    @property
    def github_token(self) -> str:
        return os.environ.get("GITHUB_TOKEN", "")

    @property
    def github_repo(self) -> str:
        """
        Repository to open issues in.
        For AKS platform bugs: Azure/AKS
        For your own tracking: your-org/your-repo
        """
        return self._raw.get("github", {}).get("issues_repo", "Azure/AKS")

    @property
    def github_tracking_repo(self) -> str:
        """Your own repo for internal tracking issues."""
        return self._raw.get("github", {}).get("tracking_repo", "")

    # ── Database settings ─────────────────────────────────────────────────

    @property
    def db_path(self) -> str:
        return self._raw.get("database", {}).get("path", "data/issues.db")

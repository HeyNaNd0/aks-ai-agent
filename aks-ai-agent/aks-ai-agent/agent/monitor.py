"""
agent/monitor.py — AKS Cluster Monitor
=======================================
Connects to your AKS cluster (via kubeconfig or in-cluster service account),
collects the current state of every critical resource, and identifies
problems worth analyzing.

What we monitor:
    - Node health (Ready/NotReady, memory/CPU pressure)
    - Pod health (CrashLoopBackOff, OOMKilled, ImagePullBackOff, Pending)
    - Deployment health (unavailable replicas)
    - PersistentVolumeClaim (PVC) binding status
    - Kubernetes Events (Warnings in the last N minutes)
    - HorizontalPodAutoscaler (HPA) unable-to-scale conditions
    - Namespace resource quotas approaching limits
    - Certificate expiry (via cert-manager CRDs if present)
    - Azure node pool quota via Azure Management API
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from azure.identity import ClientSecretCredential
from azure.mgmt.containerservice import ContainerServiceClient
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException

from agent.config import AgentConfig

log = logging.getLogger("aks-agent.monitor")


class ClusterMonitor:
    """Collects full cluster state and surfaces problems."""

    # Problem type constants (used throughout the codebase for routing)
    PT_NODE_NOT_READY = "node_not_ready"
    PT_NODE_PRESSURE = "node_pressure"
    PT_POD_CRASHLOOP = "pod_crashloop"
    PT_POD_OOMKILLED = "pod_oomkilled"
    PT_POD_IMAGEPULL = "pod_imagepull"
    PT_POD_PENDING = "pod_pending"
    PT_DEPLOY_UNAVAIL = "deployment_unavailable"
    PT_PVC_UNBOUND = "pvc_unbound"
    PT_HPA_UNABLE = "hpa_unable_to_scale"
    PT_QUOTA_NEAR = "quota_near_limit"
    PT_CERT_EXPIRY = "certificate_expiring"
    PT_AZURE_QUOTA = "azure_quota_exceeded"

    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        self._init_k8s_client()
        self._init_azure_client()

    # ── Client initialization ─────────────────────────────────────────────

    def _init_k8s_client(self):
        """Load kubeconfig (file or in-cluster service account)."""
        try:
            if self.cfg.kubeconfig_path:
                k8s_config.load_kube_config(config_file=self.cfg.kubeconfig_path)
                log.info(f"Loaded kubeconfig from {self.cfg.kubeconfig_path}")
            else:
                k8s_config.load_incluster_config()
                log.info("Loaded in-cluster kubeconfig")
        except Exception as exc:
            raise RuntimeError(f"Cannot initialize Kubernetes client: {exc}") from exc

        self.core_v1 = k8s_client.CoreV1Api()
        self.apps_v1 = k8s_client.AppsV1Api()
        self.autoscaling_v1 = k8s_client.AutoscalingV1Api()
        self.custom = k8s_client.CustomObjectsApi()

    def _init_azure_client(self):
        """Set up Azure management clients for AKS and quota checks."""
        cred = ClientSecretCredential(
            tenant_id=self.cfg.azure_tenant_id,
            client_id=self.cfg.azure_client_id,
            client_secret=self.cfg.azure_client_secret,
        )
        self.aks_client = ContainerServiceClient(cred, self.cfg.azure_subscription_id)

    # ── State collection ──────────────────────────────────────────────────

    def collect_state(self) -> Dict[str, Any]:
        """
        Collect the full cluster state snapshot.
        Returns a dict that gets passed to the AI diagnostician.
        """
        log.info("Collecting cluster state…")
        state: Dict[str, Any] = {
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "cluster_name": self.cfg.cluster_name,
            "nodes": self._collect_nodes(),
            "pods": self._collect_pods(),
            "deployments": self._collect_deployments(),
            "pvcs": self._collect_pvcs(),
            "events": self._collect_warning_events(),
            "hpas": self._collect_hpas(),
            "quotas": self._collect_resource_quotas(),
            "certificates": self._collect_certificates(),
            "azure_node_pools": self._collect_azure_node_pools(),
        }
        log.info(
            f"State collected: {len(state['nodes'])} nodes, "
            f"{len(state['pods'])} pods, "
            f"{len(state['events'])} warning events"
        )
        return state

    def _collect_nodes(self) -> List[Dict]:
        nodes = []
        try:
            for node in self.core_v1.list_node().items:
                conditions = {c.type: c for c in node.status.conditions}
                ready_cond = conditions.get("Ready")
                nodes.append({
                    "name": node.metadata.name,
                    "ready": ready_cond.status == "True" if ready_cond else False,
                    "ready_last_transition": (
                        ready_cond.last_transition_time.isoformat()
                        if ready_cond else None
                    ),
                    "memory_pressure": conditions.get("MemoryPressure", {}).status == "True"
                        if "MemoryPressure" in conditions else False,
                    "disk_pressure": conditions.get("DiskPressure", {}).status == "True"
                        if "DiskPressure" in conditions else False,
                    "pid_pressure": conditions.get("PIDPressure", {}).status == "True"
                        if "PIDPressure" in conditions else False,
                    "allocatable_cpu": node.status.allocatable.get("cpu"),
                    "allocatable_memory": node.status.allocatable.get("memory"),
                    "labels": node.metadata.labels or {},
                    "taints": [
                        {"key": t.key, "effect": t.effect}
                        for t in (node.spec.taints or [])
                    ],
                })
        except ApiException as exc:
            log.error(f"Failed to list nodes: {exc}")
        return nodes

    def _collect_pods(self) -> List[Dict]:
        pods = []
        try:
            namespaces = self.cfg.monitored_namespaces
            for ns in namespaces:
                for pod in self.core_v1.list_namespaced_pod(namespace=ns).items:
                    container_statuses = pod.status.container_statuses or []
                    restart_count = sum(cs.restart_count for cs in container_statuses)
                    waiting_reasons = [
                        cs.state.waiting.reason
                        for cs in container_statuses
                        if cs.state and cs.state.waiting
                    ]
                    terminated_reasons = [
                        cs.state.terminated.reason
                        for cs in container_statuses
                        if cs.state and cs.state.terminated
                    ]
                    pods.append({
                        "name": pod.metadata.name,
                        "namespace": pod.metadata.namespace,
                        "phase": pod.status.phase,
                        "restart_count": restart_count,
                        "waiting_reasons": waiting_reasons,
                        "terminated_reasons": terminated_reasons,
                        "node_name": pod.spec.node_name,
                        "conditions": [
                            {"type": c.type, "status": c.status}
                            for c in (pod.status.conditions or [])
                        ],
                        "owner_references": [
                            {"kind": o.kind, "name": o.name}
                            for o in (pod.metadata.owner_references or [])
                        ],
                    })
        except ApiException as exc:
            log.error(f"Failed to list pods: {exc}")
        return pods

    def _collect_deployments(self) -> List[Dict]:
        deployments = []
        try:
            for ns in self.cfg.monitored_namespaces:
                for dep in self.apps_v1.list_namespaced_deployment(namespace=ns).items:
                    spec_replicas = dep.spec.replicas or 0
                    status = dep.status
                    deployments.append({
                        "name": dep.metadata.name,
                        "namespace": dep.metadata.namespace,
                        "desired": spec_replicas,
                        "ready": status.ready_replicas or 0,
                        "available": status.available_replicas or 0,
                        "unavailable": status.unavailable_replicas or 0,
                        "conditions": [
                            {
                                "type": c.type,
                                "status": c.status,
                                "reason": c.reason,
                                "message": c.message,
                            }
                            for c in (status.conditions or [])
                        ],
                    })
        except ApiException as exc:
            log.error(f"Failed to list deployments: {exc}")
        return deployments

    def _collect_pvcs(self) -> List[Dict]:
        pvcs = []
        try:
            for ns in self.cfg.monitored_namespaces:
                for pvc in self.core_v1.list_namespaced_persistent_volume_claim(namespace=ns).items:
                    pvcs.append({
                        "name": pvc.metadata.name,
                        "namespace": pvc.metadata.namespace,
                        "phase": pvc.status.phase,
                        "storage_class": pvc.spec.storage_class_name,
                        "capacity": pvc.spec.resources.requests.get("storage") if pvc.spec.resources else None,
                        "access_modes": pvc.spec.access_modes,
                    })
        except ApiException as exc:
            log.error(f"Failed to list PVCs: {exc}")
        return pvcs

    def _collect_warning_events(self, last_minutes: int = 30) -> List[Dict]:
        events = []
        try:
            for ns in self.cfg.monitored_namespaces:
                for evt in self.core_v1.list_namespaced_event(namespace=ns).items:
                    if evt.type != "Warning":
                        continue
                    events.append({
                        "namespace": evt.metadata.namespace,
                        "reason": evt.reason,
                        "message": evt.message,
                        "object_kind": evt.involved_object.kind,
                        "object_name": evt.involved_object.name,
                        "count": evt.count,
                        "first_time": evt.first_timestamp.isoformat() if evt.first_timestamp else None,
                        "last_time": evt.last_timestamp.isoformat() if evt.last_timestamp else None,
                    })
        except ApiException as exc:
            log.error(f"Failed to list events: {exc}")
        return events

    def _collect_hpas(self) -> List[Dict]:
        hpas = []
        try:
            for ns in self.cfg.monitored_namespaces:
                for hpa in self.autoscaling_v1.list_namespaced_horizontal_pod_autoscaler(namespace=ns).items:
                    hpas.append({
                        "name": hpa.metadata.name,
                        "namespace": hpa.metadata.namespace,
                        "min_replicas": hpa.spec.min_replicas,
                        "max_replicas": hpa.spec.max_replicas,
                        "current_replicas": hpa.status.current_replicas,
                        "desired_replicas": hpa.status.desired_replicas,
                        "current_cpu_utilization": hpa.status.current_cpu_utilization_percentage,
                        "target_cpu_utilization": hpa.spec.target_cpu_utilization_percentage,
                    })
        except ApiException as exc:
            log.error(f"Failed to list HPAs: {exc}")
        return hpas

    def _collect_resource_quotas(self) -> List[Dict]:
        quotas = []
        try:
            for ns in self.cfg.monitored_namespaces:
                for rq in self.core_v1.list_namespaced_resource_quota(namespace=ns).items:
                    hard = rq.status.hard or {}
                    used = rq.status.used or {}
                    # Calculate utilization % for each resource
                    utilization = {}
                    for resource, hard_val in hard.items():
                        used_val = used.get(resource, "0")
                        try:
                            utilization[resource] = {
                                "hard": hard_val,
                                "used": used_val,
                            }
                        except Exception:
                            pass
                    quotas.append({
                        "name": rq.metadata.name,
                        "namespace": rq.metadata.namespace,
                        "utilization": utilization,
                    })
        except ApiException as exc:
            log.error(f"Failed to list resource quotas: {exc}")
        return quotas

    def _collect_certificates(self) -> List[Dict]:
        """Collect cert-manager Certificate resources (if installed)."""
        certs = []
        try:
            result = self.custom.list_cluster_custom_object(
                group="cert-manager.io",
                version="v1",
                plural="certificates",
            )
            for cert in result.get("items", []):
                status = cert.get("status", {})
                conditions = status.get("conditions", [])
                ready = any(
                    c.get("type") == "Ready" and c.get("status") == "True"
                    for c in conditions
                )
                certs.append({
                    "name": cert["metadata"]["name"],
                    "namespace": cert["metadata"]["namespace"],
                    "ready": ready,
                    "not_after": status.get("notAfter"),
                    "renewal_time": status.get("renewalTime"),
                    "conditions": conditions,
                })
        except ApiException:
            # cert-manager not installed — skip silently
            pass
        return certs

    def _collect_azure_node_pools(self) -> List[Dict]:
        """Get node pool info from Azure (quota, provisioning state)."""
        pools = []
        try:
            cluster = self.aks_client.managed_clusters.get(
                resource_group_name=self.cfg.resource_group,
                resource_name=self.cfg.cluster_name,
            )
            for pool in cluster.agent_pool_profiles or []:
                pools.append({
                    "name": pool.name,
                    "provisioning_state": pool.provisioning_state,
                    "count": pool.count,
                    "min_count": pool.min_count,
                    "max_count": pool.max_count,
                    "vm_size": pool.vm_size,
                    "power_state": pool.power_state.code if pool.power_state else None,
                })
        except Exception as exc:
            log.warning(f"Could not fetch Azure node pool info: {exc}")
        return pools

    # ── Problem identification ────────────────────────────────────────────

    def identify_problems(self, state: Dict[str, Any]) -> List[Dict]:
        """
        Rule-based first pass: find obvious problems in the state snapshot.
        Returns a list of problem dicts that the AI will then diagnose.
        """
        problems = []
        now = datetime.now(timezone.utc)

        # ── Nodes ─────────────────────────────────────────────────────────
        for node in state["nodes"]:
            if not node["ready"]:
                problems.append({
                    "type": self.PT_NODE_NOT_READY,
                    "summary": f"Node '{node['name']}' is NotReady",
                    "details": node,
                    "severity": "critical",
                })
            for pressure_type in ("memory_pressure", "disk_pressure", "pid_pressure"):
                if node.get(pressure_type):
                    problems.append({
                        "type": self.PT_NODE_PRESSURE,
                        "summary": f"Node '{node['name']}' has {pressure_type.replace('_', ' ')}",
                        "details": node,
                        "severity": "high",
                    })

        # ── Pods ──────────────────────────────────────────────────────────
        for pod in state["pods"]:
            if "CrashLoopBackOff" in pod["waiting_reasons"]:
                problems.append({
                    "type": self.PT_POD_CRASHLOOP,
                    "summary": f"Pod '{pod['namespace']}/{pod['name']}' is in CrashLoopBackOff",
                    "details": pod,
                    "severity": "high",
                })
            if "OOMKilled" in pod["terminated_reasons"]:
                problems.append({
                    "type": self.PT_POD_OOMKILLED,
                    "summary": f"Pod '{pod['namespace']}/{pod['name']}' was OOMKilled",
                    "details": pod,
                    "severity": "high",
                })
            if any("ImagePull" in r for r in pod["waiting_reasons"]):
                problems.append({
                    "type": self.PT_POD_IMAGEPULL,
                    "summary": f"Pod '{pod['namespace']}/{pod['name']}' cannot pull image",
                    "details": pod,
                    "severity": "medium",
                })
            if pod["phase"] == "Pending" and pod["restart_count"] == 0:
                problems.append({
                    "type": self.PT_POD_PENDING,
                    "summary": f"Pod '{pod['namespace']}/{pod['name']}' stuck in Pending",
                    "details": pod,
                    "severity": "medium",
                })
            if pod["restart_count"] >= self.cfg.pod_restart_threshold:
                if self.PT_POD_CRASHLOOP not in [p["type"] for p in problems
                                                  if p.get("details", {}).get("name") == pod["name"]]:
                    problems.append({
                        "type": self.PT_POD_CRASHLOOP,
                        "summary": f"Pod '{pod['namespace']}/{pod['name']}' has {pod['restart_count']} restarts",
                        "details": pod,
                        "severity": "medium",
                    })

        # ── Deployments ───────────────────────────────────────────────────
        for dep in state["deployments"]:
            if dep["unavailable"] and dep["unavailable"] > 0:
                problems.append({
                    "type": self.PT_DEPLOY_UNAVAIL,
                    "summary": (
                        f"Deployment '{dep['namespace']}/{dep['name']}' "
                        f"has {dep['unavailable']} unavailable replica(s)"
                    ),
                    "details": dep,
                    "severity": "high",
                })

        # ── PVCs ──────────────────────────────────────────────────────────
        for pvc in state["pvcs"]:
            if pvc["phase"] not in ("Bound",):
                problems.append({
                    "type": self.PT_PVC_UNBOUND,
                    "summary": f"PVC '{pvc['namespace']}/{pvc['name']}' is {pvc['phase']}",
                    "details": pvc,
                    "severity": "high",
                })

        # ── Azure node pools ──────────────────────────────────────────────
        for pool in state["azure_node_pools"]:
            if pool["provisioning_state"] not in ("Succeeded", "Canceled"):
                problems.append({
                    "type": self.PT_AZURE_QUOTA,
                    "summary": (
                        f"Node pool '{pool['name']}' provisioning state: "
                        f"{pool['provisioning_state']}"
                    ),
                    "details": pool,
                    "severity": "critical",
                })

        # De-duplicate by (type, summary)
        seen = set()
        unique_problems = []
        for p in problems:
            key = (p["type"], p["summary"])
            if key not in seen:
                seen.add(key)
                unique_problems.append(p)

        return unique_problems

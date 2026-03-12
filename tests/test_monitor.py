"""
tests/test_monitor.py — Unit tests for the cluster monitor
===========================================================
These tests use mocking to avoid needing a real Kubernetes cluster.
Run with: pytest tests/ -v
"""

import json
import unittest
from unittest.mock import MagicMock, patch

# Mock the Kubernetes config loading before importing our module
with patch("kubernetes.config.load_kube_config"):
    with patch("kubernetes.config.load_incluster_config"):
        with patch("azure.identity.ClientSecretCredential"):
            with patch("azure.mgmt.containerservice.ContainerServiceClient"):
                import os
                # Set minimum required env vars for config loading
                os.environ.setdefault("ANTHROPIC_API_KEY", "test")
                os.environ.setdefault("AZURE_SUBSCRIPTION_ID", "test")
                os.environ.setdefault("AZURE_TENANT_ID", "test")
                os.environ.setdefault("AZURE_CLIENT_ID", "test")
                os.environ.setdefault("AZURE_CLIENT_SECRET", "test")
                os.environ.setdefault("ADMIN_EMAIL_PASSWORD", "test")

                from agent.monitor import ClusterMonitor


class TestProblemIdentification(unittest.TestCase):
    """Test the rule-based problem detection logic."""

    def _make_monitor(self):
        """Create a monitor with a mocked config."""
        cfg = MagicMock()
        cfg.pod_restart_threshold = 5
        cfg.node_not_ready_threshold_seconds = 120
        cfg.monitored_namespaces = ["default"]
        cfg.cluster_name = "test-cluster"
        cfg.resource_group = "test-rg"
        cfg.azure_tenant_id = "test"
        cfg.azure_client_id = "test"
        cfg.azure_client_secret = "test"
        cfg.azure_subscription_id = "test"
        cfg.kubeconfig_path = "/fake/path"

        with patch("kubernetes.config.load_kube_config"):
            with patch("azure.identity.ClientSecretCredential"):
                with patch("azure.mgmt.containerservice.ContainerServiceClient"):
                    monitor = ClusterMonitor.__new__(ClusterMonitor)
                    monitor.cfg = cfg
                    monitor.core_v1 = MagicMock()
                    monitor.apps_v1 = MagicMock()
                    monitor.autoscaling_v1 = MagicMock()
                    monitor.custom = MagicMock()
                    monitor.aks_client = MagicMock()
                    return monitor

    def _make_state(self, **overrides):
        """Create a minimal healthy cluster state."""
        state = {
            "collected_at": "2024-01-01T00:00:00Z",
            "cluster_name": "test-cluster",
            "nodes": [],
            "pods": [],
            "deployments": [],
            "pvcs": [],
            "events": [],
            "hpas": [],
            "quotas": [],
            "certificates": [],
            "azure_node_pools": [],
        }
        state.update(overrides)
        return state

    def test_healthy_cluster_returns_no_problems(self):
        monitor = self._make_monitor()
        state = self._make_state()
        problems = monitor.identify_problems(state)
        self.assertEqual(problems, [])

    def test_notready_node_detected(self):
        monitor = self._make_monitor()
        state = self._make_state(nodes=[{
            "name": "node-1",
            "ready": False,
            "ready_last_transition": "2024-01-01T00:00:00",
            "memory_pressure": False,
            "disk_pressure": False,
            "pid_pressure": False,
            "labels": {},
            "taints": [],
        }])
        problems = monitor.identify_problems(state)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["type"], ClusterMonitor.PT_NODE_NOT_READY)
        self.assertEqual(problems[0]["severity"], "critical")

    def test_crashloop_pod_detected(self):
        monitor = self._make_monitor()
        state = self._make_state(pods=[{
            "name": "bad-pod",
            "namespace": "default",
            "phase": "Running",
            "restart_count": 3,
            "waiting_reasons": ["CrashLoopBackOff"],
            "terminated_reasons": [],
            "node_name": "node-1",
            "conditions": [],
            "owner_references": [{"kind": "ReplicaSet", "name": "my-deploy-abc"}],
        }])
        problems = monitor.identify_problems(state)
        self.assertTrue(any(p["type"] == ClusterMonitor.PT_POD_CRASHLOOP for p in problems))

    def test_oomkilled_pod_detected(self):
        monitor = self._make_monitor()
        state = self._make_state(pods=[{
            "name": "oom-pod",
            "namespace": "default",
            "phase": "Running",
            "restart_count": 2,
            "waiting_reasons": [],
            "terminated_reasons": ["OOMKilled"],
            "node_name": "node-1",
            "conditions": [],
            "owner_references": [],
        }])
        problems = monitor.identify_problems(state)
        self.assertTrue(any(p["type"] == ClusterMonitor.PT_POD_OOMKILLED for p in problems))

    def test_imagepull_pod_detected(self):
        monitor = self._make_monitor()
        state = self._make_state(pods=[{
            "name": "bad-image-pod",
            "namespace": "default",
            "phase": "Pending",
            "restart_count": 0,
            "waiting_reasons": ["ImagePullBackOff"],
            "terminated_reasons": [],
            "node_name": None,
            "conditions": [],
            "owner_references": [],
        }])
        problems = monitor.identify_problems(state)
        self.assertTrue(any(p["type"] == ClusterMonitor.PT_POD_IMAGEPULL for p in problems))

    def test_unbound_pvc_detected(self):
        monitor = self._make_monitor()
        state = self._make_state(pvcs=[{
            "name": "my-pvc",
            "namespace": "default",
            "phase": "Pending",
            "storage_class": "managed-csi",
            "capacity": "10Gi",
            "access_modes": ["ReadWriteOnce"],
        }])
        problems = monitor.identify_problems(state)
        self.assertTrue(any(p["type"] == ClusterMonitor.PT_PVC_UNBOUND for p in problems))

    def test_dedup_same_problem_not_doubled(self):
        """Two nodes with same issue should produce one problem each, not duplicates."""
        monitor = self._make_monitor()
        state = self._make_state(nodes=[
            {"name": "node-1", "ready": False, "ready_last_transition": None,
             "memory_pressure": False, "disk_pressure": False, "pid_pressure": False,
             "labels": {}, "taints": []},
            {"name": "node-1", "ready": False, "ready_last_transition": None,
             "memory_pressure": False, "disk_pressure": False, "pid_pressure": False,
             "labels": {}, "taints": []},
        ])
        problems = monitor.identify_problems(state)
        node_problems = [p for p in problems if p["type"] == ClusterMonitor.PT_NODE_NOT_READY]
        self.assertEqual(len(node_problems), 1)

    def test_memory_bump_helper(self):
        from agent.remediation import Remediator
        self.assertEqual(Remediator._bump_memory("256Mi", 1.5), "384Mi")
        self.assertEqual(Remediator._bump_memory("1Gi", 2.0), "2Gi")
        self.assertEqual(Remediator._bump_memory("512Mi", 1.0), "512Mi")


class TestDiagnosticsResponseParsing(unittest.TestCase):
    """Test the AI response parser handles various edge cases."""

    def _make_diagnostician(self):
        from agent.diagnostics import Diagnostician
        cfg = MagicMock()
        cfg.anthropic_api_key = "test"
        cfg.claude_model = "claude-opus-4-6"
        cfg.ai_max_tokens = 4096
        diag = Diagnostician.__new__(Diagnostician)
        diag.cfg = cfg
        diag.client = MagicMock()
        return diag

    def test_valid_json_parsed_correctly(self):
        diag = self._make_diagnostician()
        valid_response = json.dumps({
            "origin": "configuration",
            "severity": "high",
            "auto_fixable": True,
            "confidence": 0.92,
            "root_cause": "Memory limit too low",
            "fix_steps": ["Step 1", "Step 2"],
            "kubectl_commands": ["kubectl describe pod bad-pod"],
            "human_steps_if_needed": [],
            "platform_evidence": None,
            "documentation_tags": ["oom", "memory"],
            "estimated_fix_time_minutes": 5,
        })
        problem = {"type": "pod_oomkilled", "summary": "test", "severity": "high"}
        result = diag._parse_response(valid_response, problem)
        self.assertEqual(result["origin"], "configuration")
        self.assertTrue(result["auto_fixable"])
        self.assertEqual(result["confidence"], 0.92)

    def test_fallback_diagnosis_never_auto_fixes(self):
        diag = self._make_diagnostician()
        problem = {"type": "pod_crashloop", "summary": "test", "severity": "high"}
        result = diag._fallback_diagnosis(problem, "API timeout")
        self.assertFalse(result["auto_fixable"])
        self.assertEqual(result["origin"], "ambiguous")
        self.assertEqual(result["confidence"], 0.0)


if __name__ == "__main__":
    unittest.main()

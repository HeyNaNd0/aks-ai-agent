"""
agent/diagnostics.py — AI-Powered Diagnostician
================================================
Sends problem + cluster context to Claude (via Anthropic API) and gets back
a structured diagnosis: origin (config vs platform), severity, auto-fixable
flag, root cause explanation, and recommended remediation steps.

Why Claude?
    Rule-based checks in monitor.py catch *what* broke.
    Claude tells us *why* and *how to fix it*, understanding context that
    rigid rules can't — e.g. "this OOMKill happened right after a deployment
    that doubled the replica count without changing memory limits."
"""

import json
import logging
from typing import Any, Dict

import anthropic

from agent.config import AgentConfig

log = logging.getLogger("aks-agent.diagnostics")

# ── System prompt for Claude ─────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an expert AKS (Azure Kubernetes Service) Site Reliability Engineer.

Your job is to analyze a specific cluster problem and return a structured JSON diagnosis.

You must determine:
1. **origin** — Is this a "configuration" issue (something in the user's K8s manifests,
   resource limits, app code, RBAC, networking policies, etc.) or a "platform" issue
   (Azure infrastructure, AKS control plane bug, VM quota exhaustion, Azure networking,
   Azure storage backend, known AKS bugs)?
2. **severity** — "critical" | "high" | "medium" | "low"
3. **auto_fixable** — true if the agent can resolve this without human involvement
4. **root_cause** — A concise 1-3 sentence plain-English explanation of WHY this is happening
5. **fix_steps** — Numbered list of concrete steps to resolve the issue
6. **kubectl_commands** — List of exact kubectl/az CLI commands that would help
7. **human_steps_if_needed** — Steps a human admin must take if auto-fix is not possible
8. **platform_evidence** — If origin=platform, describe exactly why you believe this
   is an Azure/AKS platform issue (e.g., known bug, service outage indicators, quota)
9. **documentation_tags** — List of relevant tags for categorizing this issue
   (e.g., ["networking", "oom", "node-pool", "rbac"])

IMPORTANT RULES:
- If you see an OOMKill, check if memory limits exist in the pod spec. If not → configuration.
- If Azure node pool provisioning_state is "Failed" with no user config change → platform.
- If ImagePullBackOff and the registry is Azure Container Registry → could be RBAC (config)
  or ACR service outage (platform). Look at the error message carefully.
- A pod stuck Pending with no nodes available might be: node pool autoscaler misconfigured
  (config), or Azure not provisioning new VMs (platform). Check node pool status.
- CrashLoopBackOff is almost always configuration unless it started during an AKS upgrade.
- When in doubt about platform vs config, lean toward configuration (user-fixable).

Respond ONLY with valid JSON matching this schema (no markdown, no explanation outside JSON):
{
  "origin": "configuration" | "platform" | "ambiguous",
  "severity": "critical" | "high" | "medium" | "low",
  "auto_fixable": true | false,
  "confidence": 0.0-1.0,
  "root_cause": "string",
  "fix_steps": ["step 1", "step 2", ...],
  "kubectl_commands": ["kubectl ...", "az ..."],
  "human_steps_if_needed": ["step 1", "step 2", ...],
  "platform_evidence": "string or null",
  "documentation_tags": ["tag1", "tag2"],
  "estimated_fix_time_minutes": integer
}
"""


class Diagnostician:
    """Uses Claude AI to deeply analyze cluster problems."""

    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        self.client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    def diagnose(self, problem: Dict[str, Any], cluster_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send problem + relevant cluster context to Claude and parse the response.

        Args:
            problem:       A problem dict from monitor.py (type, summary, details, severity)
            cluster_state: Full cluster snapshot from monitor.collect_state()

        Returns:
            Structured diagnosis dict.
        """
        prompt = self._build_prompt(problem, cluster_state)

        log.debug(f"Sending diagnosis request to Claude for: {problem['summary']}")

        try:
            message = self.client.messages.create(
                model=self.cfg.claude_model,
                max_tokens=self.cfg.ai_max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_response = message.content[0].text
            diagnosis = self._parse_response(raw_response, problem)
            log.info(
                f"Claude diagnosis: origin={diagnosis['origin']}, "
                f"auto_fixable={diagnosis['auto_fixable']}, "
                f"confidence={diagnosis['confidence']}"
            )
            return diagnosis

        except anthropic.APIError as exc:
            log.error(f"Anthropic API error: {exc}")
            return self._fallback_diagnosis(problem, str(exc))
        except json.JSONDecodeError as exc:
            log.error(f"Could not parse Claude response as JSON: {exc}")
            return self._fallback_diagnosis(problem, "AI response was not valid JSON")

    def _build_prompt(self, problem: Dict, cluster_state: Dict) -> str:
        """
        Build a focused prompt. We include only the relevant portions of
        cluster state to keep tokens low and diagnosis accurate.
        """
        # Filter cluster state to only relevant resources
        relevant_state = self._extract_relevant_context(problem, cluster_state)

        return f"""## PROBLEM DETECTED

**Type:** {problem['type']}
**Summary:** {problem['summary']}
**Detected Severity:** {problem['severity']}
**Timestamp:** {cluster_state.get('collected_at', 'unknown')}
**Cluster:** {cluster_state.get('cluster_name', 'unknown')}

## PROBLEM DETAILS
```json
{json.dumps(problem.get('details', {}), indent=2, default=str)}
```

## RELEVANT CLUSTER CONTEXT
```json
{json.dumps(relevant_state, indent=2, default=str)}
```

## TASK
Analyze this problem and return a structured JSON diagnosis following the schema
in your system prompt. Be specific — reference actual values from the data above
in your root_cause and fix_steps.
"""

    def _extract_relevant_context(self, problem: Dict, state: Dict) -> Dict:
        """
        Pull only the parts of cluster state relevant to this problem type.
        Avoids sending thousands of tokens of unrelated data to Claude.
        """
        ctx = {
            "cluster_name": state.get("cluster_name"),
            "collected_at": state.get("collected_at"),
        }
        ptype = problem["type"]
        pname = problem.get("details", {}).get("name", "")
        pns = problem.get("details", {}).get("namespace", "")

        # Always include nodes and events — they're almost always relevant
        ctx["nodes"] = state.get("nodes", [])
        ctx["recent_warning_events"] = [
            e for e in state.get("events", [])
            if pname in (e.get("object_name", "") or "")
        ][:20]  # cap at 20 events

        if ptype in ("pod_crashloop", "pod_oomkilled", "pod_imagepull", "pod_pending"):
            ctx["pod"] = problem.get("details", {})
            # Include the deployment that owns this pod (if any)
            ctx["deployments"] = [
                d for d in state.get("deployments", [])
                if d.get("namespace") == pns
            ]

        if ptype == "deployment_unavailable":
            ctx["deployment"] = problem.get("details", {})
            ctx["pods_in_namespace"] = [
                p for p in state.get("pods", [])
                if p.get("namespace") == pns
            ]

        if ptype == "pvc_unbound":
            ctx["pvc"] = problem.get("details", {})

        if ptype == "azure_quota_exceeded":
            ctx["azure_node_pools"] = state.get("azure_node_pools", [])

        if ptype == "node_not_ready":
            ctx["all_node_pools"] = state.get("azure_node_pools", [])

        return ctx

    def _parse_response(self, raw: str, problem: Dict) -> Dict:
        """Parse Claude's JSON response into a diagnosis dict."""
        # Strip any accidental markdown fences
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip().rstrip("```").strip()

        diagnosis = json.loads(clean)

        # Ensure required fields with safe defaults
        diagnosis.setdefault("origin", "ambiguous")
        diagnosis.setdefault("severity", problem.get("severity", "medium"))
        diagnosis.setdefault("auto_fixable", False)
        diagnosis.setdefault("confidence", 0.5)
        diagnosis.setdefault("root_cause", "Unknown")
        diagnosis.setdefault("fix_steps", [])
        diagnosis.setdefault("kubectl_commands", [])
        diagnosis.setdefault("human_steps_if_needed", [])
        diagnosis.setdefault("platform_evidence", None)
        diagnosis.setdefault("documentation_tags", [])
        diagnosis.setdefault("estimated_fix_time_minutes", 15)

        # Attach original problem reference
        diagnosis["problem_type"] = problem["type"]
        diagnosis["problem_summary"] = problem["summary"]

        return diagnosis

    def _fallback_diagnosis(self, problem: Dict, error: str) -> Dict:
        """Safe fallback when AI call fails — never auto-fix, always notify human."""
        return {
            "origin": "ambiguous",
            "severity": problem.get("severity", "high"),
            "auto_fixable": False,
            "confidence": 0.0,
            "root_cause": f"AI diagnosis unavailable: {error}",
            "fix_steps": ["Manual investigation required — AI diagnosis failed"],
            "kubectl_commands": [
                f"kubectl describe {problem.get('details', {}).get('kind', 'pod')} "
                f"{problem.get('details', {}).get('name', '')} -n "
                f"{problem.get('details', {}).get('namespace', 'default')}",
                "kubectl get events --sort-by=.lastTimestamp",
            ],
            "human_steps_if_needed": ["Review cluster manually", "Check agent logs"],
            "platform_evidence": None,
            "documentation_tags": ["ai-diagnosis-failed"],
            "estimated_fix_time_minutes": 30,
            "problem_type": problem["type"],
            "problem_summary": problem["summary"],
        }

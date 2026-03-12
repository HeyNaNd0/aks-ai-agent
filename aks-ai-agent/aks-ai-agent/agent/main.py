"""
AKS AI Monitoring Agent — Main Orchestrator
============================================
This is the brain of the operation. It runs on a schedule, pulls cluster
health data, feeds it to Claude AI for diagnosis, then routes to the
correct action: auto-fix, email admin, open Azure support case, or
open a GitHub issue.

Flow:
    monitor.py  →  diagnostics.py  →  remediation.py
                                   →  notifier.py
                                   →  documenter.py
"""

import asyncio
import logging
import signal
import sys
import time
from datetime import datetime
from typing import Optional

import schedule

from agent.config import AgentConfig
from agent.diagnostics import Diagnostician
from agent.documenter import Documenter
from agent.monitor import ClusterMonitor
from agent.notifier import Notifier
from agent.remediation import Remediator

# ── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/agent.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("aks-agent.main")


class AKSAgent:
    """
    Top-level agent class.

    Responsibilities:
        1. Kick off a monitoring cycle on a configurable interval.
        2. For each detected problem, orchestrate the diagnosis → action loop.
        3. Keep a heartbeat log so operators know the agent is alive.
    """

    def __init__(self, config_path: str = "config/config.yaml"):
        log.info("═══ AKS AI Agent starting up ═══")
        self.cfg = AgentConfig(config_path)
        self.monitor = ClusterMonitor(self.cfg)
        self.diagnostician = Diagnostician(self.cfg)
        self.remediator = Remediator(self.cfg)
        self.notifier = Notifier(self.cfg)
        self.documenter = Documenter(self.cfg)
        self._running = True

    # ── Main monitoring cycle ─────────────────────────────────────────────

    def run_cycle(self) -> None:
        """
        One full monitoring cycle:
            1. Collect cluster state.
            2. Identify problems.
            3. Diagnose each problem with AI.
            4. Route to fix / notify / escalate.
        """
        cycle_start = datetime.utcnow()
        log.info(f"▶ Starting monitoring cycle at {cycle_start.isoformat()}Z")

        try:
            # ── Step 1: Collect cluster state ───────────────────────────
            cluster_state = self.monitor.collect_state()
            problems = self.monitor.identify_problems(cluster_state)

            if not problems:
                log.info("✅ No problems detected. Cluster healthy.")
                self.documenter.log_heartbeat(cluster_state)
                return

            log.warning(f"⚠️  {len(problems)} problem(s) detected. Analyzing…")

            # ── Step 2: Diagnose each problem ────────────────────────────
            for problem in problems:
                self._handle_problem(problem, cluster_state)

        except Exception as exc:
            log.error(f"💥 Unhandled error in monitoring cycle: {exc}", exc_info=True)
            self.notifier.send_agent_error_alert(str(exc))

        elapsed = (datetime.utcnow() - cycle_start).total_seconds()
        log.info(f"◀ Cycle complete in {elapsed:.1f}s")

    def _handle_problem(self, problem: dict, cluster_state: dict) -> None:
        """
        Diagnose one problem and take the appropriate action.

        Decision tree:
            ┌──────────────────────────┐
            │  Diagnose with Claude AI │
            └──────────────┬───────────┘
                           │
               ┌───────────▼────────────┐
               │  Config issue?         │
               └───┬───────────────┬────┘
            Yes ───┘               └─── No → Azure platform issue
               │                              │
        ┌──────▼──────┐              ┌────────▼────────┐
        │  Auto-fix?  │              │ Open AZ support │
        └──┬──────┬───┘              │ Open GH issue   │
      Yes ─┘      └─ No              └─────────────────┘
           │           │
    ┌──────▼──┐  ┌─────▼──────────────────┐
    │  Fix it │  │ Email admin w/ steps   │
    └─────────┘  └────────────────────────┘
                 Both branches → document
        """
        log.info(f"  🔍 Analyzing: [{problem['type']}] {problem['summary']}")

        # ── AI Diagnosis ─────────────────────────────────────────────────
        diagnosis = self.diagnostician.diagnose(problem, cluster_state)
        log.info(
            f"  🤖 AI Verdict: origin={diagnosis['origin']}, "
            f"severity={diagnosis['severity']}, "
            f"auto_fixable={diagnosis['auto_fixable']}"
        )

        # ── Route based on diagnosis ─────────────────────────────────────
        if diagnosis["origin"] == "configuration":
            self._handle_config_issue(problem, diagnosis, cluster_state)
        elif diagnosis["origin"] == "platform":
            self._handle_platform_issue(problem, diagnosis, cluster_state)
        else:
            # Unknown / ambiguous — notify and document
            self._handle_ambiguous_issue(problem, diagnosis, cluster_state)

        # ── Always document ───────────────────────────────────────────────
        self.documenter.record_issue(problem, diagnosis)

    def _handle_config_issue(
        self, problem: dict, diagnosis: dict, cluster_state: dict
    ) -> None:
        """Configuration problems: try to auto-fix; email admin if blocked."""
        if diagnosis["auto_fixable"]:
            log.info("  🔧 Attempting auto-remediation…")
            result = self.remediator.fix(problem, diagnosis)

            if result["success"]:
                log.info(f"  ✅ Auto-fix succeeded: {result['action_taken']}")
                self.documenter.update_issue_resolved(problem, result)
                self.notifier.send_fix_notification(problem, diagnosis, result)
            else:
                log.warning(f"  ❌ Auto-fix failed: {result['reason']}")
                self.notifier.send_admin_intervention_email(
                    problem, diagnosis, result
                )
                self.documenter.update_issue_needs_human(problem, result)
        else:
            log.info("  📧 Issue requires human intervention. Emailing admin…")
            self.notifier.send_admin_intervention_email(problem, diagnosis, {})
            self.documenter.update_issue_needs_human(problem, {})

    def _handle_platform_issue(
        self, problem: dict, diagnosis: dict, cluster_state: dict
    ) -> None:
        """Azure platform problems: open support case + GitHub issue."""
        log.info("  ☁️  Azure platform issue — escalating to Microsoft…")

        support_case_id: Optional[str] = None
        gh_issue_url: Optional[str] = None

        try:
            support_case_id = self.notifier.open_azure_support_case(
                problem, diagnosis
            )
            log.info(f"  📋 Azure support case opened: {support_case_id}")
        except Exception as exc:
            log.error(f"  ⚠️  Could not open Azure support case: {exc}")

        try:
            gh_issue_url = self.notifier.open_github_issue(problem, diagnosis)
            log.info(f"  🐙 GitHub issue opened: {gh_issue_url}")
        except Exception as exc:
            log.error(f"  ⚠️  Could not open GitHub issue: {exc}")

        # Also notify admin with escalation summary
        self.notifier.send_platform_escalation_email(
            problem, diagnosis, support_case_id, gh_issue_url
        )
        self.documenter.update_issue_escalated(
            problem, support_case_id, gh_issue_url
        )

    def _handle_ambiguous_issue(
        self, problem: dict, diagnosis: dict, cluster_state: dict
    ) -> None:
        """Unclear root cause — notify admin and await guidance."""
        log.warning("  ❓ Root cause ambiguous — notifying admin for review")
        self.notifier.send_admin_intervention_email(
            problem, diagnosis, {"reason": "Root cause could not be determined automatically"}
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the scheduled agent. Blocks until interrupted."""
        interval = self.cfg.monitoring_interval_seconds
        log.info(f"⏱  Scheduling monitoring cycle every {interval}s")

        # Run once immediately, then on schedule
        self.run_cycle()
        schedule.every(interval).seconds.do(self.run_cycle)

        # Heartbeat every hour
        schedule.every(1).hour.do(
            lambda: log.info("💓 Agent heartbeat — still running")
        )

        # Register graceful shutdown
        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT, self._shutdown)

        log.info("✅ Agent running. Press Ctrl+C to stop.")
        while self._running:
            schedule.run_pending()
            time.sleep(5)

    def _shutdown(self, signum, frame) -> None:
        log.info(f"🛑 Shutdown signal received ({signum}). Cleaning up…")
        self._running = False
        self.documenter.close()
        sys.exit(0)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    import os
    os.makedirs("logs", exist_ok=True)
    config_path = os.environ.get("AKS_AGENT_CONFIG", "config/config.yaml")
    agent = AKSAgent(config_path)
    agent.start()


if __name__ == "__main__":
    main()

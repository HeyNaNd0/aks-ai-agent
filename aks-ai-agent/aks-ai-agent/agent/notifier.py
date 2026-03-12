"""
agent/notifier.py — Notification & Escalation Hub
==================================================
Handles all outbound communication:
    1. Email admin with intervention steps (HTML email, human-readable)
    2. Email agent fix notifications (FYI emails when agent self-healed)
    3. Open Azure Support cases via Azure Management REST API
    4. Open GitHub issues on Azure/AKS (platform bugs) and your own repo (tracking)
    5. Agent error alerts when the agent itself crashes
"""

import json
import logging
import smtplib
import textwrap
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, Optional

import requests
from github import Github, GithubException

from agent.config import AgentConfig

log = logging.getLogger("aks-agent.notifier")


class Notifier:
    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        self._gh: Optional[Github] = None
        if cfg.github_token:
            self._gh = Github(cfg.github_token)

    # ── Email ─────────────────────────────────────────────────────────────

    def send_admin_intervention_email(
        self, problem: Dict, diagnosis: Dict, result: Dict
    ) -> None:
        """
        Email the admin when human steps are required.
        Includes: what broke, why (AI analysis), exact steps to take,
        and kubectl commands to run.
        """
        subject = (
            f"[AKS Agent] 🔧 Human Intervention Required — "
            f"{problem['summary'][:60]}"
        )
        steps_html = self._numbered_list_html(diagnosis.get("human_steps_if_needed", []))
        commands_html = self._code_block_html(diagnosis.get("kubectl_commands", []))
        fix_steps_html = self._numbered_list_html(diagnosis.get("fix_steps", []))
        reason = result.get("reason", "Auto-fix not possible for this problem type")

        html = f"""
        <html><body style="font-family: Arial, sans-serif; max-width: 800px; margin: auto;">
        <div style="background:#e74c3c; color:white; padding:16px; border-radius:8px 8px 0 0;">
            <h2 style="margin:0">⚠️ AKS Agent: Human Intervention Required</h2>
            <p style="margin:4px 0 0">{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
        </div>
        <div style="background:#f8f9fa; padding:16px; border:1px solid #dee2e6;">

            <h3>🔍 Problem Detected</h3>
            <table style="width:100%; border-collapse:collapse;">
                <tr><td style="font-weight:bold; width:160px; padding:6px;">Type</td>
                    <td style="padding:6px;">{problem['type']}</td></tr>
                <tr style="background:#fff;"><td style="font-weight:bold; padding:6px;">Summary</td>
                    <td style="padding:6px;">{problem['summary']}</td></tr>
                <tr><td style="font-weight:bold; padding:6px;">Severity</td>
                    <td style="padding:6px; color:{'#e74c3c' if diagnosis['severity']=='critical' else '#e67e22'};">
                    {diagnosis['severity'].upper()}</td></tr>
                <tr style="background:#fff;"><td style="font-weight:bold; padding:6px;">Origin</td>
                    <td style="padding:6px;">{diagnosis['origin']}</td></tr>
                <tr><td style="font-weight:bold; padding:6px;">Cluster</td>
                    <td style="padding:6px;">{self.cfg.cluster_name}</td></tr>
            </table>

            <h3>🤖 AI Root Cause Analysis</h3>
            <p style="background:#fff; padding:12px; border-left:4px solid #3498db;">
                {diagnosis.get('root_cause', 'Not available')}
            </p>
            <p><em>AI Confidence: {int(float(diagnosis.get('confidence', 0)) * 100)}%</em></p>

            <h3>❌ Why the Agent Could Not Auto-Fix This</h3>
            <p style="background:#fff3cd; padding:12px; border-radius:4px;">{reason}</p>

            <h3>📋 Steps For You To Take</h3>
            <p>The agent has prepared these steps. Once you complete them,
               the agent will continue with the remaining automated fixes.</p>
            {steps_html}

            <h3>💡 Full Recommended Fix Steps</h3>
            {fix_steps_html}

            <h3>⌨️ Helpful Commands</h3>
            {commands_html}

            <h3>📊 Problem Details</h3>
            <pre style="background:#2c3e50; color:#ecf0f1; padding:12px; border-radius:4px;
                        overflow-x:auto; font-size:12px;">
{json.dumps(problem.get('details', {}), indent=2, default=str)}</pre>
        </div>
        <div style="background:#95a5a6; color:white; padding:8px 16px; border-radius:0 0 8px 8px;
                    font-size:12px;">
            AKS AI Monitoring Agent | Cluster: {self.cfg.cluster_name}
        </div>
        </body></html>
        """
        self._send_email(subject, html)

    def send_fix_notification(
        self, problem: Dict, diagnosis: Dict, result: Dict
    ) -> None:
        """FYI email when the agent successfully auto-fixed a problem."""
        subject = f"[AKS Agent] ✅ Auto-Fixed — {problem['summary'][:60]}"
        html = f"""
        <html><body style="font-family: Arial, sans-serif; max-width:700px; margin:auto;">
        <div style="background:#27ae60; color:white; padding:16px; border-radius:8px 8px 0 0;">
            <h2 style="margin:0">✅ AKS Agent Auto-Fixed an Issue</h2>
            <p style="margin:4px 0 0">{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
        </div>
        <div style="background:#f8f9fa; padding:16px; border:1px solid #dee2e6;">
            <p><strong>Problem:</strong> {problem['summary']}</p>
            <p><strong>Root Cause:</strong> {diagnosis.get('root_cause')}</p>
            <p><strong>Action Taken:</strong> {result.get('action_taken')}</p>
            <p><strong>Verified:</strong> {'Yes ✓' if result.get('verified') else 'Not yet verified'}</p>
            <h4>Commands Executed:</h4>
            {self._code_block_html(result.get('commands_run', []))}
        </div>
        </body></html>
        """
        self._send_email(subject, html)

    def send_platform_escalation_email(
        self, problem: Dict, diagnosis: Dict,
        support_case_id: Optional[str], gh_issue_url: Optional[str]
    ) -> None:
        """Email admin about a platform issue being escalated to Microsoft."""
        subject = f"[AKS Agent] ☁️ Azure Platform Issue Escalated — {problem['summary'][:55]}"
        html = f"""
        <html><body style="font-family: Arial, sans-serif; max-width:700px; margin:auto;">
        <div style="background:#2980b9; color:white; padding:16px; border-radius:8px 8px 0 0;">
            <h2 style="margin:0">☁️ Azure Platform Issue — Escalated</h2>
        </div>
        <div style="background:#f8f9fa; padding:16px; border:1px solid #dee2e6;">
            <p><strong>Problem:</strong> {problem['summary']}</p>
            <p><strong>Platform Evidence:</strong> {diagnosis.get('platform_evidence', 'N/A')}</p>
            <p><strong>Azure Support Case:</strong> {support_case_id or 'Not opened (check agent logs)'}</p>
            <p><strong>GitHub Issue:</strong>
               <a href="{gh_issue_url}">{gh_issue_url}</a> if {gh_issue_url} else 'Not opened'</p>
            <h4>What the agent did:</h4>
            <ul>
                <li>{'✅' if support_case_id else '❌'} Opened Azure support case</li>
                <li>{'✅' if gh_issue_url else '❌'} Opened GitHub issue on {self.cfg.github_repo}</li>
                <li>✅ Documented issue in local database</li>
            </ul>
            <h4>What YOU should do:</h4>
            <ol>
                <li>Monitor Azure Service Health for this region/service</li>
                <li>Check the Azure support case portal for updates</li>
                <li>Subscribe to the GitHub issue for resolution updates</li>
                <li>Consider enabling cluster autoscaler or increasing VM quota if quota-related</li>
            </ol>
        </div>
        </body></html>
        """
        self._send_email(subject, html)

    def send_agent_error_alert(self, error_message: str) -> None:
        """Alert admin when the agent itself encounters an unhandled error."""
        subject = "[AKS Agent] 💥 Agent Error — Monitoring May Be Interrupted"
        html = f"""
        <html><body>
        <div style="background:#8e44ad; color:white; padding:16px;">
            <h2>💥 AKS Agent Encountered an Error</h2>
        </div>
        <div style="padding:16px;">
            <p><strong>Error:</strong></p>
            <pre style="background:#2c3e50; color:#ecf0f1; padding:12px; border-radius:4px;">
{error_message}</pre>
            <p>Check agent logs at <code>logs/agent.log</code> for full stack trace.</p>
        </div>
        </body></html>
        """
        self._send_email(subject, html)

    def _send_email(self, subject: str, html_body: str) -> None:
        """Send an HTML email via SMTP."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.cfg.sender_email
        msg["To"] = self.cfg.admin_email
        msg.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port) as server:
                server.starttls()
                server.login(self.cfg.sender_email, self.cfg.email_password)
                server.sendmail(
                    self.cfg.sender_email, self.cfg.admin_email, msg.as_string()
                )
            log.info(f"📧 Email sent: {subject}")
        except Exception as exc:
            log.error(f"Failed to send email '{subject}': {exc}")

    # ── Azure Support ─────────────────────────────────────────────────────

    def open_azure_support_case(self, problem: Dict, diagnosis: Dict) -> Optional[str]:
        """
        Open an Azure Support ticket via the Azure Support REST API.
        Returns the support ticket name/ID on success.

        Docs: https://docs.microsoft.com/en-us/rest/api/support/
        """
        from azure.identity import ClientSecretCredential
        from azure.mgmt.support import MicrosoftSupport
        from azure.mgmt.support.models import SupportTicketDetails, ContactProfile, ServiceLevelAgreement, TechnicalTicketDetails

        cred = ClientSecretCredential(
            tenant_id=self.cfg.azure_tenant_id,
            client_id=self.cfg.azure_client_id,
            client_secret=self.cfg.azure_client_secret,
        )
        support_client = MicrosoftSupport(cred, self.cfg.azure_subscription_id)

        ticket_name = f"aks-agent-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        title = f"[AKS Agent Auto-Report] {problem['summary'][:100]}"
        description = textwrap.dedent(f"""
            This support ticket was automatically opened by the AKS AI Monitoring Agent.

            CLUSTER: {self.cfg.cluster_name}
            RESOURCE GROUP: {self.cfg.resource_group}
            PROBLEM TYPE: {problem['type']}
            PROBLEM SUMMARY: {problem['summary']}
            DETECTED AT: {datetime.utcnow().isoformat()}Z

            AI ROOT CAUSE ANALYSIS:
            {diagnosis.get('root_cause', 'N/A')}

            PLATFORM EVIDENCE:
            {diagnosis.get('platform_evidence', 'N/A')}

            PROBLEM DETAILS:
            {json.dumps(problem.get('details', {}), indent=2, default=str)}
        """).strip()

        try:
            ticket = support_client.support_tickets.begin_create(
                support_ticket_name=ticket_name,
                create_support_ticket_parameters=SupportTicketDetails(
                    title=title,
                    description=description,
                    problem_classification_id=(
                        "/providers/Microsoft.Support/services/58c6c498-cdd0-5e1e-9ffd-27519163ebe0"
                        "/problemClassifications/2d74e0f1-1f4d-63cc-b5d2-28448f01c4af"
                    ),  # AKS → Cluster management
                    severity="minimal",
                    contact_details=ContactProfile(
                        first_name="AKS",
                        last_name="Agent",
                        primary_email_address=self.cfg.admin_email,
                        preferred_contact_method="email",
                        preferred_time_zone="UTC",
                        preferred_support_language="en-US",
                        country="USA",
                    ),
                ),
            ).result()
            return ticket.name
        except Exception as exc:
            log.error(f"Could not open Azure support case: {exc}")
            raise

    # ── GitHub Issues ─────────────────────────────────────────────────────

    def open_github_issue(self, problem: Dict, diagnosis: Dict) -> Optional[str]:
        """
        Open a GitHub issue on Azure/AKS (for platform bugs) and optionally
        on your own tracking repo.
        Returns the HTML URL of the created issue.
        """
        if not self._gh:
            raise RuntimeError("GitHub token not configured (GITHUB_TOKEN env var)")

        # Build issue body (Markdown)
        steps_md = "\n".join(
            f"{i+1}. {s}" for i, s in enumerate(diagnosis.get("fix_steps", []))
        )
        cmds_md = "\n".join(f"```\n{c}\n```" for c in diagnosis.get("kubectl_commands", []))
        tags = ", ".join(f"`{t}`" for t in diagnosis.get("documentation_tags", []))

        body = textwrap.dedent(f"""
            ## 🤖 Auto-reported by AKS AI Monitoring Agent

            **Cluster:** `{self.cfg.cluster_name}`
            **Resource Group:** `{self.cfg.resource_group}`
            **Detected At:** `{datetime.utcnow().isoformat()}Z`

            ## Problem
            **Type:** `{problem['type']}`
            **Summary:** {problem['summary']}
            **Severity:** `{diagnosis['severity']}`

            ## AI Root Cause Analysis
            {diagnosis.get('root_cause', 'N/A')}

            ## Platform Evidence
            {diagnosis.get('platform_evidence', 'N/A')}

            ## Problem Details
            <details><summary>Expand technical details</summary>

            ```json
            {json.dumps(problem.get('details', {}), indent=2, default=str)}
            ```

            </details>

            ## Recommended Fix Steps
            {steps_md}

            ## Helpful Commands
            {cmds_md}

            ## Tags
            {tags}

            ---
            *This issue was automatically created by [AKS AI Monitoring Agent](https://github.com/HeyNaNd0/aks-ai-agent).*
        """).strip()

        try:
            repo = self._gh.get_repo(self.cfg.github_repo)
            labels = self._get_or_create_labels(repo, diagnosis.get("documentation_tags", []))
            issue = repo.create_issue(
                title=f"[AKS Agent] {problem['summary'][:120]}",
                body=body,
                labels=labels,
            )
            log.info(f"GitHub issue created: {issue.html_url}")

            # Also open on tracking repo if configured
            if self.cfg.github_tracking_repo:
                try:
                    tracking_repo = self._gh.get_repo(self.cfg.github_tracking_repo)
                    tracking_repo.create_issue(
                        title=f"[Tracking] {problem['summary'][:100]}",
                        body=f"See Azure/AKS issue: {issue.html_url}\n\n{body}",
                    )
                except GithubException as exc:
                    log.warning(f"Could not create tracking issue: {exc}")

            return issue.html_url
        except GithubException as exc:
            log.error(f"GitHub API error: {exc}")
            raise

    def _get_or_create_labels(self, repo, tags: list) -> list:
        """Get or create GitHub labels from documentation tags."""
        labels = []
        for tag in tags[:5]:  # GitHub has label limits
            try:
                label = repo.get_label(tag)
                labels.append(label)
            except GithubException:
                pass  # Label doesn't exist on this repo — skip
        return labels

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _numbered_list_html(items: list) -> str:
        if not items:
            return "<p><em>No steps specified.</em></p>"
        rows = "".join(f"<li style='margin:6px 0;'>{item}</li>" for item in items)
        return f"<ol>{rows}</ol>"

    @staticmethod
    def _code_block_html(items: list) -> str:
        if not items:
            return "<p><em>No commands specified.</em></p>"
        cmds = "\n".join(items)
        return (
            f"<pre style='background:#2c3e50; color:#ecf0f1; padding:12px; "
            f"border-radius:4px; overflow-x:auto; font-size:13px;'>{cmds}</pre>"
        )

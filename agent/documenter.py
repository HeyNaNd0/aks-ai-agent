"""
agent/documenter.py — Issue Documentation & Audit Trail
========================================================
Every problem detected, every action taken, every resolution — stored in
a local SQLite database AND exported to Markdown reports.

Tables:
    issues        — One row per detected problem
    heartbeats    — Periodic "cluster is healthy" snapshots
    action_log    — Every automated action taken by the agent

Why SQLite?
    - Zero dependencies (built into Python)
    - Human-readable with tools like DB Browser for SQLite
    - Easily exportable to CSV/JSON for dashboards
    - Works in a pod with an attached PVC for persistence
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from agent.config import AgentConfig

log = logging.getLogger("aks-agent.documenter")


class Documenter:
    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        db_path = Path(cfg.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        log.info(f"Documentation DB ready at {db_path}")

    def _init_schema(self):
        """Create tables if they don't exist yet."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS issues (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_at     TEXT NOT NULL,
                cluster_name    TEXT,
                problem_type    TEXT,
                problem_summary TEXT,
                severity        TEXT,
                origin          TEXT,
                auto_fixable    INTEGER,
                confidence      REAL,
                root_cause      TEXT,
                fix_steps       TEXT,    -- JSON array
                kubectl_commands TEXT,   -- JSON array
                tags            TEXT,    -- JSON array
                status          TEXT DEFAULT 'open',
                resolved_at     TEXT,
                action_taken    TEXT,
                azure_case_id   TEXT,
                github_issue    TEXT,
                notes           TEXT,
                raw_problem     TEXT,    -- Full JSON
                raw_diagnosis   TEXT     -- Full JSON
            );

            CREATE TABLE IF NOT EXISTS heartbeats (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at     TEXT NOT NULL,
                cluster_name    TEXT,
                node_count      INTEGER,
                pod_count       INTEGER,
                warning_events  INTEGER,
                summary         TEXT
            );

            CREATE TABLE IF NOT EXISTS action_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT NOT NULL,
                issue_id        INTEGER REFERENCES issues(id),
                action_type     TEXT,
                action_detail   TEXT,
                success         INTEGER,
                commands_run    TEXT,    -- JSON array
                outcome         TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_issues_status    ON issues(status);
            CREATE INDEX IF NOT EXISTS idx_issues_type      ON issues(problem_type);
            CREATE INDEX IF NOT EXISTS idx_issues_detected  ON issues(detected_at);
        """)
        self.conn.commit()

    # ── Issue lifecycle ───────────────────────────────────────────────────

    def record_issue(self, problem: Dict, diagnosis: Dict) -> int:
        """Insert a new issue record. Returns the new row ID."""
        now = datetime.utcnow().isoformat()
        cursor = self.conn.execute(
            """
            INSERT INTO issues (
                detected_at, cluster_name, problem_type, problem_summary,
                severity, origin, auto_fixable, confidence, root_cause,
                fix_steps, kubectl_commands, tags, status, raw_problem, raw_diagnosis
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                now,
                self.cfg.cluster_name,
                problem["type"],
                problem["summary"],
                diagnosis.get("severity", "unknown"),
                diagnosis.get("origin", "unknown"),
                1 if diagnosis.get("auto_fixable") else 0,
                diagnosis.get("confidence", 0.0),
                diagnosis.get("root_cause", ""),
                json.dumps(diagnosis.get("fix_steps", [])),
                json.dumps(diagnosis.get("kubectl_commands", [])),
                json.dumps(diagnosis.get("documentation_tags", [])),
                json.dumps(problem, default=str),
                json.dumps(diagnosis, default=str),
            ),
        )
        self.conn.commit()
        issue_id = cursor.lastrowid
        log.debug(f"Issue recorded: ID={issue_id} | {problem['summary']}")
        return issue_id

    def update_issue_resolved(self, problem: Dict, result: Dict) -> None:
        """Mark an issue as resolved after successful auto-fix."""
        self.conn.execute(
            """
            UPDATE issues SET
                status       = 'resolved',
                resolved_at  = ?,
                action_taken = ?,
                notes        = ?
            WHERE problem_type = ? AND problem_summary = ? AND status = 'open'
            ORDER BY detected_at DESC LIMIT 1
            """,
            (
                datetime.utcnow().isoformat(),
                result.get("action_taken", ""),
                f"Auto-fixed. Commands: {json.dumps(result.get('commands_run', []))}",
                problem["type"],
                problem["summary"],
            ),
        )
        self.conn.commit()
        self._log_action(problem, result, action_type="auto_fix")

    def update_issue_needs_human(self, problem: Dict, result: Dict) -> None:
        """Mark an issue as awaiting human intervention."""
        self.conn.execute(
            """
            UPDATE issues SET
                status = 'pending_human',
                notes  = ?
            WHERE problem_type = ? AND problem_summary = ? AND status = 'open'
            ORDER BY detected_at DESC LIMIT 1
            """,
            (
                f"Needs human: {result.get('reason', 'See diagnosis')}",
                problem["type"],
                problem["summary"],
            ),
        )
        self.conn.commit()
        self._log_action(problem, result, action_type="escalate_human")

    def update_issue_escalated(
        self,
        problem: Dict,
        azure_case_id: Optional[str],
        github_issue_url: Optional[str],
    ) -> None:
        """Mark an issue as escalated to Azure/GitHub."""
        self.conn.execute(
            """
            UPDATE issues SET
                status        = 'escalated',
                azure_case_id = ?,
                github_issue  = ?,
                notes         = ?
            WHERE problem_type = ? AND problem_summary = ? AND status = 'open'
            ORDER BY detected_at DESC LIMIT 1
            """,
            (
                azure_case_id or "",
                github_issue_url or "",
                f"Escalated to Azure ({azure_case_id}) and GitHub ({github_issue_url})",
                problem["type"],
                problem["summary"],
            ),
        )
        self.conn.commit()
        self._log_action(
            problem,
            {"action_taken": "escalated_to_azure_and_github"},
            action_type="escalate_platform",
        )

    def _log_action(self, problem: Dict, result: Dict, action_type: str) -> None:
        """Write an entry to the action_log table."""
        # Get the latest issue ID for this problem
        row = self.conn.execute(
            """
            SELECT id FROM issues
            WHERE problem_type = ? AND problem_summary = ?
            ORDER BY detected_at DESC LIMIT 1
            """,
            (problem["type"], problem["summary"]),
        ).fetchone()
        issue_id = row["id"] if row else None

        self.conn.execute(
            """
            INSERT INTO action_log
                (timestamp, issue_id, action_type, action_detail, success, commands_run, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(),
                issue_id,
                action_type,
                result.get("action_taken", ""),
                1 if result.get("success") else 0,
                json.dumps(result.get("commands_run", [])),
                result.get("reason", ""),
            ),
        )
        self.conn.commit()

    # ── Heartbeat ─────────────────────────────────────────────────────────

    def log_heartbeat(self, cluster_state: Dict) -> None:
        """Record a healthy-cluster heartbeat."""
        self.conn.execute(
            """
            INSERT INTO heartbeats (recorded_at, cluster_name, node_count, pod_count, warning_events, summary)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(),
                self.cfg.cluster_name,
                len(cluster_state.get("nodes", [])),
                len(cluster_state.get("pods", [])),
                len(cluster_state.get("events", [])),
                "Cluster healthy — no problems detected",
            ),
        )
        self.conn.commit()

    # ── Reporting ─────────────────────────────────────────────────────────

    def generate_markdown_report(self, output_path: str = "data/report.md") -> str:
        """
        Generate a Markdown status report of all issues.
        Useful for weekly digests or README badges.
        """
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [
            "# AKS Agent Issue Report\n",
            f"**Generated:** {now}  ",
            f"**Cluster:** {self.cfg.cluster_name}\n",
            "---\n",
        ]

        # Summary counts
        counts = self.conn.execute(
            "SELECT status, COUNT(*) as cnt FROM issues GROUP BY status"
        ).fetchall()
        lines.append("## Summary\n")
        for row in counts:
            emoji = {
                "open": "🔴", "resolved": "✅", "pending_human": "🟡",
                "escalated": "🔵",
            }.get(row["status"], "⚪")
            lines.append(f"- {emoji} **{row['status'].replace('_', ' ').title()}:** {row['cnt']}")
        lines.append("\n---\n")

        # Open and pending issues
        open_issues = self.conn.execute(
            "SELECT * FROM issues WHERE status IN ('open','pending_human','escalated') "
            "ORDER BY detected_at DESC"
        ).fetchall()

        if open_issues:
            lines.append("## Active Issues\n")
            for issue in open_issues:
                sev_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(
                    issue["severity"], "⚪"
                )
                lines.append(f"### {sev_icon} [{issue['problem_type']}] {issue['problem_summary']}\n")
                lines.append(f"- **Status:** `{issue['status']}`")
                lines.append(f"- **Origin:** `{issue['origin']}`")
                lines.append(f"- **Detected:** `{issue['detected_at']}`")
                lines.append(f"- **Root Cause:** {issue['root_cause']}")
                if issue["azure_case_id"]:
                    lines.append(f"- **Azure Case:** `{issue['azure_case_id']}`")
                if issue["github_issue"]:
                    lines.append(f"- **GitHub:** [{issue['github_issue']}]({issue['github_issue']})")
                lines.append("")

        # Recently resolved
        resolved = self.conn.execute(
            "SELECT * FROM issues WHERE status = 'resolved' "
            "ORDER BY resolved_at DESC LIMIT 10"
        ).fetchall()
        if resolved:
            lines.append("## Recently Resolved\n")
            for issue in resolved:
                lines.append(
                    f"- ✅ `{issue['resolved_at'][:10]}` — "
                    f"**{issue['problem_type']}** — {issue['problem_summary'][:80]}"
                )

        report = "\n".join(lines)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            f.write(report)

        log.info(f"Report written to {output_path}")
        return report

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self):
        """Close the database connection gracefully."""
        try:
            self.generate_markdown_report()  # Final report on shutdown
        except Exception:
            pass
        self.conn.close()
        log.info("Documentation DB closed")

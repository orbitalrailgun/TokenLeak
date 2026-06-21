"""Generate CSV export of alerts with full scan and repo context."""

from __future__ import annotations

import csv
import io
from typing import Optional

from tokenleak.db.base import Database

# Column order matches the reference SQL query below.
CSV_COLUMNS = [
    "alert_id",
    "repo_url",
    "repo_provider",
    "repo_name",
    "commit_sha",
    "commit_date",
    "commit_message",
    "commit_author",
    "branch",
    "scan_mode",
    "scan_status",
    "ai_model",
    "input_tokens",
    "output_tokens",
    "tokens_used",
    "scan_error",
    "file_path",
    "line_start",
    "line_end",
    "alert_type",
    "severity",
    "description",
    "code_snippet",
    "how_used",
    "confirmation",
    "is_false_positive",
    "triggered_by",
    "alert_created_at",
]

# Equivalent raw SQL for direct DB access (SQLite dialect):
REFERENCE_SQL = """
SELECT
    alerts.id                                                              AS alert_id,
    repos.url                                                              AS repo_url,
    repos.provider                                                         AS repo_provider,
    repos.name                                                             AS repo_name,
    alerts.commit_sha,
    alerts.commit_date,
    scans.commit_message,
    scans.commit_author,
    scans.branch,
    scans.scan_mode,
    scans.status                                                           AS scan_status,
    alerts.ai_model,
    scans.input_tokens,
    scans.output_tokens,
    scans.tokens_used,
    scans.error_message                                                    AS scan_error,
    alerts.file_path,
    CASE WHEN alerts.line_start < 0 THEN NULL ELSE alerts.line_start END  AS line_start,
    CASE WHEN alerts.line_end   < 0 THEN NULL ELSE alerts.line_end   END  AS line_end,
    alerts.alert_type,
    alerts.severity,
    json_extract(alerts.agent_json, '$.description')                       AS description,
    json_extract(alerts.agent_json, '$.code_snippet')                      AS code_snippet,
    json_extract(alerts.agent_json, '$.how_used')                          AS how_used,
    json_extract(alerts.agent_json, '$.confirmation')                      AS confirmation,
    alerts.is_false_positive,
    alerts.triggered_by,
    alerts.created_at                                                      AS alert_created_at
FROM alerts
LEFT JOIN repos  ON alerts.repo_id  = repos.id
LEFT JOIN scans  ON alerts.scan_id  = scans.id
-- Narrow scope with WHERE as needed:
--   WHERE alerts.scan_id = ?
--   WHERE alerts.repo_id = ?
ORDER BY alerts.id;
"""


def generate_alerts_csv(
    db: Database,
    scan_id: Optional[int] = None,
    repo_id: Optional[int] = None,
) -> str:
    """Return UTF-8 CSV (with BOM) of alerts joined with scan and repo data.

    Precedence: scan_id > repo_id > all repos.
    Negative line numbers (synthetic for binary files) are rendered as empty cells.
    """
    if scan_id is not None:
        alerts = db.list_alerts(scan_id)
    elif repo_id is not None:
        alerts = db.list_alerts_for_repo(repo_id)
    else:
        alerts = []
        for repo in db.list_repos():
            alerts.extend(db.list_alerts_for_repo(repo.id))

    scan_cache: dict = {}
    repo_map: dict = {r.id: r for r in db.list_repos()}

    buf = io.StringIO()
    buf.write("﻿")  # BOM — Excel auto-detects UTF-8 correctly
    writer = csv.DictWriter(
        buf,
        fieldnames=CSV_COLUMNS,
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writeheader()

    for alert in alerts:
        if alert.scan_id not in scan_cache:
            scan_cache[alert.scan_id] = db.get_scan_by_id(alert.scan_id)
        scan = scan_cache[alert.scan_id]

        repo_id_for_lookup = alert.repo_id or (scan.repo_id if scan else None)
        repo = repo_map.get(repo_id_for_lookup) if repo_id_for_lookup else None

        aj = alert.agent_json or {}
        line_start = alert.line_start if alert.line_start > 0 else None
        line_end = alert.line_end if alert.line_end > 0 else None

        writer.writerow({
            "alert_id":          alert.id,
            "repo_url":          repo.url if repo else None,
            "repo_provider":     repo.provider if repo else None,
            "repo_name":         repo.name if repo else None,
            "commit_sha":        alert.commit_sha,
            "commit_date":       alert.commit_date,
            "commit_message":    scan.commit_message if scan else None,
            "commit_author":     scan.commit_author if scan else None,
            "branch":            scan.branch if scan else None,
            "scan_mode":         scan.scan_mode if scan else None,
            "scan_status":       scan.status if scan else None,
            "ai_model":          alert.ai_model,
            "input_tokens":      scan.input_tokens if scan else None,
            "output_tokens":     scan.output_tokens if scan else None,
            "tokens_used":       scan.tokens_used if scan else None,
            "scan_error":        scan.error_message if scan else None,
            "file_path":         alert.file_path,
            "line_start":        line_start,
            "line_end":          line_end,
            "alert_type":        alert.alert_type,
            "severity":          alert.severity,
            "description":       aj.get("description"),
            "code_snippet":      aj.get("code_snippet"),
            "how_used":          aj.get("how_used"),
            "confirmation":      aj.get("confirmation"),
            "is_false_positive": int(alert.is_false_positive),
            "triggered_by":      alert.triggered_by,
            "alert_created_at":  alert.created_at,
        })

    return buf.getvalue()

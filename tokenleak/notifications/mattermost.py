"""Mattermost notification sender via incoming webhook or personal access token."""

from __future__ import annotations

import datetime
from typing import Optional

import httpx

from tokenleak.config import Config
from tokenleak.logging_setup import get_logger

log = get_logger()


class Mattermost:
    def __init__(self, config: Config) -> None:
        self._url = (config.mattermost_url or "").rstrip("/")
        self._token = config.mattermost_token
        self._default_channel = config.mattermost_channel
        self._channel_id = config.mattermost_channel_id or ""
        self._enabled = bool(self._url and self._token)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send(self, message: str, channel: Optional[str] = None) -> None:
        """Send a raw message.  Raises on failure — callers decide how to handle it."""
        if not self._enabled:
            return
        ch = channel or self._default_channel or ""
        payload: dict = {"text": message}
        if ch:
            payload["channel"] = ch
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        try:
            resp = httpx.post(
                f"{self._url}/api/v4/posts",
                json=payload,
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            log.info("Mattermost: message sent to #%s", ch or "default")
        except Exception as exc:
            log.warning("Mattermost send failed: %s", exc)
            raise

    def send_scan_summary(self, repo_url: str, alerts: list, scan_id: int) -> None:
        """Send a scan summary. Errors are logged and swallowed — never kills a scan."""
        if not self._enabled:
            return

        severity_counts: dict[str, int] = {}
        for a in alerts:
            key = (a.severity or "unknown").lower()
            severity_counts[key] = severity_counts.get(key, 0) + 1

        lines = [f"### TokenLeak scan complete: `{repo_url}`"]
        if alerts:
            lines.append(f"**{len(alerts)} alert(s) found:**")
            for sev, count in sorted(severity_counts.items()):
                lines.append(f"  - {sev.upper()}: {count}")
            lines.append("")
            for alert in alerts[:10]:
                aj = alert.agent_json or {}
                sev_label = (alert.severity or "unknown").upper()
                file_label = alert.file_path or "(unknown file)"
                lines.append(
                    f"- **[{sev_label}]** `{file_label}` "
                    f"({alert.alert_type or '?'}): {aj.get('description', '')[:120]}"
                )
            if len(alerts) > 10:
                lines.append(f"  … and {len(alerts) - 10} more. See DB scan_id={scan_id}.")
        else:
            lines.append("No secrets or sensitive data found.")

        try:
            self.send("\n".join(lines))
        except Exception as exc:
            log.warning("Mattermost send_scan_summary failed for %s: %s", repo_url, exc)

    def send_skipped_large_repo(self, repo_url: str, size_mb: float, limit_mb: int) -> None:
        """Notify about a skipped oversized repo. Errors are logged and swallowed."""
        if not self._enabled:
            return
        try:
            self.send(
                f":warning: **TokenLeak**: Repository `{repo_url}` skipped — "
                f"size {size_mb:.0f} MB exceeds limit {limit_mb} MB."
            )
        except Exception as exc:
            log.warning("Mattermost send_skipped_large_repo failed for %s: %s", repo_url, exc)

    def send_alerts_csv_file(
        self,
        repo_url: str,
        scan_id: int,
        csv_content: str,
        scan_mode: str = "",
        alert_count: int = 0,
    ) -> None:
        """Upload a CSV alerts report as a file attachment to Mattermost.

        Requires TOKENLEAK_MATTERMOST_CHANNEL_ID to be set — the Mattermost file
        upload API needs a channel_id, not a channel name.  If not configured,
        a warning is logged and the upload is skipped (text notifications still work).

        Errors are logged and swallowed — never kills a scan.
        """
        if not self._enabled:
            return
        if not self._channel_id:
            log.warning(
                "Mattermost: TOKENLEAK_MATTERMOST_CHANNEL_ID is not set — "
                "CSV file upload skipped for scan_id=%d", scan_id,
            )
            return
        if not csv_content:
            return

        repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
        date_str = datetime.date.today().isoformat()
        mode_suffix = f"_{scan_mode}" if scan_mode else ""
        filename = f"tokenleak_{repo_name}{mode_suffix}_{date_str}_scan{scan_id}.csv"

        headers_auth = {"Authorization": f"Bearer {self._token}"}

        try:
            upload = httpx.post(
                f"{self._url}/api/v4/files",
                headers=headers_auth,
                data={"channel_id": self._channel_id},
                files={"files": (filename, csv_content.encode("utf-8"), "text/csv")},
                timeout=60,
            )
            upload.raise_for_status()
            file_id = upload.json()["file_infos"][0]["id"]

            mode_label = f" режим `{scan_mode}`" if scan_mode else ""
            n_label = f"{alert_count} alert(s)" if alert_count else "0 alerts"
            message = (
                f":bar_chart: **TokenLeak — CSV-отчёт** | `{repo_name}`{mode_label} | "
                f"scan_id={scan_id} | {n_label}"
            )
            post = httpx.post(
                f"{self._url}/api/v4/posts",
                headers={**headers_auth, "Content-Type": "application/json"},
                json={
                    "channel_id": self._channel_id,
                    "message": message,
                    "file_ids": [file_id],
                },
                timeout=15,
            )
            post.raise_for_status()
            log.info("Mattermost: CSV report uploaded for scan_id=%d (%s)", scan_id, filename)
        except Exception as exc:
            log.warning("Mattermost send_alerts_csv_file failed for scan_id=%d: %s", scan_id, exc)

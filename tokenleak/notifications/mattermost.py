"""Mattermost notification sender via incoming webhook or personal access token."""

from __future__ import annotations

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
        self._enabled = bool(self._url and self._token)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send(self, message: str, channel: Optional[str] = None) -> None:
        if not self._enabled:
            return
        ch = channel or self._default_channel
        payload = {"channel": ch, "text": message}
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
            log.info("Mattermost: message sent to #%s", ch)
        except httpx.HTTPError as exc:
            log.error("Mattermost send failed: %s", exc)
            raise

    def send_scan_summary(self, repo_url: str, alerts: list, scan_id: int) -> None:
        if not self._enabled:
            return
        severity_counts: dict[str, int] = {}
        for a in alerts:
            severity_counts[a.severity] = severity_counts.get(a.severity, 0) + 1

        lines = [f"### TokenLeak scan complete: `{repo_url}`"]
        if alerts:
            lines.append(f"**{len(alerts)} alert(s) found:**")
            for sev, count in sorted(severity_counts.items()):
                lines.append(f"  - {sev.upper()}: {count}")
            lines.append("")
            for alert in alerts[:10]:
                aj = alert.agent_json or {}
                lines.append(
                    f"- **[{alert.severity.upper()}]** `{alert.file_path}` "
                    f"({alert.alert_type}): {aj.get('description', '')[:120]}"
                )
            if len(alerts) > 10:
                lines.append(f"  … and {len(alerts) - 10} more. See DB scan_id={scan_id}.")
        else:
            lines.append("No secrets or sensitive data found.")

        self.send("\n".join(lines))

    def send_skipped_large_repo(self, repo_url: str, size_mb: float, limit_mb: int) -> None:
        if not self._enabled:
            return
        self.send(
            f":warning: **TokenLeak**: Repository `{repo_url}` skipped — "
            f"size {size_mb:.0f} MB exceeds limit {limit_mb} MB."
        )

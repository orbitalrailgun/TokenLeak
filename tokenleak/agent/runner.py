"""Agent orchestration: two-pass scan of a single repository commit.

Pass 1 — MAP
  The agent receives the file tree and commit log and produces a risk map
  (saved as a note).  It identifies high-risk files and directories.

Pass 2 — DEEP SCAN
  The agent reads individual files (via read_file / read_file_at_commit),
  analyses content for secrets/PII/corporate data, and calls save_alert()
  for every finding.

The agent loop continues until:
  - The AI returns a message with no tool calls (done), OR
  - max_iterations is reached (safety limit).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from tokenleak.agent.client import build_client, chat, extract_usage
from tokenleak.config import Config
from tokenleak.db.base import Database
from tokenleak.logging_setup import get_logger
from tokenleak.mcp_server import server as mcp_server
from tokenleak.scanner.walker import get_commit_log_text, get_file_tree

log = get_logger()

_PASS1_PROMPT = """You are starting a security audit of a git repository.

Your goal in THIS pass:
1. Study the file tree and commit log provided.
2. Identify files, directories, and commits that are HIGH RISK for containing
   secrets, tokens, passwords, PII, or corporate-sensitive information.
3. Call save_note() with a structured risk map: list high-risk files first,
   then medium-risk, briefly note why each is risky.
4. Do NOT call save_alert() in this pass — focus on mapping only.

After saving your note, reply with a plain text summary of the map and stop.
"""

_PASS2_PROMPT = """You are now performing the DEEP SCAN pass on this repository.

Your risk map from Pass 1 is available via get_notes().
Start by reading your notes, then systematically inspect each high-risk file.

For EVERY confirmed finding call save_alert() with:
  - The exact file path and line numbers
  - Alert type: secret | token | pii | corporate_secret | password | key
  - Severity: critical | high | medium | low
  - A clear description of what was found
  - The relevant code snippet (do not include actual credential values verbatim if very long)
  - How the secret appears to be used
  - Your confirmation that it is a real secret (not a placeholder like "CHANGE_ME")

Also check:
  - All commit messages for accidentally committed secrets
  - Deleted files that appear in git history (use read_file_at_commit)
  - Configuration files, .env files, CI/CD configs, deployment scripts

When you have exhaustively inspected all high-risk areas, call send_mattermost()
with a concise summary of findings (if Mattermost is configured), then reply
with a final summary and stop (no more tool calls).
"""


def _load_agent_md(config: Config) -> str:
    path = Path(config.agent_md_path)
    if path.exists():
        return path.read_text(errors="replace")
    return ""


def _call_tool(name: str, arguments: dict) -> str:
    fn = mcp_server.TOOLS.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    try:
        result = fn(**arguments)
        return str(result)
    except Exception as exc:
        log.warning("Tool %s failed: %s", name, exc)
        return f"Tool error: {exc}"


def _agent_loop(
    client,
    model: str,
    system_prompt: str,
    user_message: str,
    tools: list[dict],
    max_iterations: int,
    on_tokens: Optional[Callable[[int], None]] = None,
) -> tuple[int, str]:
    """Run one agent conversation loop. Returns (total_tokens, final_text)."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    total_tokens = 0
    final_text = ""

    for iteration in range(max_iterations):
        response = chat(client, model, messages, tools=tools)
        tokens = extract_usage(response)
        total_tokens += tokens
        if on_tokens:
            on_tokens(total_tokens)

        msg = response.choices[0].message
        # Add assistant message to history
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            final_text = msg.content or ""
            log.debug("Agent done after %d iterations, %d tokens", iteration + 1, total_tokens)
            break

        # Execute all tool calls
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            log.debug("Tool call: %s(%s)", tc.function.name, list(args.keys()))
            result = _call_tool(tc.function.name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })
    else:
        log.warning("Agent reached max_iterations=%d", max_iterations)

    return total_tokens, final_text


def run_scan(
    repo_path: Path,
    scan_id: int,
    db: Database,
    config: Config,
    notifications=None,
    on_tokens: Optional[Callable[[int], None]] = None,
) -> int:
    """Run the full two-pass agent scan. Returns total tokens used."""
    mcp_server.init_context(db, scan_id, repo_path, notifications)

    client = build_client(config)
    agent_instructions = _load_agent_md(config)
    base_system = agent_instructions or (
        "You are a security expert auditing a git repository for leaked secrets, "
        "tokens, passwords, PII, and corporate-sensitive information. "
        "Be thorough. Trust the code context to confirm findings."
    )

    file_tree = get_file_tree(repo_path)
    commit_log = get_commit_log_text(repo_path, limit=200)

    # ── Pass 1: Map ────────────────────────────────────────────────────────────
    log.info("[scan %d] Starting Pass 1 (map)", scan_id)
    pass1_user = (
        f"REPOSITORY FILE TREE:\n```\n{file_tree}\n```\n\n"
        f"RECENT COMMIT LOG:\n```\n{commit_log}\n```\n\n"
        "Build your risk map now."
    )
    tokens1, _ = _agent_loop(
        client=client,
        model=config.ai_model,
        system_prompt=base_system + "\n\n" + _PASS1_PROMPT,
        user_message=pass1_user,
        tools=mcp_server.TOOL_SCHEMAS,
        max_iterations=config.ai_max_iterations,
        on_tokens=on_tokens,
    )

    # ── Pass 2: Deep scan ──────────────────────────────────────────────────────
    log.info("[scan %d] Starting Pass 2 (deep scan)", scan_id)
    tokens2, final_text = _agent_loop(
        client=client,
        model=config.ai_model,
        system_prompt=base_system + "\n\n" + _PASS2_PROMPT,
        user_message="Begin the deep scan. Start by reading your notes from Pass 1.",
        tools=mcp_server.TOOL_SCHEMAS,
        max_iterations=config.ai_max_iterations,
        on_tokens=on_tokens,
    )

    total = tokens1 + tokens2
    db.update_scan_tokens(scan_id, total)
    log.info("[scan %d] Agent finished. Total tokens: %d", scan_id, total)
    return total

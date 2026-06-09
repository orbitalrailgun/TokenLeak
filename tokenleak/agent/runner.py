"""Agent orchestration: two scan modes for a single repository commit.

DIFF MODE  (used for individual commits in `scan`)
  Fast and token-efficient. The agent receives only the *added* lines of the
  commit diff, pre-filtered by entropy/regex locally. A single agent pass
  analyses the candidates and calls save_alert() directly — no read_file
  loop needed. The agent can still call read_file() for surrounding context.

FULL MODE  (used for HEAD in `scan` first run and in `rescan`)
  Thorough two-pass scan. Pass 1 builds a risk map of the full repo structure.
  Pass 2 reads every high-risk file in full and analyses it.

In both modes the agent loop runs until:
  - The AI returns a message with no tool calls (done), OR
  - max_iterations is reached (safety limit).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

from tokenleak.agent.client import build_client, chat, extract_usage, ContextWindowExceededError
from tokenleak.config import Config
from tokenleak.db.base import Database, ScanRow
from tokenleak.logging_setup import get_logger
from tokenleak.mcp_server import server as mcp_server
from tokenleak.scanner.prefilter import filter_file, should_send_to_ai
from tokenleak.scanner.walker import (
    DiffAdditions,
    get_commit_diff_additions,
    get_commit_image_files,
    get_commit_notebooks,
    get_repo_image_files,
    get_repo_notebooks,
    get_commit_log_text as _walker_commit_log,
    get_file_tree as _walker_file_tree,
)

log = get_logger()

_MAX_DIFF_CHARS = 400_000  # ~100K tokens; stays well under GLM-4.7's 202K-token limit

# ── Prompts ───────────────────────────────────────────────────────────────────

_DIFF_SCAN_PROMPT = """You are performing a security audit of a single git commit diff.

You are given ONLY the lines that were ADDED in this commit (pre-filtered for
likely secrets by local entropy and regex analysis). Your task:

1. For EACH line or block of lines that contains a confirmed secret, token,
   password, PII, or corporate-sensitive value — call save_alert().
2. If you need surrounding context, use read_file() to read the full file.
3. Ignore placeholder values like "CHANGE_ME", "your-key-here", "example.com".
4. Ignore masked/redacted values — any value that is entirely repeated masking
   characters such as asterisks (***...), dashes (---...), or hashes (###...) is
   a redacted placeholder, not a real secret. Do NOT flag these.
4. When finished, reply with a plain-text summary and stop (no more tool calls).
   Do NOT call send_mattermost() — per-alert notifications are sent automatically
   when you call save_alert(), and the overall repo summary is sent separately.

For each save_alert() call provide:
  - file_path, line_start, line_end
  - alert_type: secret | token | pii | corporate_secret | password | key
  - severity: critical | high | medium | low
  - description, code_snippet, how_used, confirmation
"""

_PASS1_PROMPT = """You are starting a security audit of a git repository.

Your goal in THIS pass:
1. Study ONLY the file tree and commit log provided in this message.
2. Identify files, directories, and commits that are HIGH RISK for containing
   secrets, tokens, passwords, PII, or corporate-sensitive information.
3. Call save_note() ONCE with a structured risk map: high-risk files first,
   then medium-risk, with a brief reason for each.
4. Do NOT call save_alert() in this pass.
5. Do NOT call read_file(), search_content(), list_files(), or any other tool
   except save_note() — the file tree and commit log are sufficient for the map.
   File reading happens in Pass 2.

After saving your single note, reply with a brief summary and stop immediately.
"""

_PASS2_PROMPT = """You are now performing the DEEP SCAN pass on this repository.

Start by reading your Pass 1 notes via get_notes(), then systematically inspect
each high-risk file with read_file().

For EVERY confirmed finding call save_alert() with:
  - The exact file path and line numbers
  - alert_type: secret | token | pii | corporate_secret | password | key
  - severity: critical | high | medium | low
  - A clear description, the relevant code snippet, how the secret is used,
    and your confirmation that it is real (not a placeholder).

Also check:
  - Commit messages for accidentally committed secrets
  - Deleted files in git history (use read_file_at_commit)
  - CI/CD configs, deployment scripts, .env files

Efficiency rules — IMPORTANT:
  - You may call search_content() at most 5 times IN TOTAL across all iterations.
    After 5 calls, stop using search_content and move on.
  - Call read_file() only for files flagged in your Pass 1 risk map.
  - For image files (.png, .jpg, .jpeg, .gif, .webp) and Jupyter notebooks (.ipynb),
    call analyze_image_file() to OCR-scan for credentials or sensitive data in screenshots.
  - Do not call get_commit_log() or get_file_tree() — that info is already in your notes.
  - Once you have checked all high-risk files, stop and give your summary.

Never flag masked or redacted values — a credential value that consists entirely
of repeated characters like asterisks (****), dashes (----), or hashes (####) is
a placeholder, not a real leaked secret.

When done, call send_mattermost() ONCE with a brief summary of all findings
(if configured), then reply with a plain-text summary and stop.
NOTE: do NOT call send_mattermost() instead of save_alert() — Mattermost
notifications for individual alerts are sent automatically when you call save_alert().
"""

_DEFAULT_SYSTEM = (
    "You are a security expert auditing a git repository for leaked secrets, "
    "tokens, passwords, PII, and corporate-sensitive information. "
    "Be thorough. Trust the code context to confirm findings."
)


# ── Shared agent loop ─────────────────────────────────────────────────────────

def _call_tool(name: str, arguments: dict) -> str:
    fn = mcp_server.TOOLS.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    try:
        return str(fn(**arguments))
    except Exception as exc:
        log.warning("Tool %s failed (scan_id=%s): %s", name, mcp_server._scan_id, exc)
        return f"Tool error: {exc}"


def _tool_status(name: str, args: dict) -> str:
    """Format a short human-readable status string for an MCP tool call."""
    if name == "read_file":
        return f"⚙  read_file → {args.get('path', '')}"
    if name == "read_file_at_commit":
        sha = args.get("commit_sha", "")[:8]
        return f"⚙  read_file_at_commit → {args.get('path', '')} @{sha}"
    if name == "save_alert":
        sev = args.get("severity", "?")
        fp = args.get("file_path", "")
        return f"⚙  save_alert [{sev}] → {fp}"
    if name == "save_note":
        snippet = str(args.get("content", ""))[:40].replace("\n", " ")
        return f"⚙  save_note → {snippet}…"
    if name == "get_notes":
        return "⚙  get_notes"
    if name == "search_content":
        return f'⚙  search_content → "{args.get("pattern", "")}"'
    if name == "list_files":
        return f"⚙  list_files → {args.get('pattern', '*')}"
    if name == "get_file_tree":
        return "⚙  get_file_tree"
    if name == "get_commit_log":
        return "⚙  get_commit_log"
    if name == "send_mattermost":
        return "⚙  send_mattermost"
    if name == "analyze_image_file":
        return f"🖼  analyze_image_file → {args.get('path', '')}"
    return f"⚙  {name}"


def _agent_loop(
    client,
    model: str,
    system_prompt: str,
    user_message: str,
    tools: list[dict],
    max_iterations: int,
    on_tokens: Optional[Callable[[int], None]] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> tuple[int, str]:
    """Run one agent conversation. Returns (total_tokens, final_text)."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    total_tokens = 0
    final_text = ""

    for iteration in range(max_iterations):
        if on_status:
            on_status(f"🧠 thinking… (iteration {iteration + 1})")
        try:
            response = chat(client, model, messages, tools=tools)
        except ContextWindowExceededError as exc:
            log.warning(
                "Context window exceeded after %d iteration(s) — stopping agent loop. "
                "Alerts saved so far are preserved. (%s)",
                iteration, exc,
            )
            if on_status:
                on_status("⚠  context window full — stopping early")
            break
        tokens = extract_usage(response)
        total_tokens += tokens
        if on_tokens:
            on_tokens(tokens)  # incremental, not cumulative — counter.add() accumulates itself

        msg = response.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            final_text = msg.content or ""
            if on_status:
                on_status("✓ analysis complete")
            log.debug("Agent done after %d iterations, %d tokens", iteration + 1, total_tokens)
            break

        for tc in msg.tool_calls:
            raw = tc.function.arguments
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                # Model occasionally emits invalid JSON escape sequences (e.g. \p, \e).
                # Valid JSON escapes: " \ / b f n r t uXXXX — replace everything else.
                try:
                    repaired = re.sub(r'\\(?!["\\/bfnrtu]|u[0-9a-fA-F]{4})', r'\\\\', raw)
                    args = json.loads(repaired)
                    log.warning(
                        "Repaired invalid JSON escape in %s arguments", tc.function.name
                    )
                except json.JSONDecodeError as exc:
                    log.error(
                        "Failed to parse tool arguments for %s: %s", tc.function.name, exc
                    )
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "content": f"Tool error: invalid JSON in arguments: {exc}",
                    })
                    continue
            log.debug("Tool call: %s(%s)", tc.function.name, list(args.keys()))
            if on_status:
                on_status(_tool_status(tc.function.name, args))
            result = _call_tool(tc.function.name, args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
    else:
        log.warning("Agent reached max_iterations=%d", max_iterations)

    return total_tokens, final_text


def _load_agent_md(config: Config) -> str:
    path = Path(config.agent_md_path)
    if path.exists():
        return path.read_text(errors="replace")
    return ""


# ── OCR helpers ───────────────────────────────────────────────────────────────

def _ocr_images(
    image_map: dict[str, bytes],
    client,
    model: str,
    scan_id: int,
    db: Database,
    on_tokens: Optional[Callable[[int], None]],
    on_status: Optional[Callable[[str], None]],
    repo_id: Optional[int] = None,
    commit_sha: Optional[str] = None,
    commit_date=None,
    triggered_by: Optional[str] = None,
) -> int:
    """Run OCR on a map of {path: raw_bytes}. Saves alerts directly. Returns tokens used."""
    from tokenleak.scanner.ocr import analyze_image, mime_for_extension
    total = 0
    for img_path, img_bytes in image_map.items():
        mime = mime_for_extension(Path(img_path).suffix) or "image/png"
        if on_status:
            on_status(f"🖼  OCR → {img_path}")
        finding, tokens = analyze_image(client, model, img_bytes, mime, context=img_path)
        total += tokens
        if on_tokens:
            on_tokens(tokens)
        if finding:
            db.save_alert(
                scan_id=scan_id,
                file_path=img_path,
                line_start=0,
                line_end=0,
                alert_type="secret",
                severity="high",
                agent_json={"description": finding, "source": "ocr"},
                repo_id=repo_id,
                commit_sha=commit_sha,
                commit_date=commit_date,
                triggered_by=triggered_by,
                ai_model=model or None,
            )
            log.info("[scan %d] OCR alert in image file: %s", scan_id, img_path)
    return total


def _ocr_notebooks(
    notebook_map: dict,
    client,
    model: str,
    scan_id: int,
    db: Database,
    on_tokens: Optional[Callable[[int], None]],
    on_status: Optional[Callable[[str], None]],
    repo_id: Optional[int] = None,
    commit_sha: Optional[str] = None,
    commit_date=None,
    triggered_by: Optional[str] = None,
) -> int:
    """Run OCR on images embedded in notebooks. notebook_map is {path: json_text | Path}.
    Saves alerts directly. Returns tokens used.
    """
    from tokenleak.scanner.ocr import analyze_image, extract_notebook_images
    total = 0
    for nb_path, nb_source in notebook_map.items():
        if hasattr(nb_source, "read_text"):
            # Path object (from get_repo_notebooks)
            try:
                nb_text = nb_source.read_text(errors="replace")
            except OSError:
                continue
        else:
            nb_text = nb_source  # already a string (from get_commit_notebooks)

        images = extract_notebook_images(nb_text)
        for cell_idx, mime, img_bytes in images:
            context = f"{nb_path} cell[{cell_idx}]"
            if on_status:
                on_status(f"📓  OCR → {context}")
            finding, tokens = analyze_image(client, model, img_bytes, mime, context=context)
            total += tokens
            if on_tokens:
                on_tokens(tokens)
            if finding:
                db.save_alert(
                    scan_id=scan_id,
                    file_path=nb_path,
                    line_start=0,
                    line_end=0,
                    alert_type="secret",
                    severity="high",
                    agent_json={
                        "description": finding,
                        "source": "ocr",
                        "cell_index": cell_idx,
                    },
                    repo_id=repo_id,
                    commit_sha=commit_sha,
                    commit_date=commit_date,
                    triggered_by=triggered_by,
                    ai_model=model or None,
                )
                log.info("[scan %d] OCR alert in notebook: %s cell[%d]", scan_id, nb_path, cell_idx)
    return total


def _run_ocr_for_commit(
    repo_path: Path,
    sha: str,
    db: Database,
    scan_id: int,
    config: Config,
    client,
    on_tokens: Optional[Callable[[int], None]] = None,
    on_status: Optional[Callable[[str], None]] = None,
    repo_id: Optional[int] = None,
    commit_date=None,
    triggered_by: Optional[str] = None,
) -> int:
    """OCR scan of all images and notebook outputs added/modified in one commit.
    Returns total OCR tokens used.
    """
    model = config.ocr_model
    if on_status:
        on_status("🖼  OCR: extracting images from commit…")

    image_files = get_commit_image_files(repo_path, sha)
    notebooks = get_commit_notebooks(repo_path, sha)

    if not image_files and not notebooks:
        log.debug("[scan %d] OCR: no images or notebooks in commit %s", scan_id, sha[:8])
        return 0

    log.info("[scan %d] OCR: %d image file(s), %d notebook(s) in commit %s",
             scan_id, len(image_files), len(notebooks), sha[:8])

    tokens = _ocr_images(
        image_files, client, model, scan_id, db, on_tokens, on_status,
        repo_id=repo_id, commit_sha=sha, commit_date=commit_date,
        triggered_by=triggered_by,
    )
    tokens += _ocr_notebooks(
        notebooks, client, model, scan_id, db, on_tokens, on_status,
        repo_id=repo_id, commit_sha=sha, commit_date=commit_date,
        triggered_by=triggered_by,
    )

    if on_status:
        on_status(f"✓ OCR done ({tokens:,} tokens)")
    log.info("[scan %d] OCR commit scan done. Tokens: %d", scan_id, tokens)
    return tokens


def _run_ocr_for_repo(
    repo_path: Path,
    db: Database,
    scan_id: int,
    config: Config,
    client,
    on_tokens: Optional[Callable[[int], None]] = None,
    on_status: Optional[Callable[[str], None]] = None,
    repo_id: Optional[int] = None,
    commit_sha: Optional[str] = None,
    commit_date=None,
    triggered_by: Optional[str] = None,
) -> int:
    """OCR scan of ALL image files and notebooks in the current HEAD of the repo.
    Used as a safety net after full scan Pass 2.
    Returns total OCR tokens used.
    """
    model = config.ocr_model
    if on_status:
        on_status("🖼  OCR: scanning all repo images…")

    image_files_map = get_repo_image_files(repo_path)
    notebooks_map = get_repo_notebooks(repo_path)

    if not image_files_map and not notebooks_map:
        log.debug("[scan %d] OCR: no images or notebooks in repo", scan_id)
        return 0

    log.info("[scan %d] OCR full repo: %d image file(s), %d notebook(s)",
             scan_id, len(image_files_map), len(notebooks_map))

    # Convert Path values to bytes for _ocr_images
    image_bytes_map: dict[str, bytes] = {}
    for rel_path, full_path in image_files_map.items():
        try:
            image_bytes_map[rel_path] = full_path.read_bytes()
        except OSError:
            pass

    tokens = _ocr_images(
        image_bytes_map, client, model, scan_id, db, on_tokens, on_status,
        repo_id=repo_id, commit_sha=commit_sha, commit_date=commit_date,
        triggered_by=triggered_by,
    )
    tokens += _ocr_notebooks(
        notebooks_map, client, model, scan_id, db, on_tokens, on_status,
        repo_id=repo_id, commit_sha=commit_sha, commit_date=commit_date,
        triggered_by=triggered_by,
    )

    if on_status:
        on_status(f"✓ OCR full scan done ({tokens:,} tokens)")
    log.info("[scan %d] OCR full repo scan done. Tokens: %d", scan_id, tokens)
    return tokens


# ── Diff mode helpers ─────────────────────────────────────────────────────────

def _format_diff_for_agent(
    sha: str,
    author: str,
    message: str,
    candidates: DiffAdditions,
) -> str:
    """Format pre-filtered diff additions as a readable block for the agent."""
    header = [
        f"## Commit `{sha[:12]}` by {author}",
        f"## Message: {message!r}",
        "",
    ]
    parts = list(header)
    total_chars = sum(len(p) + 1 for p in header)

    for file_path, lines in candidates.items():
        file_parts = [f"### File: `{file_path}`"]
        for lineno, content in lines:
            file_parts.append(f"  Line {lineno}: {content}")
        file_parts.append("")
        file_str = "\n".join(file_parts)

        if len(file_str) > _MAX_DIFF_CHARS // 2:
            # Single file too large on its own — skip and continue to next file
            parts.append(f"### File: `{file_path}` [SKIPPED — file diff too large ({len(file_str):,} chars)]")
            log.warning("Skipping large file in diff: %s (%d chars)", file_path, len(file_str))
            continue

        if total_chars + len(file_str) > _MAX_DIFF_CHARS:
            # Accumulated content too large — stop here
            parts.append(f"### File: `{file_path}` [OMITTED — cumulative diff budget exhausted]")
            log.warning("Diff budget exhausted before %s (accumulated %d chars)", file_path, total_chars)
            break

        parts.append(file_str)
        total_chars += len(file_str) + 1

    return "\n".join(parts)


def _prefilter_diff(
    additions: DiffAdditions,
    prefilter_enabled: bool,
    on_file_progress: Optional[Callable[[int, int], None]] = None,
) -> DiffAdditions:
    """Return only the files/lines that pass the pre-filter."""
    total = len(additions)
    if not prefilter_enabled:
        if on_file_progress:
            on_file_progress(total, total)
        return additions

    candidates: DiffAdditions = {}
    for i, (file_path, lines) in enumerate(additions.items()):
        if on_file_progress:
            on_file_progress(i, total)
        synthetic_content = "\n".join(line for _, line in lines)
        result = filter_file(Path(file_path), synthetic_content)
        if should_send_to_ai(result, enabled=True):
            candidates[file_path] = lines
    if on_file_progress:
        on_file_progress(total, total)
    return candidates


# ── Public API ────────────────────────────────────────────────────────────────

def run_diff_scan(
    repo_path: Path,
    scan_id: int,
    commit_sha: str,
    commit_author: str,
    commit_message: str,
    db: Database,
    config: Config,
    notifications=None,
    on_tokens: Optional[Callable[[int], None]] = None,
    on_status: Optional[Callable[[str], None]] = None,
    on_file_progress: Optional[Callable[[int, int], None]] = None,
    repo_id: Optional[int] = None,
    commit_date=None,
    triggered_by: Optional[str] = None,
) -> int:
    """Scan only the diff (added lines) of one commit.

    Flow:
      1. Extract added lines via git show --unified=0
      2. Pre-filter with entropy + regex (unless disabled)
      3. Single agent pass: AI gets diff content directly, no read_file loop
      4. Agent calls save_alert() for each confirmed finding

    Returns total tokens used.
    """
    client = build_client(config)
    mcp_server.init_context(
        db, scan_id, repo_path, notifications,
        ocr_client=client if config.ocr_model else None,
        ocr_model=config.ocr_model,
        ai_model=config.ai_model,
        repo_id=repo_id,
        commit_sha=commit_sha,
        commit_date=commit_date,
        triggered_by=triggered_by,
    )
    system = _load_agent_md(config) or _DEFAULT_SYSTEM

    if on_status:
        on_status(f"📂 extracting diff {commit_sha[:8]}…")
    additions = get_commit_diff_additions(repo_path, commit_sha)
    if not additions:
        log.info("[scan %d] Empty diff for %s, skipping", scan_id, commit_sha[:8])
        if on_status:
            on_status("⏭  empty diff — skipped")
        return 0

    if on_file_progress:
        on_file_progress(0, len(additions))
    if on_status:
        on_status(f"🔎 prefiltering {len(additions)} file(s)…")
    candidates = _prefilter_diff(additions, config.prefilter_enabled, on_file_progress)
    if not candidates:
        log.info(
            "[scan %d] Pre-filter: no candidates in %s (%d file(s) checked)",
            scan_id, commit_sha[:8], len(additions),
        )
        if on_status:
            on_status(f"✓ prefilter: no candidates in {len(additions)} file(s)")
        tokens = 0
    else:
        log.info(
            "[scan %d] Diff candidates: %d/%d file(s) pass pre-filter",
            scan_id, len(candidates), len(additions),
        )
        if on_status:
            on_status(f"🧪 {len(candidates)}/{len(additions)} files passed prefilter → sending to AI")

        diff_text = _format_diff_for_agent(commit_sha, commit_author, commit_message, candidates)
        tokens, _ = _agent_loop(
            client=client,
            model=config.ai_model,
            system_prompt=system + "\n\n" + _DIFF_SCAN_PROMPT,
            user_message=diff_text,
            tools=mcp_server.TOOL_SCHEMAS,
            max_iterations=config.ai_max_iterations,
            on_tokens=on_tokens,
            on_status=on_status,
        )

    if config.ocr_model:
        ocr_tokens = _run_ocr_for_commit(
            repo_path, commit_sha, db, scan_id, config, client,
            on_tokens=on_tokens, on_status=on_status,
            repo_id=repo_id, commit_date=commit_date,
            triggered_by=triggered_by,
        )
        tokens += ocr_tokens

    db.update_scan_tokens(scan_id, tokens)
    log.info("[scan %d] Diff scan done. Tokens: %d", scan_id, tokens)
    return tokens


def run_full_scan(
    repo_path: Path,
    scan_id: int,
    db: Database,
    config: Config,
    notifications=None,
    on_tokens: Optional[Callable[[int], None]] = None,
    on_status: Optional[Callable[[str], None]] = None,
    on_file_progress: Optional[Callable[[int, int], None]] = None,
    repo_id: Optional[int] = None,
    commit_sha: Optional[str] = None,
    commit_date=None,
    triggered_by: Optional[str] = None,
) -> int:
    """Two-pass full-file scan of the repository at its current state.

    Pass 1: Agent builds a risk map from the file tree and commit log.
    Pass 2: Agent reads high-risk files in full and saves alerts.

    Returns total tokens used.
    """
    client = build_client(config)
    mcp_server.init_context(
        db, scan_id, repo_path, notifications,
        ocr_client=client if config.ocr_model else None,
        ocr_model=config.ocr_model,
        ai_model=config.ai_model,
        repo_id=repo_id,
        commit_sha=commit_sha or "",
        commit_date=commit_date,
        triggered_by=triggered_by,
    )
    system = _load_agent_md(config) or _DEFAULT_SYSTEM

    if on_status:
        on_status("📁 loading file tree & commit log…")
    file_tree = _walker_file_tree(repo_path)
    commit_log = _walker_commit_log(repo_path, limit=200)

    if on_file_progress:
        on_file_progress(0, 2)
    log.info("[scan %d] Full scan — Pass 1 (map)", scan_id)
    if on_status:
        on_status("🗺  Pass 1 — building risk map…")
    tokens1, _ = _agent_loop(
        client=client,
        model=config.ai_model,
        system_prompt=system + "\n\n" + _PASS1_PROMPT,
        user_message=(
            f"REPOSITORY FILE TREE:\n```\n{file_tree}\n```\n\n"
            f"RECENT COMMIT LOG:\n```\n{commit_log}\n```\n\n"
            "Build your risk map now."
        ),
        tools=mcp_server.TOOL_SCHEMAS,
        max_iterations=config.ai_max_iterations,
        on_tokens=on_tokens,
        on_status=on_status,
    )

    if on_file_progress:
        on_file_progress(1, 2)
    log.info("[scan %d] Full scan — Pass 2 (deep scan)", scan_id)
    if on_status:
        on_status("🔬 Pass 2 — deep file scan…")
    tokens2, _ = _agent_loop(
        client=client,
        model=config.ai_model,
        system_prompt=system + "\n\n" + _PASS2_PROMPT,
        user_message="Begin the deep scan. Start by reading your notes from Pass 1.",
        tools=mcp_server.TOOL_SCHEMAS,
        max_iterations=config.ai_max_iterations,
        on_tokens=on_tokens,
        on_status=on_status,
    )

    if on_file_progress:
        on_file_progress(2, 2)
    ocr_tokens = 0
    if config.ocr_model:
        ocr_tokens = _run_ocr_for_repo(
            repo_path, db, scan_id, config, client,
            on_tokens=on_tokens, on_status=on_status,
            repo_id=repo_id, commit_sha=commit_sha, commit_date=commit_date,
            triggered_by=triggered_by,
        )

    total = tokens1 + tokens2 + ocr_tokens
    db.update_scan_tokens(scan_id, total)
    log.info("[scan %d] Full scan done. Tokens: %d", scan_id, total)
    return total

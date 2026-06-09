"""Command-line interface and main scanning orchestration."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table

from tokenleak import __version__
from tokenleak.config import Config, get_config
from tokenleak.db import create_db
from tokenleak.db.base import ScanStatus
from tokenleak.logging_setup import setup_logging, get_logger
from tokenleak.providers import resolve_targets
from tokenleak.scanner import clone as clone_mod
from tokenleak.scanner.walker import (
    list_commits, get_head_sha, list_branch_tips,
    checkout_detach, checkout_previous,
    get_head_branch, get_head_file_count, get_diff_added_lines,
)
from tokenleak.animation import TokenCounter, set_enabled, simple_print
from tokenleak.notifications.mattermost import Mattermost
from tokenleak.report.markdown import generate as gen_report, write_report

console = Console()
log = get_logger()


# ── Input list helpers ────────────────────────────────────────────────────────

def _load_targets_from_file(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        log.warning("Repos list not found: %s", path)
        return []
    return [line.strip() for line in p.read_text().splitlines() if line.strip() and not line.startswith("#")]


def _fetch_config_repo(config: Config) -> Optional[Path]:
    """Clone the config repository and return its local path."""
    if not config.config_repo_url:
        return None
    log.info("Fetching config repo: %s", config.config_repo_url)
    try:
        return clone_mod.clone(config.config_repo_url, config)
    except Exception as exc:
        log.error("Cannot fetch config repo: %s", exc)
        return None


def collect_targets(cli_targets: list[str], config: Config) -> list[str]:
    """Resolve all targets to a deduplicated list of plain git clone URLs."""
    if cli_targets:
        raw = cli_targets
    else:
        config_path = None
        if config.config_repo_url:
            config_path = _fetch_config_repo(config)

        list_file = (
            str(config_path / config.repos_list_path)
            if config_path
            else config.repos_list_path
        )
        raw = _load_targets_from_file(list_file)

        if config_path:
            clone_mod.remove(config_path)

    urls = list(dict.fromkeys(resolve_targets(raw, config)))  # dedup, preserve order
    log.info("Collected %d target repo(s)", len(urls))
    return urls


# ── Per-repo scanning ─────────────────────────────────────────────────────────

def scan_repo(
    url: str,
    config: Config,
    db,
    mm: Mattermost,
    rescan: bool,
    sha_filter: Optional[str],
) -> None:
    """Scan a single repository.

    Behavioral model
    ────────────────
    scan (first run for this repo):
        Full scan of HEAD, then diff scan every historical commit.
    scan (subsequent runs):
        Diff scan only commits newer than the last scanned commit.
    rescan:
        Always behaves like first run (full HEAD + all historical diffs).
    scan --sha X:
        Diff scan that specific commit only.
    rescan --sha X:
        Full scan at that specific commit (treats HEAD as that commit).
    """
    from tokenleak.agent.runner import run_diff_scan, run_full_scan
    from tokenleak.agent.client import InsufficientFundsError

    triggered_by = "rescan" if rescan else "scan"
    provider = _guess_provider(url)
    repo_id = db.upsert_repo(url, provider, name=url.rstrip("/").split("/")[-1].removesuffix(".git"))
    repo_path: Optional[Path] = None
    counter = TokenCounter(model=config.ai_model)

    try:
        try:
            repo_path = clone_mod.clone(url, config)
        except RuntimeError as exc:
            log.error("Clone failed for %s: %s", url, exc)
            console.print(f"[red]Clone failed:[/red] {url}\n{exc}")
            return

        size_mb = clone_mod.repo_size_mb(repo_path)
        if size_mb > config.max_repo_size_mb:
            msg = f"Skipping {url} — size {size_mb:.0f} MB > limit {config.max_repo_size_mb} MB"
            log.warning(msg)
            console.print(f"[yellow]⚠ {msg}[/yellow]")
            mm.send_skipped_large_repo(url, size_mb, config.max_repo_size_mb)
            return

        # All commits in the repo, newest first, no merge commits
        all_commits = list_commits(repo_path, skip_merges=True)

        # Start the single animation for this entire repo scan
        counter.start()

        # ── --sha filter: single-commit mode ──────────────────────────────────
        if sha_filter:
            target = next((c for c in all_commits if c.sha.startswith(sha_filter)), None)
            if not target:
                console.print(f"[yellow]No matching commit for --sha {sha_filter} in {url}[/yellow]")
                return

            if rescan:
                # rescan --sha: full scan (HEAD treated as this commit's state)
                console.print(f"[dim]Mode: full (rescan --sha) | Commit: {target.sha[:8]}[/dim]")
                scan_id = db.create_scan(
                    repo_id, target.sha, target.message, target.author, target.date,
                    scan_mode="full", ai_model=config.ai_model,
                )
                db.start_scan(scan_id)
                head_branch = get_head_branch(repo_path)
                file_count = get_head_file_count(repo_path)
                counter.set_commit(
                    target.sha,
                    branch=head_branch,
                    author=target.author,
                    date=target.date,
                    mode="full",
                    data_size=f"{file_count} files",
                )
                try:
                    run_full_scan(
                        repo_path=repo_path, scan_id=scan_id, db=db, config=config,
                        notifications=mm if mm.enabled else None,
                        on_tokens=counter.add, on_status=counter.set_action,
                        on_file_progress=counter.set_file_progress,
                        repo_id=repo_id, commit_sha=target.sha, commit_date=target.date,
                        triggered_by=triggered_by,
                    )
                    db.finish_scan(scan_id, ScanStatus.DONE)
                except InsufficientFundsError as exc:
                    db.finish_scan(scan_id, ScanStatus.ERROR, error=str(exc))
                    raise
                except Exception as exc:
                    log.error("Scan error for %s@%s: %s", url, target.sha[:8], exc)
                    db.finish_scan(scan_id, ScanStatus.ERROR, error=str(exc))
            else:
                # scan --sha: diff scan that one commit
                console.print(f"[dim]Mode: diff (scan --sha) | Commit: {target.sha[:8]}[/dim]")
                scan_id = db.create_scan(
                    repo_id, target.sha, target.message, target.author, target.date,
                    scan_mode="diff", ai_model=config.ai_model,
                )
                db.start_scan(scan_id)
                added_lines = get_diff_added_lines(repo_path, target.sha)
                counter.set_commit(
                    target.sha,
                    author=target.author,
                    date=target.date,
                    mode="diff",
                    data_size=f"{added_lines:,} lines",
                )
                try:
                    run_diff_scan(
                        repo_path=repo_path, scan_id=scan_id,
                        commit_sha=target.sha, commit_author=target.author,
                        commit_message=target.message, db=db, config=config,
                        notifications=mm if mm.enabled else None,
                        on_tokens=counter.add, on_status=counter.set_action,
                        on_file_progress=counter.set_file_progress,
                        repo_id=repo_id, commit_date=target.date,
                        triggered_by=triggered_by,
                    )
                    db.finish_scan(scan_id, ScanStatus.DONE)
                except InsufficientFundsError as exc:
                    db.finish_scan(scan_id, ScanStatus.ERROR, error=str(exc))
                    raise
                except Exception as exc:
                    log.error("Scan error for %s@%s: %s", url, target.sha[:8], exc)
                    db.finish_scan(scan_id, ScanStatus.ERROR, error=str(exc))
            _post_scan(db, scan_id, mm, url, config)
            return

        # ── Normal scan / rescan ───────────────────────────────────────────────
        # Determine which commits have already been successfully scanned
        done_shas: set[str] = set()
        if not rescan:
            for s in db.list_scans(repo_id=repo_id):
                # Include scans for the current model AND legacy scans where
                # ai_model is NULL (created before per-model tracking was added).
                if s.status == ScanStatus.DONE and (
                    s.ai_model == config.ai_model or s.ai_model is None
                ):
                    done_shas.add(s.commit_sha)

        first_run = rescan or not done_shas

        if first_run:
            # Pre-compute history so we know the total commit count up front
            history = all_commits[1:]
            _ctotal = 1 + len(history)   # HEAD full scan + all history diffs
            _coffset = 1                  # history loop starts after HEAD scan
            counter.set_commit_progress(0, _ctotal)

            # Phase 1a: full scan of HEAD (default branch)
            head_commit = all_commits[0] if all_commits else None
            if head_commit:
                branch_count = len(list_branch_tips(repo_path, exclude_shas={head_commit.sha})) if config.scan_all_branches else 0
                mode_label = f"full (HEAD + {branch_count} branch tip(s)) + diff (history)" if branch_count else "full (HEAD) + diff (history)"
                console.print(f"[dim]Mode: {mode_label} | Commits: {len(all_commits)}[/dim]")
                scan_id = db.create_scan(
                    repo_id, head_commit.sha, head_commit.message,
                    head_commit.author, head_commit.date, scan_mode="full",
                    ai_model=config.ai_model,
                )
                db.start_scan(scan_id)
                head_branch = get_head_branch(repo_path)
                file_count = get_head_file_count(repo_path)
                counter.set_commit(
                    head_commit.sha,
                    branch=head_branch,
                    author=head_commit.author,
                    date=head_commit.date,
                    mode="full",
                    data_size=f"{file_count} files",
                )
                try:
                    run_full_scan(
                        repo_path=repo_path, scan_id=scan_id, db=db, config=config,
                        notifications=mm if mm.enabled else None,
                        on_tokens=counter.add, on_status=counter.set_action,
                        on_file_progress=counter.set_file_progress,
                        repo_id=repo_id, commit_sha=head_commit.sha,
                        commit_date=head_commit.date, triggered_by=triggered_by,
                    )
                    db.finish_scan(scan_id, ScanStatus.DONE)
                    done_shas.add(head_commit.sha)
                except InsufficientFundsError as exc:
                    db.finish_scan(scan_id, ScanStatus.ERROR, error=str(exc))
                    raise
                except Exception as exc:
                    log.error("Full scan error for %s: %s", url, exc)
                    db.finish_scan(scan_id, ScanStatus.ERROR, error=str(exc))
                counter.set_commit_progress(1, _ctotal)
                _post_scan(db, scan_id, mm, url, config)

            # Phase 1b: full scan of every other branch tip (if enabled)
            if config.scan_all_branches:
                branch_tips = list_branch_tips(repo_path, exclude_shas=done_shas)
                for tip in branch_tips:
                    log.info("Full scan of branch tip %s for %s", tip.sha[:8], url)
                    scan_id = db.create_scan(
                        repo_id, tip.sha, tip.message, tip.author, tip.date,
                        scan_mode="full", ai_model=config.ai_model,
                    )
                    db.start_scan(scan_id)
                    checked_out = False
                    try:
                        checkout_detach(repo_path, tip.sha)
                        checked_out = True
                        tip_file_count = get_head_file_count(repo_path)
                        counter.set_commit(
                            tip.sha,
                            branch=tip.branch,
                            author=tip.author,
                            date=tip.date,
                            mode="full",
                            data_size=f"{tip_file_count} files",
                        )
                        run_full_scan(
                            repo_path=repo_path, scan_id=scan_id, db=db, config=config,
                            notifications=mm if mm.enabled else None,
                            on_tokens=counter.add, on_status=counter.set_action,
                            on_file_progress=counter.set_file_progress,
                            repo_id=repo_id, commit_sha=tip.sha,
                            commit_date=tip.date, triggered_by=triggered_by,
                        )
                        db.finish_scan(scan_id, ScanStatus.DONE)
                        done_shas.add(tip.sha)
                    except InsufficientFundsError as exc:
                        db.finish_scan(scan_id, ScanStatus.ERROR, error=str(exc))
                        raise
                    except Exception as exc:
                        log.error("Branch tip full scan error for %s@%s: %s",
                                  url, tip.sha[:8], exc)
                        db.finish_scan(scan_id, ScanStatus.ERROR, error=str(exc))
                    finally:
                        if checked_out:
                            try:
                                checkout_previous(repo_path)
                            except Exception as e:
                                log.warning("Failed to restore HEAD after branch scan: %s", e)
                    _post_scan(db, scan_id, mm, url, config)

            # Phase 2: diff scan all historical commits (skip HEAD and branch tips, already full-scanned)
            # history was pre-computed above
        else:
            # Subsequent run: diff scan only new commits (not in done_shas)
            history = [c for c in all_commits if c.sha not in done_shas]
            _ctotal = len(history)
            _coffset = 0
            console.print(f"[dim]Mode: diff (incremental) | New commits: {len(history)}[/dim]")
            if _ctotal > 0:
                counter.set_commit_progress(0, _ctotal)

        history_total = len(history)
        history_done = 0
        for commit in history:
            if commit.sha in done_shas:
                log.info("Skipping already-scanned commit %s in %s", commit.sha[:8], url)
                continue

            scan_id = db.create_scan(
                repo_id, commit.sha, commit.message, commit.author, commit.date,
                scan_mode="diff", ai_model=config.ai_model,
            )
            db.start_scan(scan_id)
            added_lines = get_diff_added_lines(repo_path, commit.sha)
            counter.set_commit(
                commit.sha,
                author=commit.author,
                date=commit.date,
                mode="diff",
                data_size=f"{added_lines:,} lines",
            )
            try:
                run_diff_scan(
                    repo_path=repo_path, scan_id=scan_id,
                    commit_sha=commit.sha, commit_author=commit.author,
                    commit_message=commit.message, db=db, config=config,
                    notifications=mm if mm.enabled else None,
                    on_tokens=counter.add, on_status=counter.set_action,
                    on_file_progress=counter.set_file_progress,
                    repo_id=repo_id, commit_date=commit.date,
                    triggered_by=triggered_by,
                )
                db.finish_scan(scan_id, ScanStatus.DONE)
            except InsufficientFundsError as exc:
                log.critical("API funds exhausted at %s@%s: %s", url, commit.sha[:8], exc)
                db.finish_scan(scan_id, ScanStatus.ERROR, error=str(exc))
                raise
            except Exception as exc:
                log.error("Scan error for %s@%s: %s", url, commit.sha[:8], exc)
                db.finish_scan(scan_id, ScanStatus.ERROR, error=str(exc))
            history_done += 1
            counter.set_commit_progress(_coffset + history_done, _ctotal)
            _post_scan(db, scan_id, mm, url, config)

    finally:
        counter.stop()
        if repo_path:
            clone_mod.remove(repo_path)


def _post_scan(db, scan_id: int, mm: Mattermost, url: str, config) -> None:
    """Generate report and send Mattermost notification after a completed scan."""
    if config.report_output:
        md = gen_report(db, scan_id, url)
        write_report(md, config.report_output)
    if mm.enabled:
        alerts = db.list_alerts(scan_id)
        mm.send_scan_summary(url, alerts, scan_id)


def _guess_provider(url: str) -> str:
    url_l = url.lower()
    if "github.com" in url_l:
        return "github"
    if "gitlab" in url_l:
        return "gitlab"
    if "gitea" in url_l or "forgejo" in url_l:
        return "gitea"
    return "generic"


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_scan(
    targets: list[str],
    sha: Optional[str],
    rescan: bool,
    config: Config,
) -> None:
    db = create_db(config)
    db.connect()
    mm = Mattermost(config)

    urls = collect_targets(targets, config)
    if not urls:
        console.print("[yellow]No repositories to scan.[/yellow]")
        return

    from tokenleak.agent.client import InsufficientFundsError

    console.print(f"[bold]TokenLeak v{__version__}[/bold] — scanning {len(urls)} repo(s)  🤖 {config.ai_model}")
    for url in urls:
        console.rule(f"[cyan]{url}[/cyan]")
        try:
            scan_repo(url, config, db, mm, rescan=rescan, sha_filter=sha)
        except InsufficientFundsError as exc:
            console.print(
                f"\n[bold red]⛔ API funds exhausted — scanning stopped.[/bold red]\n"
                f"[red]{exc}[/red]\n"
                f"[yellow]Top up your API account balance and re-run to continue.[/yellow]"
            )
            log.critical("Scan aborted: API funds exhausted — %s", exc)
            break

    db.close()
    console.print("[bold green]Done.[/bold green]")


def _check_ai(config: Config) -> str:
    """Send a minimal request to the AI API and return a status string."""
    from tokenleak.agent.client import build_client
    try:
        client = build_client(config)
        client.chat.completions.create(
            model=config.ai_model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        return f"[green]available[/green] ({config.ai_provider} / {config.ai_model})"
    except Exception as exc:
        short = str(exc).split("\n")[0][:80]
        return f"[red]unavailable[/red] — {short}"


def cmd_status(config: Config) -> None:
    db = create_db(config)
    db.connect()
    s = db.summary()
    db.close()

    with console.status("[dim]Checking AI API…[/dim]", spinner="dots"):
        ai_status = _check_ai(config)

    table = Table(title="TokenLeak Status", show_header=False, box=None)
    table.add_column("Key", style="bold cyan")
    table.add_column("Value")

    table.add_row("AI provider", ai_status)
    table.add_row("Repositories", str(s["repos"]))
    for status, count in sorted((s.get("scans") or {}).items()):
        table.add_row(f"  Scans ({status})", str(count))
    table.add_row("Total alerts", str(s["alerts"]))
    table.add_row("Tokens used", f"{s['tokens_used']:,}")
    table.add_row("Last scan finished", s["last_scan_finished"] or "never")

    console.print(table)


def cmd_mcp() -> None:
    """Start the FastMCP server over stdio for external MCP client connections."""
    from tokenleak.mcp_server.server import mcp
    mcp.run()

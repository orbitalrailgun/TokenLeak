"""Entry point: python -m tokenleak <command> [options]."""

import argparse
import sys

from tokenleak import __version__
from tokenleak.config import get_config
from tokenleak.animation import set_enabled as set_animation
from tokenleak.logging_setup import setup_logging
from tokenleak.lock import acquire as lock_acquire, release as lock_release, LockError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tokenleak",
        description="AI-powered git repository security scanner",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--noanimation", action="store_true", help="Disable token leak animation")
    parser.add_argument(
        "--no-prefilter",
        action="store_true",
        help="Disable pre-filter: send all file content to AI (slower, more thorough)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # ── scan ──────────────────────────────────────────────────────────────────
    scan_p = sub.add_parser(
        "scan",
        help="Scan repositories (first run: full HEAD + diff history; subsequent: diff new commits only)",
    )
    scan_p.add_argument(
        "targets",
        nargs="*",
        metavar="TARGET",
        help="Git URL(s) or specifiers (github:user, gitlab:user, server:URL). "
             "Reads from repos list if omitted.",
    )
    scan_p.add_argument("--sha", metavar="SHA", help="Diff-scan only this specific commit SHA")
    scan_p.add_argument(
        "--report",
        nargs="?",
        const="-",
        metavar="FILE",
        help="Write Markdown report to FILE (omit FILE to print to stdout)",
    )

    # ── rescan ────────────────────────────────────────────────────────────────
    rescan_p = sub.add_parser(
        "rescan",
        help="Re-scan as if first run: full scan HEAD + diff scan all history",
    )
    rescan_p.add_argument("targets", nargs="*", metavar="TARGET")
    rescan_p.add_argument("--sha", metavar="SHA", help="Full-scan at this specific commit SHA")
    rescan_p.add_argument("--report", nargs="?", const="-", metavar="FILE")

    # ── status ────────────────────────────────────────────────────────────────
    sub.add_parser("status", help="Show scan summary from the database")

    # ── mcp ───────────────────────────────────────────────────────────────────
    sub.add_parser("mcp", help="Start the MCP server over stdio (for external clients)")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    config = get_config()

    # Apply CLI overrides
    set_animation(not args.noanimation)
    if getattr(args, "no_prefilter", False):
        config.prefilter_enabled = False
    if getattr(args, "report", None) is not None:
        config.report_output = args.report

    setup_logging(
        syslog_enabled=config.syslog_enabled,
        syslog_host=config.syslog_host,
        syslog_port=config.syslog_port,
        log_file=config.log_file or None,
    )

    from tokenleak.cli import cmd_scan, cmd_status, cmd_mcp

    if args.command in ("scan", "rescan"):
        # Prevent concurrent runs
        try:
            lock_acquire(config.lock_file)
        except LockError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

        try:
            cmd_scan(
                targets=getattr(args, "targets", []),
                sha=getattr(args, "sha", None),
                rescan=(args.command == "rescan"),
                config=config,
            )
        finally:
            lock_release(config.lock_file)

    elif args.command == "status":
        cmd_status(config)

    elif args.command == "mcp":
        cmd_mcp()


if __name__ == "__main__":
    main()

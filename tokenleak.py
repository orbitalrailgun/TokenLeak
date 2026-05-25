#!/usr/bin/env python3
"""
TokenLeak — entry point for running without installation.

Usage:
    python tokenleak.py scan [TARGET ...] [--sha SHA] [--report [FILE]] [--no-prefilter] [--noanimation]
    python tokenleak.py rescan [TARGET ...] [--sha SHA] [--report [FILE]]
    python tokenleak.py status
    python tokenleak.py mcp
"""
import sys
from pathlib import Path

# Make the project root importable without `pip install .`
sys.path.insert(0, str(Path(__file__).parent))

from tokenleak.__main__ import main

if __name__ == "__main__":
    main()

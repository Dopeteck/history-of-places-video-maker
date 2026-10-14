#!/usr/bin/env python3
"""Narrator-led history CLI. Use legacy_main.py for the archived V2 interface."""
import sys
from story_cli import main

if __name__ == '__main__':
    sys.exit(main([arg for arg in sys.argv[1:] if arg != '--story']))

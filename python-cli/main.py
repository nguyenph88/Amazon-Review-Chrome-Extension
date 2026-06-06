#!/usr/bin/env python3
"""Entry point: python main.py <login|list|run|capture> [options]"""
import sys

from amazon_reviewer.cli import main

if __name__ == "__main__":
    sys.exit(main())

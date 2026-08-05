#!/usr/bin/env python3
"""Convenience launcher:  python run_game.py"""
import sys

from pedzacy_piotrek.main import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

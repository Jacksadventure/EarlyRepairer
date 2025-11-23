#!/usr/bin/env python3
"""
CLI entrypoint for the betaMax engine.

This file is the preferred front-end location for running repairs:

    python3 betamax/app/betamax.py ...

It forwards to the original implementation in ``lstar.betamax`` so that
existing imports like ``from lstar import betamax`` continue to work.
"""

import os
import sys

# Ensure project root (betamax) on sys.path so 'lstar' and vendored wheels work
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Delegate to the original implementation
from lstar.betamax import main as _main


if __name__ == "__main__":
    _main()

"""Vercel entry point — DEMO MODE ONLY.

Vercel is serverless: the filesystem is ephemeral and read-only, and this app
is deployed from the repo, which deliberately does NOT contain output/trades.csv
(it is gitignored). So a hosted instance has no real blotter to read and could
only ever show synthetic data.

That is made explicit rather than accidental: demo=True is hard-coded here, so a
public deployment can never serve a real account's equity, headroom or trades
even if a blotter somehow ended up in the bundle. For real numbers, run
`python run_dashboard.py` locally against your own machine's blotter.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Vercel invokes this file directly, so the repo root is not on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.app import create_app  # noqa: E402

# demo=True is not a default to be overridden — it is the deployment contract.
app = create_app(demo=True)

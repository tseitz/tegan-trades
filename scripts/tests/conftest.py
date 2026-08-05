"""Put ``scripts/`` on the path so the probes import as plain modules.

The probes are deliberately not a package — they are one-file, throwaway-able measurements
that read ore and print, and giving them a `pyproject.toml` would make them a workspace member
with a build and a version. This is the whole cost of that choice, paid once here rather than
with a `sys.path` line at the top of every test file.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

"""Tiny credentials loader.

Reads a ``KEY=VALUE`` file (default ``.creds`` at the repo root) and returns
a dict. Lines starting with ``#`` and blank lines are ignored. Whitespace
around keys and values is stripped. Values are returned verbatim — never
logged anywhere here.

The file is expected to be ``chmod 600`` and gitignored. Keep it that way.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict


def load_creds(path: str | Path = ".creds") -> Dict[str, str]:
    """Load KEY=VALUE pairs from ``path``.

    Returns an empty dict if the file is missing — callers should decide
    whether that is an error.
    """
    p = Path(path)
    if not p.exists():
        return {}
    out: Dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        # Strip quotes if present
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        if k:
            out[k] = v
    return out


def redact(value: str) -> str:
    """Return a redacted form of a secret value for safe logging."""
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]

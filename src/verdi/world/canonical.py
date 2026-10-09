"""Canonical serialization helpers for deterministic hashing.

Canonical JSON is stable across runs and platforms: sorted keys, no
whitespace, ``ensure_ascii=False``, UTF-8 encoded.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> bytes:
    """Serialize ``obj`` to deterministic canonical JSON bytes."""
    return json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(obj: Any) -> str:
    """Hex sha256 of the canonical JSON serialization of ``obj``."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()

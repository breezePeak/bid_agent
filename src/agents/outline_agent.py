from __future__ import annotations

from pathlib import Path

from outline_generator import generate_outline


def run(root: Path) -> Path:
    return generate_outline(root)

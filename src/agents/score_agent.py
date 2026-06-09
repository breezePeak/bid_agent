from __future__ import annotations

from pathlib import Path

from score_parser import parse_score


def run(root: Path) -> Path:
    return parse_score(root)

from __future__ import annotations

from pathlib import Path

from fact_extractor import extract_facts


def run(root: Path) -> Path:
    return extract_facts(root)

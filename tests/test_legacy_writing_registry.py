from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_legacy_chapter_writing_commands_are_not_registered() -> None:
    from pipeline_registry import stage_command_map, stage_spec_by_command, stage_spec_by_id

    assert "write-all" not in stage_command_map().values()
    assert "review-fix-all" not in stage_command_map().values()
    with pytest.raises(KeyError):
        stage_spec_by_command("write-all")
    with pytest.raises(KeyError):
        stage_spec_by_command("review-fix-all")
    with pytest.raises(KeyError):
        stage_spec_by_id("write_chapters")
    with pytest.raises(KeyError):
        stage_spec_by_id("review_fix_chapters")


def test_legacy_writer_modules_are_not_executable_bypasses() -> None:
    from subagent_registry import list_subagents

    names = {item.name for item in list_subagents()}
    commands = {item.command for item in list_subagents()}
    assert "chapter_writer" not in names
    assert "chapter_rewriter" not in names
    assert "write-all" not in commands
    assert "review-fix-all" not in commands
    assert not (SRC / "chapter_writer.py").exists()
    assert not (SRC / "subagent_runner.py").exists()
    assert not (SRC / "graph" / "chapter_subgraph.py").exists()


import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.document_planner import DocumentPlanner


def test_writable_child_becomes_unit_root_when_structural_parent_is_not_writable():
    nodes = [
        SimpleNamespace(
            node_id="chapter-child",
            parent_node_id="chapter-structural-parent",
        ),
        SimpleNamespace(
            node_id="chapter-grandchild",
            parent_node_id="chapter-child",
        ),
    ]
    plan = SimpleNamespace(
        revision=1,
        source_hashes={},
        contract_revision=1,
    )

    units = DocumentPlanner._content_units(nodes, plan)

    assert len(units) == 1
    assert units[0].node_ids == ["chapter-child", "chapter-grandchild"]

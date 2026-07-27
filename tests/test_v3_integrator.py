from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.integrator import DocumentIntegrator  # noqa: E402


class V3IntegratorTests(unittest.TestCase):
    def test_integration_removes_duplicate_requirement_content(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            runs = base / "runs"
            workspace = runs / "alpha"
            workspace.mkdir(parents=True)
            content = workspace / "workspace" / "v3" / "content_units"
            content.mkdir(parents=True)
            block = {"target_node_id": "n1", "type": "paragraph", "content": "响应交付成果。", "requirement_ids": ["R1"], "score_point_ids": [], "topic_ids": ["requirement:R1"], "evidence_ids": [], "fact_ids": [], "confidence": 0.8, "human_locked": False, "critical_claims": []}
            (content / "unit-a.json").write_text(json.dumps({"blocks": [{**block, "block_id": "b1"}]}, ensure_ascii=False), encoding="utf-8")
            (content / "unit-b.json").write_text(json.dumps({"blocks": [{**block, "block_id": "b2", "target_node_id": "n2"}]}, ensure_ascii=False), encoding="utf-8")
            document = DocumentIntegrator(WorkspaceContext.resolve(runs, "alpha")).integrate(contract_revision=1, plan_revision=1)
            self.assertEqual([block.block_id for block in document.blocks], ["b1"])
            trace = json.loads((workspace / "workspace" / "v3" / "reports" / "rewrite_trace.json").read_text(encoding="utf-8"))
            self.assertEqual(trace["actions"][0]["action"], "delete_duplicate")


if __name__ == "__main__":
    unittest.main()

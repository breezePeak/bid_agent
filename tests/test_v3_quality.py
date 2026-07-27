from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.quality import QualityGate  # noqa: E402


class V3QualityTests(unittest.TestCase):
    def test_mandatory_requirement_gap_fails_delivery_gate(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp); runs = base / "runs"; workspace = runs / "alpha"; (workspace / "workspace" / "v3").mkdir(parents=True)
            (workspace / "workspace" / "v3" / "requirement_ledger.json").write_text(json.dumps({"schema_version":"v3","revision":1,"source_hashes":{},"requirements":[{"requirement_id":"R1","kind":"mandatory","source_anchor":{"source_input_id":"I1","chunk_id":"C1","location":"p1"},"original_text":"必须响应","normalized_requirement":"必须响应","severity":"blocking","response_type":"mandatory_response","evidence_policy":"tender_traceable","status":"open"}]}), encoding="utf-8")
            (workspace / "workspace" / "v3" / "integrated_document.json").write_text(json.dumps({"schema_version":"v3","revision":1,"source_hashes":{},"contract_revision":1,"plan_revision":1,"blocks":[]}), encoding="utf-8")
            report = QualityGate(WorkspaceContext.resolve(runs, "alpha")).verify()
            self.assertEqual(report.verdict, "fail")
            self.assertEqual(report.findings[0]["code"], "REQUIREMENT_UNCOVERED")


if __name__ == "__main__":
    unittest.main()

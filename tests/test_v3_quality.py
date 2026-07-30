from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import WorkspaceContext  # noqa: E402
from document_pipeline.contracts import InputRole  # noqa: E402
from document_pipeline.input_manifest import InputManifestService  # noqa: E402
from document_pipeline.quality import QualityGate  # noqa: E402
from document_pipeline.stage_runner import V3StageRunner  # noqa: E402


class V3QualityTests(unittest.TestCase):
    def test_mandatory_requirement_gap_fails_delivery_gate(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp); runs = base / "runs"; workspace = runs / "alpha"; (workspace / "workspace" / "v3").mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            tender = base / "tender.md"
            tender.write_text("投标人必须响应本项目全部采购要求。", encoding="utf-8")
            InputManifestService(context).register_local_file(tender, InputRole.TENDER)
            runner = V3StageRunner.for_deterministic_tests(context)
            runner.run("normalize_sources")
            runner.run("build_requirement_ledger")
            (workspace / "workspace" / "v3" / "integrated_document.json").write_text(json.dumps({"schema_version":"v3","revision":1,"source_hashes":{},"contract_revision":1,"plan_revision":1,"blocks":[]}), encoding="utf-8")
            report = QualityGate(context).verify()
            self.assertEqual(report.verdict, "fail")
            self.assertEqual(report.findings[0]["code"], "REQUIREMENT_UNCOVERED")


if __name__ == "__main__":
    unittest.main()

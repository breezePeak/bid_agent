from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane import ControlStore, WorkspaceContext  # noqa: E402
from document_pipeline.document_planner import CONTENT_UNITS_PATH  # noqa: E402
from document_pipeline.integrator import DocumentIntegrator  # noqa: E402
from document_pipeline.writer_policy import (  # noqa: E402
    writer_base_fingerprint,
    writer_fingerprint,
)
from utils import write_json  # noqa: E402


class V3IntegratorTests(unittest.TestCase):
    def test_integration_removes_duplicate_requirement_content(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            runs = base / "runs"
            workspace = runs / "alpha"
            workspace.mkdir(parents=True)
            content = workspace / "workspace" / "v3" / "content_units"
            content.mkdir(parents=True)
            context = WorkspaceContext.resolve(runs, "alpha")
            substantive = (
                "输入资料包括采购需求、现状数据、接口清单和经确认的实施计划。"
                "技术方法采用需求核验、数据比对、配置实施和联调测试，按准备、处理、复核、整改和交付步骤执行。"
                "质量控制设置实施人员自检、技术负责人复核和成果校验，所有问题进入闭环台账。"
                "成果输出包括实施记录、测试报告、问题清单和交付清单，验收时逐项检查完整性、正确性和可追溯性。"
                "对资料延迟、接口异常和数据偏差设置风险预警，发现问题后分析影响、执行修复并复测确认。"
            )
            block = {"target_node_id": "n1", "type": "paragraph", "content": substantive, "requirement_ids": ["R1"], "score_point_ids": [], "topic_ids": ["requirement:R1"], "evidence_ids": [], "fact_ids": [], "confidence": 0.8, "human_locked": False, "critical_claims": []}
            units = [
                {"unit_id": "unit-a", "contract_revision": 1, "node_ids": ["n1"], "upstream_unit_ids": []},
                {"unit_id": "unit-b", "contract_revision": 1, "node_ids": ["n2"], "upstream_unit_ids": []},
            ]
            write_json(context.root / CONTENT_UNITS_PATH, {"schema_version": "v3", "units": units})
            store = ControlStore(context)
            for index, unit in enumerate(units, start=1):
                base_fingerprint = writer_base_fingerprint(
                    context,
                    unit_id=unit["unit_id"],
                    contract_revision=1,
                    node_ids=unit["node_ids"],
                    deterministic_test=True,
                )
                fingerprint = writer_fingerprint(base_fingerprint, [])
                path = content / f"{unit['unit_id']}.json"
                write_json(
                    path,
                    {
                        "unit_id": unit["unit_id"],
                        "writer_base_fingerprint": base_fingerprint,
                        "writer_fingerprint": fingerprint,
                        "evidence_batches": [],
                        "blocks": [
                            {
                                **block,
                                "block_id": f"b{index}",
                                "target_node_id": unit["node_ids"][0],
                            }
                        ],
                    },
                )
                store.upsert_content_unit_state(
                    {
                        "unit_id": unit["unit_id"],
                        "contract_revision": 1,
                        "state": "completed",
                        "writer_fingerprint": fingerprint,
                        "output_artifact_id": (
                            f"workspace/v3/content_units/{path.name}"
                        ),
                    }
                )
            document = DocumentIntegrator(
                context,
                deterministic_test=True,
            ).integrate(contract_revision=1, plan_revision=1)
            self.assertEqual([block.block_id for block in document.blocks], ["b1"])
            trace = json.loads((workspace / "workspace" / "v3" / "reports" / "rewrite_trace.json").read_text(encoding="utf-8"))
            self.assertEqual(trace["actions"][0]["action"], "delete_duplicate")


if __name__ == "__main__":
    unittest.main()

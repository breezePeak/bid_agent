from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.goal import create_goal, load_goal, set_goal_status
from materials_checklist import (
    build_material_recovery_plan,
    mark_material_uploaded,
    update_item_response,
)
from utils import write_json


class MaterialResumeTests(unittest.TestCase):
    def test_mark_uploaded_resumes_goal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            ws.mkdir(parents=True)
            # minimal checklist
            data = {
                "version": "1.0.0",
                "summary": {"total": 1, "deferred": 1, "ready": 0},
                "items": [
                    {
                        "item_id": "mat_cert_1",
                        "category": "qualification",
                        "requirement": "资质证书",
                        "evidence_status": "missing",
                        "response_status": "deferred",
                        "lifecycle_status": "missing",
                        "severity": "block",
                        "suggested_attachment": "证书扫描件",
                        "target_chapter_hints": ["资格"],
                    }
                ],
            }
            write_json(ws / "materials_checklist.json", data)
            (ws / "chapters").mkdir(parents=True)
            (ws / "chapters" / "01.md").write_text(
                "<!-- MATERIAL_GAP:item_id=mat_cert_1 -->待补<!-- /MATERIAL_GAP -->\n",
                encoding="utf-8",
            )
            create_goal(
                root,
                raw_user_goal="补齐材料并出稿",
                objectives=[{"type": "export"}],
                success_criteria=[{"check": "artifact_exists", "path": "outputs/final.docx"}],
            )
            set_goal_status(root, "blocked_human", blocked_reason="缺证书")
            # PR-A5: upload alone does not resume; needs verified lifecycle
            result = mark_material_uploaded(root, "mat_cert_1", note="已上传证书", rebuild=False)
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(result.get("lifecycle_status"), "uploaded")
            self.assertIn("01", result.get("affected_chapters") or [])
            goal = load_goal(root)
            self.assertEqual(goal.get("status"), "blocked_human")
            # simulate verification pass → resume
            from agent.goal import resume_goal_after_materials

            verified = data["items"][0]
            verified.update(
                {
                    "response_status": "ready",
                    "evidence_status": "verified",
                    "lifecycle_status": "verified",
                }
            )
            data["summary"] = {"total": 1, "deferred": 0, "ready": 1}
            write_json(ws / "materials_checklist.json", data)
            resume_goal_after_materials(
                root,
                note="material_verified:mat_cert_1",
                item_ids=["mat_cert_1"],
            )
            goal = load_goal(root)
            self.assertEqual(goal.get("status"), "in_progress")
            plan = build_material_recovery_plan(root, item_ids=["mat_cert_1"])
            self.assertFalse(plan.get("full_rerun"))
            self.assertTrue(plan.get("steps"))

    def test_update_lifecycle_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws = root / "workspace"
            ws.mkdir(parents=True)
            write_json(
                ws / "materials_checklist.json",
                {
                    "items": [
                        {
                            "item_id": "x1",
                            "response_status": "deferred",
                            "evidence_status": "missing",
                            "requirement": "r",
                            "category": "qualification",
                        }
                    ],
                    "summary": {},
                },
            )
            # rebuild would need tender; call update without rebuild
            result = update_item_response(root, "x1", response_status="uploaded", rebuild=False)
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(result.get("response_status"), "ready")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.goal_compiler import compile_goal_from_message, validate_goal_draft


class GoalCompilerTests(unittest.TestCase):
    def test_validate_compound_draft(self) -> None:
        ok, reason, norm = validate_goal_draft(
            {
                "objectives": [
                    {"type": "fix_coverage"},
                    {"type": "fix_compliance"},
                    {"type": "export"},
                ],
                "scope": {"chapter_ids": [], "exclude_sections": ["报价"]},
                "constraints": {
                    "forbid_price_changes": True,
                    "allow_placeholders_for_missing_materials": True,
                    "require_compliance_pass_before_export": True,
                },
                "success_criteria": [
                    {"check": "score_coverage_min", "ratio": 0.95},
                    {"check": "no_open_blocks"},
                    {"check": "artifact_exists", "path": "outputs/final.docx"},
                ],
            }
        )
        self.assertTrue(ok, reason)
        self.assertTrue(norm["constraints"].get("forbid_price_chapters"))
        tools = [s.get("tool") for s in norm["plan"]]
        self.assertIn("analyze_coverage", tools)
        self.assertIn("analyze_compliance", tools)
        self.assertTrue(any(t in {"build_export", "export_preflight"} for t in tools))

    def test_reject_unknown_objective(self) -> None:
        ok, reason, _ = validate_goal_draft({"objectives": [{"type": "hack_system"}]})
        self.assertFalse(ok)
        self.assertEqual(reason, "no_valid_objectives")

    def test_rules_fallback_compound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = compile_goal_from_message(
                "补齐所有可自动补齐的评分点，修复合规问题；不要修改报价章节；最后生成 Word",
                root=root,
                use_llm=False,
            )
            types = {o.get("type") for o in result.get("objectives") or []}
            self.assertIn("fix_coverage", types)
            self.assertTrue(result["constraints"].get("forbid_price_chapters"))
            self.assertEqual(result.get("compiler", {}).get("source"), "rules")

    def test_forbid_price_propagates_to_plan_args(self) -> None:
        ok, _, norm = validate_goal_draft(
            {
                "objectives": [{"type": "fix_coverage"}, {"type": "export"}],
                "constraints": {"forbid_price_changes": True},
                "success_criteria": [{"check": "score_coverage_min", "ratio": 0.95}],
            }
        )
        self.assertTrue(ok)
        mut = [s for s in norm["plan"] if s.get("tool") in {"fix_coverage", "rewrite_chapters"}]
        for step in mut:
            self.assertTrue(step.get("args", {}).get("forbid_price_chapters"))


if __name__ == "__main__":
    unittest.main()

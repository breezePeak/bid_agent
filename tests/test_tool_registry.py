from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.flags import agent_supervisor_enabled, agent_use_tool_runtime
from agent.tool_registry import (
    get_tool,
    list_tools,
    reset_tool_index,
    stage_tools,
    tool_manifest,
)
from agent.tool_runtime import invoke
from pipeline_registry import STAGE_SPECS, workflow_stage_specs


class ToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tool_index()

    def test_stage_count_matches_registry(self) -> None:
        stages = stage_tools()
        registered = workflow_stage_specs(include_utility=True)
        self.assertEqual(len(stages), len(registered))
        self.assertEqual([t.stage_id for t in stages], [s.id for s in registered])
        self.assertEqual([t.command for t in stages], [s.command for s in registered])

    def test_list_tools_includes_run_stage_and_all_stages(self) -> None:
        tools = list_tools()
        names = {t.name for t in tools}
        ids = {t.id for t in tools}
        self.assertIn("run_stage", names)
        self.assertIn("run_stage", ids)
        for stage in workflow_stage_specs(include_utility=True):
            self.assertIn(stage.command, names)
            self.assertIn(f"stage:{stage.id}", ids)

    def test_get_tool_aliases(self) -> None:
        by_command = get_tool("parse-score")
        by_stage_id = get_tool("parse_score")
        by_stage_key = get_tool("stage:parse_score")
        self.assertIsNotNone(by_command)
        self.assertIsNotNone(by_stage_id)
        self.assertIsNotNone(by_stage_key)
        assert by_command and by_stage_id and by_stage_key
        self.assertEqual(by_command.stage_id, "parse_score")
        self.assertEqual(by_stage_id.stage_id, "parse_score")
        self.assertEqual(by_stage_key.stage_id, "parse_score")
        self.assertEqual(by_command.runner, "score_parser.parse_score")

    def test_manifest_stable_fields(self) -> None:
        manifest = tool_manifest()
        self.assertTrue(manifest)
        for item in manifest:
            for key in ("id", "name", "label", "description", "kind", "risk_level", "params_schema"):
                self.assertIn(key, item)

    def test_workflow_stage_specs_alignment(self) -> None:
        workflow_ids = [s.id for s in workflow_stage_specs(include_utility=True)]
        tool_ids = [t.stage_id for t in stage_tools()]
        self.assertEqual(workflow_ids, tool_ids)


class ToolRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tool_index()

    def test_unknown_tool(self) -> None:
        result = invoke("not_a_real_tool", {}, root=ROOT)
        self.assertFalse(result.ok)
        assert result.error is not None
        self.assertEqual(result.error.code, "unknown_tool")

    def test_run_stage_requires_command_or_stage_id(self) -> None:
        result = invoke("run_stage", {}, root=ROOT)
        self.assertFalse(result.ok)
        assert result.error is not None
        self.assertEqual(result.error.code, "invalid_args")

    def test_run_stage_invalid_extra_args(self) -> None:
        result = invoke("run_stage", {"command": "parse-score", "hack": 1}, root=ROOT)
        self.assertFalse(result.ok)
        assert result.error is not None
        self.assertEqual(result.error.code, "invalid_args")

    def test_run_stage_dry_run_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = invoke(
                "run_stage",
                {"command": "init", "dry_run": True},
                root=root,
            )
            self.assertTrue(result.ok)
            self.assertIn("dry_run", result.summary_for_llm)
            self.assertFalse((root / "sources").exists())

    def test_run_stage_init_and_idempotent_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = invoke("run_stage", {"command": "init"}, root=root)
            self.assertTrue(first.ok, first.summary_for_llm)
            self.assertFalse(first.skipped)
            # init produces virtual artifacts only; stage_outputs_ready may still be true
            second = invoke("run_stage", {"command": "init"}, root=root)
            self.assertTrue(second.ok, second.summary_for_llm)

    def test_stage_command_alias_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = invoke("split-docs", {"dry_run": True}, root=root)
            # missing requires should fail before dry execution when requires not met
            # split-docs requires inputs; dry_run still checks requires first
            self.assertFalse(result.ok)
            assert result.error is not None
            self.assertEqual(result.error.code, "missing_requires")

    def test_missing_requires_on_parse_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = invoke("run_stage", {"stage_id": "parse_score"}, root=root)
            self.assertFalse(result.ok)
            assert result.error is not None
            self.assertEqual(result.error.code, "missing_requires")
            self.assertIn("inputs/score.md", result.error.message)

    def test_result_to_dict_json_serializable(self) -> None:
        result = invoke("nope", {}, root=ROOT)
        payload = result.to_dict()
        json.dumps(payload, ensure_ascii=False)


class AgentFlagsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        # Do not assert absolute env; only that functions return bool
        self.assertIsInstance(agent_supervisor_enabled(), bool)
        self.assertIsInstance(agent_use_tool_runtime(), bool)




class QueryDiagnoseToolTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_tool_index()

    def test_meta_tools_registered(self) -> None:
        for name in ("query_status", "query_artifacts", "diagnose_failure"):
            self.assertIsNotNone(get_tool(name), name)

    def test_query_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = invoke("query_status", {"view": "summary"}, root=root)
            self.assertTrue(result.ok, result.summary_for_llm)
            self.assertIn("进度", result.summary_for_llm)

    def test_query_artifacts_path_traversal_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = invoke("query_artifacts", {"path": "../secrets.txt"}, root=root)
            self.assertFalse(result.ok)
            assert result.error is not None
            self.assertEqual(result.error.code, "invalid_args")

    def test_query_artifacts_whitelist_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "workspace" / "note.txt"
            target.parent.mkdir(parents=True)
            target.write_text("hello-agent", encoding="utf-8")
            result = invoke("query_artifacts", {"path": "workspace/note.txt", "max_chars": 100}, root=root)
            self.assertTrue(result.ok, result.summary_for_llm)
            self.assertIn("hello-agent", result.summary_for_llm)

    def test_diagnose_failure_empty_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = invoke("diagnose_failure", {}, root=root)
            self.assertTrue(result.ok, result.summary_for_llm)
            self.assertIn("诊断", result.summary_for_llm)



class StageRunnerIntegrityTests(unittest.TestCase):
    def test_all_stage_runners_importable(self) -> None:
        from agent.tool_runtime import _resolve_stage_callable

        for stage in STAGE_SPECS:
            if not stage.runner:
                self.fail(f"{stage.id} missing runner")
            if stage.runner == "main.init_project":
                import main as main_mod

                self.assertTrue(callable(getattr(main_mod, "init_project", None)))
                continue
            func = _resolve_stage_callable(stage.runner)
            self.assertTrue(callable(func), stage.runner)

    def test_cli_tool_parser_accepts_list_and_name(self) -> None:
        from main import build_parser

        parser = build_parser()
        args = parser.parse_args(["tool", "--list"])
        self.assertEqual(args.command, "tool")
        self.assertTrue(args.list_tools)
        args2 = parser.parse_args(["tool", "--name", "query_status", "--args", "{}", "--dry-run"])
        self.assertEqual(args2.name, "query_status")
        self.assertTrue(args2.dry_run)


if __name__ == "__main__":
    unittest.main()

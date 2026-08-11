"""Chapter research planner: distill facts; model decides search."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from document_pipeline.chapter_research_planner import (  # noqa: E402
    distill_chapter_research_brief,
    plan_chapter_research,
)


class ChapterResearchPlannerTests(unittest.TestCase):
    def test_distill_keeps_compact_chapter_relevant_facts(self) -> None:
        chapter = {
            "chapter_id": "ch-diagram",
            "title": "技术路线图",
            "blueprint_node": {
                "purpose": "以图呈现总体技术路线阶段与节点",
            },
            "context": {"items": []},
        }
        project = {
            "identity": {"project_name": "城市地下管网普查项目", "purchaser": "某市局"},
            "scope": ["开展地下管网普查"],
            "work_packages": ["管网数据采集与成果复核"],
            "confirmed_facts": [{"fact_id": "f1", "statement": "x" * 2000}],
            "background": ["很长的背景" * 50],
        }
        sibling = {
            "chapter_role": "visual",
            "siblings": [
                {
                    "chapter_id": "ch-overview",
                    "title": "总体技术路线",
                    "has_content": True,
                    "summary": "核查准备→数据接收→内业核查→成果复核",
                    "purpose": "阶段框架",
                }
            ],
            "missing_upstream": [],
        }
        brief = distill_chapter_research_brief(
            chapter,
            project_context=project,
            sibling_context=sibling,
        )
        text = brief["brief_text"]
        self.assertIn("城市地下管网普查项目", text)
        self.assertIn("技术路线图", text)
        self.assertIn("核查准备", text)
        self.assertNotIn("confirmed_facts", text)
        self.assertLess(len(text), 800)

    def test_model_can_skip_research(self) -> None:
        chapter = {
            "chapter_id": "ch-diagram",
            "title": "技术路线图",
            "blueprint_node": {"purpose": "画技术路线图"},
        }

        def fake_chat(messages, **_kwargs):
            return (
                '{"need_research": false, "reason": "兄弟章已有阶段骨架，可直接成图",'
                ' "search_query": ""}'
            )

        with mock.patch("llm_client.chat", side_effect=fake_chat):
            plan = plan_chapter_research(
                chapter,
                project_context={
                    "identity": {"project_name": "城市地下管网普查项目"},
                    "scope": ["开展地下管网普查"],
                },
                sibling_context={
                    "chapter_role": "visual",
                    "siblings": [
                        {
                            "chapter_id": "ch-overview",
                            "title": "总体技术路线",
                            "has_content": True,
                            "summary": "四阶段质控路线",
                        }
                    ],
                },
            )
        self.assertFalse(plan["need_research"])
        self.assertEqual(plan["search_query"], "")
        self.assertEqual(plan["decision_source"], "chapter_agent")
        self.assertIn("成图", plan["reason"])

    def test_model_research_query_is_distilled_not_full_tender(self) -> None:
        chapter = {
            "chapter_id": "ch-bg",
            "title": "项目任务背景",
            "blueprint_node": {"purpose": "说明任务背景与行业现状"},
        }

        def fake_chat(messages, **_kwargs):
            user = messages[1]["content"]
            self.assertIn("已整理要点", user)
            self.assertNotIn("confirmed_facts", user)
            self.assertNotIn("不应进入检索串的长事实", user)
            return (
                '{"need_research": true, "reason": "需要行业现状公开资料",'
                ' "search_query": "城市地下管网普查项目 地下管网普查 行业现状 政策要求"}'
            )

        with mock.patch("llm_client.chat", side_effect=fake_chat):
            plan = plan_chapter_research(
                chapter,
                project_context={
                    "identity": {"project_name": "城市地下管网普查项目"},
                    "scope": ["开展地下管网普查"],
                    "work_packages": ["管网数据采集与成果复核"],
                    "confirmed_facts": [
                        {"fact_id": "f1", "statement": "不应进入检索串的长事实" * 20}
                    ],
                },
            )
        self.assertTrue(plan["need_research"])
        self.assertEqual(plan["decision_source"], "chapter_agent")
        query = plan["search_query"]
        self.assertIn("城市地下管网普查项目", query)
        self.assertNotIn("confirmed_facts", query)
        self.assertNotIn("不应进入检索串的长事实", query)
        self.assertLess(len(query), 500)

    def test_agent_unavailable_skips_without_heuristic_need(self) -> None:
        chapter = {
            "chapter_id": "ch-bg",
            "title": "项目任务背景",
            "blueprint_node": {"purpose": "说明任务背景"},
        }
        with mock.patch("llm_client.chat", side_effect=RuntimeError("offline")):
            plan = plan_chapter_research(
                chapter,
                project_context={
                    "identity": {"project_name": "城市地下管网普查项目"},
                    "scope": ["开展地下管网普查"],
                },
            )
        self.assertFalse(plan["need_research"])
        self.assertEqual(plan["decision_source"], "agent_unavailable")
        self.assertEqual(plan["search_query"], "")

    def test_model_dump_like_query_is_sanitized(self) -> None:
        chapter = {
            "chapter_id": "ch-m",
            "title": "关键技术方法",
            "blueprint_node": {"purpose": "方法"},
        }
        dump = '{"identity": {"project_name": "x"}, "confirmed_facts": [' + '"a",' * 200 + ']}'

        def fake_chat(messages, **_kwargs):
            return json.dumps(
                {
                    "need_research": True,
                    "reason": "需要方法",
                    "search_query": dump,
                },
                ensure_ascii=False,
            )

        with mock.patch("llm_client.chat", side_effect=fake_chat):
            plan = plan_chapter_research(
                chapter,
                project_context={
                    "identity": {"project_name": "城市地下管网普查项目"},
                    "scope": ["开展地下管网普查"],
                },
            )
        self.assertTrue(plan["need_research"])
        self.assertNotIn("confirmed_facts", plan["search_query"])
        self.assertIn("城市地下管网普查项目", plan["search_query"])


if __name__ == "__main__":
    unittest.main()

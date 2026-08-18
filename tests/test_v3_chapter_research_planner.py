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
        self.assertIn("总体技术路线", text)
        self.assertNotIn("confirmed_facts", text)
        self.assertLess(len(text), 800)

    def test_orientation_and_existing_materials_block_search(self) -> None:
        chapter = {
            "chapter_id": "ch-diagram",
            "title": "技术路线图",
            "blueprint_node": {"purpose": "画技术路线图"},
        }

        def fake_chat(messages, **_kwargs):
            user = messages[1]["content"]
            self.assertIn("写作目的", user)
            self.assertIn("全书位置", user)
            return json.dumps(
                {
                    "orientation_confirmed": True,
                    "orientation_summary": "本章画技术路线图，位于技术路线下，上游是总体技术路线。",
                    "existing_materials_sufficient": True,
                    "need_research": True,
                    "reason": "资料已够，但模型仍写了检索",
                    "search_query": "不该被采用的检索",
                },
                ensure_ascii=False,
            )

        with mock.patch("llm_client.chat", side_effect=fake_chat):
            plan = plan_chapter_research(
                chapter,
                writing_orientation={
                    "writing_purpose": {
                        "title": "技术路线图",
                        "purpose": "以图呈现总体技术路线",
                        "role": "visual",
                        "role_label": "图示/路线图",
                        "is_leaf": True,
                    },
                    "document_position": {"path_label": "技术路线 / 技术路线图"},
                    "chapter_relations": {
                        "items": [
                            {
                                "title": "总体技术路线",
                                "relation": "upstream",
                                "relation_label": "上游同级",
                            }
                        ]
                    },
                    "existing_materials": {
                        "notes": ["已物化章节目的/写作目标 2 条"],
                        "has_local_materials": True,
                    },
                    "summary_text": "写作目的：画图。全书位置：技术路线 / 技术路线图。",
                },
            )
        self.assertFalse(plan["need_research"])
        self.assertEqual(plan["search_query"], "")
        self.assertTrue(plan["orientation_confirmed"])
        self.assertTrue(plan["existing_materials_sufficient"])
        self.assertIn("技术路线图", plan["brief"]["brief_text"])
        self.assertIn("全书位置", plan["brief"]["brief_text"])

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

    def test_explicit_force_research_overrides_model_skip(self) -> None:
        chapter = {
            "chapter_id": "ch-bg",
            "title": "项目背景",
            "blueprint_node": {"purpose": "说明行业现状"},
        }
        with mock.patch(
            "llm_client.chat",
            return_value=json.dumps(
                {
                    "orientation_confirmed": False,
                    "existing_materials_sufficient": True,
                    "need_research": False,
                    "reason": "已有资料足够",
                    "search_query": "",
                },
                ensure_ascii=False,
            ),
        ):
            plan = plan_chapter_research(
                chapter,
                project_context={"scope": ["行业现状调研"]},
                instruction="去联网搜索啊",
                force_research=True,
            )
        self.assertTrue(plan["need_research"])
        self.assertTrue(plan["search_query"])

    def test_explicit_force_research_has_fallback_when_agent_unavailable(self) -> None:
        chapter = {
            "chapter_id": "ch-bg",
            "title": "项目背景",
            "blueprint_node": {"purpose": "说明行业现状"},
        }
        with mock.patch("llm_client.chat", side_effect=RuntimeError("offline")):
            plan = plan_chapter_research(
                chapter,
                project_context={"scope": ["行业现状调研"]},
                instruction="重新搜索啊",
                force_research=True,
            )
        self.assertTrue(plan["need_research"])
        self.assertEqual(plan["decision_source"], "explicit_request_fallback")
        self.assertIn("项目背景", plan["search_query"])

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

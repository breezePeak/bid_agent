#!/usr/bin/env python3
"""
导出指定工作区或 json 文件的章节目录为 Markdown 格式文件。
用法：
  python scripts/export_outline_to_md.py --input workspace/outline.json --output 章节目录.md
  python scripts/export_outline_to_md.py --workspace-id <workspace_id>
"""

import sys
import json
import argparse
from pathlib import Path

def outline_json_to_md(data: dict) -> str:
    md_lines = ["# 章节目录\n"]
    chapters = data.get("chapters", [])
    if isinstance(chapters, dict) and "items" in chapters:
        chapters = chapters["items"]

    def process_node(node, depth=0):
        indent = "  " * depth
        title = node.get("title") or node.get("chapter_id") or node.get("id") or "未命名章节"
        md_lines.append(f"{indent}- {title}")
        children = node.get("children") or []
        for child in children:
            process_node(child, depth + 1)

    if isinstance(chapters, list):
        # 可能是树形结构或者扁平列表 (带 depth 或 parent_chapter_id)
        # 如果包含 parent_chapter_id 并且不是嵌套形态，构造层级
        has_parent_field = any("parent_chapter_id" in ch for ch in chapters if isinstance(ch, dict))
        has_children_field = any("children" in ch for ch in chapters if isinstance(ch, dict))

        if has_parent_field and not has_children_field:
            by_id = {ch.get("chapter_id"): ch for ch in chapters if isinstance(ch, dict)}
            for ch in chapters:
                if not isinstance(ch, dict):
                    continue
                depth = 0
                parent_id = ch.get("parent_chapter_id")
                visited = set()
                while parent_id and parent_id in by_id and parent_id not in visited:
                    visited.add(parent_id)
                    depth += 1
                    parent_id = by_id[parent_id].get("parent_chapter_id")
                indent = "  " * depth
                title = ch.get("title") or ch.get("chapter_id") or "未命名章节"
                md_lines.append(f"{indent}- {title}")
        else:
            for ch in chapters:
                if isinstance(ch, dict):
                    process_node(ch, 0)
                elif isinstance(ch, str):
                    md_lines.append(f"- {ch}")

    return "\n".join(md_lines) + "\n"

def main():
    parser = argparse.ArgumentParser(description="导出章节目录为 Markdown 文件")
    parser.add_argument("--input", "-i", type=str, help="输入 JSON 文件路径 (例如 outline.json)")
    parser.add_argument("--output", "-o", type=str, default="章节目录.md", help="输出 MD 文件路径")
    args = parser.parse_args()

    if not args.input:
        print("请指定输入文件路径: --input <path/to/outline.json>", file=sys.stderr)
        sys.exit(1)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"找不到输入文件: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    md_content = outline_json_to_md(data)
    output_path = Path(args.output)
    output_path.write_text(md_content, encoding="utf-8")
    print(f"成功导出章节目录至: {output_path.resolve()}")

if __name__ == "__main__":
    main()

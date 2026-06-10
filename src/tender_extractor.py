from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from config import (
    TENDER_EXTENSIONS,
    BLOCK_MAX_CHARS,
    BLOCK_ID_PREFIX,
    BATCH_SIZE,
    CLASSIFY_TEMPERATURE,
    LOW_CONFIDENCE_THRESHOLD,
    SCORE_RATIO_WARN,
    HINT_CATEGORY_MAP,
    VALID_CATEGORIES,
    VALID_TARGET_FILES,
)
from document_converter import convert_to_markdown
from llm_client import chat
from utils import (
    ensure_dirs,
    extract_json_text,
    project_root,
    write_json,
    write_text,
)


# ============================================================
#  rule_hints 提取
# ============================================================

def build_rule_hints(block: dict[str, Any]) -> list[str]:
    content = block.get("content", "")
    hints: list[str] = []
    for category, keywords in HINT_CATEGORY_MAP.items():
        matched = [kw for kw in keywords if kw in content]
        if matched:
            hints.append(f"包含{category}关键词：{'、'.join(matched[:5])}")
    return hints


# ============================================================
#  切块
# ============================================================

def _heading_level(line: str) -> int | None:
    stripped = line.strip()

    m = re.match(r"^(#{1,6})\s+(.+)", stripped)
    if m:
        return len(m.group(1))

    if re.match(r"^第[一二三四五六七八九十百零\d]+章", stripped):
        return 1
    if re.match(r"^[一二三四五六七八九十]+[、．，,]", stripped):
        return 2
    if re.match(r"^（[一二三四五六七八九十]+）", stripped):
        return 3
    if re.match(r"^\d+[\.\、]\s", stripped):
        return 3
    if re.match(r"^\d+\.\d+[\.\s]", stripped):
        return 4
    if re.match(r"^[A-Z]\.\s", stripped):
        return 5

    return None


def _heading_title(line: str) -> str:
    stripped = line.strip()
    return re.sub(
        r"^(#{1,6}\s+|"
        r"第[^章]+章\s*|"
        r"[一二三四五六七八九十]+[、．，,]\s*|"
        r"（[^）]+）\s*|"
        r"\d+[\.\、]\s+|\d+\.\d+[\.\s]+|"
        r"[A-Z]\.\s+)",
        "",
        stripped,
    ).strip()


def _split_by_heading(markdown: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    title_stack: list[str] = []
    current_lines: list[str] = []
    current_path: list[str] = ["正文"]

    def flush() -> None:
        nonlocal current_lines
        content = "\n".join(current_lines).strip()
        if content:
            blocks.append({"title_path": current_path.copy(), "content": content})
        current_lines.clear()

    for line in markdown.splitlines():
        level = _heading_level(line)
        if level is not None:
            flush()
            title = _heading_title(line)
            title_stack = title_stack[: level - 1]
            title_stack.append(title)
            current_path = title_stack.copy()
        current_lines.append(line)

    flush()
    return blocks


def _split_long_block(content: str, max_chars: int) -> list[str]:
    if len(content) <= max_chars:
        return [content]

    parts: list[str] = []
    paragraphs = re.split(r"(\n\s*\n)", content)
    buffer = ""
    for part in paragraphs:
        if len(buffer) + len(part) <= max_chars:
            buffer += part
            continue
        if buffer.strip():
            parts.append(buffer.strip())
            buffer = ""
        while len(part) > max_chars:
            parts.append(part[:max_chars].strip())
            part = part[max_chars:]
        buffer = part
    if buffer.strip():
        parts.append(buffer.strip())
    return parts


def split_tender_into_blocks(
    raw_markdown: str,
    max_chars: int = BLOCK_MAX_CHARS,
) -> list[dict[str, Any]]:
    raw_blocks = _split_by_heading(raw_markdown)
    output: list[dict[str, Any]] = []
    counter = 1

    source = "tender_raw.md"
    source_match = re.search(r"<!--\s*来源:\s*(.+?)\s*-->", raw_markdown)
    if source_match:
        source = source_match.group(1).strip()

    for block in raw_blocks:
        title_path = block["title_path"]
        for part in _split_long_block(block["content"], max_chars):
            content = part.strip()
            if not content:
                continue
            hints = build_rule_hints({"content": content})
            chunk = {
                "id": f"{BLOCK_ID_PREFIX}{counter:03d}",
                "source": source,
                "title_path": title_path,
                "content": content,
                "char_count": len(content),
                "rule_hints": hints,
            }
            output.append(chunk)
            counter += 1

    return output


# ============================================================
#  AI 分类
# ============================================================

def _build_classify_prompt(root: Path) -> str:
    prompt_path = root / "prompts" / "classify_tender_blocks.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")

    return """你是投标文件内容分析专家。你的任务是判断招标文件中每个内容块属于什么类别。

类别定义：

1. **score（评分标准）**：评分标准、评分细则、评标办法、评审办法、详细评审、分值表、评分因素、废标项、否决投标、符合性审查、资格性审查中用于评审打分/通过否决的内容。

2. **requirement（采购需求）**：项目背景、采购需求、技术要求、服务要求、交付要求、实施要求、商务响应要求。

3. **contract（合同条款）**：合同条款、付款方式、履约要求、验收要求、违约责任。

4. **notice（招标通知）**：招标公告、投标人须知、投标流程、时间地点、投标文件递交要求。

5. **format（投标格式）**：投标文件格式、声明函、承诺函、报价表模板、附件模板。

6. **qualification（资格要求）**：供应商资格要求、资质要求、人员要求、业绩要求。

7. **appendix（附录）**：附件、附录、参考资料。

8. **unknown（无法判断）**：以上都不符合或无法明确判断。

target_file 规则：
- **score.md**：category 为 score 的内容；或 qualification 中用于评审/打分/废标的资格性审查内容。
- **tender.md**：category 为 requirement、contract、notice、format 的内容；或 qualification 中是普通响应要求的资格内容。
- **other.md**：category 为 appendix、unknown 的内容。

请对以下内容块逐个进行分类。输出格式为 JSON 数组，每个元素包含 id、category、target_file、confidence、reason 字段。只输出 JSON，不要输出任何解释。"""


def _build_batch_messages(
    blocks: list[dict[str, Any]],
    system_prompt: str,
) -> list[dict[str, str]]:
    blocks_text: list[str] = []
    for block in blocks:
        hints = "\n".join(f"  - {h}" for h in block["rule_hints"]) if block["rule_hints"] else "  无"
        content_snippet = block["content"][:2000]
        blocks_text.append(
            f"--- 块 {block['id']} ---\n"
            f"标题路径: {' > '.join(block['title_path'])}\n"
            f"字数: {block['char_count']}\n"
            f"规则提示:\n{hints}\n"
            f"内容:\n{content_snippet}\n"
        )

    user_message = "请对以下招标文件内容块进行分类，输出 JSON 数组：\n\n" + "\n".join(blocks_text)

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]


def _fallback_classify_one(block: dict[str, Any]) -> dict[str, Any]:
    content = block.get("content", "")

    score_hits = sum(1 for kw in HINT_CATEGORY_MAP["评分相关"] if kw in content)
    qualification_hits = sum(1 for kw in HINT_CATEGORY_MAP["资格相关"] if kw in content)
    requirement_hits = sum(1 for kw in HINT_CATEGORY_MAP["需求相关"] if kw in content)
    contract_hits = sum(1 for kw in HINT_CATEGORY_MAP["合同相关"] if kw in content)
    notice_hits = sum(1 for kw in HINT_CATEGORY_MAP["须知相关"] if kw in content)
    format_hits = sum(1 for kw in HINT_CATEGORY_MAP["格式相关"] if kw in content)

    category = "unknown"
    target_file = "other.md"
    reason = "规则兜底：未知类别"

    if score_hits > 0:
        category = "score"
        target_file = "score.md"
        reason = "规则兜底：命中评分关键词"
    elif qualification_hits > 0:
        category = "qualification"
        target_file = "tender.md"
        reason = "规则兜底：命中资格关键词"
    elif requirement_hits > 0:
        category = "requirement"
        target_file = "tender.md"
        reason = "规则兜底：命中需求关键词"
    elif contract_hits > 0:
        category = "contract"
        target_file = "tender.md"
        reason = "规则兜底：命中合同关键词"
    elif notice_hits > 0:
        category = "notice"
        target_file = "tender.md"
        reason = "规则兜底：命中须知关键词"
    elif format_hits > 0:
        category = "format"
        target_file = "tender.md"
        reason = "规则兜底：命中格式关键词"

    result = dict(block)
    result["category"] = category
    result["target_file"] = target_file
    result["confidence"] = 0.3
    result["reason"] = reason
    return result


def fallback_classify_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_fallback_classify_one(b) for b in blocks]


def classify_tender_blocks_with_ai(
    blocks: list[dict[str, Any]],
    root: Path,
    batch_size: int = BATCH_SIZE,
) -> list[dict[str, Any]]:
    system_prompt = _build_classify_prompt(root)
    classified: list[dict[str, Any]] = []
    classified_ids: set[str] = set()
    debug_dir = root / "workspace"
    debug_dir.mkdir(parents=True, exist_ok=True)

    total_batches = (len(blocks) + batch_size - 1) // batch_size

    for batch_idx in range(total_batches):
        start = batch_idx * batch_size
        end = min(start + batch_size, len(blocks))
        batch = blocks[start:end]

        print(f"  [AI 分类] 批次 {batch_idx + 1}/{total_batches}，块 {start + 1}-{end}/{len(blocks)} ...")

        try:
            messages = _build_batch_messages(batch, system_prompt)
            raw_response = chat(messages, temperature=CLASSIFY_TEMPERATURE)

            json_text = extract_json_text(raw_response)
            results = json.loads(json_text)

            if not isinstance(results, list):
                raise ValueError(f"AI 响应不是数组: {type(results)}")

            batch_ids = {b["id"] for b in batch}
            for item in results:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                if item_id not in batch_ids:
                    continue

                category = item.get("category", "unknown")
                target_file = item.get("target_file", "other.md")
                confidence = item.get("confidence", 0.5)
                reason = item.get("reason", "")

                if category not in VALID_CATEGORIES:
                    category = "unknown"
                if target_file not in VALID_TARGET_FILES:
                    target_file = "other.md"
                if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
                    confidence = 0.5

                for block in batch:
                    if block["id"] == item_id:
                        classified.append({
                            **block,
                            "category": category,
                            "target_file": target_file,
                            "confidence": confidence,
                            "reason": str(reason),
                        })
                        classified_ids.add(item_id)
                        break

        except Exception as exc:
            print(f"  [警告] 批次 {batch_idx + 1} AI 分类失败: {exc}，使用规则兜底。")
            debug_path = debug_dir / f"debug_classify_tender_blocks_batch_{batch_idx + 1:03d}.txt"
            try:
                debug_path.write_text(str(exc), encoding="utf-8")
            except Exception:
                pass

        for block in batch:
            if block["id"] not in classified_ids:
                fallback = _fallback_classify_one(block)
                classified.append(fallback)
                classified_ids.add(block["id"])

    return classified


# ============================================================
#  拼接输出
# ============================================================

def assemble_inputs_from_classified_blocks(
    classified: list[dict[str, Any]],
    root: Path,
) -> dict[str, str]:
    score_parts: list[str] = []
    tender_parts: list[str] = []
    other_parts: list[str] = []

    for block in classified:
        target = block.get("target_file", "other.md")
        category = block.get("category", "unknown")
        confidence = block.get("confidence", 0)

        header = (
            f"<!-- block: {block['id']},"
            f" category: {category},"
            f" confidence: {confidence:.2f} -->\n\n"
        )

        if target == "score.md":
            score_parts.append(header + block["content"])
        elif target == "tender.md":
            tender_parts.append(header + block["content"])
        else:
            other_parts.append(header + block["content"])

    score_md = "\n\n---\n\n".join(score_parts)
    tender_md = "\n\n---\n\n".join(tender_parts)
    other_md = "\n\n---\n\n".join(other_parts)

    return {
        "tender_md": tender_md,
        "score_md": score_md,
        "other_md": other_md,
    }


# ============================================================
#  分类报告
# ============================================================

def _generate_classification_report(
    classified: list[dict[str, Any]],
    raw_markdown: str,
) -> dict[str, Any]:
    total = len(classified)
    score_blocks = [b for b in classified if b.get("target_file") == "score.md"]
    tender_blocks = [b for b in classified if b.get("target_file") == "tender.md"]
    other_blocks = [b for b in classified if b.get("target_file") == "other.md"]
    low_conf = [
        b["id"] for b in classified
        if b.get("confidence", 0) < LOW_CONFIDENCE_THRESHOLD
    ]
    fallback_blocks = [
        b["id"] for b in classified
        if "规则兜底" in str(b.get("reason", ""))
    ]

    raw_chars = len(raw_markdown) if raw_markdown else 0
    score_chars = sum(b.get("char_count", 0) for b in score_blocks)

    warnings: list[str] = []

    if len(score_blocks) == 0:
        warnings.append("未识别到评分标准，请人工检查 inputs/score.md。")

    if raw_chars > 0 and score_chars / raw_chars > SCORE_RATIO_WARN:
        warnings.append(
            f"评分标准抽取比例过高（{score_chars / raw_chars:.1%}），可能误分类，请人工检查。"
        )

    if fallback_blocks:
        warnings.append(
            f"{len(fallback_blocks)} 个块使用规则兜底分类，可能不准确: {', '.join(fallback_blocks[:10])}"
        )

    if low_conf:
        warnings.append(
            f"{len(low_conf)} 个块置信度低于 {LOW_CONFIDENCE_THRESHOLD}: {', '.join(low_conf[:10])}"
        )

    return {
        "total_blocks": total,
        "score_blocks": len(score_blocks),
        "tender_blocks": len(tender_blocks),
        "other_blocks": len(other_blocks),
        "warnings": warnings,
        "low_confidence_blocks": low_conf,
        "fallback_blocks": fallback_blocks,
    }


# ============================================================
#  文件扫描与合并（保留原有逻辑）
# ============================================================

def scan_and_merge_tender(root: Path | None = None) -> str:
    root = root or project_root()
    sources_dir = root / "sources" / "tender"
    if not sources_dir.exists() or not any(sources_dir.iterdir()):
        raise FileNotFoundError(
            f"招标文件夹为空或不存在: {sources_dir}，请先将招标文件放入 sources/tender/"
        )

    files = sorted(sources_dir.iterdir())
    supported_files = [f for f in files if f.suffix.lower() in TENDER_EXTENSIONS and f.is_file()]
    if not supported_files:
        raise FileNotFoundError(
            f"sources/tender/ 中没有可识别的文件（支持: {TENDER_EXTENSIONS}）"
        )

    all_parts: list[str] = []
    for file_path in supported_files:
        print(f"  [转换] {file_path.name} ...")
        try:
            content = convert_to_markdown(file_path)
            header = f"<!-- 来源: {file_path.name} -->\n\n"
            all_parts.append(header + content)
        except Exception as exc:
            print(f"  [警告] 转换 {file_path.name} 失败: {exc}")

    if not all_parts:
        raise ValueError("所有招标文件转换失败，请检查文件格式。")

    return "\n\n---\n\n".join(all_parts)


# ============================================================
#  主入口（改造后流程）
# ============================================================

def run_tender_import(root: Path | None = None) -> Path:
    root = root or project_root()

    # Step 1: 文件转换 → raw.md
    merged = scan_and_merge_tender(root)

    imported_dir = root / "workspace" / "imported"
    imported_dir.mkdir(parents=True, exist_ok=True)
    raw_path = imported_dir / "tender_raw.md"
    write_text(raw_path, merged)
    print(f"[完成] 已写入招标原文: {raw_path} ({len(merged)} 字符)")

    # Step 2: 切块 → blocks.json
    blocks = split_tender_into_blocks(merged)
    blocks_path = imported_dir / "tender_blocks.json"
    write_json(blocks_path, blocks)
    print(f"[完成] 已切分为 {len(blocks)} 个块: {blocks_path}")

    # Step 3: AI 分类 → classified_blocks.json
    classified = classify_tender_blocks_with_ai(blocks, root)
    classified_path = imported_dir / "tender_classified_blocks.json"
    write_json(classified_path, classified)
    print(f"[完成] 已完成 AI 分类: {classified_path}")

    # Step 4: 拼接输出
    assembled = assemble_inputs_from_classified_blocks(classified, root)
    tender_md = assembled["tender_md"]
    score_md = assembled["score_md"]
    other_md = assembled["other_md"]

    tender_path = root / "inputs" / "tender.md"
    score_path = root / "inputs" / "score.md"
    other_path = imported_dir / "tender_other.md"

    ensure_dirs(root, ["inputs"])
    write_text(tender_path, tender_md)
    write_text(score_path, score_md)
    write_text(other_path, other_md)

    print(f"[完成] 已生成招标文件: {tender_path} ({len(tender_md)} 字符)")
    print(f"[完成] 已生成评分标准: {score_path} ({len(score_md)} 字符)")
    print(f"[完成] 已生成其他内容: {other_path} ({len(other_md)} 字符)")

    # Step 5: 分类报告
    report = _generate_classification_report(classified, merged)
    report_path = imported_dir / "tender_classification_report.json"
    write_json(report_path, report)
    print(f"[完成] 已生成分类报告: {report_path}")

    if report["warnings"]:
        for w in report["warnings"]:
            print(f"  [警告] {w}")

    return tender_path

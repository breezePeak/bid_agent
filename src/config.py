from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from utils import project_root


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str
    model: str
    timeout: int = 120
    max_retries: int = 3


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f".env 第 {line_number} 行格式错误，应为 KEY=VALUE。")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def get_settings(root: Path | None = None) -> Settings:
    root = root or project_root()
    env_path = root / ".env"
    file_values = _parse_env_file(env_path)
    values = {**file_values, **os.environ}

    required_keys = ["OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"]
    missing = [key for key in required_keys if not str(values.get(key, "")).strip()]
    if missing:
        missing_text = ", ".join(missing)
        raise ConfigError(
            f"缺少必要配置: {missing_text}。请在 {env_path} 中配置，"
            "可参考 .env.example，且不要把 API Key 写入代码。"
        )

    timeout = int(values.get("OPENAI_TIMEOUT", 120))
    max_retries = int(values.get("OPENAI_MAX_RETRIES", 3))

    return Settings(
        base_url=str(values["OPENAI_BASE_URL"]).strip().rstrip("/"),
        api_key=str(values["OPENAI_API_KEY"]).strip(),
        model=str(values["OPENAI_MODEL"]).strip(),
        timeout=timeout,
        max_retries=max_retries,
    )


# ============================================================
#  Tender 切块 + AI 分类 全局配置
# ============================================================

TENDER_EXTENSIONS = {".md", ".docx", ".pdf"}

BLOCK_MAX_CHARS = 3000
BLOCK_ID_PREFIX = "B"

BATCH_SIZE = 12
CLASSIFY_TEMPERATURE = 0.1
LOW_CONFIDENCE_THRESHOLD = 0.6

SCORE_RATIO_WARN = 0.4

SCORE_HINT_KEYWORDS = [
    "评分", "评分标准", "评分细则", "评分办法", "评分项", "评分点",
    "分值", "评审", "评审因素", "评审标准", "评审办法", "评标办法",
    "综合评分", "技术评分", "商务评分", "价格评分", "详细评审",
    "符合性审查", "资格性审查", "废标", "否决投标",
]

REQUIREMENT_HINT_KEYWORDS = [
    "项目背景", "采购需求", "技术要求", "服务要求", "交付要求",
    "实施要求", "商务响应要求", "技术参数", "功能要求",
]

CONTRACT_HINT_KEYWORDS = [
    "合同条款", "付款方式", "履约保证金", "履约要求", "验收标准",
    "验收要求", "违约责任",
]

NOTICE_HINT_KEYWORDS = [
    "招标公告", "投标人须知", "投标流程", "递交截止", "开标时间",
    "开标地点", "投标文件递交",
]

FORMAT_HINT_KEYWORDS = [
    "投标文件格式", "声明函", "承诺函", "报价表", "法定代表人",
    "授权委托书",
]

QUALIFICATION_HINT_KEYWORDS = [
    "供应商资格", "资质要求", "人员要求", "业绩要求", "资格条件",
    "营业执照", "资质证书",
]

HINT_CATEGORY_MAP = {
    "评分相关": SCORE_HINT_KEYWORDS,
    "需求相关": REQUIREMENT_HINT_KEYWORDS,
    "合同相关": CONTRACT_HINT_KEYWORDS,
    "须知相关": NOTICE_HINT_KEYWORDS,
    "格式相关": FORMAT_HINT_KEYWORDS,
    "资格相关": QUALIFICATION_HINT_KEYWORDS,
}

VALID_CATEGORIES = {"score", "requirement", "contract", "notice", "format", "qualification", "appendix", "unknown"}
VALID_TARGET_FILES = {"score.md", "tender.md", "other.md"}

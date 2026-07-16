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
    provider: str = "openai"  # openai | anthropic
    timeout: int = 300
    max_retries: int = 3
    retry_initial_delay: float = 2.0
    retry_max_delay: float = 30.0
    stream: bool = False
    verify_ssl: bool = True


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


def _parse_bool(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "no", "off"}


def get_settings(root: Path | None = None) -> Settings:
    root = root or project_root()
    app_root = Path(__file__).resolve().parent.parent
    app_env_path = app_root / ".env"
    run_env_path = root / ".env"
    config_root_text = os.environ.get("BID_AGENT_CONFIG_ROOT", "").strip()
    config_env_path = Path(config_root_text).resolve() / ".env" if config_root_text else app_env_path
    app_values = _parse_env_file(app_env_path)
    run_values = _parse_env_file(run_env_path) if run_env_path not in {app_env_path, config_env_path} else {}
    config_values = _parse_env_file(config_env_path) if config_env_path != app_env_path else app_values

    if config_root_text:
        # Web 管理的子进程会继承服务启动时的环境变量。中央配置文件必须最后覆盖这些
        # 旧环境值，且本函数每次请求都会重新读取文件，从而让所有工作空间热切换模型。
        file_values = {**app_values, **run_values, **config_values}
        values = {**os.environ, **file_values}
    else:
        # 独立 CLI 仍保留显式环境变量覆盖 .env 的传统行为。
        file_values = {**app_values, **run_values}
        values = {**file_values, **os.environ}

    required_keys = ["OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"]
    missing = [key for key in required_keys if not str(values.get(key, "")).strip()]
    if missing:
        missing_text = ", ".join(missing)
        raise ConfigError(
            f"缺少必要配置: {missing_text}。请在 {config_env_path} 中配置，"
            "可参考 .env.example，且不要把 API Key 写入代码。"
        )

    timeout = int(values.get("OPENAI_TIMEOUT", 300))
    max_retries = max(1, int(values.get("OPENAI_MAX_RETRIES", 3)))
    retry_initial_delay = max(0.1, float(values.get("OPENAI_RETRY_INITIAL_DELAY", 2)))
    retry_max_delay = max(retry_initial_delay, float(values.get("OPENAI_RETRY_MAX_DELAY", 30)))
    stream = _parse_bool(values.get("OPENAI_STREAM"), default=False)
    verify_ssl = _parse_bool(values.get("OPENAI_VERIFY_SSL"), default=True)

    provider = str(values.get("OPENAI_PROVIDER") or values.get("LLM_PROVIDER") or "openai").strip().lower()
    if provider not in {"openai", "anthropic"}:
        provider = "openai"

    return Settings(
        base_url=str(values["OPENAI_BASE_URL"]).strip().rstrip("/"),
        api_key=str(values["OPENAI_API_KEY"]).strip(),
        model=str(values["OPENAI_MODEL"]).strip(),
        provider=provider,
        timeout=timeout,
        max_retries=max_retries,
        retry_initial_delay=retry_initial_delay,
        retry_max_delay=retry_max_delay,
        stream=stream,
        verify_ssl=verify_ssl,
    )


# ============================================================
#  Tender 切块 + AI 分类 全局配置
# ============================================================

TENDER_EXTENSIONS = {".md", ".docx", ".pdf"}

BLOCK_MAX_CHARS = 6000
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

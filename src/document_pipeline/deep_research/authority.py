from __future__ import annotations

import os
import urllib.parse

from ..contracts import EvidenceSourceType


def _configured_domains(name: str, defaults: tuple[str, ...]) -> tuple[str, ...]:
    configured = tuple(
        value.strip().lower().lstrip(".")
        for value in os.environ.get(name, "").split(",")
        if value.strip()
    )
    return tuple(dict.fromkeys((*defaults, *configured)))


def _matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def classify_source_type(url: str) -> EvidenceSourceType:
    """Classify authority from the URL host only; page text is untrusted."""
    host = (urllib.parse.urlsplit(str(url or "")).hostname or "").lower().rstrip(".")
    if not host:
        return EvidenceSourceType.WEB
    standard = _configured_domains(
        "BID_AGENT_DEEP_RESEARCH_STANDARD_DOMAINS",
        ("std.samr.gov.cn", "openstd.samr.gov.cn", "gb688.cn"),
    )
    official = _configured_domains(
        "BID_AGENT_DEEP_RESEARCH_OFFICIAL_DOMAINS",
        ("gov.cn", "gov"),
    )
    academic = _configured_domains(
        "BID_AGENT_DEEP_RESEARCH_ACADEMIC_DOMAINS",
        ("edu.cn", "edu", "ac.cn"),
    )
    if _matches(host, standard):
        return EvidenceSourceType.STANDARD
    if _matches(host, official):
        return EvidenceSourceType.OFFICIAL
    if _matches(host, academic):
        return EvidenceSourceType.ACADEMIC
    return EvidenceSourceType.WEB


def source_type_is_authoritative(source_type: EvidenceSourceType) -> bool:
    return source_type in {
        EvidenceSourceType.OFFICIAL,
        EvidenceSourceType.STANDARD,
        EvidenceSourceType.ACADEMIC,
    }

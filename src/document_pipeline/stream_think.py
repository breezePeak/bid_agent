"""Split model stream output into thinking vs visible body text.

Some providers send reasoning on a dedicated channel. Others embed
``<think>`` / ``<thinking>`` tags inside content. Draft text saved to Word
must never include that thinking.
"""

from __future__ import annotations

import re

_OPEN_TAGS = ("<think>", "<thinking>")
_CLOSE_TAGS = ("</think>", "</thinking>")
_COMPLETE_THINK = re.compile(
    r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>",
    re.IGNORECASE,
)
_ORPHAN_TAG = re.compile(r"</?think(?:ing)?>", re.IGNORECASE)


def _is_tag_prefix(text: str, tags: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(tag.startswith(lowered) for tag in tags if lowered and len(lowered) < len(tag))


def strip_think_tags(text: str) -> str:
    """Remove complete think blocks and leftover tags from finished text."""
    cleaned = _COMPLETE_THINK.sub("", str(text or ""))
    cleaned = _ORPHAN_TAG.sub("", cleaned)
    return cleaned.strip()


class StreamThinkSplitter:
    """Incrementally split mixed content into (thinking, body)."""

    def __init__(self) -> None:
        self.in_think = False
        self._hold = ""

    def feed(self, chunk: str) -> tuple[str, str]:
        data = f"{self._hold}{chunk or ''}"
        self._hold = ""
        thinking_parts: list[str] = []
        body_parts: list[str] = []
        index = 0
        while index < len(data):
            if not self.in_think:
                start = _next_tag(data, index, _OPEN_TAGS)
                if start is None:
                    tail = data[index:]
                    if _is_tag_prefix(tail, _OPEN_TAGS):
                        self._hold = tail
                    else:
                        body_parts.append(tail)
                    break
                pos, end = start
                if pos > index:
                    body_parts.append(data[index:pos])
                self.in_think = True
                index = end
                continue
            close = _next_tag(data, index, _CLOSE_TAGS)
            if close is None:
                tail = data[index:]
                if _is_tag_prefix(tail, _CLOSE_TAGS):
                    self._hold = tail
                else:
                    thinking_parts.append(tail)
                break
            pos, end = close
            if pos > index:
                thinking_parts.append(data[index:pos])
            self.in_think = False
            index = end
        return "".join(thinking_parts), "".join(body_parts)


def _next_tag(text: str, start: int, tags: tuple[str, ...]) -> tuple[int, int] | None:
    lowered = text.lower()
    found: tuple[int, int] | None = None
    for tag in tags:
        pos = lowered.find(tag, start)
        if pos < 0:
            continue
        if found is None or pos < found[0]:
            found = (pos, pos + len(tag))
    return found

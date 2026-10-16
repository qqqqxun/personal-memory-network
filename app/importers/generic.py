from __future__ import annotations

from typing import Any


def parse_generic(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and "turns" in data:
        data = [data]
    if not isinstance(data, list):
        raise ValueError("无法识别的导出格式")
    items = []
    for index, conv in enumerate(data):
        if not isinstance(conv, dict):
            continue
        turns = conv.get("turns") or conv.get("messages") or []
        normalized = []
        for msg in turns:
            if not isinstance(msg, dict):
                continue
            role = (msg.get("role") or msg.get("sender") or "user").replace("human", "user")
            if role not in {"user", "assistant"}:
                continue
            text = msg.get("text") or msg.get("content") or ""
            if isinstance(text, list):
                text = "\n".join(str(x) for x in text)
            if text:
                normalized.append({"role": role, "text": str(text), "created_at": msg.get("created_at")})
        if not normalized:
            continue
        items.append(
            {
                "platform": conv.get("platform") or "generic",
                "conversation_id": str(conv.get("conversation_id") or conv.get("id") or index),
                "title": conv.get("title") or "未命名对话",
                "occurred_at": conv.get("occurred_at") or conv.get("updated_at") or conv.get("created_at"),
                "created_at": conv.get("created_at"),
                "turns": normalized,
            }
        )
    if not items:
        raise ValueError("没有可用的对话")
    return items

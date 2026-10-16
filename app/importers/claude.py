from __future__ import annotations

from typing import Any


def parse_claude(data: Any) -> list[dict[str, Any]]:
    conversations = _as_list(data)
    items = []
    for conv in conversations:
        if not isinstance(conv, dict):
            continue
        messages = conv.get("chat_messages") or conv.get("messages") or []
        if not messages and not conv.get("name") and not conv.get("title"):
            continue
        turns = []
        for msg in messages:
            sender = (msg.get("sender") or msg.get("role") or "").lower()
            role = "user" if sender in {"human", "user"} else "assistant" if sender in {"assistant", "claude"} else ""
            if not role:
                continue
            text = msg.get("text") or _content_text(msg.get("content"))
            if text:
                turns.append({"role": role, "text": text, "created_at": msg.get("created_at")})
        if not turns:
            continue
        items.append(
            {
                "platform": "claude",
                "conversation_id": str(conv.get("uuid") or conv.get("id") or conv.get("name") or len(items)),
                "title": conv.get("name") or conv.get("title") or "未命名对话",
                "occurred_at": conv.get("updated_at") or conv.get("created_at"),
                "created_at": conv.get("created_at"),
                "turns": turns,
            }
        )
    if not items:
        raise ValueError("不是可识别的 Claude 导出")
    return items


def _as_list(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("conversations", "chats", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        if data.get("chat_messages") or data.get("messages"):
            return [data]
    return []


def _content_text(content: Any) -> str:
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        return "\n".join(parts)
    return str(content)

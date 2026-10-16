from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_chatgpt(data: Any) -> list[dict[str, Any]]:
    conversations = _as_list(data)
    items = []
    for conv in conversations:
        if not isinstance(conv, dict):
            continue
        mapping = conv.get("mapping")
        if not mapping and not conv.get("messages"):
            continue
        turns = _turns_from_mapping(mapping) if mapping else _turns_from_messages(conv.get("messages") or [])
        if not turns:
            continue
        items.append(
            {
                "platform": "chatgpt",
                "conversation_id": str(conv.get("conversation_id") or conv.get("id") or conv.get("title") or len(items)),
                "title": conv.get("title") or "未命名对话",
                "occurred_at": _ts(conv.get("update_time") or conv.get("create_time")),
                "created_at": _ts(conv.get("create_time")),
                "turns": turns,
            }
        )
    if not items:
        raise ValueError("不是可识别的 ChatGPT 导出")
    return items


def _as_list(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("conversations", "items", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        if "mapping" in data:
            return [data]
    return []


def _turns_from_mapping(mapping: dict) -> list[dict[str, Any]]:
    rows = []
    for node in mapping.values():
        if not isinstance(node, dict):
            continue
        message = node.get("message") or {}
        author = (message.get("author") or {}).get("role")
        if author not in {"user", "assistant"}:
            continue
        content = message.get("content") or {}
        parts = content.get("parts") or []
        text = _parts_text(parts)
        if not text:
            continue
        rows.append({"role": author, "text": text, "created_at": _ts(message.get("create_time")), "sort": message.get("create_time") or 0})
    rows.sort(key=lambda r: r.get("sort") or 0)
    for row in rows:
        row.pop("sort", None)
    return rows


def _turns_from_messages(messages: list) -> list[dict[str, Any]]:
    rows = []
    for msg in messages:
        role = (msg.get("role") or msg.get("author") or "").replace("human", "user")
        if role not in {"user", "assistant"}:
            continue
        text = msg.get("content") or msg.get("text") or ""
        if isinstance(text, list):
            text = _parts_text(text)
        if text:
            rows.append({"role": role, "text": str(text), "created_at": _ts(msg.get("create_time") or msg.get("created_at"))})
    return rows


def _parts_text(parts: list) -> str:
    chunks = []
    for part in parts:
        if isinstance(part, str):
            chunks.append(part)
        elif isinstance(part, dict):
            if part.get("text"):
                chunks.append(str(part["text"]))
            elif part.get("content"):
                chunks.append(str(part["content"]))
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def _ts(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat()
    return str(value)

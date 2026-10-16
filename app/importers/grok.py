from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_grok(data: Any) -> list[dict[str, Any]]:
    wrappers = _wrappers(data)
    if not wrappers:
        raise ValueError("不是可识别的 Grok 导出。请上传包含 prod-grok-backend.json 的 zip，而不是账单或登录记录。")

    items = []
    for wrapper in wrappers:
        conversation = wrapper.get("conversation") if isinstance(wrapper.get("conversation"), dict) else wrapper
        responses = wrapper.get("responses")
        if responses is None:
            responses = conversation.get("messages") or conversation.get("responses") or []
        turns = _turns(responses if isinstance(responses, list) else [])
        if not turns:
            continue
        conv_id = str(conversation.get("id") or conversation.get("conversation_id") or conversation.get("_id") or len(items))
        title = conversation.get("title") or next((turn["text"] for turn in turns if turn["role"] == "user"), "Grok 对话")
        items.append(
            {
                "platform": "grok",
                "conversation_id": conv_id,
                "title": str(title)[:80],
                "occurred_at": _time(conversation.get("modify_time") or conversation.get("updated_at") or conversation.get("create_time")),
                "created_at": _time(conversation.get("create_time") or conversation.get("created_at")),
                "turns": turns,
            }
        )
    if not items:
        raise ValueError("Grok 导出里没有可读取的对话")
    return items


def _wrappers(data: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 4:
        return []
    found: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and (_is_wrapper(item) or _is_plain_chat(item)):
                found.append(item)
        return found
    if not isinstance(data, dict):
        return []
    for key in ("conversations", "chats", "data"):
        if isinstance(data.get(key), list):
            found.extend(_wrappers(data[key], depth + 1))
            if found:
                return found
    if _is_wrapper(data) or _is_plain_chat(data):
        return [data]
    for value in data.values():
        if isinstance(value, (dict, list)):
            found.extend(_wrappers(value, depth + 1))
            if found:
                return found
    return found


def _is_wrapper(item: dict[str, Any]) -> bool:
    return isinstance(item.get("conversation"), dict) and isinstance(item.get("responses"), list)


def _is_plain_chat(item: dict[str, Any]) -> bool:
    messages = item.get("messages") or item.get("responses")
    return isinstance(messages, list) and ("id" in item or "title" in item or "conversation_id" in item)


def _turns(responses: list) -> list[dict[str, Any]]:
    ordered = _order(responses)
    turns = []
    for node in ordered:
        text = str(node.get("message") or node.get("content") or node.get("text") or "").strip()
        if not text:
            continue
        sender = str(node.get("sender") or node.get("role") or "assistant")
        role = "user" if sender.lower() in {"human", "user"} else "assistant"
        turns.append({"role": role, "text": text, "created_at": _time(node.get("create_time") or node.get("created_at"))})
    return turns


def _order(responses: list) -> list[dict[str, Any]]:
    nodes = []
    for item in responses:
        if not isinstance(item, dict):
            continue
        node = item.get("response") if isinstance(item.get("response"), dict) else item
        nodes.append(node)
    ids = {node.get("_id") or node.get("id") for node in nodes}
    ids.discard(None)
    children: dict[Any, list[dict[str, Any]]] = {item_id: [] for item_id in ids}
    roots = []
    for node in nodes:
        parent = node.get("parent_response_id") or node.get("parent_id")
        if parent in ids:
            children.setdefault(parent, []).append(node)
        else:
            roots.append(node)
    ordered: list[dict[str, Any]] = []
    seen: set[Any] = set()

    def walk(node: dict[str, Any]) -> None:
        node_id = node.get("_id") or node.get("id")
        if node_id in seen:
            return
        seen.add(node_id)
        ordered.append(node)
        for child in children.get(node_id, []):
            walk(child)

    for root in roots:
        walk(root)
    for node in nodes:
        node_id = node.get("_id") or node.get("id")
        if node_id not in seen:
            ordered.append(node)
    return ordered


def _time(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        inner = value.get("$date", value)
        if isinstance(inner, dict) and "$numberLong" in inner:
            millis = int(inner["$numberLong"])
            return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).replace(microsecond=0).isoformat()
        if isinstance(inner, str):
            return inner
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 1e12:
            stamp /= 1000.0
        return datetime.fromtimestamp(stamp, tz=timezone.utc).replace(microsecond=0).isoformat()
    return str(value)

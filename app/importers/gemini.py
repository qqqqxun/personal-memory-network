from __future__ import annotations

import json
import re
from typing import Any

CONV_RE = re.compile(r"/app/(?:c/)?([A-Za-z0-9_-]{4,})")
NOISE = {
    "gemini",
    "gemini apps",
    "bard",
    "used gemini apps",
    "request",
    "response",
    "user",
    "model",
    "prompt",
}


def parse_gemini(data: Any) -> list[dict[str, Any]]:
    events = _events(data)
    if not events or not _looks_like_gemini(events):
        raise ValueError("不是可识别的 Gemini 导出")

    groups: dict[str, list[tuple[str, list[dict[str, Any]], str]]] = {}
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        turns = _event_turns(event)
        if not turns:
            continue
        url = str(event.get("titleUrl") or "")
        match = CONV_RE.search(url)
        conv_id = match.group(1) if match else f"gemini-{index}"
        stamp = str(event.get("time") or "")
        groups.setdefault(conv_id, []).append((stamp, turns, url))

    items = []
    for conv_id, chunks in groups.items():
        chunks.sort(key=lambda item: item[0])
        turns: list[dict[str, Any]] = []
        for _, part, _ in chunks:
            turns.extend(part)
        if not turns:
            continue
        first_user = next((turn["text"] for turn in turns if turn["role"] == "user" and turn.get("text")), "")
        title = (first_user or "Gemini 对话").strip().replace("\n", " ")[:42]
        items.append(
            {
                "platform": "gemini",
                "conversation_id": conv_id,
                "title": title,
                "occurred_at": chunks[-1][0] or None,
                "created_at": chunks[0][0] or None,
                "turns": turns,
            }
        )
    if not items:
        raise ValueError("Gemini 导出里没有可读取的对话。活动记录若已暂停，导出可能是空的。")
    return items


def _events(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("events", "items", "activities", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _looks_like_gemini(events: list) -> bool:
    for event in events[:30]:
        if not isinstance(event, dict):
            continue
        blob = " ".join(
            [
                str(event.get("header") or ""),
                str(event.get("title") or ""),
                str(event.get("titleUrl") or ""),
                " ".join(str(item) for item in (event.get("products") or [])),
            ]
        ).lower()
        if "gemini" in blob or "bard" in blob or event.get("userInteractions"):
            return True
        if str(event.get("title") or "").lower().startswith("prompted"):
            return True
    return False


def _event_turns(event: dict[str, Any]) -> list[dict[str, Any]]:
    stamp = event.get("time")
    turns: list[dict[str, Any]] = []
    for detail in event.get("details") or []:
        if not isinstance(detail, dict):
            continue
        name = str(detail.get("name") or "").lower()
        value = _plain(detail.get("value") or detail.get("text") or "")
        if not value:
            continue
        role = "assistant" if any(word in name for word in ("response", "reply", "回复", "回答")) else "user"
        turns.append({"role": role, "text": value, "created_at": stamp})
    if turns:
        return turns

    for item in event.get("userInteractions") or []:
        interaction = item.get("userInteraction") if isinstance(item, dict) else None
        interaction = interaction or item
        if not isinstance(interaction, dict):
            continue
        request = _best_text(interaction.get("request"))
        response = _best_text(interaction.get("response"))
        if request:
            turns.append({"role": "user", "text": request, "created_at": stamp})
        if response:
            turns.append({"role": "assistant", "text": response, "created_at": stamp})
    if turns:
        return turns

    title = str(event.get("title") or "")
    prompted = re.sub(r"(?i)^prompted\s+", "", title).strip()
    if prompted and prompted.lower() not in NOISE:
        turns.append({"role": "user", "text": prompted, "created_at": stamp})
    for sub in event.get("subtitles") or []:
        if isinstance(sub, dict):
            text = _plain(sub.get("value") or sub.get("name") or "")
        else:
            text = _plain(sub)
        if text and text.lower() not in NOISE and text != prompted:
            turns.append({"role": "user", "text": text, "created_at": stamp})
            break
    html = event.get("safeHtmlItem") or event.get("html") or ""
    if isinstance(html, list):
        html = " ".join(str(part) for part in html)
    html_text = re.sub(r"<[^>]+>", " ", str(html))
    html_text = _plain(html_text)
    if html_text and html_text.lower() not in NOISE:
        turns.append({"role": "assistant", "text": html_text, "created_at": stamp})
    return turns


def _plain(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text)


def _best_text(value: Any) -> str:
    if value is None:
        return ""
    node = value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ""
        try:
            node = json.loads(raw)
        except json.JSONDecodeError:
            return raw[:20000]
    strings: list[str] = []

    def walk(current: Any, depth: int = 0) -> None:
        if depth > 10 or len(strings) > 40:
            return
        if isinstance(current, str):
            text = current.strip()
            if len(text) >= 2 and text.lower() not in NOISE and not text.startswith("http"):
                strings.append(text)
        elif isinstance(current, dict):
            preferred = [current[key] for key in ("text", "content", "value", "prompt", "message") if key in current]
            targets = preferred or list(current.values())
            for child in targets:
                walk(child, depth + 1)
        elif isinstance(current, list):
            for child in current:
                walk(child, depth + 1)

    walk(node)
    if not strings:
        return ""
    strings.sort(key=len, reverse=True)
    return strings[0][:20000]

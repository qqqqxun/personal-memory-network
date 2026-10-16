from __future__ import annotations

from typing import Any

import httpx

from app.settings import load_settings

EXTRACT_PROMPT = """你是个人记忆网络的抽取器。只根据对话抽取结构化 JSON，不要编造。
区分「随口一问」和「表达立场」：问优点不等于偏好。不确定就降低 confidence。

返回 JSON，字段：
{
  "thread_title": "问题线标题或空",
  "new_thread": false,
  "attempts": [{"plan": "", "process": "", "result": "", "outcome": "success|failure|partial|", "reason": ""}],
  "candidates": [{"plan": "", "reason": ""}],
  "open_questions": [{"question": ""}],
  "facts": [{"content": "", "confidence": 0.0}],
  "preferences": [{"content": "", "confidence": 0.0}],
  "entities": [{"name": "", "type": "project|tool|tech|person|org|place|other", "alias": ""}],
  "better_than_current": false
}
"""


class LLMClient:
    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = settings or load_settings()
        self.llm = self.settings.get("llm", {})

    @property
    def enabled(self) -> bool:
        return self.llm.get("provider") not in (None, "", "none")

    def extract(self, conversation_text: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        provider = self.llm.get("provider")
        if provider == "ollama":
            return self._ollama(conversation_text)
        if provider in {"openai", "openai_compat"}:
            return self._openai(conversation_text)
        return None

    def classify_sensitive(self, text: str) -> str | None:
        if not self.enabled or self.llm.get("provider") != "ollama":
            return None
        prompt = (
            "判断文本隐私级别，只返回 public、normal 或 sensitive 之一。"
            "涉及健康、财务、家庭、人际关系为 sensitive。不要解释。\n"
            + text[:4000]
        )
        data = self._ollama_raw(prompt)
        if not data:
            return None
        for level in ("sensitive", "normal", "public"):
            if level in data.lower():
                return level
        return None

    def _ollama(self, text: str) -> dict[str, Any] | None:
        raw = self._ollama_raw(EXTRACT_PROMPT + "\n对话：\n" + text[:12000])
        return _parse_json(raw)

    def _ollama_raw(self, prompt: str) -> str | None:
        base = self.llm.get("base_url") or "http://127.0.0.1:11434"
        model = self.llm.get("model") or "qwen2.5"
        try:
            response = httpx.post(
                f"{base.rstrip('/')}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
                timeout=60.0,
            )
            response.raise_for_status()
            return response.json().get("response", "")
        except Exception:
            return None

    def _openai(self, text: str) -> dict[str, Any] | None:
        base = self.llm.get("base_url") or "https://api.openai.com/v1"
        model = self.llm.get("model") or "gpt-4o-mini"
        key = self.llm.get("api_key") or ""
        try:
            response = httpx.post(
                f"{base.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": EXTRACT_PROMPT},
                        {"role": "user", "content": text[:12000]},
                    ],
                },
                timeout=60.0,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return _parse_json(content)
        except Exception:
            return None


def _parse_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    import json
    import re

    raw = raw.strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]+\}", raw)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

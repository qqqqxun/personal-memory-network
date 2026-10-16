from __future__ import annotations

import json
import tarfile
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from app.config import IMPORT_MONTHS_DEFAULT
from app.importers.chatgpt import parse_chatgpt
from app.importers.claude import parse_claude
from app.importers.gemini import parse_gemini
from app.importers.generic import parse_generic
from app.importers.grok import parse_grok

SKIP_PARTS = (
    "user.json",
    "users.json",
    "message_feedback",
    "shared_conversations",
    "model_comparisons",
    "user_settings",
    "prod-mc-auth",
    "prod-mc-billing",
    "billing",
)


def parse_export(payload: bytes | str | dict | list, filename: str = "", platform_hint: str = "") -> list[dict[str, Any]]:
    documents = load_documents(payload, filename)
    if not documents:
        raise ValueError(_missing_message(filename))
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for name, data in documents:
        try:
            parsed = parse_document(name, data, platform_hint)
        except ValueError as exc:
            errors.append(f"{Path(name).name}: {exc}")
            continue
        for item in parsed:
            key = (item.get("platform") or "", item.get("conversation_id") or "")
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    if items:
        return items
    detail = errors[0] if errors else _missing_message(filename)
    raise ValueError(detail)


def parse_document(name: str, data: Any, platform_hint: str = "") -> list[dict[str, Any]]:
    low = name.lower().replace("\\", "/")
    hint = (platform_hint or "").lower()
    if hint == "gemini" or "myactivity" in low:
        return parse_gemini(data)
    if hint == "grok" or "prod-grok-backend" in low or low.endswith("grok.json"):
        return parse_grok(data)
    if hint == "chatgpt" or "chatgpt" in low:
        return parse_chatgpt(data)
    if hint == "claude" or "claude" in low:
        return parse_claude(data)
    last_error: Exception | None = None
    for parser in (parse_chatgpt, parse_claude, parse_gemini, parse_grok, parse_generic):
        try:
            items = parser(data)
            if items:
                return items
        except Exception as exc:  # noqa: BLE001 — 按格式逐个尝试
            last_error = exc
    raise ValueError(str(last_error) if last_error else "无法识别的导出格式")


def load_documents(payload: bytes | str | dict | list, filename: str = "") -> list[tuple[str, Any]]:
    if isinstance(payload, (dict, list)):
        return [(filename or "upload.json", payload)]
    if isinstance(payload, str):
        path = Path(payload)
        raw = path.read_bytes()
        filename = filename or path.name
    else:
        raw = payload
    name = filename or "upload.json"
    if _is_zip(raw, name):
        return _read_zip(raw)
    if _is_tar(raw, name):
        return _read_tar(raw)
    return [(name, _json_bytes(raw, name))]


def filter_recent(items: list[dict[str, Any]], months: int = IMPORT_MONTHS_DEFAULT) -> list[dict[str, Any]]:
    if months <= 0:
        return items
    cutoff = datetime.now(timezone.utc) - timedelta(days=30 * months)
    kept = []
    for item in items:
        stamp = _parse_time(item.get("occurred_at") or item.get("created_at"))
        if stamp is None or stamp >= cutoff:
            kept.append(item)
    return kept


def _is_zip(raw: bytes, filename: str) -> bool:
    return filename.lower().endswith(".zip") or raw[:2] == b"PK"


def _is_tar(raw: bytes, filename: str) -> bool:
    low = filename.lower()
    return low.endswith((".tgz", ".tar.gz", ".tar")) or raw[:2] == b"\x1f\x8b"


def _read_zip(raw: bytes) -> list[tuple[str, Any]]:
    try:
        archive = zipfile.ZipFile(BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("压缩包已损坏") from exc
    with archive:
        names = [name for name in archive.namelist() if _wanted(name)]
        docs = []
        for name in names:
            docs.append((name, _json_bytes(archive.read(name), name)))
        return docs


def _read_tar(raw: bytes) -> list[tuple[str, Any]]:
    try:
        archive = tarfile.open(fileobj=BytesIO(raw), mode="r:*")
    except tarfile.TarError as exc:
        raise ValueError("压缩包已损坏") from exc
    docs = []
    with archive:
        for member in archive.getmembers():
            if not member.isfile() or not _wanted(member.name):
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            docs.append((member.name, _json_bytes(handle.read(), member.name)))
    return docs


def _wanted(name: str) -> bool:
    low = name.lower().replace("\\", "/")
    if not low.endswith(".json") or low.endswith("/"):
        return False
    if any(part in low for part in SKIP_PARTS):
        return False
    base = low.rsplit("/", 1)[-1]
    if base == "myactivity.json":
        return "gemini" in low or "bard" in low or "my activity" in low
    if "prod-grok-backend" in base:
        return True
    if base == "conversations.json" or base.startswith("conversations-"):
        return True
    return "conversation" in base


def _json_bytes(raw: bytes, name: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{Path(name).name} 不是合法的 JSON") from exc


def _missing_message(filename: str) -> str:
    return (
        "没有找到对话文件。"
        "ChatGPT / Claude 需要 conversations.json，"
        "Gemini 需要 My Activity/Gemini Apps/MyActivity.json，"
        "Grok 需要 prod-grok-backend.json。"
        f"（收到：{filename or '未命名文件'}）"
    )


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 1e12:
            stamp /= 1000.0
        return datetime.fromtimestamp(stamp, tz=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed

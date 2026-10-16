from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import (
    ASSEMBLE_MAX_CHARS,
    BOOTLOADER_BUDGET_CHARS,
    DATA_DIR,
    DEFAULT_VAULT_DIR,
    DESTINATION_MAX_LEVEL,
    IMPORT_MONTHS_DEFAULT,
    PRINCIPLES_BUDGET_CHARS,
    PROJECT_BUDGET_CHARS,
    ensure_data_dir,
)

DEFAULTS: dict[str, Any] = {
    "vault_dir": str(DEFAULT_VAULT_DIR),
    "llm": {
        "provider": "gemini",
        "model": "gemini-3.8-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key": "",
        "proxy": "",
    },
    "budgets": {
        "bootloader": BOOTLOADER_BUDGET_CHARS,
        "principles": PRINCIPLES_BUDGET_CHARS,
        "project": PROJECT_BUDGET_CHARS,
    },
    "archive_after_distill": False,
    "import_months": IMPORT_MONTHS_DEFAULT,
    "assemble_max_chars": ASSEMBLE_MAX_CHARS,
    "destinations": dict(DESTINATION_MAX_LEVEL),
    "explore_enabled_thread_ids": [],
}


def settings_path() -> Path:
    ensure_data_dir()
    return DATA_DIR / "settings.json"


def load_settings() -> dict[str, Any]:
    path = settings_path()
    if not path.exists():
        save_settings(DEFAULTS)
        return json.loads(json.dumps(DEFAULTS))
    data = json.loads(path.read_text(encoding="utf-8"))
    merged = json.loads(json.dumps(DEFAULTS))
    merged.update(data)
    merged["llm"] = {**DEFAULTS["llm"], **data.get("llm", {})}
    merged["budgets"] = {**DEFAULTS["budgets"], **data.get("budgets", {})}
    merged["destinations"] = {**DEFAULTS["destinations"], **data.get("destinations", {})}
    if not merged.get("llm", {}).get("model") or merged["llm"].get("model") == "gemini-2.5-flash":
        merged["llm"]["model"] = "gemini-3.8-flash"
    if merged.get("llm", {}).get("provider") in (None, "", "none"):
        merged["llm"]["provider"] = "gemini"
    if merged["llm"].get("provider") == "gemini" and (
        not merged["llm"].get("base_url") or "11434" in str(merged["llm"].get("base_url"))
    ):
        merged["llm"]["base_url"] = "https://generativelanguage.googleapis.com/v1beta"
    return merged


def save_settings(data: dict[str, Any]) -> dict[str, Any]:
    path = settings_path()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data

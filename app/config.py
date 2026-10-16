from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MEMORY_NET_DATA_DIR", ROOT / "data"))
HOST = os.environ.get("MEMORY_NET_HOST", "127.0.0.1")
PORT = int(os.environ.get("MEMORY_NET_PORT", "8765"))
SERVICE_NAME = "personal-memory-network"
KEYRING_USER = "master-key"
TEST_MODE = os.environ.get("MEMORY_NET_TEST") == "1"

LEVELS = ("public", "normal", "sensitive", "confidential")
LEVEL_RANK = {name: index for index, name in enumerate(LEVELS)}

THREAD_STATUSES = ("active", "paused", "resolved", "abandoned")
RECORD_STATUSES = ("active", "superseded", "archived")
OUTCOMES = ("success", "failure", "partial")
REVIEW_IMPACTS = ("must", "sample", "none")
REVIEW_STATUSES = ("pending", "accepted", "rejected", "modified")

MUST_REVIEW_KINDS = {
    "replace_solution",
    "new_thread",
    "merge_thread",
    "profile_change",
    "lower_sensitivity",
    "uncertain_entity_merge",
}
SAMPLE_REVIEW_KINDS = {
    "new_fact",
    "new_preference",
    "entity_align",
    "insight",
    "new_candidate",
}

DESTINATION_MAX_LEVEL = {
    "chatgpt": "normal",
    "claude": "normal",
    "gemini": "normal",
    "grok": "normal",
    "mcp": "normal",
    "cloud_api": "public",
    "search": "public",
    "local": "sensitive",
    "review": "sensitive",
}

ASSEMBLE_MAX_CHARS = 2500
PROFILE_MAX_CHARS = 800
IMPORT_MONTHS_DEFAULT = 6

DEFAULT_VAULT_DIR = Path(os.environ.get("MEMORY_NET_VAULT_DIR", "C:/个人记忆库"))
BOOTLOADER_BUDGET_CHARS = 1500
PRINCIPLES_BUDGET_CHARS = 1800
PROJECT_BUDGET_CHARS = 2000
SEGMENT_MAX_CHARS = 20000
BATCH_MAX_CHARS = 80000
CARD_MOUNT_BUDGET_CHARS = 2500


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR

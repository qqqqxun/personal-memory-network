from __future__ import annotations

import re
from typing import Any

from app.config import DESTINATION_MAX_LEVEL, LEVEL_RANK
from app.db import Store
from app.privacy import classify_text
from app.settings import load_settings
from app.textutil import clip

PERSON_RE = re.compile(r"(?:同事|朋友|经理|老师|客户)([\u4e00-\u9fff]{1,3})")
MONEY_RE = re.compile(r"(?:¥|￥|人民币)?\s*\d{2,9}(?:\.\d{1,2})?\s*(?:元|万|块)")
CITY_KEEP_RE = re.compile(r"([\u4e00-\u9fff]{2,3}(?:市|省))")


class EgressGate:
    def __init__(self, store: Store) -> None:
        self.store = store

    def max_level(self, destination: str) -> str:
        settings = load_settings()
        mapping = settings.get("destinations") or DESTINATION_MAX_LEVEL
        return mapping.get(destination, "public")

    def process(
        self,
        text: str,
        destination: str,
        *,
        source_ids: list[str] | None = None,
        minimize: bool = True,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        privacy = classify_text(text)
        allowed_level = self.max_level(destination)
        redactions: list[dict[str, Any]] = list(privacy.hits)
        work = privacy.redacted
        work, extra = self._anonymize(work)
        redactions.extend(extra)

        blocked = False
        reason = ""
        if LEVEL_RANK[privacy.level] > LEVEL_RANK[allowed_level] and privacy.level == "confidential":
            blocked = True
            reason = "机密级内容不可外发"
        elif privacy.level == "sensitive" and LEVEL_RANK[allowed_level] < LEVEL_RANK["sensitive"]:
            blocked = True
            reason = "敏感级内容默认不可发往该目的地"
        elif LEVEL_RANK["normal"] > LEVEL_RANK[allowed_level] and privacy.level == "normal":
            # 普通级发往仅允许公开的目的地时，去掉明显个人进度表述。
            work = _strip_personal(work)
            redactions.append({"kind": "minimize_personal", "placeholder": "[个人细节已省略]"})

        if minimize and max_chars:
            work = clip(work, max_chars)

        emitted = "" if blocked else work
        log = self.store.upsert(
            "egress_log",
            {
                "destination": destination,
                "allowed": 0 if blocked else 1,
                "blocked_reason": reason,
                "emitted": emitted,
                "original_preview": clip(text, 400),
                "redactions_json": redactions,
                "source_ids": source_ids or [],
                "sensitivity": privacy.level,
            },
        )
        return {
            "allowed": not blocked,
            "blocked_reason": reason,
            "text": emitted,
            "level": privacy.level,
            "destination": destination,
            "max_level": allowed_level,
            "redactions": redactions,
            "egress_id": log["id"],
        }

    def _anonymize(self, text: str) -> tuple[str, list[dict[str, Any]]]:
        hits: list[dict[str, Any]] = []
        names: dict[str, str] = {}
        index = 1

        def person(match: re.Match[str]) -> str:
            nonlocal index
            raw = match.group(0)
            if raw not in names:
                names[raw] = f"[同事{chr(64 + index)}]" if index <= 26 else f"[人物{index}]"
                index += 1
                hits.append({"kind": "person", "placeholder": names[raw]})
            return names[raw]

        work = PERSON_RE.sub(person, text or "")
        work = MONEY_RE.sub(lambda m: (hits.append({"kind": "money", "placeholder": "[金额区间]"}) or "[金额区间]"), work)
        return work, hits


def _strip_personal(text: str) -> str:
    drop = ("我的项目", "我在做", "进度", "工资", "家里")
    lines = []
    for line in (text or "").splitlines():
        if any(token in line for token in drop):
            continue
        lines.append(line)
    return "\n".join(lines).strip()

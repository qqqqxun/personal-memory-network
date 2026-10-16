from __future__ import annotations

from collections import Counter
from typing import Any

from app.db import Store
from app.ids import now_iso
from app.textutil import jaccard, tokens


class JobRunner:
    def __init__(self, store: Store) -> None:
        self.store = store

    def reflect(self) -> dict[str, Any]:
        attempts = self.store.list("attempts", limit=80)
        fail_words = Counter()
        for attempt in attempts:
            if attempt.get("outcome") == "failure":
                fail_words.update(tokens((attempt.get("reason") or "") + " " + (attempt.get("plan") or "")))
        common = [w for w, n in fail_words.most_common(6) if n >= 2]
        created = []
        if common:
            content = "近期失败尝试中反复出现：" + "、".join(common)
            insight = self.store.upsert(
                "insights",
                {
                    "content": content,
                    "thread_ids": [],
                    "entity_ids": [],
                    "confidence": 0.4,
                    "reviewed": 0,
                },
            )
            review = self.store.upsert(
                "review_items",
                {
                    "kind": "insight",
                    "impact": "sample",
                    "payload_json": {"insight_id": insight["id"], "content": content},
                    "status": "pending",
                },
            )
            created.append(review["id"])
        return {"created": created, "ran_at": now_iso()}

    def consolidate(self) -> dict[str, Any]:
        entities = self.store.list("entities", "record_status='active'")
        merged = 0
        for i, left in enumerate(entities):
            if left.get("record_status") != "active":
                continue
            for right in entities[i + 1 :]:
                if right.get("record_status") != "active":
                    continue
                if jaccard(left.get("name") or "", right.get("name") or "") < 0.86:
                    continue
                self.store.upsert(
                    "review_items",
                    {
                        "kind": "uncertain_entity_merge",
                        "impact": "must",
                        "payload_json": {
                            "keep_id": left["id"],
                            "drop_id": right["id"],
                            "keep": left.get("name"),
                            "drop": right.get("name"),
                        },
                        "status": "pending",
                    },
                )
                merged += 1
        return {"proposed_merges": merged, "ran_at": now_iso()}

    def decay(self) -> dict[str, Any]:
        changed = []
        for thread in self.store.list("threads", "record_status!='archived'"):
            mentions = len(thread.get("source_ids") or [])
            if mentions == 0 and (thread.get("weight") or 1) > 0.4:
                thread["weight"] = round(float(thread.get("weight") or 1.0) * 0.85, 3)
                if thread.get("status") == "active":
                    # 不自动暂停，只降权。
                    pass
                self.store.upsert("threads", thread)
                changed.append(thread["id"])
        return {"decayed": changed, "ran_at": now_iso()}

    def consistency(self) -> dict[str, Any]:
        issues = []
        for fact in self.store.list("facts", "record_status='active'"):
            if not fact.get("source_ids"):
                issues.append({"type": "fact_without_source", "id": fact["id"]})
        for thread in self.store.list("threads", "record_status!='archived'"):
            if thread.get("current_solution_id"):
                solution = self.store.get("solutions", thread["current_solution_id"])
                if not solution:
                    issues.append({"type": "missing_solution", "id": thread["id"]})
        isolated = []
        related = {row["from_id"] for row in self.store.list("relations")} | {
            row["to_id"] for row in self.store.list("relations")
        }
        for entity in self.store.list("entities", "record_status='active'"):
            if entity["id"] not in related:
                isolated.append(entity["id"])
        return {"issues": issues, "isolated_entities": isolated, "ran_at": now_iso()}

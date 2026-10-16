from __future__ import annotations

from typing import Any

from app.db import Store
from app.ids import now_iso
from app.privacy import LEVEL_RANK, normalize_level
from app.ratchet import Ratchet


class ReviewError(ValueError):
    pass


class ReviewBoard:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.ratchet = Ratchet(store)

    def pending(self, impact: str | None = None) -> list[dict[str, Any]]:
        if impact:
            rows = self.store.list("review_items", "status='pending' AND impact=?", (impact,))
        else:
            rows = self.store.list("review_items", "status='pending'")
        grouped = []
        for row in rows:
            thread = self.store.get("threads", row["thread_id"]) if row.get("thread_id") else None
            item = dict(row)
            item["thread_title"] = thread.get("title") if thread else ""
            grouped.append(item)
        grouped.sort(key=lambda r: (0 if r["impact"] == "must" else 1, r.get("created_at") or ""))
        return grouped

    def decide(self, item_id: str, action: str, note: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        item = self.store.get("review_items", item_id)
        if not item:
            raise ReviewError("审核项不存在")
        if item.get("status") != "pending":
            raise ReviewError("该审核项已处理")
        action = action.strip()
        if action not in {"accept", "reject", "modify"}:
            raise ReviewError("未知操作")
        data = dict(item.get("payload_json") or {})
        if payload:
            data.update(payload)
        result: dict[str, Any] = {}
        if action == "reject":
            result = self._reject(item, data)
            item["status"] = "rejected"
        elif action == "modify":
            result = self._accept(item, data)
            item["status"] = "modified"
        else:
            result = self._accept(item, data)
            item["status"] = "accepted"
        item["reviewer_note"] = note
        item["reviewed_at"] = now_iso()
        item["reviewed"] = 1
        item["payload_json"] = data
        self.store.upsert("review_items", item)
        return {"item": self.store.get("review_items", item_id), "result": result}

    def _accept(self, item: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        kind = item["kind"]
        if kind == "replace_solution":
            plan = data.get("plan") or ""
            evidence = data.get("evidence") or ""
            return self.ratchet.promote(item["thread_id"], plan=plan, evidence=evidence)
        if kind == "new_thread":
            thread = self.store.get("threads", item["thread_id"])
            if thread:
                if data.get("title"):
                    thread["title"] = data["title"]
                if data.get("goal"):
                    thread["goal"] = data["goal"]
                thread["reviewed"] = 1
                thread["confidence"] = max(float(thread.get("confidence") or 0), 0.75)
                self.store.upsert("threads", thread)
            return {"thread_id": item["thread_id"]}
        if kind == "merge_thread":
            return self._merge_threads(data.get("keep_id"), data.get("drop_id"))
        if kind == "profile_change":
            row = self.store.get("profile", data.get("profile_id"))
            if row:
                if data.get("content"):
                    row["content"] = data["content"]
                row["reviewed"] = 1
                row["confidence"] = 0.85
                self.store.upsert("profile", row)
            return {"profile_id": data.get("profile_id")}
        if kind == "lower_sensitivity":
            return self._set_level(data, allow_downgrade=True)
        if kind == "uncertain_entity_merge":
            return self._merge_entities(data.get("keep_id"), data.get("drop_id"))
        if kind in {"new_fact", "new_preference", "entity_align", "insight", "new_candidate"}:
            self._mark_reviewed(kind, data)
            return data
        return data

    def _reject(self, item: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        kind = item["kind"]
        if kind == "new_thread" and item.get("thread_id"):
            thread = self.store.get("threads", item["thread_id"])
            if thread:
                thread["record_status"] = "archived"
                thread["status"] = "abandoned"
                self.store.upsert("threads", thread)
        if kind == "new_candidate" and data.get("candidate_id"):
            row = self.store.get("candidates", data["candidate_id"])
            if row:
                row["record_status"] = "archived"
                self.store.upsert("candidates", row)
        if kind == "new_fact" and data.get("fact_id"):
            row = self.store.get("facts", data["fact_id"])
            if row:
                row["record_status"] = "archived"
                self.store.upsert("facts", row)
        if kind == "insight" and data.get("insight_id"):
            row = self.store.get("insights", data["insight_id"])
            if row:
                row["record_status"] = "archived"
                self.store.upsert("insights", row)
        return {"rejected": True}

    def _mark_reviewed(self, kind: str, data: dict[str, Any]) -> None:
        mapping = {
            "new_fact": ("facts", "fact_id"),
            "new_preference": ("profile", "profile_id"),
            "entity_align": ("entities", "entity_id"),
            "insight": ("insights", "insight_id"),
            "new_candidate": ("candidates", "candidate_id"),
        }
        table, key = mapping[kind]
        item_id = data.get(key)
        if not item_id:
            return
        row = self.store.get(table, item_id)
        if not row:
            return
        row["reviewed"] = 1
        row["confidence"] = max(float(row.get("confidence") or 0), 0.75)
        if kind == "entity_align" and data.get("alias"):
            aliases = list(row.get("aliases_json") or [])
            if data["alias"] not in aliases:
                aliases.append(data["alias"])
            row["aliases_json"] = aliases
        if data.get("content") and "content" in row:
            row["content"] = data["content"]
        if data.get("plan") and "plan" in row:
            row["plan"] = data["plan"]
        self.store.upsert(table, row)

    def _merge_threads(self, keep_id: str, drop_id: str) -> dict[str, Any]:
        keep = self.store.get("threads", keep_id)
        drop = self.store.get("threads", drop_id)
        if not keep or not drop:
            raise ReviewError("合并问题线失败")
        for table in ("attempts", "candidates", "solutions", "open_questions"):
            for row in self.store.list(table, "thread_id=?", (drop_id,)):
                row["thread_id"] = keep_id
                self.store.upsert(table, row)
        drop["record_status"] = "archived"
        drop["parent_id"] = keep_id
        self.store.upsert("threads", drop)
        return {"keep_id": keep_id, "drop_id": drop_id}

    def _merge_entities(self, keep_id: str, drop_id: str) -> dict[str, Any]:
        keep = self.store.get("entities", keep_id)
        drop = self.store.get("entities", drop_id)
        if not keep or not drop:
            raise ReviewError("合并实体失败")
        aliases = list(keep.get("aliases_json") or [])
        for name in [drop.get("name"), *(drop.get("aliases_json") or [])]:
            if name and name not in aliases:
                aliases.append(name)
        keep["aliases_json"] = aliases
        keep["reviewed"] = 1
        self.store.upsert("entities", keep)
        drop["record_status"] = "archived"
        self.store.upsert("entities", drop)
        for fact in self.store.list("facts", "subject_entity_id=?", (drop_id,)):
            fact["subject_entity_id"] = keep_id
            self.store.upsert("facts", fact)
        return {"keep_id": keep_id, "drop_id": drop_id}

    def _set_level(self, data: dict[str, Any], allow_downgrade: bool) -> dict[str, Any]:
        table = data.get("table")
        item_id = data.get("id")
        level = normalize_level(data.get("sensitivity") or "normal")
        row = self.store.get(table, item_id)
        if not row:
            raise ReviewError("目标不存在")
        old = row.get("sensitivity") or "normal"
        if not allow_downgrade and LEVEL_RANK[level] < LEVEL_RANK[old]:
            raise ReviewError("降低隐私级别必须经过人工审核")
        row["sensitivity"] = level
        row["reviewed"] = 1
        self.store.upsert(table, row)
        return {"id": item_id, "sensitivity": level}

from __future__ import annotations

from typing import Any

from app.db import Store
from app.ids import now_iso


class RatchetError(ValueError):
    pass


class Ratchet:
    def __init__(self, store: Store) -> None:
        self.store = store

    def propose_from_attempt(self, attempt_id: str) -> dict[str, Any]:
        attempt = self.store.get("attempts", attempt_id)
        if not attempt:
            raise RatchetError("尝试不存在")
        thread = self.store.get("threads", attempt["thread_id"])
        if not thread:
            raise RatchetError("问题线不存在")
        return {
            "thread_id": thread["id"],
            "attempt_id": attempt_id,
            "current_solution_id": thread.get("current_solution_id"),
            "plan": attempt.get("plan"),
            "evidence": attempt.get("result") or attempt.get("reason"),
        }

    def promote(self, thread_id: str, *, plan: str, evidence: str = "", source_ids: list[str] | None = None) -> dict[str, Any]:
        thread = self.store.get("threads", thread_id)
        if not thread:
            raise RatchetError("问题线不存在")
        old_id = thread.get("current_solution_id")
        new_solution = self.store.upsert(
            "solutions",
            {
                "thread_id": thread_id,
                "plan": plan,
                "evidence": evidence,
                "effective_from": now_iso(),
                "record_status": "active",
                "reviewed": 1,
                "confidence": 0.8,
                "source_ids": source_ids or [],
                "sensitivity": thread.get("sensitivity") or "normal",
            },
        )
        if old_id:
            old = self.store.get("solutions", old_id)
            if old:
                old["record_status"] = "superseded"
                old["superseded_by"] = new_solution["id"]
                old["supersede_reason"] = f"被更优方案取代：{plan}"
                self.store.upsert("solutions", old)
        thread["current_solution_id"] = new_solution["id"]
        thread["reviewed"] = 1
        parts = [thread.get("title") or "", "当前最优解：" + plan]
        thread["summary"] = "。".join(p for p in parts if p)[:800]
        self.store.upsert("threads", thread)
        return {"thread": self.store.get("threads", thread_id), "solution": new_solution, "previous_id": old_id}

    def history(self, thread_id: str) -> list[dict[str, Any]]:
        return self.store.list("solutions", "thread_id=?", (thread_id,), order="created_at ASC")

from __future__ import annotations

from typing import Any

from app.db import Store
from app.ids import now_iso
from app.settings import load_settings
from app.textutil import jaccard


class Explorer:
    """阶段一实现跨线借鉴与填补空白；联网前沿扫描留到阶段五。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def run(self, thread_id: str | None = None) -> dict[str, Any]:
        threads = (
            [self.store.get("threads", thread_id)]
            if thread_id
            else self.store.list("threads", "status='active' AND record_status!='archived'")
        )
        created = []
        for thread in threads:
            if not thread:
                continue
            created.extend(self._borrow(thread))
            created.extend(self._fill_gaps(thread))
            created.extend(self._challenge(thread))
        return {"candidates": created, "count": len(created), "ran_at": now_iso()}

    def _borrow(self, thread: dict[str, Any]) -> list[str]:
        ids = []
        existing = [c.get("plan") or "" for c in self.store.list("candidates", "thread_id=?", (thread["id"],))]
        others = self.store.list("attempts", "outcome='success' AND thread_id!=?", (thread["id"],))
        for attempt in others:
            plan = attempt.get("plan") or ""
            if not plan or any(jaccard(plan, old) > 0.7 for old in existing):
                continue
            other = self.store.get("threads", attempt["thread_id"])
            if not other:
                continue
            if jaccard(thread.get("title") or "", other.get("title") or "") < 0.08 and jaccard(
                thread.get("summary") or "", other.get("summary") or ""
            ) < 0.08:
                continue
            row = self.store.upsert(
                "candidates",
                {
                    "thread_id": thread["id"],
                    "plan": plan,
                    "origin": "borrow",
                    "origin_note": f"来自问题线《{other.get('title')}》的成功尝试",
                    "reason": attempt.get("reason") or attempt.get("result") or "其他问题线已验证有效，可能可迁移。",
                    "priority": 2,
                    "sensitivity": thread.get("sensitivity") or "normal",
                    "confidence": 0.4,
                },
            )
            ids.append(row["id"])
            existing.append(plan)
        return ids

    def _fill_gaps(self, thread: dict[str, Any]) -> list[str]:
        ids = []
        questions = self.store.list("open_questions", "thread_id=? AND status='open'", (thread["id"],))
        facts = self.store.list("facts", "record_status='active'")
        for question in questions:
            q = question.get("question") or ""
            matches = [f for f in facts if jaccard(q, f.get("content") or "") >= 0.28]
            if not matches:
                continue
            plan = f"用已有结论回答开放问题：{q}"
            row = self.store.upsert(
                "candidates",
                {
                    "thread_id": thread["id"],
                    "plan": plan,
                    "origin": "fill_gap",
                    "reason": matches[0].get("content"),
                    "priority": 2,
                    "sensitivity": thread.get("sensitivity") or "normal",
                    "confidence": 0.35,
                },
            )
            ids.append(row["id"])
        return ids

    def _challenge(self, thread: dict[str, Any]) -> list[str]:
        if not thread.get("current_solution_id"):
            return []
        solution = self.store.get("solutions", thread["current_solution_id"])
        if not solution:
            return []
        failures = self.store.list("attempts", "thread_id=? AND outcome='failure'", (thread["id"],), limit=3)
        if not failures:
            return []
        plan = f"挑战当前最优解「{solution.get('plan')}」：针对已暴露弱点寻找完全不同的路径"
        row = self.store.upsert(
            "candidates",
            {
                "thread_id": thread["id"],
                "plan": plan,
                "origin": "challenge",
                "reason": "弱点：" + "；".join((f.get("reason") or f.get("plan") or "") for f in failures),
                "priority": 3,
                "sensitivity": thread.get("sensitivity") or "normal",
                "confidence": 0.3,
            },
        )
        return [row["id"]]

    def weekly_brief(self) -> dict[str, Any]:
        settings = load_settings()
        items = []
        for candidate in self.store.list("candidates", "record_status='active'", limit=30):
            thread = self.store.get("threads", candidate["thread_id"])
            items.append(
                {
                    "candidate": candidate,
                    "thread_title": thread.get("title") if thread else "",
                }
            )
        return {"title": "探索简报", "items": items[:20], "settings_note": settings.get("llm", {}).get("provider")}

from __future__ import annotations

from typing import Any

from app.config import ASSEMBLE_MAX_CHARS, LEVEL_RANK, PROFILE_MAX_CHARS
from app.db import Store
from app.egress import EgressGate
from app.settings import load_settings
from app.textutil import clip, cosine, hashed_vector, jaccard


class Assembler:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.gate = EgressGate(store)

    def assemble(self, query: str, destination: str = "local", max_chars: int | None = None) -> dict[str, Any]:
        settings = load_settings()
        limit = max_chars or int(settings.get("assemble_max_chars") or ASSEMBLE_MAX_CHARS)
        allowed = self.gate.max_level(destination)
        ranked = [
            (thread, score)
            for thread, score in self.rank_threads(query, limit=8)
            if LEVEL_RANK[thread.get("sensitivity") or "normal"] <= LEVEL_RANK[allowed]
        ][:5]
        standing = self._standing_layer(allowed)
        on_demand = []
        thread_ids = []
        for thread, score in ranked:
            thread_ids.append(thread["id"])
            on_demand.append(self._thread_layer(thread, score))
        raw = "\n\n".join([standing] + on_demand).strip()
        raw = clip(raw, limit)
        gated = self.gate.process(raw, destination, max_chars=limit)
        gated["thread_ids"] = thread_ids
        gated["standing"] = standing
        gated["layers"] = on_demand
        gated["query"] = query
        return gated

    def rank_threads(self, query: str, limit: int = 5) -> list[tuple[dict[str, Any], float]]:
        qvec = hashed_vector(query)
        embeddings = {row["owner_id"]: row["vector_json"] for row in self.store.embeddings("threads")}
        scored: list[tuple[dict[str, Any], float]] = []
        for thread in self.store.list("threads", "record_status!='archived'"):
            blob = " ".join(
                [
                    thread.get("title") or "",
                    thread.get("summary") or "",
                    thread.get("goal") or "",
                ]
            )
            lexical = jaccard(query, blob)
            semantic = cosine(qvec, embeddings[thread["id"]]) if thread["id"] in embeddings else 0.0
            weight = float(thread.get("weight") or 1.0)
            boost = 0.15 if thread.get("status") == "active" else 0.0
            score = (0.55 * lexical + 0.45 * semantic + boost) * weight
            if score > 0.05:
                scored.append((thread, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        if scored:
            return scored[:limit]
        active = [(t, 0.01) for t in self.store.list("threads", "status='active' AND record_status!='archived'", limit=limit)]
        return active[:limit]

    def _standing_layer(self, max_level: str = "sensitive") -> str:
        rows = [
            row
            for row in self.store.list("profile", "record_status='active'", order="updated_at DESC")
            if LEVEL_RANK[row.get("sensitivity") or "normal"] <= LEVEL_RANK[max_level]
        ]
        if not rows:
            return "【常驻层】尚未填写身份档案与偏好。"
        identity = [r["content"] for r in rows if r.get("category") == "identity"]
        prefs = [r["content"] for r in rows if r.get("category") != "identity"]
        parts = ["【常驻层】"]
        if identity:
            parts.append("身份：" + "；".join(identity[:6]))
        if prefs:
            parts.append("偏好：" + "；".join(prefs[:8]))
        return clip("\n".join(parts), PROFILE_MAX_CHARS)

    def _thread_layer(self, thread: dict[str, Any], score: float) -> str:
        lines = [
            f"【问题线】{thread.get('title')}（相关度 {score:.2f}，状态 {thread.get('status')}）",
        ]
        if thread.get("goal"):
            lines.append(f"目标：{thread['goal']}")
        if thread.get("constraints"):
            lines.append(f"约束：{thread['constraints']}")
        if thread.get("current_solution_id"):
            solution = self.store.get("solutions", thread["current_solution_id"])
            if solution:
                lines.append(f"当前最优解：{solution.get('plan')}")
                if solution.get("evidence"):
                    lines.append(f"证据：{solution.get('evidence')}")
        failures = self.store.list(
            "attempts",
            "thread_id=? AND outcome='failure'",
            (thread["id"],),
            order="updated_at DESC",
            limit=5,
        )
        if failures:
            lines.append("已失败/已否定（请勿再推荐）：")
            for item in failures:
                reason = item.get("reason") or item.get("result") or ""
                lines.append(f"- {item.get('plan')}。原因：{reason}")
        superseded = self.store.list(
            "solutions",
            "thread_id=? AND record_status='superseded'",
            (thread["id"],),
            limit=3,
        )
        for item in superseded:
            lines.append(f"- 旧方案（已被取代）：{item.get('plan')}。{item.get('supersede_reason') or ''}")
        questions = self.store.list(
            "open_questions",
            "thread_id=? AND status='open'",
            (thread["id"],),
            limit=5,
        )
        if questions:
            lines.append("开放问题：")
            for item in questions:
                lines.append(f"- {item.get('question')}")
        return "\n".join(lines)

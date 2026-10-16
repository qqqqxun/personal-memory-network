from __future__ import annotations

import re
from typing import Any

from app.config import MUST_REVIEW_KINDS, SAMPLE_REVIEW_KINDS
from app.db import Store
from app.ids import new_id, now_iso
from app.llm import LLMClient
from app.privacy import classify_text, inherit_level
from app.textutil import hashed_vector, jaccard, join_turns, tokens

QUESTION_RE = re.compile(r".{6,}(?:[？\?]|(?:吗|呢|如何|怎么|怎样|为何|为什么)[。？\?]?)$")
ATTEMPT_RE = re.compile(r"(?:试了|尝试了|试过|搞了|用过)\s*([^。\n]{4,80})")
FAIL_RE = re.compile(r"(失败|没用|不行|没成功|踩坑|放弃了|太慢|不稳定)")
SUCCESS_RE = re.compile(r"(成功了|有效|搞定了|能用|明显更好|终于)")
CANDIDATE_RE = re.compile(r"(?:可以试试|不如用|考虑用|方案是|改用)\s*([^。\n]{4,80})")
FACT_HINT_RE = re.compile(r"(结论是|目前最好|最优|已经确认|事实是)")
ENTITY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}|(?:《)([^》]{2,30})(?:》)")


def impact_for(kind: str) -> str:
    if kind in MUST_REVIEW_KINDS:
        return "must"
    if kind in SAMPLE_REVIEW_KINDS:
        return "sample"
    return "none"


class Digester:
    def __init__(self, store: Store, llm: LLMClient | None = None) -> None:
        self.store = store
        self.llm = llm or LLMClient()

    def ingest_conversation(
        self,
        *,
        platform: str,
        conversation_id: str,
        title: str,
        turns: list[dict],
        occurred_at: str | None = None,
        skip_record: bool = False,
    ) -> dict[str, Any]:
        if skip_record:
            return {"skipped": True, "reason": "not_recorded"}

        text = f"{title}\n" + join_turns(turns)
        privacy = classify_text(text)
        stored_text = privacy.redacted
        level = "sensitive" if privacy.level == "confidential" else privacy.level
        llm_ok = privacy.level in {"public", "normal"}

        existing = self.store.find_source(platform, conversation_id) if conversation_id else None
        if existing:
            if (existing.get("raw_text") or "") == stored_text:
                return {"skipped": True, "reason": "unchanged", "source_id": existing["id"]}
            existing["raw_text"] = stored_text
            existing["title"] = title or existing.get("title")
            existing["occurred_at"] = occurred_at or existing.get("occurred_at")
            existing["sensitivity"] = inherit_level(existing.get("sensitivity") or "normal", level)
            existing["process_status"] = "pending"
            source = self.store.upsert("sources", existing)
            result = self.digest_source(
                source["id"],
                llm_ok=llm_ok,
                thread_id=self._thread_id_for_source(source["id"]),
            )
            result["updated"] = True
            result["source_id"] = source["id"]
            result["sensitivity"] = source["sensitivity"]
            result["redaction_hits"] = privacy.hits
            return result

        source = self.store.upsert(
            "sources",
            {
                "platform": platform,
                "conversation_id": conversation_id,
                "title": title,
                "occurred_at": occurred_at or now_iso(),
                "raw_text": stored_text,
                "process_status": "pending",
                "sensitivity": level,
            },
        )
        result = self.digest_source(source["id"], llm_ok=llm_ok)
        result["source_id"] = source["id"]
        result["sensitivity"] = source["sensitivity"]
        result["redaction_hits"] = privacy.hits
        return result

    def digest_source(self, source_id: str, llm_ok: bool = True, thread_id: str | None = None) -> dict[str, Any]:
        source = self.store.get("sources", source_id)
        if not source:
            raise KeyError(source_id)
        text = source.get("raw_text") or ""
        extracted = heuristic_extract(source.get("title") or "", text)
        if llm_ok:
            llm_data = self.llm.extract(text)
            if llm_data:
                extracted = merge_extract(extracted, llm_data)

        bound = self.store.get("threads", thread_id) if thread_id else None
        if bound:
            new_thread = False
            ids = list(bound.get("source_ids") or [])
            if source["id"] not in ids:
                ids.append(source["id"])
            bound["source_ids"] = ids
            bound["sensitivity"] = inherit_level(bound.get("sensitivity") or "normal", source.get("sensitivity") or "normal")
            thread = self.store.upsert("threads", bound)
        else:
            thread, new_thread = self._assign_thread(source, extracted)
        level = inherit_level(source.get("sensitivity") or "normal", thread.get("sensitivity") or "normal")
        created = {"attempts": [], "candidates": [], "questions": [], "facts": [], "entities": [], "reviews": []}

        for item in extracted.get("attempts") or []:
            plan = (item.get("plan") or "").strip()
            if not plan or self._similar_exists("attempts", thread["id"], "plan", plan):
                continue
            row = self.store.upsert(
                "attempts",
                {
                    "thread_id": thread["id"],
                    "plan": item.get("plan") or "",
                    "process": item.get("process") or "",
                    "result": item.get("result") or "",
                    "outcome": item.get("outcome") or None,
                    "reason": item.get("reason") or "",
                    "sensitivity": level,
                    "source_ids": [source_id],
                    "confidence": float(item.get("confidence") or 0.45),
                    "reviewed": 0,
                },
            )
            created["attempts"].append(row["id"])
            self._embed("attempts", row["id"], row.get("plan") or "")

        for item in extracted.get("candidates") or []:
            row = self._add_candidate(thread["id"], item, source_id, level, origin="conversation")
            if row:
                created["candidates"].append(row["id"])

        for item in extracted.get("open_questions") or []:
            question = (item.get("question") or "").strip()
            if not question:
                continue
            if self._similar_exists("open_questions", thread["id"], "question", question):
                continue
            row = self.store.upsert(
                "open_questions",
                {
                    "thread_id": thread["id"],
                    "question": question,
                    "status": "open",
                    "sensitivity": level,
                    "source_ids": [source_id],
                    "confidence": 0.5,
                },
            )
            created["questions"].append(row["id"])

        for item in extracted.get("entities") or []:
            entity, review_id = self._align_entity(item, source_id, level)
            if entity:
                created["entities"].append(entity["id"])
                linked = self.store.list("relations", "from_id=? AND to_id=?", (thread["id"], entity["id"]))
                if not any(row.get("rel_type") == "involves" for row in linked):
                    self.store.upsert(
                        "relations",
                        {
                            "from_id": thread["id"],
                            "from_type": "thread",
                            "to_id": entity["id"],
                            "to_type": "entity",
                            "rel_type": "involves",
                            "source_ids": [source_id],
                        },
                    )
            if review_id:
                created["reviews"].append(review_id)

        for item in extracted.get("facts") or []:
            fact, review_id = self._upsert_fact(item, source_id, level)
            if fact:
                created["facts"].append(fact["id"])
            if review_id:
                created["reviews"].append(review_id)

        for item in extracted.get("preferences") or []:
            content = (item.get("content") or "").strip()
            if not content or float(item.get("confidence") or 0) < 0.55:
                continue
            known = self.store.list("profile", "category='preference' AND record_status='active'")
            if any(jaccard(content, row.get("content") or "") >= 0.75 for row in known):
                continue
            pref = self.store.upsert(
                "profile",
                {
                    "category": "preference",
                    "content": content,
                    "sensitivity": level,
                    "source_ids": [source_id],
                    "confidence": float(item.get("confidence") or 0.55),
                    "reviewed": 0,
                },
            )
            created["reviews"].append(
                self._queue_review("new_preference", thread["id"], {"profile_id": pref["id"], "content": content})
            )

        if extracted.get("better_than_current") and created["attempts"]:
            attempt = self.store.get("attempts", created["attempts"][0])
            if attempt and thread.get("current_solution_id"):
                created["reviews"].append(
                    self._queue_review(
                        "replace_solution",
                        thread["id"],
                        {
                            "attempt_id": attempt["id"],
                            "current_solution_id": thread["current_solution_id"],
                            "plan": attempt.get("plan"),
                        },
                    )
                )
            elif attempt and not thread.get("current_solution_id"):
                created["reviews"].append(
                    self._queue_review(
                        "replace_solution",
                        thread["id"],
                        {"attempt_id": attempt["id"], "current_solution_id": None, "plan": attempt.get("plan")},
                    )
                )

        if new_thread:
            created["reviews"].append(
                self._queue_review(
                    "new_thread",
                    thread["id"],
                    {"title": thread.get("title"), "source_id": source_id},
                )
            )

        source["process_status"] = "processed"
        source["source_ids"] = [source_id]
        self.store.upsert("sources", source)
        self._refresh_thread_summary(thread["id"])
        return {"thread_id": thread["id"], "new_thread": new_thread, "created": created}

    def _assign_thread(self, source: dict[str, Any], extracted: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        title = (extracted.get("thread_title") or source.get("title") or "未命名问题").strip()
        text = source.get("raw_text") or ""
        best = None
        best_score = 0.0
        for thread in self.store.list("threads", "record_status!='archived'"):
            other = thread.get("title") or ""
            score = max(
                jaccard(title, other),
                jaccard(text[:500], thread.get("summary") or other),
            )
            shared = set(tokens(title)) & set(tokens(other))
            if len(shared) >= 2:
                score = max(score, 0.42)
            if score > best_score:
                best, best_score = thread, score
        if best and best_score >= 0.26 and not extracted.get("new_thread"):
            ids = list(best.get("source_ids") or [])
            if source["id"] not in ids:
                ids.append(source["id"])
            best["source_ids"] = ids
            best["sensitivity"] = inherit_level(best.get("sensitivity") or "normal", source.get("sensitivity") or "normal")
            return self.store.upsert("threads", best), False

        thread = self.store.upsert(
            "threads",
            {
                "title": title,
                "goal": "",
                "constraints": "",
                "summary": text[:300],
                "status": "active",
                "sensitivity": source.get("sensitivity") or "normal",
                "source_ids": [source["id"]],
                "confidence": 0.35,
                "reviewed": 0,
            },
        )
        self._embed("threads", thread["id"], title + " " + (thread.get("summary") or ""))
        return thread, True

    def _thread_id_for_source(self, source_id: str) -> str | None:
        for thread in self.store.list("threads", "record_status!='archived'"):
            if source_id in (thread.get("source_ids") or []):
                return thread["id"]
        return None

    def _add_candidate(self, thread_id: str, item: dict[str, Any], source_id: str, level: str, origin: str) -> dict[str, Any] | None:
        plan = (item.get("plan") or "").strip()
        if not plan or self._similar_exists("candidates", thread_id, "plan", plan):
            return None
        row = self.store.upsert(
            "candidates",
            {
                "thread_id": thread_id,
                "plan": plan,
                "origin": origin,
                "origin_note": item.get("origin_note") or "",
                "reason": item.get("reason") or "",
                "source_url": item.get("source_url") or "",
                "priority": int(item.get("priority") or 3),
                "sensitivity": level,
                "source_ids": [source_id] if source_id else [],
                "confidence": float(item.get("confidence") or 0.4),
            },
        )
        review_id = self._queue_review("new_candidate", thread_id, {"candidate_id": row["id"], "plan": plan})
        row["_review_id"] = review_id
        self._embed("candidates", row["id"], plan)
        return row

    def _align_entity(self, item: dict[str, Any], source_id: str, level: str) -> tuple[dict[str, Any] | None, str | None]:
        name = (item.get("name") or item.get("alias") or "").strip()
        if not name or name.lower() in {"http", "https", "json", "the"}:
            return None, None
        for entity in self.store.list("entities", "record_status='active'"):
            aliases = [entity.get("name") or ""] + list(entity.get("aliases_json") or [])
            if any(jaccard(name, alias) >= 0.72 or name.lower() == (alias or "").lower() for alias in aliases):
                if name not in aliases:
                    aliases.append(name)
                    entity["aliases_json"] = aliases
                    if jaccard(name, entity.get("name") or "") < 0.9:
                        review_id = self._queue_review(
                            "entity_align",
                            None,
                            {"entity_id": entity["id"], "alias": name},
                        )
                        self.store.upsert("entities", entity)
                        return entity, review_id
                    self.store.upsert("entities", entity)
                return entity, None
        entity = self.store.upsert(
            "entities",
            {
                "name": name,
                "type": item.get("type") or "other",
                "aliases_json": [name],
                "sensitivity": level,
                "source_ids": [source_id],
                "confidence": 0.45,
            },
        )
        return entity, None

    def _upsert_fact(self, item: dict[str, Any], source_id: str, level: str) -> tuple[dict[str, Any] | None, str | None]:
        content = (item.get("content") or "").strip()
        if not content:
            return None, None
        for fact in self.store.list("facts", "record_status='active'"):
            score = jaccard(content, fact.get("content") or "")
            if score >= 0.82:
                return fact, None
            if score >= 0.45 and content != fact.get("content"):
                new_fact = self.store.upsert(
                    "facts",
                    {
                        "content": content,
                        "subject_entity_id": item.get("subject_entity_id"),
                        "sensitivity": inherit_level(level, fact.get("sensitivity") or "normal"),
                        "source_ids": [source_id],
                        "confidence": float(item.get("confidence") or 0.4),
                    },
                )
                fact["record_status"] = "superseded"
                fact["superseded_by"] = new_fact["id"]
                self.store.upsert("facts", fact)
                review_id = self._queue_review(
                    "new_fact",
                    None,
                    {"fact_id": new_fact["id"], "supersedes": fact["id"], "content": content},
                )
                return new_fact, review_id
        fact = self.store.upsert(
            "facts",
            {
                "content": content,
                "subject_entity_id": item.get("subject_entity_id"),
                "sensitivity": level,
                "source_ids": [source_id],
                "confidence": float(item.get("confidence") or 0.4),
            },
        )
        review_id = self._queue_review("new_fact", None, {"fact_id": fact["id"], "content": content})
        return fact, review_id

    def _similar_exists(self, table: str, thread_id: str, field: str, text: str) -> bool:
        for row in self.store.list(table, "thread_id=?", (thread_id,)):
            if jaccard(text, row.get(field) or "") >= 0.75:
                return True
        return False

    def _queue_review(self, kind: str, thread_id: str | None, payload: dict[str, Any]) -> str:
        impact = impact_for(kind)
        if impact == "none":
            return ""
        item = self.store.upsert(
            "review_items",
            {
                "kind": kind,
                "impact": impact,
                "thread_id": thread_id,
                "payload_json": payload,
                "status": "pending",
                "sensitivity": "normal",
            },
        )
        return item["id"]

    def _embed(self, owner_type: str, owner_id: str, text: str) -> None:
        if not text:
            return
        self.store.put_embedding(owner_type, owner_id, hashed_vector(text))

    def _refresh_thread_summary(self, thread_id: str) -> None:
        thread = self.store.get("threads", thread_id)
        if not thread:
            return
        attempts = self.store.list("attempts", "thread_id=?", (thread_id,))
        questions = self.store.list("open_questions", "thread_id=? AND status='open'", (thread_id,))
        current = self.store.get("solutions", thread["current_solution_id"]) if thread.get("current_solution_id") else None
        parts = [thread.get("title") or ""]
        if current:
            parts.append("当前最优解：" + (current.get("plan") or ""))
        if attempts:
            latest = attempts[0]
            parts.append(f"最近尝试：{latest.get('plan') or ''}（{latest.get('outcome') or '未知'}）")
        if questions:
            parts.append("开放问题：" + "；".join(q.get("question") or "" for q in questions[:3]))
        thread["summary"] = "。".join(p for p in parts if p)[:800]
        self.store.upsert("threads", thread)
        self._embed("threads", thread_id, thread["summary"])


def heuristic_extract(title: str, text: str) -> dict[str, Any]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    attempts = []
    candidates = []
    questions = []
    facts = []
    entities = []
    preferences = []
    better = False

    for line in lines:
        attempt_match = ATTEMPT_RE.search(line)
        if attempt_match:
            outcome = None
            if FAIL_RE.search(line):
                outcome = "failure"
            elif SUCCESS_RE.search(line):
                outcome = "success"
                better = True
            attempts.append(
                {
                    "plan": attempt_match.group(1).strip(),
                    "process": line,
                    "result": line,
                    "outcome": outcome,
                    "reason": line,
                    "confidence": 0.5,
                }
            )
        cand = CANDIDATE_RE.search(line)
        if cand:
            candidates.append({"plan": cand.group(1).strip(), "reason": line, "confidence": 0.4})
        if line.startswith("我：") and QUESTION_RE.search(line[2:].strip()):
            raw_q = line[2:].strip()
            last = [part.strip() for part in re.split(r"[。；]", raw_q) if part.strip()]
            questions.append({"question": last[-1] if last else raw_q})
        if FACT_HINT_RE.search(line):
            facts.append({"content": line, "confidence": 0.45})
        if re.search(r"(我更喜欢|我倾向|请用|不要再用)", line) and "吗" not in line:
            preferences.append({"content": line, "confidence": 0.6})
        for match in ENTITY_RE.finditer(line):
            name = match.group(1) if match.lastindex else match.group(0)
            if name:
                entities.append({"name": name, "type": "tech" if re.match(r"^[A-Za-z]", name) else "other"})

    if not attempts and FAIL_RE.search(text or ""):
        attempts.append({"plan": title or "未命名尝试", "process": text[:400], "outcome": "failure", "reason": "对话中出现失败描述", "confidence": 0.35})
    if not attempts and SUCCESS_RE.search(text or ""):
        attempts.append({"plan": title or "未命名尝试", "process": text[:400], "outcome": "success", "reason": "对话中出现成功描述", "confidence": 0.35})
        better = True

    seen = set()
    unique_entities = []
    for entity in entities:
        key = entity["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        unique_entities.append(entity)

    return {
        "thread_title": title,
        "new_thread": False,
        "attempts": attempts[:8],
        "candidates": candidates[:8],
        "open_questions": questions[:8],
        "facts": facts[:8],
        "preferences": preferences[:4],
        "entities": unique_entities[:16],
        "better_than_current": better,
    }


def merge_extract(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    if extra.get("thread_title"):
        merged["thread_title"] = extra["thread_title"]
    if extra.get("new_thread"):
        merged["new_thread"] = True
    if extra.get("better_than_current"):
        merged["better_than_current"] = True
    for key in ("attempts", "candidates", "open_questions", "facts", "preferences", "entities"):
        merged[key] = list(base.get(key) or []) + list(extra.get(key) or [])
    return merged


def cluster_titles(items: list[dict[str, Any]], threshold: float = 0.36) -> list[list[dict[str, Any]]]:
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, left in enumerate(items):
        for j in range(i + 1, len(items)):
            if jaccard(left.get("title") or "", items[j].get("title") or "") >= threshold:
                a, b = find(i), find(j)
                parent[b] = a
    groups: dict[int, list[dict[str, Any]]] = {}
    for i, item in enumerate(items):
        groups.setdefault(find(i), []).append(item)
    return list(groups.values())

from __future__ import annotations

from pathlib import Path

from app.config import DATA_DIR, ensure_data_dir
from app.db import Store


def export_markdown(store: Store, dest: Path | None = None) -> Path:
    ensure_data_dir()
    root = dest or (DATA_DIR / "export")
    threads_dir = root / "threads"
    entities_dir = root / "entities"
    threads_dir.mkdir(parents=True, exist_ok=True)
    entities_dir.mkdir(parents=True, exist_ok=True)

    index_lines = ["# 个人记忆网络导出", "", "这是只读视图，不是数据源。", ""]
    for thread in store.list("threads", "record_status!='archived' AND sensitivity!='sensitive'", order="title ASC"):
        if thread.get("sensitivity") not in {"public", "normal"}:
            continue
        slug = _slug(thread.get("title") or thread["id"])
        path = threads_dir / f"{slug}.md"
        body = _thread_markdown(store, thread)
        path.write_text(body, encoding="utf-8")
        index_lines.append(f"- [[{slug}|{thread.get('title')}]]")

    for entity in store.list("entities", "record_status='active' AND sensitivity!='sensitive'"):
        slug = _slug(entity.get("name") or entity["id"])
        path = entities_dir / f"{slug}.md"
        aliases = ", ".join(entity.get("aliases_json") or [])
        path.write_text(
            f"# {entity.get('name')}\n\n类型：{entity.get('type')}\n\n别名：{aliases}\n",
            encoding="utf-8",
        )

    (root / "index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    return root


def _thread_markdown(store: Store, thread: dict) -> str:
    lines = [f"# {thread.get('title')}", ""]
    if thread.get("goal"):
        lines += [f"- 目标：{thread['goal']}"]
    if thread.get("constraints"):
        lines += [f"- 约束：{thread['constraints']}"]
    lines += [f"- 状态：{thread.get('status')}", ""]
    if thread.get("current_solution_id"):
        solution = store.get("solutions", thread["current_solution_id"])
        if solution:
            lines += ["## 当前最优解", "", solution.get("plan") or "", ""]
            if solution.get("evidence"):
                lines += [f"证据：{solution['evidence']}", ""]
    attempts = store.list("attempts", "thread_id=?", (thread["id"],), order="created_at ASC")
    if attempts:
        lines += ["## 尝试记录", ""]
        for item in attempts:
            lines.append(f"- {item.get('plan')} — {item.get('outcome') or '未知'}。{item.get('reason') or ''}")
        lines.append("")
    questions = store.list("open_questions", "thread_id=? AND status='open'", (thread["id"],))
    if questions:
        lines += ["## 开放问题", ""]
        for item in questions:
            lines.append(f"- {item.get('question')}")
        lines.append("")
    return "\n".join(lines)


def _slug(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_ " else "" for ch in text).strip()
    return (cleaned.replace(" ", "-") or "item")[:80]

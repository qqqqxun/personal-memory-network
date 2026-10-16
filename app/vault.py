from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from app.config import (
    BOOTLOADER_BUDGET_CHARS,
    CARD_MOUNT_BUDGET_CHARS,
    DEFAULT_VAULT_DIR,
    PRINCIPLES_BUDGET_CHARS,
    PROJECT_BUDGET_CHARS,
    SEGMENT_MAX_CHARS,
)
from app.importers import parse_export
from app.privacy import redact_secrets
from app.settings import load_settings
from app.textutil import cosine, hashed_vector, jaccard

INBOX_FOLDER = "01_投喂区_Inbox"
ARCHIVE_FOLDER = "02_已消化归档_Archive"
BRAIN_FOLDER = "03_知识结晶_Brain"
EPOCHS_FOLDER = "04_迭代快照_Epochs"
PROJECTS_SUBFOLDER = "项目与问题线"
CARDS_SUBFOLDER = "细节卡片"

BOOTLOADER_FILE = "00_个人主基座_Bootloader.md"
PRINCIPLES_FILE = "01_思维与工程铁律.md"
INBOX_README_NAME = "README_投喂说明.md"

CHILD_SEP = "__"
DIRECTORY_MARKER = "<!-- 以下「领域专卷目录」由系统根据专卷自动生成，手动修改会被覆盖 -->"
BUDGET_TOLERANCE = 1.1

INITIAL_PROFILE = """# 个人主基座（Bootloader）

> 与任何新 AI 开启对话或启动新项目时挂载，让它在开始前就了解我。

## 1. 我是谁与核心定位
- 把外部大模型当作无状态的运算器，本地知识库是唯一的长期记忆。
- 追求信息密度高、可以直接验证的方案。

## 2. 协作约定（AI 必须遵守）
- 先给结论和可执行动作，再解释原因。
- 专卷里标记为「已证伪」的方案不要再推荐。
- 优先本地优先、纯文本可控的方案，除非我明确要求，不要引入重型组件。
"""

INITIAL_PRINCIPLES = """# 思维与工程铁律（L2 认知层）

> 从多次经历中归纳出的跨领域规律、偏好和避坑准则，不记单次事件。

## 一、工程与架构
1. 反过度设计：工具直接服务于核心闭环，砍掉繁琐的审批流和表面形式。
2. 知识库越炼越薄：新认知通过覆盖和归纳替代旧细节，而不是无限追加。

## 二、个人工作流与偏好
- 偏好简洁直接的中文沟通。
- 核心资产保存在本地独立目录，与代码仓库分开。
"""

INBOX_README = """# 01_投喂区（Inbox）使用说明

把想让个人记忆大脑吸收的文件直接放进这个文件夹（可以建子文件夹分类）：

1. 各家 AI 的官方导出：ChatGPT / Claude 的 zip 或 conversations.json，Gemini（Google Takeout）的 MyActivity.json 或 zip，Grok 的 zip 或 prod-grok-backend.json。
2. 自己的项目文档和经历：设计文档、复盘笔记、README、关键代码、随手记的想法。

说明：
- 大文件会被自动切成小段，每段只消化一次。同一段对话出现在多个导出包里（比如每月导出一次 ChatGPT），也只消化一次。
- 文件修改后，只有新增或改动过的段落会被重新消化。
- 本文件会被扫描器忽略。
"""

SUPPORTED_TEXT_EXTS = {
    ".md",
    ".txt",
    ".rst",
    ".csv",
    ".log",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".html",
}
SUPPORTED_EXPORT_EXTS = {".json", ".zip", ".tgz", ".gz"}

CARD_META_RE = re.compile(
    r"^<!-- card: (?P<id>\S+) \| source: (?P<source>.*?) \| epoch: (?P<epoch>\d+) \| origin: (?P<origin>\S+) -->$"
)
DIRECTORY_HEADING_RE = re.compile(r"\n?#{1,3}\s*(?:\d+\.\s*)?(?:领域专卷目录|分领域知识专卷索引)[\s\S]*$")


def slugify_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (name or "").strip())
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._ ")
    return (cleaned or "未命名专卷")[:120]


def strip_directory(text: str) -> str:
    head = (text or "").split(DIRECTORY_MARKER)[0]
    return DIRECTORY_HEADING_RE.sub("", head).rstrip()


def ensure_heading(content: str, title: str) -> str:
    text = (content or "").strip()
    if text.startswith("# "):
        return text
    return f"# {title}\n\n{text}"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _extract_md_title(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _volume_summary(content: str) -> str:
    for line in (content or "").splitlines():
        line = line.strip()
        if line.startswith(("- ", "* ")):
            text = line[2:].strip().lstrip("📌❌❓ ").strip()
            if text:
                return text if len(text) <= 50 else text[:49] + "…"
    return ""


def _split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    buf: list[str] = []
    size = 0
    for line in text.splitlines():
        while len(line) > max_chars:
            if buf:
                pieces.append("\n".join(buf))
                buf, size = [], 0
            pieces.append(line[:max_chars])
            line = line[max_chars:]
        if buf and size + len(line) + 1 > max_chars:
            pieces.append("\n".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        pieces.append("\n".join(buf))
    return [piece for piece in pieces if piece.strip()]


def _conversation_segments(conv: dict[str, Any]) -> list[dict[str, str]]:
    title = (conv.get("title") or "未命名对话").strip()
    platform = conv.get("platform") or "ai"
    when = conv.get("occurred_at") or conv.get("created_at") or ""
    header = f"### 对话：《{title}》（{platform}{'，' + str(when) if when else ''}）"
    units = []
    for turn in conv.get("turns") or []:
        text = (turn.get("text") or "").strip()
        if text:
            units.append(f"{'我' if turn.get('role') == 'user' else 'AI'}：{text}")
    if not units:
        return []
    bodies = _split_text("\n".join(units), SEGMENT_MAX_CHARS - len(header) - 30)
    total = len(bodies)
    out = []
    for index, body in enumerate(bodies, 1):
        part = f"（第 {index}/{total} 部分）" if total > 1 else ""
        out.append({"title": f"{title}{part}", "text": f"{header}{part}\n{body}"})
    return out


def _document_segments(name: str, text: str) -> list[dict[str, str]]:
    header = f"### 文档：《{name}》"
    bodies = _split_text(text.strip(), SEGMENT_MAX_CHARS - len(header) - 30)
    total = len(bodies)
    out = []
    for index, body in enumerate(bodies, 1):
        part = f"（第 {index}/{total} 部分）" if total > 1 else ""
        out.append({"title": f"{name}{part}", "text": f"{header}{part}\n{body}"})
    return out


class Vault:
    """独立记忆库目录（默认 C:\\个人记忆库）的读写、切段扫描、专卷、细节卡片与快照。"""

    def __init__(self, root: Path | str | None = None) -> None:
        if root is None:
            root = load_settings().get("vault_dir") or DEFAULT_VAULT_DIR
        self.root = Path(root)
        self.inbox_dir = self.root / INBOX_FOLDER
        self.archive_dir = self.root / ARCHIVE_FOLDER
        self.brain_dir = self.root / BRAIN_FOLDER
        self.projects_dir = self.brain_dir / PROJECTS_SUBFOLDER
        self.cards_dir = self.brain_dir / CARDS_SUBFOLDER
        self.epochs_dir = self.root / EPOCHS_FOLDER
        self.state_file = self.root / ".vault_state.json"
        self.ensure_structure()

    def ensure_structure(self) -> None:
        for folder in (
            self.inbox_dir,
            self.archive_dir,
            self.brain_dir,
            self.projects_dir,
            self.cards_dir,
            self.epochs_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)
        readme = self.inbox_dir / INBOX_README_NAME
        if not readme.exists():
            readme.write_text(INBOX_README, encoding="utf-8")
        if not (self.brain_dir / BOOTLOADER_FILE).exists():
            self.write_profile(INITIAL_PROFILE)
        if not (self.brain_dir / PRINCIPLES_FILE).exists():
            self.write_principles(INITIAL_PRINCIPLES)
        if not self.state_file.exists():
            self._save_state(self._load_state())

    # ── 状态 ────────────────────────────────────────────────────────

    def _load_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {}
        if self.state_file.exists():
            try:
                state = json.loads(self.state_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                state = {}
        state.setdefault("epoch", 0)
        state.setdefault("processed_segments", {})
        state.setdefault("file_index", {})
        state.setdefault("history", [])
        state.setdefault("manual_batch", [])
        state.pop("processed_files", None)
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def next_epoch(self) -> int:
        return int(self._load_state()["epoch"]) + 1

    def mark_segments_processed(self, hashes: list[str], epoch: int) -> None:
        state = self._load_state()
        for digest in hashes:
            state["processed_segments"][digest] = epoch
        self._save_state(state)

    def set_manual_batch(self, hashes: list[str]) -> None:
        state = self._load_state()
        state["manual_batch"] = hashes
        self._save_state(state)

    def get_manual_batch(self) -> list[str]:
        return list(self._load_state().get("manual_batch") or [])

    # ── 主基座（个人画像 + 自动目录）与铁律 ──────────────────────────

    def read_bootloader(self) -> str:
        path = self.brain_dir / BOOTLOADER_FILE
        return path.read_text(encoding="utf-8") if path.exists() else INITIAL_PROFILE

    def read_profile(self) -> str:
        return strip_directory(self.read_bootloader())

    def write_profile(self, profile: str) -> None:
        body = strip_directory(profile) or INITIAL_PROFILE.strip()
        text = f"{body}\n\n{DIRECTORY_MARKER}\n{self.render_directory()}\n"
        (self.brain_dir / BOOTLOADER_FILE).write_text(text, encoding="utf-8")

    def write_bootloader(self, content: str) -> None:
        self.write_profile(content)

    def refresh_directory(self) -> None:
        self.write_profile(self.read_profile())

    def render_directory(self) -> str:
        volumes = self.list_volumes() if self.projects_dir.exists() else []
        lines = ["## 领域专卷目录", "需要某个领域的细节时，请让我挂载对应专卷。", ""]
        tops = [vol for vol in volumes if not vol["parent"]]
        for vol in tops:
            family = [v for v in volumes if v["slug"] == vol["slug"] or v["slug"].startswith(vol["slug"] + CHILD_SEP)]
            extra = []
            if len(family) > 1:
                extra.append(f"{len(family) - 1} 个子卷")
            cards = sum(v["cards"] for v in family)
            if cards:
                extra.append(f"{cards} 张细节卡片")
            line = f"- 《{vol['title']}》"
            if vol["summary"]:
                line += f"：{vol['summary']}"
            if extra:
                line += f"（{'，'.join(extra)}）"
            lines.append(line)
        if not tops:
            lines.append("- 还没有专卷。")
        return "\n".join(lines)

    def read_principles(self) -> str:
        path = self.brain_dir / PRINCIPLES_FILE
        return path.read_text(encoding="utf-8") if path.exists() else INITIAL_PRINCIPLES

    def write_principles(self, content: str) -> None:
        (self.brain_dir / PRINCIPLES_FILE).write_text(content.strip() + "\n", encoding="utf-8")

    # ── 专卷与子卷 ──────────────────────────────────────────────────

    def _volume_record(self, path: Path) -> dict[str, Any]:
        content = path.read_text(encoding="utf-8")
        slug = path.stem
        parent = slug.rsplit(CHILD_SEP, 1)[0] if CHILD_SEP in slug else None
        stat = path.stat()
        return {
            "slug": slug,
            "title": _extract_md_title(content) or slug.rsplit(CHILD_SEP, 1)[-1],
            "filename": path.name,
            "parent": parent,
            "depth": slug.count(CHILD_SEP),
            "chars": len(content),
            "budget": PROJECT_BUDGET_CHARS,
            "over_budget": len(content) > PROJECT_BUDGET_CHARS * BUDGET_TOLERANCE,
            "summary": _volume_summary(content),
            "mtime": stat.st_mtime,
            "updated_at": _iso(stat.st_mtime),
            "content": content,
            "cards": 0,
        }

    def list_volumes(self) -> list[dict[str, Any]]:
        records = [self._volume_record(path) for path in self.projects_dir.glob("*.md")]
        counts = self.card_counts()
        families: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            record["cards"] = counts.get(record["slug"], 0)
            families.setdefault(record["slug"].split(CHILD_SEP)[0], []).append(record)
        ordered = sorted(families.values(), key=lambda members: max(m["mtime"] for m in members), reverse=True)
        out: list[dict[str, Any]] = []
        for members in ordered:
            out.extend(sorted(members, key=lambda m: m["slug"]))
        return out

    def list_projects(self) -> list[dict[str, Any]]:
        return self.list_volumes()

    def get_project(self, slug: str) -> dict[str, Any] | None:
        path = self.projects_dir / f"{slugify_name(slug)}.md"
        if not path.exists():
            return None
        record = self._volume_record(path)
        record["cards"] = self.card_counts().get(record["slug"], 0)
        return record

    def write_project(self, name_or_slug: str, content: str, refresh: bool = True) -> dict[str, Any]:
        slug = slugify_name(name_or_slug)
        (self.projects_dir / f"{slug}.md").write_text(content.strip() + "\n", encoding="utf-8")
        if refresh:
            self.refresh_directory()
        return self.get_project(slug) or {"slug": slug, "content": content}

    def delete_project(self, slug: str) -> bool:
        path = self.projects_dir / f"{slugify_name(slug)}.md"
        if not path.exists():
            return False
        path.unlink()
        self.refresh_directory()
        return True

    def rank_volumes(self, text: str, volumes: list[dict[str, Any]] | None = None) -> list[tuple[dict[str, Any], float]]:
        query = hashed_vector((text or "")[:8000])
        ranked = []
        for vol in volumes if volumes is not None else self.list_volumes():
            blob = f"{vol['title']}\n{vol['content'][:3000]}"
            score = cosine(query, hashed_vector(blob)) + 0.5 * jaccard((text or "")[:300], vol["title"])
            ranked.append((vol, score))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked

    # ── 细节卡片 ────────────────────────────────────────────────────

    def _cards_path(self, volume_slug: str) -> Path:
        return self.cards_dir / f"{slugify_name(volume_slug)}.md"

    def list_cards(self, volume_slug: str | None = None) -> list[dict[str, Any]]:
        paths = [self._cards_path(volume_slug)] if volume_slug else sorted(self.cards_dir.glob("*.md"))
        cards: list[dict[str, Any]] = []
        for path in paths:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for block in re.split(r"^## ", text, flags=re.M)[1:]:
                lines = block.split("\n")
                title = lines[0].strip()
                meta = CARD_META_RE.match(lines[1].strip()) if len(lines) > 1 else None
                if meta:
                    content = "\n".join(lines[2:]).strip()
                    card = {
                        "id": meta["id"],
                        "source": meta["source"],
                        "epoch": int(meta["epoch"]),
                        "origin": meta["origin"],
                    }
                else:
                    content = "\n".join(lines[1:]).strip()
                    card = {"id": "M" + _text_hash(title + content)[:10], "source": "", "epoch": 0, "origin": "manual"}
                card.update({"volume": path.stem, "title": title, "content": content})
                cards.append(card)
        return cards

    def card_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for card in self.list_cards():
            counts[card["volume"]] = counts.get(card["volume"], 0) + 1
        return counts

    def _save_cards(self, volume_slug: str, cards: list[dict[str, Any]]) -> None:
        path = self._cards_path(volume_slug)
        if not cards:
            if path.exists():
                path.unlink()
            return
        volume = self.get_project(volume_slug) if (self.projects_dir / f"{slugify_name(volume_slug)}.md").exists() else None
        title = volume["title"] if volume else volume_slug
        lines = [f"# 《{title}》细节卡片", ""]
        for card in cards:
            source = str(card.get("source") or "").replace("|", "/").replace("-->", "")
            lines += [
                f"## {card['title']}",
                f"<!-- card: {card['id']} | source: {source} | epoch: {int(card.get('epoch') or 0)} | origin: {card.get('origin') or 'gemini'} -->",
                card["content"].strip(),
                "",
            ]
        path.write_text("\n".join(lines), encoding="utf-8")

    def add_cards(self, cards: list[dict[str, Any]], *, epoch: int, origin: str, source: str = "") -> int:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for raw in cards:
            title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip()[:80]
            content = re.sub(r"^#{1,2} ", "### ", str(raw.get("content") or "").strip(), flags=re.M)
            if not title or not content:
                continue
            volume = slugify_name(str(raw.get("volume") or "未归类"))
            grouped.setdefault(volume, []).append(
                {
                    "id": raw.get("id") or "C" + _text_hash(volume + title + content)[:10],
                    "title": title,
                    "content": content,
                    "source": raw.get("source") or source,
                    "epoch": epoch,
                    "origin": raw.get("origin") or origin,
                    "volume": volume,
                }
            )
        written = 0
        for volume, incoming in grouped.items():
            existing = self.list_cards(volume)
            for card in incoming:
                index = next((i for i, old in enumerate(existing) if old["id"] == card["id"]), None)
                if index is None and card["origin"] != "local":
                    index = next(
                        (
                            i
                            for i, old in enumerate(existing)
                            if old["origin"] != "local" and jaccard(old["title"], card["title"]) >= 0.85
                        ),
                        None,
                    )
                if index is None:
                    existing.append(card)
                else:
                    existing[index] = card
                written += 1
            self._save_cards(volume, existing)
        return written

    def delete_cards(self, predicate: Callable[[dict[str, Any]], bool]) -> int:
        removed = 0
        by_volume: dict[str, list[dict[str, Any]]] = {}
        for card in self.list_cards():
            by_volume.setdefault(card["volume"], []).append(card)
        for volume, cards in by_volume.items():
            kept = [card for card in cards if not predicate(card)]
            if len(kept) != len(cards):
                removed += len(cards) - len(kept)
                self._save_cards(volume, kept)
        return removed

    def delete_card(self, card_id: str) -> bool:
        return self.delete_cards(lambda card: card["id"] == card_id) > 0

    def search_cards(
        self,
        query: str,
        *,
        budget: int = CARD_MOUNT_BUDGET_CHARS,
        prefer_volumes: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        qvec = hashed_vector(query[:4000])
        scored = []
        for card in self.list_cards():
            score = cosine(qvec, hashed_vector(f"{card['title']}\n{card['content']}"))
            score += 0.4 * jaccard(query[:300], card["title"])
            if score < 0.12:
                continue
            if prefer_volumes and any(
                card["volume"] == slug or card["volume"].startswith(slug + CHILD_SEP) for slug in prefer_volumes
            ):
                score += 0.15
            scored.append((score, card))
        scored.sort(key=lambda item: item[0], reverse=True)
        picked, used = [], 0
        for _, card in scored:
            size = len(card["title"]) + len(card["content"]) + 8
            if used + size > budget:
                continue
            picked.append(card)
            used += size
        return picked

    # ── 投喂区：切段、扫描、待消化段 ────────────────────────────────

    def _inbox_files(self) -> Iterator[tuple[Path, str]]:
        for path in sorted(self.inbox_dir.rglob("*")):
            if not path.is_file() or path.name == INBOX_README_NAME or path.name.startswith("."):
                continue
            ext = path.suffix.lower()
            if ext not in SUPPORTED_TEXT_EXTS and ext not in SUPPORTED_EXPORT_EXTS:
                continue
            yield path, str(path.relative_to(self.inbox_dir)).replace("\\", "/")

    def _segments(self, path: Path) -> tuple[list[dict[str, Any]], str, str]:
        ext = path.suffix.lower()
        raw = path.read_bytes()
        pieces: list[dict[str, str]] = []
        kind = "document"
        if ext in SUPPORTED_EXPORT_EXTS:
            try:
                conversations = parse_export(raw, path.name)
            except Exception:
                conversations = []
            if conversations:
                kind = "ai_export"
                for conv in conversations:
                    pieces.extend(_conversation_segments(conv))
            elif ext != ".json":
                return [], "unsupported", "压缩包里没有找到能识别的对话文件"
        if kind == "document":
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="ignore")
            pieces = _document_segments(path.name, text)
        segments = []
        for piece in pieces:
            redacted, hits = redact_secrets(piece["text"])
            segments.append(
                {
                    "title": piece["title"],
                    "text": redacted,
                    "hash": _text_hash(redacted),
                    "redacted_hits": len(hits),
                }
            )
        return segments, kind, ""

    def scan_inbox(self, include_processed: bool = True) -> dict[str, Any]:
        state = self._load_state()
        processed = state["processed_segments"]
        index = state["file_index"]
        dirty = False
        files = []
        seen_rels = set()
        for path, rel in self._inbox_files():
            seen_rels.add(rel)
            digest = _file_sha256(path)
            entry = index.get(rel)
            if not entry or entry.get("sha256") != digest:
                segments, kind, error = self._segments(path)
                entry = {"sha256": digest, "hashes": [s["hash"] for s in segments], "kind": kind, "error": error}
                index[rel] = entry
                dirty = True
            hashes = entry["hashes"]
            done = [h for h in hashes if h in processed]
            total = len(hashes)
            if total == 0:
                status = "unsupported"
            elif len(done) == total:
                status = "processed"
            elif done:
                status = "partial"
            else:
                status = "pending"
            stat = path.stat()
            files.append(
                {
                    "rel_path": rel,
                    "name": path.name,
                    "ext": path.suffix.lower(),
                    "kind": entry.get("kind"),
                    "error": entry.get("error") or "",
                    "size": stat.st_size,
                    "modified_at": _iso(stat.st_mtime),
                    "sha256": digest,
                    "segments_total": total,
                    "segments_done": len(done),
                    "status": status,
                    "last_epoch": max((processed[h] for h in done), default=None),
                }
            )
        for rel in list(index):
            if rel not in seen_rels:
                index.pop(rel)
                dirty = True
        if dirty:
            self._save_state(state)
        return {
            "inbox_dir": str(self.inbox_dir),
            "total_files": len(files),
            "pending_files": sum(1 for f in files if f["status"] in {"pending", "partial"}),
            "pending_segments": sum(f["segments_total"] - f["segments_done"] for f in files),
            "files": files,
        }

    def load_pending_segments(
        self,
        force_all: bool = False,
        selected_rels: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        processed = self._load_state()["processed_segments"]
        seen: set[str] = set()
        out = []
        for path, rel in self._inbox_files():
            if selected_rels is not None and rel not in selected_rels:
                continue
            segments, kind, _ = self._segments(path)
            for index, seg in enumerate(segments, 1):
                if seg["hash"] in seen:
                    continue
                seen.add(seg["hash"])
                if not force_all and seg["hash"] in processed:
                    continue
                out.append(
                    {
                        "rel_path": rel,
                        "name": path.name,
                        "hash": seg["hash"],
                        "index": index,
                        "total": len(segments),
                        "title": seg["title"],
                        "kind": kind,
                        "text": seg["text"],
                        "chars": len(seg["text"]),
                        "redacted_hits": seg["redacted_hits"],
                    }
                )
        return out

    # ── 快照与报告 ──────────────────────────────────────────────────

    def record_epoch(
        self,
        *,
        epoch: int,
        report: dict[str, Any],
        rel_paths: list[str],
        archive_files: bool = False,
    ) -> dict[str, Any]:
        now = _now()
        folder = self.epochs_dir / f"epoch_{epoch:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.brain_dir, folder / BRAIN_FOLDER, dirs_exist_ok=True)
        (folder / "演进报告_Diff.md").write_text(_format_epoch_report_md(epoch, now, rel_paths, report), encoding="utf-8")
        (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        state = self._load_state()
        entry = {
            "epoch": epoch,
            "ran_at": now,
            "files_count": len(rel_paths),
            "files": rel_paths,
            "segments": report.get("segments", 0),
            "batches": report.get("batches", 0),
            "partial": bool(report.get("partial")),
            "summary": report.get("epoch_summary") or "完成一轮蒸馏",
            "overwritten": report.get("overwritten") or [],
            "abstracted": report.get("abstracted") or [],
            "pruned": report.get("pruned") or [],
            "touched_projects": report.get("touched_projects") or [],
            "warnings": report.get("warnings") or [],
            "cards": report.get("cards", 0),
            "engine": report.get("engine") or "gemini",
        }
        state["history"].append(entry)
        state["epoch"] = max(int(state["epoch"]), epoch)
        self._save_state(state)

        if archive_files:
            self._archive_finished(rel_paths, epoch)
        return entry

    def _archive_finished(self, rel_paths: list[str], epoch: int) -> None:
        scan = {item["rel_path"]: item for item in self.scan_inbox()["files"]}
        for rel in dict.fromkeys(rel_paths):
            item = scan.get(rel)
            if not item or item["status"] != "processed":
                continue
            src = self.inbox_dir / rel
            dst = self.archive_dir / f"epoch_{epoch:03d}" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))

    def brain_snapshot(self) -> dict[str, Any]:
        profile = self.read_profile()
        bootloader = self.read_bootloader()
        principles = self.read_principles()
        volumes = self.list_volumes()
        state = self._load_state()
        cards_total = sum(self.card_counts().values())
        return {
            "vault_dir": str(self.root),
            "inbox_dir": str(self.inbox_dir),
            "brain_dir": str(self.brain_dir),
            "epoch": state["epoch"],
            "bootloader": {
                "content": bootloader,
                "profile": profile,
                "directory": self.render_directory(),
                "chars": len(profile),
                "budget": BOOTLOADER_BUDGET_CHARS,
                "over_budget": len(profile) > BOOTLOADER_BUDGET_CHARS * BUDGET_TOLERANCE,
            },
            "principles": {
                "content": principles,
                "chars": len(principles),
                "budget": PRINCIPLES_BUDGET_CHARS,
                "over_budget": len(principles) > PRINCIPLES_BUDGET_CHARS * BUDGET_TOLERANCE,
            },
            "projects": [{k: v for k, v in vol.items() if k != "mtime"} for vol in volumes],
            "top_volumes": sum(1 for v in volumes if not v["parent"]),
            "cards_total": cards_total,
            "total_brain_chars": len(bootloader) + len(principles) + sum(v["chars"] for v in volumes),
            "history": list(reversed(state["history"][-30:])),
        }

    # ── 挂载包 ──────────────────────────────────────────────────────

    def build_mount_pack(
        self,
        *,
        project_slugs: list[str] | None = None,
        task_prompt: str = "",
        include_principles: bool = True,
        include_cards: bool = True,
        card_budget: int = CARD_MOUNT_BUDGET_CHARS,
    ) -> dict[str, Any]:
        volumes = self.list_volumes()
        if project_slugs:
            wanted = {s.lower() for s in project_slugs}
            selected = [v for v in volumes if v["slug"].lower() in wanted or v["title"].lower() in wanted]
        elif task_prompt.strip():
            selected = [v for v, score in self.rank_volumes(task_prompt, volumes)[:3] if score > 0.05]
        else:
            selected = [v for v in volumes if not v["parent"]][:3]

        parts = [
            "<!-- 个人记忆网络 · 挂载包 -->",
            "以下是我的个人主基座、思维铁律和相关领域专卷。请直接在这些信息的基础上回答，不要推荐专卷里标记为「已证伪」的方案。\n",
            self.read_bootloader().strip(),
        ]
        if include_principles:
            parts.append("\n---\n" + self.read_principles().strip())
        for vol in selected:
            parts.append(f"\n---\n## 专卷：{vol['title']}\n\n{vol['content'].strip()}")

        cards = []
        if include_cards and task_prompt.strip():
            cards = self.search_cards(task_prompt, budget=card_budget, prefer_volumes={v["slug"] for v in selected})
            if cards:
                parts.append("\n---\n## 相关细节卡片")
                for card in cards:
                    parts.append(f"### {card['title']}\n{card['content']}")

        if task_prompt.strip():
            parts.append(f"\n---\n## 当前需要你推进的任务\n\n{task_prompt.strip()}")
        text = "\n".join(parts).strip() + "\n"
        return {
            "text": text,
            "chars": len(text),
            "mounted_projects": [v["title"] for v in selected],
            "mounted_slugs": [v["slug"] for v in selected],
            "cards_used": len(cards),
            "epoch": self._load_state()["epoch"],
        }


def _format_epoch_report_md(epoch: int, ran_at: str, rel_paths: list[str], report: dict[str, Any]) -> str:
    lines = [
        f"# Epoch {epoch:03d} 蒸馏报告",
        "",
        f"- 时间：`{ran_at}`",
        f"- 引擎：`{report.get('engine', 'gemini')}`",
        f"- 消化：{report.get('segments', 0)} 段，分 {report.get('batches', 0)} 批，来自 {len(set(rel_paths))} 个文件",
        f"- 新增或更新细节卡片：{report.get('cards', 0)} 张",
    ]
    if report.get("partial"):
        lines.append("- 状态：**只完成了一部分**，剩下的内容下次继续")
    lines += ["", "## 本轮总结", report.get("epoch_summary") or "完成一轮蒸馏。", ""]
    sections = [
        ("被新信息取代的旧认知", "overwritten"),
        ("新归纳出的偏好或规律", "abstracted"),
        ("丢弃的内容", "pruned"),
        ("改动的专卷", "touched_projects"),
        ("需要注意", "warnings"),
    ]
    for heading, key in sections:
        lines.append(f"## {heading}")
        items = report.get(key) or []
        lines += [f"- {item}" for item in items] or ["- 无"]
        lines.append("")
    lines.append("## 数据来源")
    lines += [f"- `{rel}`" for rel in dict.fromkeys(rel_paths)]
    return "\n".join(lines) + "\n"

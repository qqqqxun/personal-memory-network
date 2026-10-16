from __future__ import annotations

import json
import os
import re
import socket
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from app.config import (
    BATCH_MAX_CHARS,
    BOOTLOADER_BUDGET_CHARS,
    PRINCIPLES_BUDGET_CHARS,
    PROJECT_BUDGET_CHARS,
)
from app.settings import load_settings, save_settings
from app.vault import BUDGET_TOLERANCE, CHILD_SEP, Vault, ensure_heading, slugify_name, strip_directory

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_FALLBACK_MODELS = ("gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-3.6-flash")
RETRY_WAITS = (5, 15, 30)
_MODEL_CACHE: dict[str, list[str]] = {}

DISTILL_SYSTEM_PROMPT = f"""你是用户「个人记忆库」的蒸馏器。用户把自己和各种 AI 的聊天记录、项目文档分批交给你，你要把其中属于用户本人的信息合并进他的知识库。

知识库分四部分：
1. 个人画像 profile_md：用户是谁、长期目标、身体/资源/技术等基线、协作偏好。不超过 {BOOTLOADER_BUDGET_CHARS} 字。不要写专卷目录，目录由系统自动生成。
2. 思维与工程铁律 principles_md：从多次经历中归纳出的跨领域规律和偏好。不超过 {PRINCIPLES_BUDGET_CHARS} 字。只写规律，不写单次事件。
3. 领域专卷 volumes：每个长期领域或项目一卷，例如「健康与运动」「记账小程序」。每卷不超过 {PROJECT_BUDGET_CHARS} 字，结构固定：
   # 标题
   ## 个人基线与目标约束
   ## 当前锁定方案与最新状态
   ## ❌ 已证伪与踩坑墓志铭（勿再推荐）
   ## ❓ 待突破问题与下一步
4. 细节卡片 cards：具体食谱、参数、步骤、命令、某个 bug 的解法等以后可能要查、但不必每次都带上的细节。每张卡片一个主题，不超过 400 字，不要用 Markdown 标题，必须写明所属专卷 volume。

规则：
- 只保留用户本人的信息：他说的事实和数据、他做的决定和偏好、他的实测反馈、双方最终采纳的方案、尚未解决的问题。AI 讲的通用知识和科普一律不要，除非用户采纳它成为自己的方案。
- 合并而不是覆盖：返回某卷时，必须给出该卷合并后的完整内容。原有信息除非被新信息明确取代，否则必须保留。被取代或被放弃的方案写进「❌ 已证伪」并注明原因。
- 同一个指标出现新值时（体重、进度、版本），用新值替换旧值，需要时在括号里保留变化轨迹。
- 按领域归卷，不要按文件建卷。优先归入已有专卷并使用它的 slug；确实是新领域才新建。
- 只返回本批有变化的专卷。画像或铁律没有变化时，对应字段返回 null。
- 专卷写不下时，把细节移到卡片里，专卷只留结论和当前状态。

只返回一个 JSON 对象：
{{
  "epoch_summary": "一句话说明这批内容让知识库发生了什么变化",
  "overwritten": ["被新信息取代的旧认知：从什么变成什么，为什么"],
  "abstracted": ["新归纳出的偏好或规律"],
  "pruned": ["丢弃了哪类内容，例如 AI 的营养学科普"],
  "profile_md": "完整的新个人画像 Markdown，或 null",
  "principles_md": "完整的新铁律 Markdown，或 null",
  "volumes": [{{"slug": "已有专卷的 slug，或新专卷的短名", "title": "专卷标题", "content_md": "该卷合并后的完整 Markdown"}}],
  "cards": [{{"volume": "所属专卷 slug", "title": "卡片标题", "content": "细节内容"}}]
}}
"""

FIT_VOLUME_PROMPT = """下面是用户知识库里的一卷专卷，有 {chars} 字，超过了 {budget} 字的上限。请在不丢失信息的前提下处理：
- 如果把细节移到卡片里就能压缩到上限以内，返回 mode 为 "compress"：content_md 是压缩后的专卷，cards 是移出去的细节。
- 如果这卷其实包含几个相对独立的子主题，返回 mode 为 "split"：content_md 是父卷（只留总体目标、跨子主题的结论和子卷列表，不超过 {budget} 字），children 是子卷（每个不超过 {budget} 字，结构与专卷相同），cards 是移出去的细节。
任何信息都不能直接删掉：要么留在专卷里，要么进子卷，要么进卡片。卡片不要用 Markdown 标题。

只返回 JSON：{{"mode": "compress 或 split", "content_md": "...", "children": [{{"title": "...", "content_md": "..."}}], "cards": [{{"title": "...", "content": "..."}}]}}

专卷标题：{title}
专卷内容：
{content}
"""

FIT_TEXT_PROMPT = """下面是用户知识库里的「{kind}」，有 {chars} 字，超过了 {budget} 字的上限。请改写到 {budget} 字以内：合并重复的内容，用更概括的说法代替具体例子，但不能丢掉任何独立的事实或规则。

只返回 JSON：{{"content_md": "..."}}

{content}
"""

CITATION_NOISE_RE = re.compile(r"(?:\b\d+\s*sources?\b|\u2060[A-Za-z0-9._\-]+)", re.IGNORECASE)
USER_SIGNAL_RE = re.compile(
    r"(我现在|我目前|我决定|我选|我试过|试了|我今天|我大概|我决心|我要|我的|我更喜欢|我倾向|"
    r"\d+\s*(?:cm|kg|斤|岁|分钟|kcal|卡|g|元|万)|失败|被拒|报错|上线)"
)
ACTIONABLE_AI_RE = re.compile(r"(总结：|结论：|结论是|目标：|每日热量|蛋白质目标|示例一天|早餐：|午餐：|晚餐：|最优)")
GENERIC_AI_FLUFF_RE = re.compile(
    r"(加油|身体会感谢你|坚持就是胜利|随时告诉我|我陪你一起走|别担心|有什么具体想调整|保持健康最重要|现在感觉怎么样)"
)

DOMAIN_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("健康与运动", re.compile(r"(减肥|减脂|体重|身高|BMI|腰围|运动|跑步|健身|有氧|力量训练|饮食|睡眠|酸痛|热量)", re.I)),
    ("个人理财", re.compile(r"(理财|资产配置|货币基金|应急金|股票|基金|仓位|收益率|预算|存款)")),
    ("软件项目", re.compile(r"(部署|上线|报错|接口|前端|后端|数据库|小程序|bug|api|python|javascript)", re.I)),
    ("学习与阅读", re.compile(r"(读书|书单|课程|笔记|学习|考试|论文)")),
]


class DistillError(RuntimeError):
    pass


def clean_for_llm(text: str) -> str:
    """只去掉引用尾注、客套话和重复行，不做内容取舍，取舍交给模型。"""
    lines: list[str] = []
    previous = None
    for raw in CITATION_NOISE_RE.sub("", text or "").splitlines():
        line = raw.strip()
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        if len(line) < 60 and GENERIC_AI_FLUFF_RE.search(line) and not USER_SIGNAL_RE.search(line):
            continue
        if line == previous:
            continue
        lines.append(line)
        previous = line
    return "\n".join(lines).strip()


def local_signals(text: str, limit: int = 4000) -> str:
    user_lines, ai_lines = [], []
    for raw in clean_for_llm(text).splitlines():
        line = raw.strip()
        if not line or line.startswith("### "):
            continue
        if line.startswith("我：") or (line.startswith("我") and len(line) <= 120) or USER_SIGNAL_RE.search(line):
            user_lines.append(line)
        elif ACTIONABLE_AI_RE.search(line):
            ai_lines.append(line)
    body = "\n".join(f"- {line}" for line in dict.fromkeys(user_lines))
    if ai_lines:
        body += "\n\nAI 给出的结论：\n" + "\n".join(f"- {line}" for line in dict.fromkeys(ai_lines))
    return body[:limit].strip()


def infer_domain(filename: str, text: str) -> str:
    blob = f"{filename}\n{text[:6000]}"
    best, hits = "", 0
    for title, pattern in DOMAIN_RULES:
        count = len(pattern.findall(blob))
        if count > hits:
            best, hits = title, count
    if hits >= 2:
        return best
    return Path(filename).stem


def rank_gemini_models(names: list[str]) -> list[str]:
    excluded = ("embedding", "image", "tts", "audio", "live", "aqa", "imagen", "veo", "robotics", "computer-use", "gemma")

    def key(name: str) -> tuple:
        low = name.lower()
        match = re.search(r"gemini-(\d+(?:\.\d+)?)", low)
        version = float(match.group(1)) if match else 0.0
        tier = 0 if "flash" in low and "lite" not in low else 1 if "flash" in low else 2
        unstable = 1 if any(tag in low for tag in ("preview", "exp")) else 0
        return (tier, unstable, -version, low)

    usable = [n for n in names if "gemini" in n.lower() and not any(e in n.lower() for e in excluded)]
    return sorted(dict.fromkeys(usable), key=key)


def make_batches(segments: list[dict[str, Any]], max_chars: int | None = None) -> list[list[dict[str, Any]]]:
    max_chars = max_chars or BATCH_MAX_CHARS
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for seg in segments:
        seg.setdefault("clean", clean_for_llm(seg["text"]))
        length = len(seg["clean"])
        if current and size + length > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(seg)
        size += length
    if current:
        batches.append(current)
    return batches


class Distiller:
    """把投喂区的待消化段分批交给模型，合并进主基座、铁律、专卷和细节卡片。"""

    def __init__(
        self,
        vault: Vault | None = None,
        settings: dict[str, Any] | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.vault = vault or Vault()
        self.settings = settings or load_settings()
        self.progress = progress
        self.sleep = time.sleep
        budgets = self.settings.get("budgets") or {}
        self.budget_profile = int(budgets.get("bootloader") or BOOTLOADER_BUDGET_CHARS)
        self.budget_principles = int(budgets.get("principles") or PRINCIPLES_BUDGET_CHARS)
        self.budget_volume = int(budgets.get("project") or PROJECT_BUDGET_CHARS)

    # ── 入口 ────────────────────────────────────────────────────────

    @property
    def llm_cfg(self) -> dict[str, Any]:
        return self.settings.get("llm") or {}

    def _api_key(self) -> str:
        return (self.llm_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY") or "").strip()

    def engine_mode(self) -> str:
        provider = (self.llm_cfg.get("provider") or "gemini").lower()
        if provider == "ollama":
            return "llm"
        if provider in {"gemini", "openai", "openai_compat", "deepseek"} and self._api_key():
            return "llm"
        return "local"

    def _notify(self, message: str, **extra: Any) -> None:
        if self.progress:
            self.progress({"message": message, **extra})

    def run_epoch(
        self,
        *,
        force_all: bool = False,
        selected_rels: list[str] | None = None,
        allow_local_fallback: bool = True,
    ) -> dict[str, Any]:
        self._notify("正在扫描投喂区")
        segments = self.vault.load_pending_segments(force_all=force_all, selected_rels=selected_rels)
        if not segments:
            raise DistillError("投喂区没有待消化的内容。请先放入文件，或勾选「强制重炼全部文件」。")

        if self.engine_mode() == "local":
            if not allow_local_fallback:
                raise DistillError("还没有配置 API Key，请到「投喂与算力配置」页填写。")
            return self._stage_locally(segments)

        batches = make_batches(segments)
        epoch = self.vault.next_epoch()
        report = _empty_report(self._engine_label(), len(batches))
        done = 0
        try:
            for number, batch in enumerate(batches, 1):
                chars = sum(len(seg["clean"]) for seg in batch)
                self._notify(f"正在蒸馏第 {number}/{len(batches)} 批（{len(batch)} 段，{chars} 字）", batch=number, total=len(batches))
                data = self._llm_json(self.build_batch_prompt(batch))
                _merge_report(report, self._apply_batch(data, batch, epoch))
                self.vault.mark_segments_processed([seg["hash"] for seg in batch], epoch)
                report["segments"] += len(batch)
                report["rel_paths"] += [seg["rel_path"] for seg in batch]
                done = number
        except DistillError as exc:
            if not done:
                raise
            report["partial"] = True
            report["warnings"].append(f"第 {done + 1}/{len(batches)} 批失败：{exc}")
            self._finish(epoch, report)
            raise DistillError(
                f"第 {done + 1}/{len(batches)} 批失败：{exc}。前 {done} 批已写入 Epoch {epoch}，剩下的内容下次继续。"
            ) from exc
        entry = self._finish(epoch, report)
        return {"epoch_entry": entry, "brain": self.vault.brain_snapshot()}

    def _finish(self, epoch: int, report: dict[str, Any]) -> dict[str, Any]:
        report["epoch_summary"] = "；".join(report.pop("summaries")) or "完成一轮蒸馏"
        rel_paths = list(dict.fromkeys(report.pop("rel_paths")))
        return self.vault.record_epoch(
            epoch=epoch,
            report=report,
            rel_paths=rel_paths,
            archive_files=bool(self.settings.get("archive_after_distill")),
        )

    def _engine_label(self) -> str:
        provider = (self.llm_cfg.get("provider") or "gemini").lower()
        model = self.llm_cfg.get("model") or ""
        return f"{provider} ({model})" if model else provider

    # ── 提示词 ──────────────────────────────────────────────────────

    def build_batch_prompt(self, batch: list[dict[str, Any]]) -> str:
        for seg in batch:
            seg.setdefault("clean", clean_for_llm(seg["text"]))
        material = "\n\n".join(seg["clean"] for seg in batch)
        volumes = self.vault.list_volumes()
        expanded = [vol for vol, _ in self.vault.rank_volumes(material[:20000], volumes)[:6]]
        expanded_slugs = {vol["slug"] for vol in expanded}
        others = [vol for vol in volumes if vol["slug"] not in expanded_slugs]

        parts = [
            DISTILL_SYSTEM_PROMPT,
            "===== 当前个人画像 =====",
            self.vault.read_profile(),
            "===== 当前思维与工程铁律 =====",
            self.vault.read_principles(),
            "===== 相关专卷（完整内容） =====",
        ]
        if expanded:
            parts += [f"--- 专卷 slug: {vol['slug']} ---\n{vol['content']}" for vol in expanded]
        else:
            parts.append("（还没有专卷）")
        if others:
            parts.append("===== 其他专卷（只列目录；相关细节可以作为卡片挂到它们名下） =====")
            parts += [f"- slug: {vol['slug']}｜《{vol['title']}》{vol['summary']}" for vol in others]
        parts.append(f"===== 本批新材料（{len(batch)} 段） =====")
        parts += [f"【来源：{seg['rel_path']}｜{seg['title']}】\n{seg['clean']}" for seg in batch]
        parts.append("请按规则只输出 JSON。")
        return "\n\n".join(parts)

    # ── 写回知识库 ──────────────────────────────────────────────────

    def _apply_batch(self, data: dict[str, Any], batch: list[dict[str, Any]], epoch: int) -> dict[str, Any]:
        warnings: list[str] = []
        touched: list[str] = []
        source = ", ".join(dict.fromkeys(seg["rel_path"] for seg in batch))

        profile = data.get("profile_md") or data.get("bootloader_md")
        if profile:
            profile = strip_directory(str(profile))
            profile = self._fit_text("个人画像", profile, self.budget_profile, warnings)
            self.vault.write_profile(profile)
        principles = data.get("principles_md")
        if principles:
            self.vault.write_principles(self._fit_text("思维与工程铁律", str(principles), self.budget_principles, warnings))

        known = {vol["slug"]: vol for vol in self.vault.list_volumes()}
        by_title = {vol["title"]: vol["slug"] for vol in known.values()}

        def resolve(hint: str, title: str) -> str:
            hint = (hint or "").strip()
            if hint in known:
                return hint
            slug = slugify_name(hint) if hint else ""
            if slug in known:
                return slug
            if title in by_title:
                return by_title[title]
            return slug or slugify_name(title)

        for vol in data.get("volumes") or data.get("projects") or []:
            if not isinstance(vol, dict):
                continue
            title = str(vol.get("title") or vol.get("slug") or "").strip()
            content = str(vol.get("content_md") or vol.get("content") or "").strip()
            if not title or not content:
                continue
            slug = resolve(str(vol.get("slug") or ""), title)
            touched += self._write_volume(slug, title, ensure_heading(content, title), warnings, source, epoch)
            known.setdefault(slug, {"slug": slug, "title": title})
            by_title.setdefault(title, slug)

        cards = []
        for card in data.get("cards") or []:
            if isinstance(card, dict):
                volume = str(card.get("volume") or "")
                card["volume"] = resolve(volume, volume) if volume else (touched and resolve("", touched[0])) or "未归类"
                cards.append(card)
        written = self.vault.add_cards(cards, epoch=epoch, origin="gemini", source=source) if cards else 0

        staged_ids = {f"L{seg['hash'][:12]}" for seg in batch}
        self.vault.delete_cards(lambda card: card["origin"] == "local" and card["id"] in staged_ids)
        self.vault.refresh_directory()
        return {
            "summary": str(data.get("epoch_summary") or "").strip(),
            "overwritten": list(data.get("overwritten") or []),
            "abstracted": list(data.get("abstracted") or []),
            "pruned": list(data.get("pruned") or []),
            "touched_projects": touched,
            "warnings": warnings,
            "cards": written,
        }

    def _write_volume(
        self,
        slug: str,
        title: str,
        content: str,
        warnings: list[str],
        source: str,
        epoch: int,
    ) -> list[str]:
        budget = self.budget_volume
        if len(content) <= budget * BUDGET_TOLERANCE:
            self.vault.write_project(slug, content, refresh=False)
            return [title]

        self._notify(f"《{title}》有 {len(content)} 字，超过 {budget} 字上限，正在压缩或拆成子卷")
        try:
            fit = self._llm_json(FIT_VOLUME_PROMPT.format(chars=len(content), budget=budget, title=title, content=content))
        except DistillError as exc:
            self.vault.write_project(slug, content, refresh=False)
            warnings.append(f"《{title}》有 {len(content)} 字，超过上限，压缩没成功（{exc}），已完整保留，没有截断。")
            return [title]

        moved = [dict(card, volume=slug) for card in fit.get("cards") or [] if isinstance(card, dict)]
        if moved:
            self.vault.add_cards(moved, epoch=epoch, origin="gemini", source=source)

        touched = [title]
        children = [
            child
            for child in fit.get("children") or []
            if isinstance(child, dict) and child.get("title") and child.get("content_md")
        ]
        if fit.get("mode") == "split" and children:
            for child in children:
                child_title = str(child["title"]).strip()
                child_content = ensure_heading(str(child["content_md"]), child_title)
                self.vault.write_project(f"{slug}{CHILD_SEP}{slugify_name(child_title)}", child_content, refresh=False)
                touched.append(child_title)
                if len(child_content) > budget * BUDGET_TOLERANCE:
                    warnings.append(f"子卷《{child_title}》拆分后仍有 {len(child_content)} 字，已完整保留。")

        parent = ensure_heading(str(fit.get("content_md") or "").strip() or content, title)
        self.vault.write_project(slug, parent, refresh=False)
        if len(parent) > budget * BUDGET_TOLERANCE:
            warnings.append(f"《{title}》处理后仍有 {len(parent)} 字，已完整保留，没有截断。")
        return touched

    def _fit_text(self, kind: str, content: str, budget: int, warnings: list[str]) -> str:
        if len(content) <= budget * BUDGET_TOLERANCE:
            return content
        self._notify(f"「{kind}」有 {len(content)} 字，超过 {budget} 字上限，正在压缩")
        try:
            fit = self._llm_json(FIT_TEXT_PROMPT.format(kind=kind, chars=len(content), budget=budget, content=content))
        except DistillError as exc:
            warnings.append(f"「{kind}」有 {len(content)} 字，超过上限，压缩没成功（{exc}），已完整保留，没有截断。")
            return content
        compressed = str(fit.get("content_md") or "").strip()
        if not compressed:
            return content
        if len(compressed) > budget * BUDGET_TOLERANCE:
            warnings.append(f"「{kind}」压缩后仍有 {len(compressed)} 字，已完整保留。")
        return compressed

    # ── 没有 API Key 时：只做预脱水卡片，不改专卷 ──────────────────

    def _stage_locally(self, segments: list[dict[str, Any]]) -> dict[str, Any]:
        epoch = int(self.vault.next_epoch()) - 1
        cards = []
        for seg in segments:
            content = local_signals(seg["text"])
            if not content:
                continue
            cards.append(
                {
                    "id": f"L{seg['hash'][:12]}",
                    "volume": infer_domain(seg["name"], seg["text"]),
                    "title": f"待蒸馏：{seg['title']}"[:80],
                    "content": content,
                    "source": seg["rel_path"],
                    "origin": "local",
                }
            )
        written = self.vault.add_cards(cards, epoch=epoch, origin="local")
        self.vault.refresh_directory()
        return {
            "mode": "local_staged",
            "staged": written,
            "segments": len(segments),
            "message": (
                f"没有可用的 API Key，只在本地给 {len(segments)} 段内容做了预脱水卡片，专卷没有改动。"
                "这些内容仍标记为待蒸馏，配置好 Gemini 后再跑一次就会正式写入专卷。"
            ),
            "brain": self.vault.brain_snapshot(),
        }

    # ── 网页算力桥接 ────────────────────────────────────────────────

    def build_manual_prompt(self, force_all: bool = False) -> dict[str, Any]:
        segments = self.vault.load_pending_segments(force_all=force_all)
        if not segments:
            raise DistillError("投喂区没有待消化的内容。")
        batches = make_batches(segments)
        first = batches[0]
        self.vault.set_manual_batch([seg["hash"] for seg in first])
        prompt = self.build_batch_prompt(first)
        return {"prompt": prompt, "chars": len(prompt), "batch": 1, "total_batches": len(batches), "segments": len(first)}

    def apply_manual_response(self, raw_json_text: str) -> dict[str, Any]:
        data = _parse_json_object(raw_json_text)
        if not data:
            raise DistillError("没能从粘贴的内容里解析出 JSON，请确认复制了完整的 JSON。")
        hashes = set(self.vault.get_manual_batch())
        batch = [seg for seg in self.vault.load_pending_segments(force_all=True) if seg["hash"] in hashes]
        if not batch:
            raise DistillError("找不到这份指令包对应的内容，请重新生成指令包。")
        epoch = self.vault.next_epoch()
        report = _empty_report("manual_web_llm", 1)
        _merge_report(report, self._apply_batch(data, batch, epoch))
        self.vault.mark_segments_processed([seg["hash"] for seg in batch], epoch)
        report["segments"] = len(batch)
        report["rel_paths"] = [seg["rel_path"] for seg in batch]
        self.vault.set_manual_batch([])
        entry = self._finish(epoch, report)
        return {"epoch_entry": entry, "brain": self.vault.brain_snapshot()}

    # ── 模型调用 ────────────────────────────────────────────────────

    def _llm_json(self, prompt: str) -> dict[str, Any]:
        provider = (self.llm_cfg.get("provider") or "gemini").lower()
        if provider == "gemini":
            return self._gemini_json(prompt)
        if provider in {"openai", "openai_compat", "deepseek"}:
            return self._openai_json(prompt)
        if provider == "ollama":
            return self._ollama_json(prompt)
        raise DistillError(f"未知的算力提供方：{provider}")

    def _gemini_base(self) -> str:
        base = (self.llm_cfg.get("base_url") or "").strip()
        if not base or "11434" in base:
            base = GEMINI_BASE
        return base.rstrip("/")

    def _gemini_json(self, prompt: str) -> dict[str, Any]:
        key = self._api_key()
        if not key:
            raise DistillError("还没有配置 Gemini API Key")
        base = self._gemini_base()
        preferred = (self.llm_cfg.get("model") or GEMINI_FALLBACK_MODELS[0]).strip()
        models = [preferred] + [m for m in GEMINI_FALLBACK_MODELS if m != preferred]
        discovered = False
        preferred_missing = False
        last_error = "Gemini 调用失败"
        position = 0
        while position < len(models):
            model = models[position]
            position += 1
            attempt = 0
            network_failures = 0
            bad_json = 0
            while True:
                try:
                    response = self._post(
                        f"{base}/models/{model}:generateContent",
                        params={"key": key},
                        json={
                            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
                        },
                    )
                except httpx.HTTPError as exc:
                    network_failures += 1
                    if network_failures <= 2:
                        self._notify(f"连接 Gemini 失败，3 秒后重试（{exc.__class__.__name__}）")
                        self.sleep(3)
                        continue
                    proxy = self._resolve_proxy() or "未使用代理"
                    raise DistillError(f"连不上 Gemini：{exc}（{proxy}）") from exc

                status = response.status_code
                if status == 200:
                    parsed = _parse_json_object(_extract_gemini_text(response.json()))
                    if parsed is not None:
                        if preferred_missing:
                            self._remember_model(model)
                        return parsed
                    bad_json += 1
                    if bad_json <= 1:
                        continue
                    last_error = f"{model} 返回的内容不是有效的 JSON"
                    break
                detail = _error_detail(response)
                if status == 404:
                    last_error = f"模型 {model} 不可用：{detail}"
                    if model == preferred:
                        preferred_missing = True
                    if not discovered:
                        discovered = True
                        models += [m for m in self._discover_models(base, key) if m not in models]
                    break
                if status in {429, 500, 502, 503, 504}:
                    last_error = f"Gemini 暂时不可用（{status}）：{detail}"
                    if attempt < len(RETRY_WAITS):
                        wait = RETRY_WAITS[attempt]
                        attempt += 1
                        self._notify(f"{model} 繁忙（{status}），{wait} 秒后重试（第 {attempt} 次）")
                        self.sleep(wait)
                        continue
                    break
                raise DistillError(f"Gemini 拒绝了请求（{status}）：{detail}")
        raise DistillError(last_error)

    def _discover_models(self, base: str, key: str) -> list[str]:
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]
        self._notify("正在查询这个 Key 可用的 Gemini 模型")
        try:
            with self._client() as client:
                response = client.get(f"{base}/models", params={"key": key, "pageSize": 200})
            models = response.json().get("models", []) if response.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            return []
        names = [
            str(m.get("name", "")).split("/", 1)[-1]
            for m in models
            if "generateContent" in (m.get("supportedGenerationMethods") or [])
        ]
        ranked = rank_gemini_models(names)
        _MODEL_CACHE[key] = ranked
        return ranked

    def _remember_model(self, model: str) -> None:
        current = load_settings()
        current.setdefault("llm", {})["model"] = model
        save_settings(current)
        self.settings.setdefault("llm", {})["model"] = model

    def _openai_json(self, prompt: str) -> dict[str, Any]:
        base = (self.llm_cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        model = self.llm_cfg.get("model") or "gpt-4o-mini"
        for attempt in range(len(RETRY_WAITS) + 1):
            try:
                response = self._post(
                    f"{base}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key()}"},
                    json={
                        "model": model,
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
            except httpx.HTTPError as exc:
                raise DistillError(f"连不上 {base}：{exc}") from exc
            if response.status_code in {429, 500, 502, 503, 504} and attempt < len(RETRY_WAITS):
                self._notify(f"接口繁忙（{response.status_code}），{RETRY_WAITS[attempt]} 秒后重试")
                self.sleep(RETRY_WAITS[attempt])
                continue
            if response.status_code >= 400:
                raise DistillError(f"接口返回 {response.status_code}：{_error_detail(response)}")
            content = response.json()["choices"][0]["message"]["content"]
            parsed = _parse_json_object(content)
            if parsed is None:
                raise DistillError("模型返回的内容不是有效的 JSON")
            return parsed
        raise DistillError("接口持续繁忙")

    def _ollama_json(self, prompt: str) -> dict[str, Any]:
        base = (self.llm_cfg.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
        model = self.llm_cfg.get("model") or "qwen2.5"
        try:
            with httpx.Client(timeout=httpx.Timeout(600.0, connect=5.0), trust_env=False) as client:
                response = client.post(
                    f"{base}/api/generate",
                    json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DistillError(f"Ollama 调用失败：{exc}") from exc
        parsed = _parse_json_object(response.json().get("response", ""))
        if parsed is None:
            raise DistillError("Ollama 返回的内容不是有效的 JSON")
        return parsed

    # ── 网络 ────────────────────────────────────────────────────────

    def _client(self) -> httpx.Client:
        return httpx.Client(proxy=self._resolve_proxy(), timeout=httpx.Timeout(300.0, connect=10.0))

    def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        with self._client() as client:
            return client.post(url, **kwargs)

    def _resolve_proxy(self) -> str | None:
        configured = (self.llm_cfg.get("proxy") or "").strip()
        if configured.lower() in {"none", "direct", "off"}:
            return None
        if configured:
            return configured if "://" in configured else f"http://{configured}"
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy"):
            value = (os.environ.get(name) or "").strip()
            if value:
                return value
        ports = [7897, 7890, 10808, 10809, 1080, 2080]
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            ) as reg:
                server, _ = winreg.QueryValueEx(reg, "ProxyServer")
            port = str(server).split(";")[-1].rsplit(":", 1)[-1]
            if port.isdigit() and int(port) not in ports:
                ports.insert(0, int(port))
        except (OSError, ImportError):
            pass
        for port in ports:
            if _is_local_port_open(port):
                return f"http://127.0.0.1:{port}"
        return None


def _empty_report(engine: str, batches: int) -> dict[str, Any]:
    return {
        "engine": engine,
        "batches": batches,
        "segments": 0,
        "cards": 0,
        "partial": False,
        "summaries": [],
        "overwritten": [],
        "abstracted": [],
        "pruned": [],
        "touched_projects": [],
        "warnings": [],
        "rel_paths": [],
    }


def _merge_report(report: dict[str, Any], change: dict[str, Any]) -> None:
    if change.get("summary"):
        report["summaries"].append(change["summary"])
    for key in ("overwritten", "abstracted", "pruned", "warnings"):
        report[key] += [str(item) for item in change.get(key) or []]
    report["touched_projects"] = list(dict.fromkeys(report["touched_projects"] + change.get("touched_projects", [])))
    report["cards"] += int(change.get("cards") or 0)


def _is_local_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _error_detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", {}).get("message") or response.text[:300])
    except ValueError:
        return response.text[:300]


def _extract_gemini_text(body: dict[str, Any]) -> str:
    candidates = body.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict) and not part.get("thought")).strip()


def _parse_json_object(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]+\}", cleaned)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

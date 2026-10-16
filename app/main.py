from __future__ import annotations

import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import HOST, PORT
from app.crypto import load_or_create_auth_token
from app.db import Store
from app.demo import seed_demo
from app.digest import Digester
from app.distiller import Distiller, DistillError
from app.egress import EgressGate
from app.explore import Explorer
from app.export_md import export_markdown
from app.ids import now_iso
from app.importers import filter_recent, parse_export
from app.jobs import JobRunner
from app.privacy import classify_text, detect_input_warnings
from app.ratchet import Ratchet, RatchetError
from app.retrieve import Assembler
from app.review import ReviewBoard, ReviewError
from app.settings import load_settings, save_settings
from app.vault import Vault

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = Store()
    vault = Vault()
    app.state.store = store
    app.state.vault = vault
    app.state.token = load_or_create_auth_token()
    print(f"个人记忆网络（蒸馏与挂载引擎）已启动 http://{HOST}:{PORT}")
    print(f"独立记忆库目录：{vault.root}")
    yield
    store.close()


app = FastAPI(title="个人记忆网络", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def store_dep(request: Request) -> Store:
    return request.app.state.store


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    return origin.startswith(
        (
            "http://127.0.0.1",
            "http://localhost",
            "chrome-extension://",
            "moz-extension://",
            "extension://",
        )
    )


def auth(request: Request) -> None:
    token = request.app.state.token
    header = request.headers.get("authorization") or ""
    provided = request.headers.get("x-memory-token") or request.cookies.get("memory_token") or ""
    if header.lower().startswith("bearer "):
        provided = header[7:].strip()
    origin = request.headers.get("origin")
    if not _origin_allowed(origin):
        raise HTTPException(403, "拒绝跨源请求")
    if provided != token:
        raise HTTPException(401, "未授权")


@app.middleware("http")
async def bind_localhost(request: Request, call_next):
    host = (request.client.host if request.client else "") or ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return JSONResponse({"detail": "只接受本机访问"}, status_code=403)
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> FileResponse:
    response = FileResponse(STATIC_DIR / "index.html")
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        "memory_token",
        request.app.state.token,
        httponly=False,
        samesite="strict",
        path="/",
    )
    return response


@app.get("/api/health")
def health(request: Request) -> dict[str, Any]:
    return {"ok": True, "host": HOST, "port": PORT}


@app.get("/api/session")
def session(request: Request, _: None = Depends(auth)) -> dict[str, Any]:
    return {"token": request.app.state.token, "port": PORT, "host": HOST}


@app.get("/api/stats")
def stats(_: None = Depends(auth), store: Store = Depends(store_dep)) -> dict[str, Any]:
    data = store.stats()
    data["recent_sources"] = store.list("sources", limit=8)
    data["pending"] = ReviewBoard(store).pending()[:8]
    return data


@app.get("/api/settings")
def get_settings(_: None = Depends(auth)) -> dict[str, Any]:
    data = load_settings()
    if data.get("llm", {}).get("api_key"):
        data = dict(data)
        data["llm"] = dict(data["llm"])
        data["llm"]["api_key"] = "***"
    return data


@app.put("/api/settings")
def put_settings(payload: dict[str, Any], _: None = Depends(auth)) -> dict[str, Any]:
    current = load_settings()
    if payload.get("llm", {}).get("api_key") == "***":
        payload.setdefault("llm", {})["api_key"] = current.get("llm", {}).get("api_key", "")
    current.update(payload)
    if "llm" in payload:
        current["llm"] = {**current.get("llm", {}), **payload["llm"]}
    return save_settings(current)


@app.get("/api/threads")
def list_threads(status: str | None = None, _: None = Depends(auth), store: Store = Depends(store_dep)) -> list[dict]:
    where = "record_status!='archived'"
    params: tuple = ()
    if status:
        where += " AND status=?"
        params = (status,)
    return store.list("threads", where, params)


@app.post("/api/threads")
def create_thread(payload: dict[str, Any], _: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    payload.setdefault("status", "active")
    payload.setdefault("sensitivity", "normal")
    payload.setdefault("reviewed", 1)
    payload.setdefault("confidence", 0.8)
    return store.upsert("threads", payload)


@app.get("/api/threads/{thread_id}")
def thread_bundle(thread_id: str, _: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    thread = store.get("threads", thread_id)
    if not thread:
        raise HTTPException(404, "问题线不存在")
    current = store.get("solutions", thread["current_solution_id"]) if thread.get("current_solution_id") else None
    return {
        "thread": thread,
        "current_solution": current,
        "attempts": store.list("attempts", "thread_id=?", (thread_id,)),
        "candidates": store.list("candidates", "thread_id=? AND record_status='active'", (thread_id,)),
        "solutions": store.list("solutions", "thread_id=?", (thread_id,), order="created_at ASC"),
        "open_questions": store.list("open_questions", "thread_id=?", (thread_id,)),
        "relations": store.list("relations", "from_id=? OR to_id=?", (thread_id, thread_id)),
    }


@app.put("/api/threads/{thread_id}")
def update_thread(thread_id: str, payload: dict[str, Any], _: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    thread = store.get("threads", thread_id)
    if not thread:
        raise HTTPException(404, "问题线不存在")
    if "sensitivity" in payload and payload["sensitivity"] != thread.get("sensitivity"):
        from app.privacy import LEVEL_RANK

        if LEVEL_RANK[payload["sensitivity"]] < LEVEL_RANK[thread.get("sensitivity") or "normal"]:
            board = ReviewBoard(store)
            item = store.upsert(
                "review_items",
                {
                    "kind": "lower_sensitivity",
                    "impact": "must",
                    "thread_id": thread_id,
                    "payload_json": {"table": "threads", "id": thread_id, "sensitivity": payload["sensitivity"]},
                    "status": "pending",
                },
            )
            return {"queued_review": item}
    thread.update(payload)
    return store.upsert("threads", thread)


@app.post("/api/threads/{thread_id}/attempts")
def add_attempt(thread_id: str, payload: dict[str, Any], _: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    if not store.get("threads", thread_id):
        raise HTTPException(404, "问题线不存在")
    payload["thread_id"] = thread_id
    payload.setdefault("reviewed", 1)
    return store.upsert("attempts", payload)


@app.post("/api/threads/{thread_id}/candidates")
def add_candidate(thread_id: str, payload: dict[str, Any], _: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    payload["thread_id"] = thread_id
    payload.setdefault("origin", "manual")
    return store.upsert("candidates", payload)


@app.post("/api/threads/{thread_id}/questions")
def add_question(thread_id: str, payload: dict[str, Any], _: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    payload["thread_id"] = thread_id
    payload.setdefault("status", "open")
    return store.upsert("open_questions", payload)


@app.post("/api/threads/{thread_id}/promote")
def promote(thread_id: str, payload: dict[str, Any], _: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    try:
        return Ratchet(store).promote(thread_id, plan=payload.get("plan") or "", evidence=payload.get("evidence") or "")
    except RatchetError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/review")
def review_queue(impact: str | None = None, _: None = Depends(auth), store: Store = Depends(store_dep)) -> list[dict]:
    return ReviewBoard(store).pending(impact)


@app.post("/api/review/{item_id}")
def review_decide(item_id: str, payload: dict[str, Any], _: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    try:
        return ReviewBoard(store).decide(
            item_id,
            payload.get("action") or "accept",
            payload.get("note") or "",
            payload.get("payload"),
        )
    except ReviewError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/ingest")
def ingest(payload: dict[str, Any], _: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    digester = Digester(store)
    return digester.ingest_conversation(
        platform=payload.get("platform") or "generic",
        conversation_id=payload.get("conversation_id") or "",
        title=payload.get("title") or "未命名对话",
        turns=payload.get("turns") or [],
        occurred_at=payload.get("occurred_at"),
        skip_record=bool(payload.get("skip_record")),
    )


@app.post("/api/import")
async def import_file(
    file: UploadFile = File(...),
    platform: str = Form(""),
    months: int = Form(6),
    _: None = Depends(auth),
    store: Store = Depends(store_dep),
) -> dict:
    raw = await file.read()
    try:
        items = filter_recent(parse_export(raw, file.filename or "", platform), months=months)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    digester = Digester(store)
    results = []
    skipped = 0
    for item in items:
        result = digester.ingest_conversation(
            platform=item["platform"],
            conversation_id=item["conversation_id"],
            title=item["title"],
            turns=item["turns"],
            occurred_at=item.get("occurred_at"),
        )
        if result.get("skipped"):
            skipped += 1
        results.append(result)
    job = store.upsert(
        "ingest_jobs",
        {"kind": "import", "status": "done", "stats_json": {"total": len(items), "skipped": skipped}},
    )
    return {"job": job, "total": len(items), "skipped": skipped, "results": results[:50], "stats": store.stats()}


@app.post("/api/assemble")
def assemble(payload: dict[str, Any], _: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    return Assembler(store).assemble(
        payload.get("query") or "",
        payload.get("destination") or "local",
        payload.get("max_chars"),
    )


@app.post("/api/privacy/classify")
def privacy_classify(payload: dict[str, Any], _: None = Depends(auth)) -> dict:
    text = payload.get("text") or ""
    result = classify_text(text)
    return {
        "level": result.level,
        "redacted": result.redacted,
        "hits": result.hits,
        "topics": result.topics,
        "warnings": detect_input_warnings(text),
    }


@app.post("/api/egress/preview")
def egress_preview(payload: dict[str, Any], _: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    return EgressGate(store).process(payload.get("text") or "", payload.get("destination") or "chatgpt")


@app.get("/api/egress")
def egress_log(_: None = Depends(auth), store: Store = Depends(store_dep)) -> list[dict]:
    return store.list("egress_log", limit=100)


@app.get("/api/profile")
def profile(_: None = Depends(auth), store: Store = Depends(store_dep)) -> list[dict]:
    return store.list("profile", "record_status='active'")


@app.post("/api/profile")
def save_profile(payload: dict[str, Any], _: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    payload.setdefault("category", "preference")
    payload.setdefault("reviewed", 1)
    return store.upsert("profile", payload)


@app.get("/api/entities")
def entities(_: None = Depends(auth), store: Store = Depends(store_dep)) -> list[dict]:
    return store.list("entities", "record_status='active'")


@app.get("/api/facts")
def facts(_: None = Depends(auth), store: Store = Depends(store_dep)) -> list[dict]:
    return store.list("facts", "record_status!='archived'", limit=200)


@app.post("/api/explore")
def explore(payload: dict[str, Any] | None = None, _: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    payload = payload or {}
    return Explorer(store).run(payload.get("thread_id"))


@app.get("/api/explore/brief")
def explore_brief(_: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    return Explorer(store).weekly_brief()


@app.post("/api/jobs/{name}")
def run_job(name: str, _: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    runner = JobRunner(store)
    mapping = {
        "reflect": runner.reflect,
        "consolidate": runner.consolidate,
        "decay": runner.decay,
        "consistency": runner.consistency,
    }
    if name not in mapping:
        raise HTTPException(404, "未知任务")
    return mapping[name]()


@app.post("/api/export/markdown")
def export_md(_: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    path = export_markdown(store)
    return {"path": str(path)}


@app.post("/api/demo/seed")
def demo_seed(_: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    return seed_demo(store)


@app.delete("/api/items/{table}/{item_id}")
def delete_item(table: str, item_id: str, _: None = Depends(auth), store: Store = Depends(store_dep)) -> dict:
    allowed = {
        "threads",
        "attempts",
        "candidates",
        "open_questions",
        "facts",
        "profile",
        "insights",
        "entities",
        "sources",
    }
    if table not in allowed:
        raise HTTPException(400, "该表不允许删除")
    row = store.get(table, item_id)
    if not row:
        raise HTTPException(404, "记录不存在")
    row["record_status"] = "archived"
    store.upsert(table, row)
    return {"ok": True}


# ── 个人数据集蒸馏与挂载引擎 API (Vault & Distiller) ──────────────────


def current_vault() -> Vault:
    return Vault()


@app.get("/api/vault/status")
def vault_status(_: None = Depends(auth)) -> dict[str, Any]:
    vault = current_vault()
    snap = vault.brain_snapshot()
    inbox = vault.scan_inbox(include_processed=True)
    cfg = load_settings()
    has_key = bool((cfg.get("llm", {}).get("api_key") or "").strip())
    effective_proxy = Distiller(vault=vault, settings=cfg)._resolve_proxy()
    return {
        "brain": snap,
        "inbox": inbox,
        "llm": {
            "provider": cfg.get("llm", {}).get("provider", "gemini"),
            "model": cfg.get("llm", {}).get("model", "gemini-3.8-flash"),
            "has_api_key": has_key,
            "proxy": effective_proxy or "",
        },
        "archive_after_distill": bool(cfg.get("archive_after_distill")),
    }


@app.post("/api/vault/inbox/upload")
async def vault_inbox_upload(
    file: UploadFile = File(...),
    _: None = Depends(auth),
) -> dict[str, Any]:
    vault = current_vault()
    safe_name = Path(file.filename or "upload.txt").name
    dest = vault.inbox_dir / safe_name
    raw = await file.read()
    dest.write_bytes(raw)
    return {"saved": str(dest), "inbox": vault.scan_inbox(include_processed=True)}


@app.post("/api/vault/inbox/seed-sample")
def vault_seed_sample(_: None = Depends(auth)) -> dict[str, Any]:
    vault = current_vault()
    sample_file = vault.inbox_dir / "示例_记账小程序与工程复盘.md"
    sample_content = """# 记账小程序与个人开发复盘记录

## 对话与尝试记录 1：部署方案踩坑
- 我之前试过直接用微信云托管部署后端，结果失败放弃了。原因是备案周期太长、权限审批繁琐，而且冷启动有明显延迟。
- 后来改用轻量服务器 + 单文件 SQLite + FastAPI 直接跑，部署速度快了十倍，结论是：个人项目早期绝不碰重型云原生组件。

## 对话与尝试记录 2：小程序审核被拒与突破
- 第一次提交审核时，只改了微信一键登录，还是被拒，理由是涉及收集账单数据但缺少隐私授权与数据删除说明。
- 最优解确认：先在设置页补齐「隐私说明」与「一键清除本地/服务端数据入口」，并用体验版给种子用户灰度验证，一次过审。

## 对话与尝试记录 3：个人 AI 记忆网络架构反思
- 一开始按重型数据中台设计了 15 张表、四级隐私闸门和逐条人工审批队列，做出来发现极其反人类，全是维护负担，没有真正的智能抽象。
- 核心认知升级（OVERWRITE）：我真正需要的是「冯·诺依曼式个人大脑」——本地独立文件夹作为存储器，各大 AI 模型作为无状态运算器，通过周期性 Epoch 蒸馏把原始记录越炼越薄、越炼越准。

## 待探索问题
- 如何让小程序在没有推广预算的情况下通过小红书/搜索自然获客？
"""
    sample_file.write_text(sample_content, encoding="utf-8")
    return {"seeded": str(sample_file), "inbox": vault.scan_inbox(include_processed=True)}


_distill_lock = threading.Lock()
_distill_job: dict[str, Any] = {"running": False, "progress": {}, "result": None, "error": None}


def _job_snapshot() -> dict[str, Any]:
    with _distill_lock:
        return json.loads(json.dumps(_distill_job, ensure_ascii=False, default=str))


def _summarize_distill(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("mode") == "local_staged":
        return {"mode": "local_staged", "message": result["message"], "staged": result["staged"]}
    return {"mode": "epoch", "epoch_entry": result["epoch_entry"]}


def _run_distill_job(force_all: bool, selected_rels: list[str] | None) -> None:
    def progress(info: dict[str, Any]) -> None:
        with _distill_lock:
            _distill_job["progress"] = {**_distill_job.get("progress", {}), **info, "updated_at": now_iso()}

    try:
        result = Distiller(vault=current_vault(), progress=progress).run_epoch(
            force_all=force_all,
            selected_rels=selected_rels,
        )
        outcome = {"result": _summarize_distill(result), "error": None}
    except DistillError as exc:
        outcome = {"result": None, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — 后台线程必须把意外错误报告给界面
        outcome = {"result": None, "error": f"意外错误：{exc.__class__.__name__}: {exc}"}
    with _distill_lock:
        _distill_job.update(outcome, running=False, finished_at=now_iso())


@app.post("/api/vault/distill")
def vault_distill(payload: dict[str, Any] | None = None, _: None = Depends(auth)) -> dict[str, Any]:
    payload = payload or {}
    force_all = bool(payload.get("force_all"))
    selected_rels = payload.get("selected_rels")
    if payload.get("wait"):
        try:
            result = Distiller(vault=current_vault()).run_epoch(
                force_all=force_all,
                selected_rels=selected_rels,
                allow_local_fallback=bool(payload.get("allow_local_fallback", True)),
            )
        except DistillError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _summarize_distill(result)
    with _distill_lock:
        if _distill_job["running"]:
            raise HTTPException(409, "已经有一轮蒸馏在进行中")
        _distill_job.update(
            running=True,
            started_at=now_iso(),
            finished_at=None,
            progress={"message": "准备开始"},
            result=None,
            error=None,
        )
    threading.Thread(target=_run_distill_job, args=(force_all, selected_rels), daemon=True).start()
    return _job_snapshot()


@app.get("/api/vault/distill/job")
def vault_distill_job(_: None = Depends(auth)) -> dict[str, Any]:
    return _job_snapshot()


@app.post("/api/vault/distill/prompt")
def vault_distill_prompt(payload: dict[str, Any] | None = None, _: None = Depends(auth)) -> dict[str, Any]:
    payload = payload or {}
    try:
        return Distiller(vault=current_vault()).build_manual_prompt(force_all=bool(payload.get("force_all")))
    except DistillError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/vault/distill/apply-manual")
def vault_distill_apply_manual(payload: dict[str, Any], _: None = Depends(auth)) -> dict[str, Any]:
    try:
        result = Distiller(vault=current_vault()).apply_manual_response(payload.get("raw_json") or "")
    except DistillError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _summarize_distill(result)


@app.get("/api/vault/cards")
def vault_cards(volume: str | None = None, _: None = Depends(auth)) -> list[dict[str, Any]]:
    return current_vault().list_cards(volume)


@app.delete("/api/vault/cards/{card_id}")
def vault_delete_card(card_id: str, _: None = Depends(auth)) -> dict[str, Any]:
    if not current_vault().delete_card(card_id):
        raise HTTPException(404, "卡片不存在")
    return {"ok": True}


@app.put("/api/vault/brain/core")
def vault_update_core(payload: dict[str, Any], _: None = Depends(auth)) -> dict[str, Any]:
    vault = current_vault()
    if "bootloader" in payload:
        vault.write_bootloader(str(payload["bootloader"]))
    if "principles" in payload:
        vault.write_principles(str(payload["principles"]))
    return vault.brain_snapshot()


@app.put("/api/vault/brain/projects/{slug}")
def vault_update_project(slug: str, payload: dict[str, Any], _: None = Depends(auth)) -> dict[str, Any]:
    vault = current_vault()
    content = str(payload.get("content") or "")
    if not content.strip():
        raise HTTPException(400, "内容不能为空")
    return vault.write_project(slug, content)


@app.delete("/api/vault/brain/projects/{slug}")
def vault_delete_project(slug: str, _: None = Depends(auth)) -> dict[str, Any]:
    vault = current_vault()
    vault.delete_project(slug)
    return {"ok": True}


@app.post("/api/vault/mount")
def vault_mount(payload: dict[str, Any] | None = None, _: None = Depends(auth)) -> dict[str, Any]:
    payload = payload or {}
    vault = current_vault()
    return vault.build_mount_pack(
        project_slugs=payload.get("project_slugs"),
        task_prompt=str(payload.get("task_prompt") or ""),
        include_principles=bool(payload.get("include_principles", True)),
        include_cards=bool(payload.get("include_cards", True)),
    )


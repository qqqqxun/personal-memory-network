from __future__ import annotations

from app.db import Store
from app.digest import Digester
from app.ids import now_iso
from app.ratchet import Ratchet


def seed_demo(store: Store) -> dict:
    if store.count("threads"):
        return {"seeded": False, "reason": "already_has_data"}

    digester = Digester(store)
    ratchet = Ratchet(store)

    conversations = [
        {
            "platform": "chatgpt",
            "conversation_id": "demo-visa-1",
            "title": "记账小程序怎么上线",
            "turns": [
                {
                    "role": "user",
                    "text": "我在做记账小程序。试过直接用微信云托管部署，失败了，原因是备案和权限审批太慢。怎么更快上线？",
                },
                {
                    "role": "assistant",
                    "text": "可以先用体验版给核心用户试用。结论是：当前最优路径是先过隐私合规清单，再提交审核。",
                },
            ],
        },
        {
            "platform": "claude",
            "conversation_id": "demo-visa-2",
            "title": "记账小程序审核被拒",
            "turns": [
                {
                    "role": "user",
                    "text": "试过把登录改成仅微信一键登录，审核还是失败。可以试试把隐私说明写进用户协议吗？",
                },
                {
                    "role": "assistant",
                    "text": "方案是补齐隐私说明，并在设置页提供数据删除入口。这通常比继续改登录方式更有效。",
                },
            ],
        },
        {
            "platform": "chatgpt",
            "conversation_id": "demo-sleep",
            "title": "改善睡眠质量",
            "turns": [
                {
                    "role": "user",
                    "text": "我更喜欢先改作息而不是立刻吃药。试过睡前刷手机，失败了，睡眠更差。如何固定入睡时间？",
                },
                {
                    "role": "assistant",
                    "text": "目前最好的做法是固定 23:30 熄屏，并在 23:00 后不再处理工作消息。",
                },
            ],
        },
        {
            "platform": "claude",
            "conversation_id": "demo-money",
            "title": "个人理财配置",
            "turns": [
                {
                    "role": "user",
                    "text": "我在做资产配置。试过把应急资金放进短期理财，部分有效，但取用不够方便。考虑用货币基金加活期的组合吗？",
                },
                {
                    "role": "assistant",
                    "text": "结论是应急金应保持高流动性。可以试试活期加货币基金分层。",
                },
            ],
        },
    ]

    results = []
    for item in conversations:
        results.append(digester.ingest_conversation(**item, occurred_at=now_iso()))

    store.upsert(
        "profile",
        {
            "category": "identity",
            "content": "独立开发者，同时推进多个产品与生活问题，偏好本地优先、可验证的方案。",
            "sensitivity": "normal",
            "reviewed": 1,
            "confidence": 0.9,
        },
    )
    store.upsert(
        "profile",
        {
            "category": "preference",
            "content": "回答先给可执行步骤，再给原因；不要重复推荐已经被否定的方案。",
            "sensitivity": "normal",
            "reviewed": 1,
            "confidence": 0.9,
        },
    )

    threads = store.list("threads", "record_status!='archived'")
    visa = next((t for t in threads if "记账" in (t.get("title") or "")), None)
    if visa:
        ratchet.promote(
            visa["id"],
            plan="先补齐隐私合规与数据删除入口，用体验版验证，再提交微信审核",
            evidence="云托管因备案过慢已失败；继续只改登录方式也被拒。",
        )

    return {"seeded": True, "ingested": results, "stats": store.stats()}

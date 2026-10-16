import json

from app.egress import EgressGate
from app.importers import parse_export
from app.retrieve import Assembler
from tests.conftest import fresh_store


CHATGPT = [
    {
        "title": "部署问题",
        "create_time": 1700000000,
        "update_time": 1700000000,
        "conversation_id": "g-1",
        "mapping": {
            "a": {
                "message": {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["试过 Docker，失败了。下一步怎么做？"]},
                    "create_time": 1700000000,
                }
            },
            "b": {
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"content_type": "text", "parts": ["可以试试先写健康检查。"]},
                    "create_time": 1700000001,
                }
            },
        },
    }
]

CLAUDE = [
    {
        "uuid": "c-1",
        "name": "部署问题",
        "created_at": "2024-01-01T00:00:00Z",
        "chat_messages": [
            {"sender": "human", "text": "我倾向最小改动。如何回滚？", "created_at": "2024-01-01T00:00:00Z"},
            {"sender": "assistant", "text": "保留上一个镜像标签。", "created_at": "2024-01-01T00:01:00Z"},
        ],
    }
]


def test_parse_chatgpt_and_claude():
    gpt = parse_export(json.dumps(CHATGPT).encode(), "conversations.json", "chatgpt")
    claude = parse_export(json.dumps(CLAUDE).encode(), "claude.json", "claude")
    assert gpt[0]["platform"] == "chatgpt"
    assert gpt[0]["turns"][0]["role"] == "user"
    assert claude[0]["platform"] == "claude"
    assert claude[0]["turns"][1]["role"] == "assistant"


def test_egress_blocks_sensitive_for_cloud():
    store = fresh_store()
    gate = EgressGate(store)
    result = gate.process("最近失眠很严重，血压也不稳", "cloud_api")
    assert result["allowed"] is False
    logs = store.list("egress_log")
    assert logs and logs[0]["allowed"] == 0


def test_assemble_includes_failures():
    store = fresh_store()
    thread = store.upsert("threads", {"title": "记账小程序上线", "status": "active", "summary": "小程序审核"})
    store.upsert("solutions", {"thread_id": thread["id"], "plan": "先补隐私合规", "record_status": "active"})
    current = store.list("solutions")[0]
    thread["current_solution_id"] = current["id"]
    store.upsert("threads", thread)
    store.upsert(
        "attempts",
        {
            "thread_id": thread["id"],
            "plan": "只用云托管硬上",
            "outcome": "failure",
            "reason": "备案太慢",
        },
    )
    assembled = Assembler(store).assemble("记账小程序审核被拒怎么办", "chatgpt")
    assert assembled["allowed"]
    assert "请勿再推荐" in assembled["text"]
    assert "云托管" in assembled["text"]
    assert "隐私合规" in assembled["text"]

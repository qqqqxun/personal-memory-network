import io
import json
import zipfile

from fastapi.testclient import TestClient

from app.digest import Digester
from app.importers import parse_export
from tests.conftest import fresh_store


def test_gemini_groups_activity_into_one_conversation():
    payload = [
        {
            "header": "Gemini Apps",
            "title": "Used Gemini Apps",
            "titleUrl": "https://gemini.google.com/app/c/abc123",
            "time": "2026-10-01T00:00:00Z",
            "products": ["Gemini Apps"],
            "details": [
                {"name": "Request", "value": "试过云托管，失败了。如何上线？"},
                {"name": "Response", "value": "先补隐私说明。"},
            ],
        },
        {
            "header": "Gemini Apps",
            "title": "Used Gemini Apps",
            "titleUrl": "https://gemini.google.com/app/c/abc123",
            "time": "2026-10-01T00:05:00Z",
            "userInteractions": [
                {
                    "userInteraction": {
                        "request": json.dumps([[["可以试试体验版吗？"]]]),
                        "response": json.dumps([[["可以，先给核心用户。"]]]),
                    }
                }
            ],
        },
    ]
    items = parse_export(json.dumps(payload).encode(), "Takeout/My Activity/Gemini Apps/MyActivity.json")
    assert len(items) == 1
    assert items[0]["platform"] == "gemini"
    assert items[0]["conversation_id"] == "abc123"
    assert [turn["role"] for turn in items[0]["turns"]] == ["user", "assistant", "user", "assistant"]
    assert "体验版" in items[0]["turns"][2]["text"]


def test_grok_unwraps_responses_and_ignores_billing_in_zip():
    grok = {
        "conversations": [
            {
                "conversation": {
                    "id": "conv1",
                    "title": "部署问题",
                    "create_time": "2026-10-02T00:00:00Z",
                },
                "responses": [
                    {
                        "response": {
                            "_id": "r1",
                            "sender": "human",
                            "message": "试过直接上线，失败了。",
                            "create_time": {"$date": {"$numberLong": "1760000000000"}},
                        }
                    },
                    {
                        "response": {
                            "_id": "r2",
                            "parent_response_id": "r1",
                            "sender": "grok-3",
                            "message": "可以试试先做健康检查。",
                            "create_time": {"$date": {"$numberLong": "1760000005000"}},
                        }
                    },
                    {"response": {"_id": "r3", "sender": "assistant", "message": "", "generated_image_urls": ["x"]}},
                ],
            }
        ]
    }
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        archive.writestr("ttl/30d/export_data/user/prod-mc-billing.json", json.dumps({"balance": 12, "api_key_hash": "secret"}))
        archive.writestr("ttl/30d/export_data/user/prod-grok-backend.json", json.dumps(grok))
    items = parse_export(blob.getvalue(), "grok-export.zip")
    assert len(items) == 1
    assert items[0]["platform"] == "grok"
    assert [turn["role"] for turn in items[0]["turns"]] == ["user", "assistant"]
    assert "健康检查" in items[0]["turns"][1]["text"]


def test_chatgpt_zip_skips_account_file():
    chats = [
        {
            "title": "部署",
            "conversation_id": "g-9",
            "create_time": 1760000000,
            "mapping": {
                "a": {
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": ["如何回滚？"]},
                        "create_time": 1760000000,
                    }
                }
            },
        }
    ]
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        archive.writestr("user.json", json.dumps({"email": "me@example.com", "id": "u1"}))
        archive.writestr("conversations.json", json.dumps(chats))
    items = parse_export(blob.getvalue(), "chatgpt.zip", "chatgpt")
    assert len(items) == 1
    assert items[0]["conversation_id"] == "g-9"
    assert "me@example.com" not in json.dumps(items)


def test_live_capture_updates_same_conversation():
    store = fresh_store()
    digester = Digester(store)
    first = digester.ingest_conversation(
        platform="chatgpt",
        conversation_id="live-1",
        title="记账小程序",
        turns=[{"role": "user", "text": "试过云托管，失败了。"}],
    )
    second = digester.ingest_conversation(
        platform="chatgpt",
        conversation_id="live-1",
        title="记账小程序",
        turns=[
            {"role": "user", "text": "试过云托管，失败了。"},
            {"role": "assistant", "text": "可以试试先发体验版。"},
            {"role": "user", "text": "又试过只改登录，还是失败了。"},
        ],
    )
    assert not first.get("skipped")
    assert second.get("updated")
    assert store.count("sources") == 1
    plans = [row.get("plan") for row in store.list("attempts")]
    assert any("云托管" in (plan or "") for plan in plans)
    assert any("登录" in (plan or "") for plan in plans)


def test_extension_origin_is_allowed_and_websites_are_not(client: TestClient):
    ok = client.get("/api/stats", headers={"Origin": "chrome-extension://abcdefghijklmnop"})
    assert ok.status_code == 200
    blocked = client.get("/api/stats", headers={"Origin": "https://chatgpt.com"})
    assert blocked.status_code == 403

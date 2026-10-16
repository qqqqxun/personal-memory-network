from app.digest import Digester, heuristic_extract
from app.privacy import classify_text
from app.ratchet import Ratchet
from tests.conftest import fresh_store


def test_heuristic_extracts_attempt_and_question():
    text = "我：试过微信云托管部署，失败了，备案太慢。下一步如何更快上线？"
    data = heuristic_extract("记账小程序上线", text)
    assert data["attempts"]
    assert data["attempts"][0]["outcome"] == "failure"
    assert data["open_questions"]


def test_ingest_assigns_thread_and_keeps_failure():
    store = fresh_store()
    digester = Digester(store)
    first = digester.ingest_conversation(
        platform="chatgpt",
        conversation_id="c1",
        title="记账小程序上线",
        turns=[
            {"role": "user", "text": "试过云托管，失败了，备案太慢。如何上线？"},
            {"role": "assistant", "text": "可以试试先发体验版。"},
        ],
    )
    second = digester.ingest_conversation(
        platform="claude",
        conversation_id="c2",
        title="记账小程序审核",
        turns=[{"role": "user", "text": "试过只改登录，还是失败了。可以试试补数据删除入口吗？"}],
    )
    assert first["thread_id"]
    assert second["thread_id"] == first["thread_id"]
    attempts = store.list("attempts", "thread_id=?", (first["thread_id"],))
    assert any(a.get("outcome") == "failure" for a in attempts)
    assert store.count("sources") == 2


def test_duplicate_conversation_skipped():
    store = fresh_store()
    digester = Digester(store)
    payload = dict(
        platform="chatgpt",
        conversation_id="same",
        title="重复",
        turns=[{"role": "user", "text": "你好"}],
    )
    assert not digester.ingest_conversation(**payload).get("skipped")
    again = digester.ingest_conversation(**payload)
    assert again["skipped"]


def test_confidential_is_redacted_before_storage():
    store = fresh_store()
    digester = Digester(store)
    digester.ingest_conversation(
        platform="chatgpt",
        conversation_id="secret",
        title="密钥",
        turns=[{"role": "user", "text": "这是 sk-abcdefghijklmnopqrstuvwxyz123456"}],
    )
    source = store.list("sources")[0]
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in (source.get("raw_text") or "")
    assert "[密钥]" in source["raw_text"]


def test_ratchet_keeps_old_solution():
    store = fresh_store()
    thread = store.upsert("threads", {"title": "睡眠", "status": "active"})
    ratchet = Ratchet(store)
    first = ratchet.promote(thread["id"], plan="固定 23:30 熄屏", evidence="第一轮")
    second = ratchet.promote(thread["id"], plan="23:00 后不看工作消息", evidence="更有效")
    history = ratchet.history(thread["id"])
    assert len(history) == 2
    old = store.get("solutions", first["solution"]["id"])
    assert old["record_status"] == "superseded"
    assert old["superseded_by"] == second["solution"]["id"]
    assert store.get("threads", thread["id"])["current_solution_id"] == second["solution"]["id"]


def test_classify_personal_progress_is_normal():
    result = classify_text("我的项目进度已经到了审核阶段，我习惯先写测试")
    assert result.level == "normal"

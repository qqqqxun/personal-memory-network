from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.distiller import Distiller, DistillError, rank_gemini_models
from app.vault import DIRECTORY_MARKER, Vault

LLM_SETTINGS = {"llm": {"provider": "gemini", "api_key": "fake-key", "model": "gemini-3.8-flash"}}


def chatgpt_export(count: int, start: int = 0) -> str:
    conversations = []
    for i in range(start, start + count):
        conversations.append(
            {
                "title": f"对话{i}",
                "conversation_id": f"c{i}",
                "create_time": 1760000000 + i,
                "update_time": 1760000000 + i,
                "mapping": {
                    "a": {
                        "message": {
                            "author": {"role": "user"},
                            "content": {"parts": [f"我第{i}次问：跑步之后膝盖酸怎么办？"]},
                            "create_time": 1760000000 + i,
                        }
                    }
                },
            }
        )
    return json.dumps(conversations, ensure_ascii=False)


def scripted(distiller: Distiller, responses: list) -> list[str]:
    prompts: list[str] = []

    def fake(prompt: str) -> dict:
        prompts.append(prompt)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    distiller._llm_json = fake
    distiller.sleep = lambda _seconds: None
    return prompts


def test_large_export_is_segmented_not_truncated(tmp_path: Path):
    vault = Vault(root=tmp_path / "v")
    (vault.inbox_dir / "conversations.json").write_text(chatgpt_export(200), encoding="utf-8")
    (vault.inbox_dir / "长文档.md").write_text("我的项目笔记。\n" * 6000, encoding="utf-8")

    scan = vault.scan_inbox()
    files = {f["rel_path"]: f for f in scan["files"]}
    assert files["conversations.json"]["segments_total"] == 200
    assert files["长文档.md"]["segments_total"] >= 3
    segments = vault.load_pending_segments()
    assert len(segments) == files["conversations.json"]["segments_total"] + files["长文档.md"]["segments_total"]
    assert sum(s["chars"] for s in segments if s["rel_path"] == "长文档.md") >= len("我的项目笔记。\n" * 6000) - 10


def test_same_conversation_in_two_exports_is_digested_once(tmp_path: Path):
    vault = Vault(root=tmp_path / "v")
    (vault.inbox_dir / "九月导出.json").write_text(chatgpt_export(3), encoding="utf-8")
    distiller = Distiller(vault=vault, settings=LLM_SETTINGS)
    scripted(distiller, [{"epoch_summary": "第一批", "volumes": []}])
    distiller.run_epoch()

    (vault.inbox_dir / "十月导出.json").write_text(chatgpt_export(5), encoding="utf-8")
    pending = vault.load_pending_segments()
    assert [s["title"] for s in pending] == ["对话3", "对话4"]


def test_llm_merge_writes_volumes_cards_and_directory(tmp_path: Path):
    vault = Vault(root=tmp_path / "v")
    (vault.inbox_dir / "健身.txt").write_text("我现在180cm，160斤。早餐我决定固定吃燕麦和牛奶。", encoding="utf-8")
    distiller = Distiller(vault=vault, settings=LLM_SETTINGS)
    prompts = scripted(
        distiller,
        [
            {
                "epoch_summary": "建立健康专卷",
                "profile_md": "# 个人主基座\n- 30 岁，180cm，80kg",
                "volumes": [
                    {"slug": "health", "title": "健康与运动", "content_md": "# 健康与运动\n\n## 个人基线与目标约束\n- 180cm，80kg"}
                ],
                "cards": [{"volume": "health", "title": "一日三餐示例", "content": "早餐：燕麦 50g + 牛奶 250ml"}],
            }
        ],
    )
    result = distiller.run_epoch()
    assert result["epoch_entry"]["touched_projects"] == ["健康与运动"]
    assert "燕麦" in prompts[0]
    bootloader = vault.read_bootloader()
    assert "30 岁" in bootloader
    assert DIRECTORY_MARKER in bootloader
    assert "《健康与运动》" in bootloader
    assert vault.list_cards("health")[0]["title"] == "一日三餐示例"
    assert vault.scan_inbox()["pending_segments"] == 0


def test_oversized_volume_is_split_not_truncated(tmp_path: Path):
    vault = Vault(root=tmp_path / "v")
    (vault.inbox_dir / "项目.md").write_text("记账小程序：审核、部署、推广的记录。", encoding="utf-8")
    distiller = Distiller(vault=vault, settings={**LLM_SETTINGS, "budgets": {"project": 200}})
    huge = "# 记账小程序\n\n" + "审核与部署的详细经过。" * 60
    scripted(
        distiller,
        [
            {"epoch_summary": "x", "volumes": [{"slug": "visa", "title": "记账小程序", "content_md": huge}]},
            {
                "mode": "split",
                "content_md": "# 记账小程序\n\n- 拆成审核和部署两个子卷",
                "children": [
                    {"title": "审核", "content_md": "# 审核\n- 补齐数据删除入口后过审"},
                    {"title": "部署", "content_md": "# 部署\n- 云托管备案太慢，已放弃"},
                ],
                "cards": [{"title": "审核被拒原文", "content": "缺少隐私授权说明"}],
            },
        ],
    )
    distiller.run_epoch()
    slugs = {v["slug"] for v in vault.list_volumes()}
    assert {"visa", "visa__审核", "visa__部署"} <= slugs
    assert vault.get_project("visa")["content"].startswith("# 记账小程序")
    assert vault.list_cards("visa")[0]["title"] == "审核被拒原文"
    directory = vault.render_directory()
    assert "2 个子卷" in directory
    assert "《审核》" not in directory


def test_failed_compression_keeps_full_text(tmp_path: Path):
    vault = Vault(root=tmp_path / "v")
    (vault.inbox_dir / "a.md").write_text("内容", encoding="utf-8")
    distiller = Distiller(vault=vault, settings={**LLM_SETTINGS, "budgets": {"project": 100}})
    long_text = "# 长卷\n\n" + "一条不能丢的事实。" * 40
    scripted(
        distiller,
        [{"volumes": [{"slug": "long", "title": "长卷", "content_md": long_text}]}, DistillError("繁忙")],
    )
    result = distiller.run_epoch()
    assert vault.get_project("long")["content"].strip() == long_text.strip()
    assert any("没有截断" in w for w in result["epoch_entry"]["warnings"])


def test_batch_failure_keeps_earlier_batches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import app.distiller as distiller_module

    monkeypatch.setattr(distiller_module, "BATCH_MAX_CHARS", 10)
    vault = Vault(root=tmp_path / "v")
    (vault.inbox_dir / "conversations.json").write_text(chatgpt_export(3), encoding="utf-8")
    distiller = Distiller(vault=vault, settings=LLM_SETTINGS)
    scripted(
        distiller,
        [{"epoch_summary": "第一批"}, DistillError("Gemini 暂时不可用（503）")],
    )
    with pytest.raises(DistillError) as info:
        distiller.run_epoch()
    assert "前 1 批已写入" in str(info.value)
    assert vault.scan_inbox()["pending_segments"] == 2
    history = vault.brain_snapshot()["history"]
    assert history[0]["partial"] is True


def test_local_mode_stages_cards_without_touching_volumes(tmp_path: Path):
    vault = Vault(root=tmp_path / "v")
    vault.write_project("健康与运动", "# 健康与运动\n\n- 已有的事实：180cm")
    (vault.inbox_dir / "chat.txt").write_text("我今天跑了二十分钟，膝盖酸痛。\n加油，坚持就是胜利！", encoding="utf-8")
    result = Distiller(vault=vault, settings={"llm": {"provider": "gemini", "api_key": ""}}).run_epoch()
    assert result["mode"] == "local_staged"
    assert "已有的事实：180cm" in vault.get_project("健康与运动")["content"]
    card = vault.list_cards()[0]
    assert card["origin"] == "local"
    assert "膝盖酸痛" in card["content"]
    assert "坚持就是胜利" not in card["content"]
    assert vault.scan_inbox()["pending_segments"] == 1


def test_mount_retrieves_relevant_cards(tmp_path: Path):
    vault = Vault(root=tmp_path / "v")
    vault.write_project("健康与运动", "# 健康与运动\n\n- 每周跑步三次")
    vault.add_cards(
        [
            {"volume": "健康与运动", "title": "跑步间歇方案", "content": "跑步 3 分钟走路 1 分钟，减轻膝盖负担"},
            {"volume": "健康与运动", "title": "季度体检预约", "content": "体检中心周三上午预约"},
        ],
        epoch=1,
        origin="gemini",
    )
    pack = vault.build_mount_pack(task_prompt="跑步后膝盖酸，间歇怎么安排")
    assert pack["mounted_projects"] == ["健康与运动"]
    assert "跑步间歇方案" in pack["text"]
    assert "季度体检预约" not in pack["text"]


def test_rank_gemini_models_prefers_newest_flash():
    names = ["gemini-2.5-pro", "gemini-3.8-flash-lite", "gemini-3.8-flash", "gemini-3.6-flash", "text-embedding-004"]
    assert rank_gemini_models(names)[:2] == ["gemini-3.8-flash", "gemini-3.6-flash"]


def test_vault_api_endpoints(client: TestClient):
    seeded = client.post("/api/vault/inbox/seed-sample").json()
    assert seeded["inbox"]["pending_segments"] >= 1
    staged = client.post("/api/vault/distill", json={"force_all": True, "wait": True}).json()
    assert staged["mode"] == "local_staged"
    assert client.get("/api/vault/cards").json()
    job = client.get("/api/vault/distill/job").json()
    assert job["running"] is False
    mounted = client.post("/api/vault/mount", json={"task_prompt": "小程序审核下一步怎么做？"}).json()
    assert "小程序审核下一步怎么做？" in mounted["text"]

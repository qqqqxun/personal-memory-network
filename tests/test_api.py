from fastapi.testclient import TestClient


def test_health_and_auth(client: TestClient):
    assert client.get("/api/health").status_code == 200
    naked = client.get("/api/stats", headers={"X-Memory-Token": "wrong"})
    assert naked.status_code == 401


def test_import_seed_review_and_assemble(client: TestClient):
    seeded = client.post("/api/demo/seed").json()
    assert seeded.get("seeded") or seeded.get("reason") == "already_has_data"
    stats = client.get("/api/stats").json()
    assert stats["threads"] >= 1
    threads = client.get("/api/threads").json()
    visa = next(t for t in threads if "记账" in t["title"])
    bundle = client.get(f"/api/threads/{visa['id']}").json()
    assert bundle["attempts"]
    preview = client.post("/api/assemble", json={"query": "小程序审核被拒下一步", "destination": "chatgpt"}).json()
    assert preview["allowed"]
    assert preview["text"]
    queued = client.get("/api/review").json()
    if queued:
        decided = client.post(f"/api/review/{queued[0]['id']}", json={"action": "accept"}).json()
        assert decided["item"]["status"] == "accepted"


def test_home_page_sets_cookie(client: TestClient):
    res = client.get("/")
    assert res.status_code == 200
    assert "memory_token" in res.cookies
    assert "个人记忆网络" in res.text

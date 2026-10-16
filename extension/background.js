async function settings() {
  const stored = await chrome.storage.local.get(["token", "port"]);
  return {
    token: stored.token || "",
    port: stored.port || 8765,
  };
}

async function call(path, options = {}) {
  const cfg = await settings();
  if (!cfg.token) {
    return { ok: false, status: 401, body: { detail: "还没有填写本机令牌" } };
  }
  let response;
  try {
    response = await fetch(`http://127.0.0.1:${cfg.port}${path}`, {
      ...options,
      headers: {
        ...(options.headers || {}),
        "X-Memory-Token": cfg.token,
      },
    });
  } catch (error) {
    return { ok: false, status: 0, body: { detail: "连不上本机记忆网络。请先运行 python -m app" } };
  }
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  return { ok: response.ok, status: response.status, body };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "ingest") {
    call("/api/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message.payload),
    }).then((result) => {
      const reason = result.ok
        ? (result.body.updated ? "已更新这段对话" : result.body.skipped ? "没有新内容" : "已记入本机")
        : (result.body.detail || "写入失败");
      chrome.action.setBadgeText({ text: result.ok ? "" : "!" });
      chrome.action.setBadgeBackgroundColor({ color: "#c97b7b" });
      chrome.storage.local.set({
        lastCapture: { ok: result.ok, reason, at: new Date().toISOString(), status: result.status },
      });
      sendResponse({ ok: result.ok, reason, body: result.body });
    });
    return true;
  }
  if (message?.type === "health") {
    settings().then(async (cfg) => {
      try {
        const response = await fetch(`http://127.0.0.1:${cfg.port}/api/health`);
        sendResponse({ ok: response.ok, hasToken: Boolean(cfg.token) });
      } catch {
        sendResponse({ ok: false, hasToken: Boolean(cfg.token) });
      }
    });
    return true;
  }
  if (message?.type === "capture_status") {
    chrome.action.setBadgeText({ text: message.ok ? "" : "!" });
    chrome.storage.local.set({ lastCapture: { ok: false, reason: message.reason, at: new Date().toISOString() } });
  }
  return false;
});

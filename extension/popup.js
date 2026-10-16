const tokenEl = document.getElementById("token");
const portEl = document.getElementById("port");
const statusEl = document.getElementById("status");
const whereEl = document.getElementById("where");
const skipEl = document.getElementById("skip");

function show(text, ok) {
  statusEl.textContent = text;
  statusEl.className = "status " + (ok ? "ok" : "bad");
}

async function currentTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function refresh() {
  const stored = await chrome.storage.local.get(["token", "port", "lastCapture", "skip"]);
  tokenEl.value = stored.token || "";
  portEl.value = stored.port || 8765;
  const health = await chrome.runtime.sendMessage({ type: "health" });
  const tab = await currentTab();
  let page = null;
  if (tab?.id) {
    try {
      page = await chrome.tabs.sendMessage(tab.id, { type: "page-info" });
    } catch {
      page = null;
    }
  }
  if (page?.platform) {
    whereEl.textContent = `当前页面：${page.platform}${page.onConversation ? " · 已识别对话" : " · 还在列表页"}`;
    const key = `${page.platform}:${page.conversation_id}`;
    skipEl.checked = Boolean((stored.skip || {})[key]);
    skipEl.disabled = !page.conversation_id;
  } else {
    whereEl.textContent = "打开 ChatGPT、Claude、Gemini 或 Grok 的具体对话后再抓取。";
    skipEl.disabled = true;
  }
  if (!health?.ok) {
    show("本机服务没开。先在项目目录运行 python -m app。", false);
  } else if (!health.hasToken) {
    show("服务在线。把「接入」页上的令牌粘贴过来。", false);
  } else if (stored.lastCapture?.reason) {
    show(stored.lastCapture.reason, stored.lastCapture.ok);
  } else {
    show("已连上本机。回答结束后会自动记入。", true);
  }
}

document.getElementById("save").onclick = async () => {
  await chrome.storage.local.set({ token: tokenEl.value.trim(), port: Number(portEl.value) || 8765 });
  show("已保存。", true);
  refresh();
};

document.getElementById("grab").onclick = async () => {
  const tab = await currentTab();
  if (!tab?.id) return show("没有当前标签页", false);
  try {
    const result = await chrome.tabs.sendMessage(tab.id, { type: "capture-now" });
    show(result?.reason || "已处理", Boolean(result?.ok));
  } catch {
    show("这个页面还不能抓取。请先打开四家之一的对话页，并重新加载扩展。", false);
  }
};

skipEl.onchange = async () => {
  const tab = await currentTab();
  if (!tab?.id) return;
  let page = null;
  try {
    page = await chrome.tabs.sendMessage(tab.id, { type: "page-info" });
  } catch {
    return;
  }
  if (!page?.conversation_id) return;
  const stored = await chrome.storage.local.get("skip");
  const skip = stored.skip || {};
  const key = `${page.platform}:${page.conversation_id}`;
  if (skipEl.checked) skip[key] = true;
  else delete skip[key];
  await chrome.storage.local.set({ skip });
  show(skipEl.checked ? "这次对话不会记入" : "已恢复记录这次对话", true);
};

refresh();

let lastHash = "";
let timer = 0;

function hashOf(payload) {
  const raw = payload.turns.map((turn) => `${turn.role}\n${turn.text}`).join("\n---\n");
  let hash = 0;
  for (let i = 0; i < raw.length; i += 1) hash = (Math.imul(31, hash) + raw.charCodeAt(i)) | 0;
  return `${payload.conversation_id}:${hash}:${raw.length}`;
}

async function capture(manual) {
  const page = readPage();
  if (!page.platform) return { ok: false, reason: "当前页面不是支持的对话网站" };
  if (!page.onConversation) return { ok: false, reason: "还没进入具体对话", idle: true };
  if (page.generating && !manual) return { ok: false, reason: "回答还没结束", idle: true };
  if (!page.turns.length) {
    const reason = "页面上没有读到对话。网站改版后会这样，请改用官方导出文件。";
    chrome.runtime.sendMessage({ type: "capture_status", ok: false, reason });
    return { ok: false, reason };
  }
  const digest = hashOf(page);
  if (!manual && digest === lastHash) return { ok: true, reason: "没有新内容", idle: true };
  const stored = await chrome.storage.local.get("skip");
  const skip = stored.skip || {};
  if (skip[`${page.platform}:${page.conversation_id}`]) {
    return { ok: true, reason: "这次对话已设为不记录", skipped: true };
  }
  const response = await chrome.runtime.sendMessage({
    type: "ingest",
    payload: {
      platform: page.platform,
      conversation_id: page.conversation_id,
      title: page.title,
      turns: page.turns,
      occurred_at: new Date().toISOString(),
    },
  });
  if (response?.ok) lastHash = digest;
  return response || { ok: false, reason: "本地服务没有响应" };
}

const observer = new MutationObserver(() => {
  clearTimeout(timer);
  timer = setTimeout(() => {
    capture(false).catch(() => {});
  }, 1800);
});

observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
setTimeout(() => capture(false).catch(() => {}), 1500);

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "capture-now") {
    capture(true).then(sendResponse);
    return true;
  }
  if (message?.type === "page-info") {
    const page = readPage();
    sendResponse(page);
    return false;
  }
  return false;
});

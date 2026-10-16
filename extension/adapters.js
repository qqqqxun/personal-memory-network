function platformOf(hostname) {
  const host = hostname.replace(/^www\./, "");
  if (host === "chatgpt.com" || host === "chat.openai.com") return "chatgpt";
  if (host === "claude.ai") return "claude";
  if (host === "gemini.google.com") return "gemini";
  if (host === "grok.com" || host === "grok.x.ai") return "grok";
  return "";
}

function conversationId(platform, href) {
  const url = new URL(href);
  const path = url.pathname;
  const patterns = {
    chatgpt: /\/c\/([a-f0-9-]{8,})/i,
    claude: /\/chat\/([A-Za-z0-9_-]{6,})/,
    gemini: /\/app\/(?:c\/)?([A-Za-z0-9_-]{4,})/,
    grok: /\/(?:c|chat)\/([A-Za-z0-9_-]{4,})/,
  };
  const match = path.match(patterns[platform] || /$^/);
  return match ? match[1] : "";
}

function textOf(node) {
  return (node?.innerText || "").replace(/\n{3,}/g, "\n\n").trim();
}

function fromRoles(nodes, roleAttr) {
  const turns = [];
  nodes.forEach((node) => {
    const role = (node.getAttribute(roleAttr) || "").toLowerCase();
    if (role !== "user" && role !== "assistant" && role !== "model") return;
    const text = textOf(node);
    if (!text) return;
    turns.push({ role: role === "model" ? "assistant" : role, text });
  });
  return turns;
}

function extractChatGPT() {
  const marked = fromRoles(document.querySelectorAll("[data-message-author-role]"), "data-message-author-role");
  if (marked.length) return marked;
  const turns = [];
  document.querySelectorAll("article").forEach((article) => {
    const user = article.querySelector("[data-message-author-role='user']");
    const assistant = article.querySelector("[data-message-author-role='assistant']");
    if (user) turns.push({ role: "user", text: textOf(user) });
    if (assistant) turns.push({ role: "assistant", text: textOf(assistant) });
  });
  return turns.filter((turn) => turn.text);
}

function extractClaude() {
  const turns = [];
  const nodes = document.querySelectorAll(
    "[data-testid='user-message'], [data-testid='assistant-message'], .font-user-message, .font-claude-message"
  );
  nodes.forEach((node) => {
    const testid = node.getAttribute("data-testid") || "";
    const cls = node.className || "";
    const role = testid.includes("user") || String(cls).includes("user-message") ? "user" : "assistant";
    const text = textOf(node);
    if (text) turns.push({ role, text });
  });
  return dedupeAdjacent(turns);
}

function extractGemini() {
  const nodes = document.querySelectorAll("user-query, model-response, .user-query, .model-response");
  const turns = [];
  nodes.forEach((node) => {
    const tag = node.tagName.toLowerCase();
    const cls = String(node.className || "");
    const role = tag.includes("user") || cls.includes("user") ? "user" : "assistant";
    const text = textOf(node.querySelector(".query-text, .markdown, message-content") || node);
    if (text) turns.push({ role, text });
  });
  return dedupeAdjacent(turns);
}

function extractGrok() {
  const marked = fromRoles(document.querySelectorAll("[data-message-author], [data-author]"), "data-message-author");
  if (marked.length) return marked;
  const turns = [];
  document.querySelectorAll("[class*='message-row'], [data-testid*='message']").forEach((node) => {
    const blob = `${node.className} ${node.getAttribute("data-testid") || ""}`.toLowerCase();
    const role = blob.includes("user") || blob.includes("human") ? "user" : "assistant";
    const text = textOf(node);
    if (text && text.length > 1) turns.push({ role, text });
  });
  return dedupeAdjacent(turns);
}

function dedupeAdjacent(turns) {
  const out = [];
  for (const turn of turns) {
    const prev = out[out.length - 1];
    if (prev && prev.role === turn.role && prev.text === turn.text) continue;
    out.push(turn);
  }
  return out;
}

function isGenerating() {
  const selectors = [
    "button[data-testid='stop-button']",
    "button[aria-label*='Stop']",
    "button[aria-label*='停止']",
    "button[aria-label*='Stop generating']",
    "[data-testid='stop-generating']",
  ];
  return selectors.some((selector) => document.querySelector(selector));
}

function pageTitle(platform) {
  const raw = (document.title || "").replace(/\s+[-|].*$/, "").trim();
  const generic = ["chatgpt", "claude", "gemini", "grok", "new chat", "新对话"];
  if (!raw || generic.includes(raw.toLowerCase())) return "";
  return raw.slice(0, 80);
}

function readPage() {
  const platform = platformOf(location.hostname);
  const conversation = conversationId(platform, location.href);
  const extractors = {
    chatgpt: extractChatGPT,
    claude: extractClaude,
    gemini: extractGemini,
    grok: extractGrok,
  };
  const turns = platform && extractors[platform] ? extractors[platform]() : [];
  return {
    platform,
    conversation_id: conversation,
    title: pageTitle(platform) || (turns.find((turn) => turn.role === "user")?.text || "未命名对话").slice(0, 42),
    turns,
    onConversation: Boolean(platform && conversation),
    generating: isGenerating(),
  };
}

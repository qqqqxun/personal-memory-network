const view = document.getElementById("view");
const titleEl = document.getElementById("page-title");
const subEl = document.getElementById("page-sub");
const toastEl = document.getElementById("toast");
const jobbar = document.getElementById("jobbar");
const distillBtn = document.getElementById("btn-distill");

const titles = {
  "/": ["蒸馏主控台", "你决定投喂什么、什么时候蒸馏；模型负责把记录合并进知识库。"],
  "/brain": ["知识结晶 (Brain)", "主基座 + 思维铁律 + 领域专卷（可拆子卷）+ 细节卡片。"],
  "/mount": ["一秒挂载 (Mount)", "主基座、相关专卷和按任务检索出的细节卡片，一起交给任何 AI。"],
  "/epochs": ["演进历史 (Epochs)", "每一轮蒸馏改了什么、丢了什么、有什么需要注意。"],
  "/connect": ["投喂与算力配置", "配置 Gemini API Key、代理和记忆库路径。"],
};

function token() {
  return document.cookie.split("; ").find((x) => x.startsWith("memory_token="))?.split("=")[1] || "";
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}), "X-Memory-Token": decodeURIComponent(token()) };
  if (options.body && !(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const res = await fetch(path, { ...options, headers, credentials: "include" });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function toast(text) {
  toastEl.hidden = false;
  toastEl.textContent = text;
  clearTimeout(toastEl._timer);
  toastEl._timer = setTimeout(() => { toastEl.hidden = true; }, 4000);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function meter(chars, budget) {
  const pct = Math.min(100, Math.round((chars / Math.max(1, budget)) * 100));
  return `<div class="meter"><i style="width:${pct}%"></i></div>`;
}

function sizeBadge(item) {
  return `<span class="badge ${item.over_budget ? "over" : "teal"}">${item.chars} / ${item.budget} 字${item.over_budget ? " · 超出" : ""}</span>`;
}

function fileStatus(f) {
  if (f.status === "unsupported") return `<span class="badge over">无法识别${f.error ? "：" + esc(f.error) : ""}</span>`;
  if (f.status === "processed") return `<span class="badge processed">已消化 ${f.segments_total} 段（Epoch ${f.last_epoch}）</span>`;
  if (f.status === "partial") return `<span class="badge partial">已消化 ${f.segments_done}/${f.segments_total} 段</span>`;
  return `<span class="badge pending">待蒸馏 ${f.segments_total} 段</span>`;
}

function route() {
  const raw = location.hash.slice(1) || "/";
  const parts = raw.split("/").filter(Boolean);
  const page = parts[0] ? `/${parts[0]}` : "/";
  const param = parts[1] ? decodeURIComponent(parts[1]) : "";
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === page));
  const meta = titles[page] || titles["/"];
  titleEl.textContent = meta[0];
  subEl.textContent = meta[1];
  if (page === "/brain") return renderBrain(param);
  if (page === "/mount") return renderMount();
  if (page === "/epochs") return renderEpochs();
  if (page === "/connect") return renderConnect();
  return renderDashboard();
}

// ── 蒸馏任务与进度 ────────────────────────────────────────────────

let pollTimer = null;

function showJob(job) {
  if (job.running) {
    jobbar.hidden = false;
    jobbar.className = "jobbar";
    jobbar.innerHTML = `<div class="spin"></div><div class="grow">${esc(job.progress?.message || "蒸馏中")}</div>`;
    distillBtn.disabled = true;
    distillBtn.textContent = "⏳ 蒸馏中…";
    return;
  }
  distillBtn.disabled = false;
  distillBtn.textContent = "⚡ 跑一轮 Epoch 蒸馏";
  if (job.error) {
    jobbar.hidden = false;
    jobbar.className = "jobbar error";
    jobbar.innerHTML = `<div class="grow">蒸馏没有完成：${esc(job.error)}</div><button class="ghost" id="jobbar-close">关闭</button>`;
  } else if (job.result) {
    const r = job.result;
    const text = r.mode === "local_staged"
      ? r.message
      : `Epoch ${r.epoch_entry.epoch} 完成：消化 ${r.epoch_entry.segments} 段，改动 ${r.epoch_entry.touched_projects.length} 卷，卡片 ${r.epoch_entry.cards} 张。${r.epoch_entry.warnings.length ? "有 " + r.epoch_entry.warnings.length + " 条需要注意，见演进历史。" : ""}`;
    jobbar.hidden = false;
    jobbar.className = "jobbar done";
    jobbar.innerHTML = `<div class="grow">${esc(text)}</div><button class="ghost" id="jobbar-close">关闭</button>`;
  } else {
    jobbar.hidden = true;
    return;
  }
  document.getElementById("jobbar-close").onclick = () => { jobbar.hidden = true; };
}

async function pollJob() {
  clearInterval(pollTimer);
  const tick = async () => {
    const job = await api("/api/vault/distill/job");
    showJob(job);
    if (!job.running) {
      clearInterval(pollTimer);
      route();
    }
  };
  pollTimer = setInterval(() => tick().catch(() => {}), 2000);
  await tick();
}

distillBtn.onclick = async () => {
  const forceAll = document.getElementById("force-all")?.checked || false;
  try {
    const job = await api("/api/vault/distill", { method: "POST", body: { force_all: forceAll } });
    showJob(job);
    pollJob();
  } catch (err) {
    toast(err.message);
  }
};

document.getElementById("btn-sample").onclick = async () => {
  await api("/api/vault/inbox/seed-sample", { method: "POST" });
  toast("已放入示例复盘文档");
  route();
};

// ── 主控台 ────────────────────────────────────────────────────────

async function renderDashboard() {
  const { brain, inbox, llm } = await api("/api/vault/status");
  const latest = brain.history[0];

  view.innerHTML = `
    <div class="path-bar">
      <span>📁 投喂文件夹：</span>
      <code>${esc(inbox.inbox_dir)}</code>
      <span style="margin-left:auto" class="badge ${llm.has_api_key ? "ok" : "pending"}">
        ${llm.has_api_key ? `${esc(llm.provider)} / ${esc(llm.model)}${llm.proxy ? ` · 代理 ${esc(llm.proxy)}` : ""}` : "未配置 API Key（只能做本地预脱水）"}
      </span>
    </div>

    <div class="grid">
      <div class="stat"><span>当前代数</span><b>Epoch ${brain.epoch}</b></div>
      <div class="stat"><span>待消化</span><b>${inbox.pending_segments} 段 <small class="muted" style="font-size:14px">/ ${inbox.pending_files} 个文件</small></b></div>
      <div class="stat"><span>领域专卷 / 细节卡片</span><b>${brain.top_volumes} 卷 <small class="muted" style="font-size:14px">共 ${brain.projects.length} 卷含子卷 · ${brain.cards_total} 张卡片</small></b></div>
      <div class="stat"><span>个人画像</span><b>${brain.bootloader.chars} <small class="muted" style="font-size:14px">/ ${brain.bootloader.budget} 字</small></b>${meter(brain.bootloader.chars, brain.bootloader.budget)}</div>
    </div>

    <div class="split">
      <div class="card">
        <div class="row">
          <h3>📥 投喂区</h3>
          <label style="margin:0"><input type="checkbox" id="force-all" style="width:auto" /> 强制重炼全部内容</label>
        </div>
        <p class="muted" style="margin:6px 0 12px">大文件会被切成小段，每段只消化一次。同一段对话出现在多个导出包里也只算一次。</p>
        <div class="actions" style="margin-bottom:14px">
          <input id="quick-file" type="file" style="max-width:280px" />
          <button id="btn-upload" class="ghost">放进投喂区</button>
          <button id="btn-manual-bridge" class="ghost">网页算力桥接</button>
        </div>
        <div class="list">
          ${inbox.files.length ? inbox.files.map((f) => `
            <div class="item">
              <div class="row"><strong>${esc(f.rel_path)}</strong>${fileStatus(f)}</div>
              <div class="muted small" style="margin-top:4px">${(f.size / 1024).toFixed(1)} KB · 修改于 ${esc(f.modified_at)}</div>
            </div>`).join("") : `<div class="empty">投喂区是空的。把文件放进 <code>${esc(inbox.inbox_dir)}</code> 即可。</div>`}
        </div>
      </div>

      <div class="card">
        <div class="row"><h3>🧬 最近一轮蒸馏</h3><a href="#/epochs">全部历史 →</a></div>
        ${latest ? `
          <p style="margin:8px 0"><strong>Epoch ${latest.epoch}</strong> · <span class="muted">${esc(latest.ran_at)} · ${esc(latest.engine)} · ${latest.segments || 0} 段 / ${latest.batches || 0} 批</span>${latest.partial ? ' <span class="badge over">部分完成</span>' : ""}</p>
          <p>${esc(latest.summary)}</p>
          <label>被新信息取代的旧认知</label><pre>${esc((latest.overwritten || []).join("\n") || "无")}</pre>
          <label>新归纳出的偏好或规律</label><pre>${esc((latest.abstracted || []).join("\n") || "无")}</pre>
          ${(latest.warnings || []).length ? `<label>需要注意</label><pre>${esc(latest.warnings.join("\n"))}</pre>` : ""}
        ` : `<div class="empty">还没有跑过蒸馏。放入文件后点右上角「⚡ 跑一轮 Epoch 蒸馏」。</div>`}
      </div>
    </div>

    <div id="bridge-modal" class="card" style="margin-top:16px" hidden>
      <h3>🌐 网页算力桥接</h3>
      <p class="muted">不用 API Key，或想用网页版更强的模型时：生成一批的指令包，粘给网页版 AI，再把它返回的 JSON 粘回来。每次处理一批。</p>
      <div class="actions"><button id="btn-gen-prompt" class="ghost">生成下一批指令包并复制</button><span id="bridge-info" class="muted small"></span></div>
      <label>指令包</label>
      <textarea id="bridge-prompt"></textarea>
      <label>网页版 AI 返回的 JSON</label>
      <textarea id="bridge-json" placeholder='{"epoch_summary": "...", "volumes": [...], ...}'></textarea>
      <div class="actions"><button id="btn-apply-json" class="ok">写入知识库</button></div>
    </div>`;

  document.getElementById("btn-upload").onclick = async () => {
    const file = document.getElementById("quick-file").files[0];
    if (!file) return toast("先选择一个文件");
    const fd = new FormData();
    fd.append("file", file);
    await api("/api/vault/inbox/upload", { method: "POST", body: fd });
    toast(`已放入投喂区：${file.name}`);
    renderDashboard();
  };
  document.getElementById("btn-manual-bridge").onclick = () => {
    const modal = document.getElementById("bridge-modal");
    modal.hidden = !modal.hidden;
  };
  document.getElementById("btn-gen-prompt").onclick = async () => {
    try {
      const res = await api("/api/vault/distill/prompt", {
        method: "POST",
        body: { force_all: document.getElementById("force-all").checked },
      });
      document.getElementById("bridge-prompt").value = res.prompt;
      document.getElementById("bridge-info").textContent = `第 ${res.batch}/${res.total_batches} 批，${res.segments} 段，${res.chars} 字`;
      await navigator.clipboard.writeText(res.prompt);
      toast("指令包已复制");
    } catch (err) {
      toast(err.message);
    }
  };
  document.getElementById("btn-apply-json").onclick = async () => {
    const raw = document.getElementById("bridge-json").value.trim();
    if (!raw) return toast("先粘贴返回的 JSON");
    try {
      const res = await api("/api/vault/distill/apply-manual", { method: "POST", body: { raw_json: raw } });
      toast(`已写入 Epoch ${res.epoch_entry.epoch}`);
      renderDashboard();
    } catch (err) {
      toast(err.message);
    }
  };
}

// ── 知识结晶 ──────────────────────────────────────────────────────

async function renderBrain(selectedSlug = "") {
  const { brain } = await api("/api/vault/status");
  const active = brain.projects.find((p) => p.slug === selectedSlug) || brain.projects[0] || null;
  const cards = active ? await api(`/api/vault/cards?volume=${encodeURIComponent(active.slug)}`) : [];

  view.innerHTML = `
    <div class="split">
      <div class="card">
        <div class="row"><h3>个人画像（主基座）</h3>${sizeBadge(brain.bootloader)}</div>
        <p class="muted small" style="margin:4px 0 8px">文件：03_知识结晶_Brain/00_个人主基座_Bootloader.md。下方的专卷目录由系统自动生成，不计入字数。</p>
        <textarea id="ed-boot" style="min-height:220px">${esc(brain.bootloader.profile)}</textarea>
        <label>自动生成的领域专卷目录</label>
        <pre>${esc(brain.bootloader.directory)}</pre>
      </div>
      <div class="card">
        <div class="row"><h3>思维与工程铁律</h3>${sizeBadge(brain.principles)}</div>
        <p class="muted small" style="margin:4px 0 8px">文件：03_知识结晶_Brain/01_思维与工程铁律.md</p>
        <textarea id="ed-prin" style="min-height:220px">${esc(brain.principles.content)}</textarea>
      </div>
    </div>
    <div class="actions"><button id="save-core">保存画像与铁律</button></div>

    <div class="split-aside" style="margin-top:20px">
      <div class="card">
        <h3>领域专卷</h3>
        <div class="list">
          ${brain.projects.map((p) => `
            <div class="item vol ${p.depth ? "child" : ""} ${p.depth > 1 ? "d2" : ""} ${active && active.slug === p.slug ? "active" : ""}" data-slug="${esc(p.slug)}">
              <div class="row"><strong>${p.depth ? "└ " : ""}${esc(p.title)}</strong>${sizeBadge(p)}</div>
              <div class="muted small">${p.cards ? `${p.cards} 张卡片 · ` : ""}${esc(p.updated_at)}</div>
            </div>`).join("") || `<div class="empty">还没有专卷。跑一轮蒸馏，或在下面手动新建。</div>`}
        </div>
        <label>手动新建专卷</label>
        <input id="new-proj-name" placeholder="例如：个人理财与资产配置" />
        <div class="actions"><button id="btn-new-proj" class="ghost">新建</button></div>
      </div>

      <div class="card">
        ${active ? `
          <div class="row"><h3>${esc(active.title)}</h3>${sizeBadge(active)}</div>
          <p class="muted small" style="margin:4px 0 8px">文件：03_知识结晶_Brain/项目与问题线/${esc(active.filename)}${active.parent ? ` · 父卷：${esc(active.parent)}` : ""}</p>
          <textarea id="ed-proj" style="min-height:320px">${esc(active.content)}</textarea>
          <div class="actions">
            <button id="save-proj">保存专卷</button>
            <button id="del-proj" class="danger">删除专卷</button>
          </div>
          <h3 style="margin-top:20px">细节卡片（${cards.length}）</h3>
          <p class="muted small" style="margin:0 0 8px">不默认挂载；在「一秒挂载」里填写任务时，会自动检索相关的卡片带上。</p>
          <div class="list">
            ${cards.map((c) => `
              <div class="card-item">
                <div class="row"><strong>${esc(c.title)}</strong><button class="ghost small" data-card="${esc(c.id)}">删除</button></div>
                <div class="muted small">${esc(c.origin === "local" ? "本地预脱水（待蒸馏）" : "蒸馏生成")} · ${esc(c.source)} · Epoch ${c.epoch}</div>
                <p>${esc(c.content)}</p>
              </div>`).join("") || `<div class="muted">这卷还没有细节卡片。</div>`}
          </div>
        ` : `<div class="empty">在左侧选择或新建一卷专卷</div>`}
      </div>
    </div>`;

  view.querySelectorAll(".vol").forEach((el) => {
    el.onclick = () => { location.hash = `#/brain/${encodeURIComponent(el.dataset.slug)}`; };
  });
  document.getElementById("save-core").onclick = async () => {
    await api("/api/vault/brain/core", {
      method: "PUT",
      body: { bootloader: document.getElementById("ed-boot").value, principles: document.getElementById("ed-prin").value },
    });
    toast("已保存");
    renderBrain(selectedSlug);
  };
  document.getElementById("btn-new-proj").onclick = async () => {
    const name = document.getElementById("new-proj-name").value.trim();
    if (!name) return toast("请输入专卷名称");
    const template = `# ${name}\n\n## 个人基线与目标约束\n- \n\n## 当前锁定方案与最新状态\n- \n\n## ❌ 已证伪与踩坑墓志铭（勿再推荐）\n- \n\n## ❓ 待突破问题与下一步\n- \n`;
    const created = await api(`/api/vault/brain/projects/${encodeURIComponent(name)}`, { method: "PUT", body: { content: template } });
    location.hash = `#/brain/${encodeURIComponent(created.slug)}`;
  };
  if (!active) return;
  document.getElementById("save-proj").onclick = async () => {
    await api(`/api/vault/brain/projects/${encodeURIComponent(active.slug)}`, {
      method: "PUT",
      body: { content: document.getElementById("ed-proj").value },
    });
    toast("专卷已保存");
    renderBrain(active.slug);
  };
  document.getElementById("del-proj").onclick = async () => {
    if (!confirm(`确定删除《${active.title}》？它的细节卡片会保留。`)) return;
    await api(`/api/vault/brain/projects/${encodeURIComponent(active.slug)}`, { method: "DELETE" });
    location.hash = "#/brain";
  };
  view.querySelectorAll("[data-card]").forEach((btn) => {
    btn.onclick = async () => {
      await api(`/api/vault/cards/${encodeURIComponent(btn.dataset.card)}`, { method: "DELETE" });
      renderBrain(active.slug);
    };
  });
}

// ── 挂载 ──────────────────────────────────────────────────────────

async function renderMount() {
  const { brain } = await api("/api/vault/status");
  view.innerHTML = `
    <div class="split-aside">
      <div class="card">
        <h3>1. 要带上的专卷</h3>
        <p class="muted small" style="margin:0 0 8px">都不勾选时：写了任务就按任务自动挑最相关的 3 卷，没写任务就带最近更新的 3 卷。</p>
        <div class="list">
          ${brain.projects.map((p) => `
            <label style="margin:2px 0 2px ${p.depth * 20}px;color:var(--ink)">
              <input type="checkbox" class="m-proj" value="${esc(p.slug)}" style="width:auto" />
              ${p.depth ? "└ " : ""}${esc(p.title)} <span class="muted">(${p.chars} 字${p.cards ? ` · ${p.cards} 张卡片` : ""})</span>
            </label>`).join("") || `<div class="muted">还没有专卷</div>`}
        </div>
        <label style="margin-top:14px"><input type="checkbox" id="m-prin" checked style="width:auto" /> 带上思维与工程铁律</label>
        <label><input type="checkbox" id="m-cards" checked style="width:auto" /> 按任务检索相关细节卡片（最多约 2500 字）</label>
        <label style="margin-top:14px">2. 这次要交给 AI 的任务</label>
        <textarea id="m-task" style="min-height:100px" placeholder="例如：根据我现在的体能状态，帮我排下周的训练计划"></textarea>
        <div class="actions"><button id="btn-build-mount">生成并复制挂载包</button></div>
      </div>
      <div class="card">
        <div class="row"><h3>挂载包预览</h3><span class="badge teal" id="mount-len"></span></div>
        <p class="muted small" id="mount-info" style="margin:6px 0"></p>
        <textarea id="mount-out" style="min-height:440px"></textarea>
      </div>
    </div>`;

  const rebuild = async (copy) => {
    const slugs = Array.from(document.querySelectorAll(".m-proj:checked")).map((el) => el.value);
    const res = await api("/api/vault/mount", {
      method: "POST",
      body: {
        project_slugs: slugs.length ? slugs : null,
        include_principles: document.getElementById("m-prin").checked,
        include_cards: document.getElementById("m-cards").checked,
        task_prompt: document.getElementById("m-task").value,
      },
    });
    document.getElementById("mount-out").value = res.text;
    document.getElementById("mount-len").textContent = `共 ${res.chars} 字`;
    document.getElementById("mount-info").textContent =
      `专卷：${res.mounted_projects.join("、") || "无"}；细节卡片：${res.cards_used} 张`;
    if (copy) {
      await navigator.clipboard.writeText(res.text);
      toast("挂载包已复制，可以直接粘给任何 AI");
    }
  };
  document.getElementById("btn-build-mount").onclick = () => rebuild(true).catch((err) => toast(err.message));
  document.querySelectorAll(".m-proj, #m-prin, #m-cards").forEach((el) => { el.onchange = () => rebuild(false); });
  rebuild(false);
}

// ── 演进历史 ──────────────────────────────────────────────────────

async function renderEpochs() {
  const { brain } = await api("/api/vault/status");
  const history = brain.history || [];
  const list = (items) => esc((items || []).join("\n") || "无");
  view.innerHTML = `<div class="list">${
    history.map((h) => `
      <div class="card">
        <div class="row">
          <strong>Epoch ${h.epoch} · ${esc(h.summary)}</strong>
          <div class="badges">
            ${h.partial ? '<span class="badge over">部分完成</span>' : ""}
            <span class="badge teal">${esc(h.engine)}</span>
            <span class="badge">${h.segments || 0} 段 / ${h.batches || 0} 批 / ${h.cards || 0} 张卡片</span>
            <span class="badge">${esc(h.ran_at)}</span>
          </div>
        </div>
        <p class="muted small" style="margin:6px 0">来源：${esc((h.files || []).join("、"))}；改动的专卷：${esc((h.touched_projects || []).join("、") || "无")}</p>
        <div class="split" style="margin-top:10px">
          <div><label>被新信息取代的旧认知</label><pre>${list(h.overwritten)}</pre></div>
          <div><label>新归纳出的偏好或规律</label><pre>${list(h.abstracted)}</pre></div>
        </div>
        <label>丢弃的内容</label><pre>${list(h.pruned)}</pre>
        ${(h.warnings || []).length ? `<label>需要注意</label><pre>${list(h.warnings)}</pre>` : ""}
      </div>`).join("") || `<div class="empty">还没有蒸馏记录。快照保存在 <code>C:\\个人记忆库\\04_迭代快照_Epochs</code>。</div>`
  }</div>`;
}

// ── 配置 ──────────────────────────────────────────────────────────

async function renderConnect() {
  const [cfg, session] = await Promise.all([api("/api/settings"), api("/api/session")]);
  const llm = cfg.llm || {};
  view.innerHTML = `
    <div class="split">
      <div class="card">
        <h3>🔑 算力配置</h3>
        <p class="muted">在 Google AI Studio（aistudio.google.com）可以免费申请 Gemini API Key。</p>
        <label>算力提供方</label>
        <select id="cfg-provider">
          <option value="gemini" ${llm.provider === "gemini" ? "selected" : ""}>Google Gemini</option>
          <option value="openai_compat" ${llm.provider === "openai_compat" ? "selected" : ""}>OpenAI / DeepSeek 兼容接口</option>
          <option value="ollama" ${llm.provider === "ollama" ? "selected" : ""}>本地 Ollama</option>
        </select>
        <label>模型名称（官方下线某个模型时，会自动改用这个 Key 可用的 flash 模型）</label>
        <input id="cfg-model" value="${esc(llm.model || "gemini-3.8-flash")}" />
        <label>API Key（保存在本机 data/settings.json）</label>
        <input id="cfg-key" type="password" value="${esc(llm.api_key || "")}" />
        <label>接口地址</label>
        <input id="cfg-url" value="${esc(llm.base_url || "https://generativelanguage.googleapis.com/v1beta")}" />
        <label>代理地址（留空自动探测 127.0.0.1:7897 / 7890 等；填 direct 表示直连）</label>
        <input id="cfg-proxy" value="${esc(llm.proxy || "")}" placeholder="自动探测" />
        <label>记忆库根目录</label>
        <input id="cfg-vault" value="${esc(cfg.vault_dir || "C:/个人记忆库")}" />
        <label><input type="checkbox" id="cfg-archive" ${cfg.archive_after_distill ? "checked" : ""} style="width:auto" /> 文件全部消化后，自动移到「02_已消化归档」</label>
        <div class="actions"><button id="save-cfg">保存配置</button></div>
      </div>
      <div class="card">
        <h3>📂 投喂方式</h3>
        <ol class="steps">
          <li>把各家 AI 的导出包或自己的文档直接放进 <code>${esc(cfg.vault_dir || "C:\\个人记忆库")}\\01_投喂区_Inbox</code>，再点「⚡ 跑一轮 Epoch 蒸馏」。</li>
          <li>浏览器扩展：加载项目目录下的 <code>extension</code> 文件夹，填入本机令牌 <code>${esc(session.token)}</code>。</li>
        </ol>
      </div>
    </div>`;

  document.getElementById("cfg-provider").onchange = (e) => {
    const url = document.getElementById("cfg-url");
    const model = document.getElementById("cfg-model");
    if (e.target.value === "gemini") {
      url.value = "https://generativelanguage.googleapis.com/v1beta";
      if (!model.value || model.value.includes("qwen") || model.value.startsWith("gpt")) model.value = "gemini-3.8-flash";
    } else if (e.target.value === "ollama") {
      url.value = "http://127.0.0.1:11434";
    } else if (url.value.includes("11434") || url.value.includes("googleapis")) {
      url.value = "https://api.openai.com/v1";
    }
  };
  document.getElementById("save-cfg").onclick = async () => {
    await api("/api/settings", {
      method: "PUT",
      body: {
        vault_dir: document.getElementById("cfg-vault").value.trim(),
        archive_after_distill: document.getElementById("cfg-archive").checked,
        llm: {
          provider: document.getElementById("cfg-provider").value,
          model: document.getElementById("cfg-model").value.trim(),
          api_key: document.getElementById("cfg-key").value.trim(),
          base_url: document.getElementById("cfg-url").value.trim(),
          proxy: document.getElementById("cfg-proxy").value.trim(),
        },
      },
    });
    toast("配置已保存");
    renderConnect();
  };
}

window.addEventListener("hashchange", () => route().catch((err) => toast(err.message)));
route().catch((err) => { view.innerHTML = `<div class="empty">加载失败：${esc(err.message)}</div>`; });
api("/api/vault/distill/job").then((job) => { if (job.running) pollJob(); }).catch(() => {});

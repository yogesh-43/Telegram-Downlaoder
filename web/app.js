const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  chat: "",
  items: [],
  selected: new Set(),
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    const msg = Array.isArray(detail)
      ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
      : (detail || data.message || res.statusText);
    throw new Error(msg);
  }
  return data;
}

async function withBusy(button, fn) {
  if (!button) return fn();
  button.disabled = true;
  button.classList.add("busy");
  try {
    return await fn();
  } finally {
    button.classList.remove("busy");
    if (button.id !== "btn-download") button.disabled = false;
  }
}

function fillApiFields(settings) {
  if (!settings) return;
  if (settings.api_id) $("#api-id").value = settings.api_id;
  if (settings.api_hash) $("#api-hash").value = settings.api_hash;
}

function showAuth(step) {
  $("#auth").hidden = false;
  $("#app").hidden = true;
  $$(".steps li").forEach((el) => el.classList.toggle("on", el.dataset.step === step));
  $("#form-api").hidden = step !== "api";
  $("#form-phone").hidden = step !== "phone";
  $("#form-code").hidden = step !== "code";
  $("#form-2fa").hidden = step !== "2fa";
}

function showApp(user, settings) {
  $("#auth").hidden = true;
  $("#app").hidden = false;
  $("#who").textContent = user ? `${user.name}${user.username ? " · @" + user.username : ""}` : "";
  if (settings) {
    $("#scan-limit").value = settings.scan_limit;
    $("#download-dir").value = settings.download_dir;
    $("#concurrency").value = settings.concurrency;
    $("#prefix-date").checked = settings.prefix_date;
    if (settings.download_dir) $("#save-path").textContent = settings.download_dir;
  }
}

function setAuthError(msg) {
  const el = $("#auth-error");
  el.hidden = !msg;
  el.textContent = msg || "";
}

function formatBytes(n) {
  if (!n) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatSpeed(n) {
  return n ? `${formatBytes(n)}/s` : "";
}

function selectedKinds() {
  return $$('input[name="kind"]:checked').map((el) => el.value);
}

function chatRef(chat) {
  return chat.ref || (chat.username ? `@${chat.username}` : String(chat.id));
}

function addChatButton(chat, extraClass) {
  const box = $("#chat-list");
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "chat" + (extraClass ? " " + extraClass : "");
  const kind = extraClass === "topic" ? "topic" : chat.kind;
  btn.innerHTML = `<strong>${escapeHtml(chat.title)}</strong><small>${escapeHtml(kind)}${chat.username ? " · @" + escapeHtml(chat.username) : ""}</small>`;
  btn.addEventListener("click", () => {
    $$(".chat").forEach((el) => el.classList.remove("active"));
    btn.classList.add("active");
    const value = chatRef(chat);
    $("#chat-input").value = value;
    state.chat = value;
  });
  box.appendChild(btn);
}

function renderChats(chats) {
  const box = $("#chat-list");
  box.innerHTML = "";
  if (!chats.length) {
    box.innerHTML = '<p class="empty">No chats match that search.</p>';
    return;
  }
  chats.forEach((chat) => {
    addChatButton(chat);
    (chat.topics || []).forEach((topic) => addChatButton(topic, "topic"));
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function pendingItems() {
  return state.items.filter((item) => !item.saved && !item.pending);
}

function badgeFor(item) {
  if (item.saved) return '<span class="badge on-disk">on disk</span>';
  if (item.pending) return '<span class="badge queued">queued</span>';
  return `<span class="badge">${escapeHtml(item.kind)}</span>`;
}

function renderMedia() {
  const grid = $("#media-grid");
  grid.innerHTML = "";
  if (!state.items.length) {
    grid.innerHTML = '<div class="empty-card"><strong>No media yet</strong><p>Pick a chat on the left, or paste a link, then click Fetch media.</p></div>';
    updateSelection();
    return;
  }
  state.items.forEach((item) => {
    const card = document.createElement("article");
    const picked = state.selected.has(item.id);
    const locked = item.saved || item.pending;
    card.className = "card" + (picked ? " picked" : "") + (item.saved ? " saved" : "") + (item.pending ? " queued" : "");
    card.innerHTML = `
      <header>
        ${badgeFor(item)}
        <input type="checkbox" ${picked ? "checked" : ""} ${locked ? "disabled" : ""} aria-label="Select ${escapeHtml(item.filename)}" />
      </header>
      <div class="name">${escapeHtml(item.filename)}</div>
      <div class="meta">${formatBytes(item.size)} · ${escapeHtml((item.date || "").slice(0, 10))}${item.saved ? " · already downloaded" : item.pending ? " · downloading" : ""}</div>
    `;
    const toggle = () => {
      if (locked) return;
      if (state.selected.has(item.id)) state.selected.delete(item.id);
      else state.selected.add(item.id);
      updateSelection();
      renderMedia();
    };
    card.addEventListener("click", (e) => {
      if (e.target.type === "checkbox") return;
      toggle();
    });
    card.querySelector("input").addEventListener("change", toggle);
    grid.appendChild(card);
  });
  updateSelection();
}

function updateSelection() {
  const pending = pendingItems();
  const n = state.selected.size;
  $("#selection-label").textContent = `${n} of ${pending.length} selected`;
  $("#btn-download").disabled = n === 0 || $("#btn-download").classList.contains("busy");
  $("#select-all").checked = pending.length > 0 && pending.every((item) => state.selected.has(item.id));
}

let lastQueueKey;

function renderQueue(jobs) {
  const box = $("#queue-list");
  const active = jobs.filter((job) => job.status !== "done" && job.status !== "skipped");
  const key = active.map((job) => `${job.id}:${job.status}:${job.received}:${job.error || ""}`).join("|");
  if (key === lastQueueKey) return;
  lastQueueKey = key;
  if (!active.length) {
    box.innerHTML = '<p class="hint">Only in-progress downloads show here. Completed files are on the hard disk, not in this browser tab.</p>';
    return;
  }
  box.innerHTML = active.map((job) => {
    const pct = job.total ? Math.min(100, Math.round((job.received / job.total) * 100)) : 0;
    return `<article class="job ${job.status}">
      <div class="row"><strong>${escapeHtml(job.filename)}</strong><span>${escapeHtml(job.status)}</span></div>
      <div class="row"><span>${escapeHtml(job.chat || "")}</span><span>${formatSpeed(job.speed)}</span></div>
      <div class="bar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><span style="width:${pct}%"></span></div>
      ${job.error ? `<div class="err">${escapeHtml(job.error)}</div>` : ""}
    </article>`;
  }).join("");
}

function syncDownloadState(jobs) {
  const active = new Set(
    jobs.filter((job) => job.status === "queued" || job.status === "downloading").map((job) => job.message_id),
  );
  const failed = new Set(jobs.filter((job) => job.status === "error").map((job) => job.message_id));
  let changed = false;
  state.items.forEach((item) => {
    if (failed.has(item.id) && item.pending) {
      item.pending = false;
      changed = true;
    } else if (active.has(item.id) && !item.pending && !item.saved) {
      item.pending = true;
      state.selected.delete(item.id);
      changed = true;
    } else if (item.pending && !active.has(item.id) && !failed.has(item.id)) {
      item.pending = false;
      item.saved = true;
      state.selected.delete(item.id);
      changed = true;
    }
  });
  if (changed) renderMedia();
}

function renderSaved(folder, saved) {
  if (folder) $("#save-path").textContent = folder;
  if (saved == null) return;
  const n = Number(saved) || 0;
  $("#save-count").textContent = n ? `${n} file(s) on the hard disk` : "Each finished file is written to the hard disk immediately.";
}

async function boot() {
  const status = await api("/api/status");
  fillApiFields(status.settings);
  if (status.ready) {
    showApp(status.user, status.settings);
    loadChats();
    renderQueue(status.queue || []);
    renderSaved(status.folder || status.settings?.download_dir, status.saved);
  } else if (status.has_api) {
    showAuth("phone");
  } else {
    showAuth("api");
  }
}

async function loadChats(q = "") {
  $("#chat-list").innerHTML = '<p class="empty">Loading chats…</p>';
  try {
    const data = await api(`/api/chats?q=${encodeURIComponent(q)}`);
    renderChats(data.chats || []);
  } catch (err) {
    $("#chat-list").innerHTML = `<p class="empty">${escapeHtml(err.message)}</p>`;
  }
}

$("#form-api").addEventListener("submit", async (e) => {
  e.preventDefault();
  setAuthError("");
  const btn = e.submitter || $("#form-api button[type=submit]");
  try {
    await withBusy(btn, () => api("/api/setup", {
      method: "POST",
      body: { api_id: Number($("#api-id").value), api_hash: $("#api-hash").value.trim() },
    }));
    showAuth("phone");
  } catch (err) {
    setAuthError(err.message);
  }
});

const AUTH_STEPS = ["api", "phone", "code", "2fa"];

function goBackTo(step) {
  setAuthError("");
  showAuth(step);
}

$$(".back").forEach((btn) => {
  btn.addEventListener("click", () => goBackTo(btn.dataset.back));
});

$$(".steps li").forEach((el) => {
  el.addEventListener("click", () => {
    const current = $(".steps li.on")?.dataset.step;
    const target = el.dataset.step;
    if (AUTH_STEPS.indexOf(target) <= AUTH_STEPS.indexOf(current)) goBackTo(target);
  });
});

$("#toggle-password").addEventListener("click", () => {
  const input = $("#password");
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  $("#toggle-password").textContent = show ? "Hide" : "Show";
  $("#toggle-password").setAttribute("aria-pressed", show ? "true" : "false");
});

$("#form-phone").addEventListener("submit", async (e) => {
  e.preventDefault();
  setAuthError("");
  const btn = e.submitter || $("#form-phone button[type=submit]");
  try {
    const data = await withBusy(btn, () => api("/api/login/send", {
      method: "POST",
      body: { phone: $("#phone").value.trim() },
    }));
    if (data.step === "ready") {
      const status = await api("/api/status");
      showApp(status.user, status.settings);
      loadChats();
    } else {
      showAuth("code");
    }
  } catch (err) {
    setAuthError(err.message);
  }
});

$("#form-code").addEventListener("submit", async (e) => {
  e.preventDefault();
  setAuthError("");
  const btn = e.submitter || $("#form-code button[type=submit]");
  try {
    const data = await withBusy(btn, () => api("/api/login/code", {
      method: "POST",
      body: { code: $("#code").value.trim() },
    }));
    if (data.step === "need_password") showAuth("2fa");
    else {
      const status = await api("/api/status");
      showApp(status.user, status.settings);
      loadChats();
    }
  } catch (err) {
    setAuthError(err.message);
  }
});

$("#form-2fa").addEventListener("submit", async (e) => {
  e.preventDefault();
  setAuthError("");
  const btn = e.submitter || $("#form-2fa button[type=submit]");
  try {
    await withBusy(btn, () => api("/api/login/2fa", { method: "POST", body: { password: $("#password").value } }));
    const status = await api("/api/status");
    showApp(status.user, status.settings);
    loadChats();
  } catch (err) {
    setAuthError(err.message);
  }
});

let searchTimer;
$("#chat-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadChats(e.target.value.trim()), 250);
});

$("#btn-scan").addEventListener("click", async () => {
  const chat = $("#chat-input").value.trim();
  if (!chat) {
    $("#scan-status").textContent = "Pick a chat or paste a link first.";
    return;
  }
  state.chat = chat;
  state.selected.clear();
  $("#scan-status").textContent = "Scanning… this can take a minute on large channels.";
  $("#media-grid").innerHTML = '<div class="empty-card"><strong>Scanning…</strong><p>Telegram is listing media in this chat.</p></div>';
  try {
    const data = await withBusy($("#btn-scan"), () => api("/api/scan", {
      method: "POST",
      body: { chat, kinds: selectedKinds(), limit: Number($("#scan-limit").value) },
    }));
    state.items = data.items || [];
    renderMedia();
    const saved = state.items.filter((item) => item.saved).length;
    $("#scan-status").textContent = saved
      ? `${data.count} files in ${data.chat} · ${saved} already on disk`
      : `${data.count} files in ${data.chat}`;
    if (!state.items.length) {
      $("#media-grid").innerHTML = '<div class="empty-card"><strong>No matching files</strong><p>Try other media types or raise the scan limit.</p></div>';
    }
  } catch (err) {
    $("#scan-status").textContent = err.message;
    $("#media-grid").innerHTML = `<div class="empty-card"><strong>Could not scan</strong><p>${escapeHtml(err.message)}</p></div>`;
  }
});

$("#select-all").addEventListener("change", (e) => {
  state.selected = e.target.checked ? new Set(pendingItems().map((item) => item.id)) : new Set();
  renderMedia();
});

$("#btn-download").addEventListener("click", async () => {
  if (!state.chat || state.selected.size === 0) return;
  const ids = [...state.selected];
  try {
    const data = await withBusy($("#btn-download"), () => api("/api/download", {
      method: "POST",
      body: { chat: state.chat, message_ids: ids },
    }));
    ids.forEach((id) => {
      const item = state.items.find((row) => row.id === id);
      if (item && !item.saved) item.pending = true;
    });
    state.selected.clear();
    renderMedia();
    lastQueueKey = "";
    const queued = (data.job_ids || []).length;
    const skipped = data.skipped || 0;
    $("#scan-status").textContent = skipped
      ? `Queued ${queued} file(s), skipped ${skipped} already on disk.`
      : `Queued ${queued} file(s). Watch the Downloads panel.`;
  } catch (err) {
    $("#scan-status").textContent = err.message;
    updateSelection();
  }
});

$("#btn-logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  showAuth("phone");
});

$("#btn-settings").addEventListener("click", () => $("#settings").showModal());
$("#save-settings").addEventListener("click", async (e) => {
  e.preventDefault();
  await api("/api/settings", {
    method: "POST",
    body: {
      download_dir: $("#download-dir").value.trim(),
      concurrency: Number($("#concurrency").value),
      prefix_date: $("#prefix-date").checked,
      scan_limit: Number($("#scan-limit").value),
    },
  });
  $("#settings").close();
  renderSaved($("#download-dir").value.trim(), null);
});

$("#btn-open-folder").addEventListener("click", async () => {
  try {
    const data = await api("/api/local/open", { method: "POST", body: {} });
    if (data.folder) $("#save-path").textContent = data.folder;
  } catch (err) {
    $("#save-path").textContent = err.message;
  }
});

setInterval(async () => {
  if ($("#app").hidden) return;
  try {
    const data = await api("/api/queue");
    renderQueue(data.queue || []);
    syncDownloadState(data.queue || []);
    renderSaved(data.folder, data.saved);
  } catch {
    /* keep last queue view if the server blips */
  }
}, 800);

boot().catch((err) => {
  setAuthError(err.message);
  showAuth("api");
});
